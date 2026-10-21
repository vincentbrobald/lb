#!/usr/bin/env python3
"""Manage the SNI-routing load balancer (HAProxy on the LB host: $LB_HOST, else
the lb host of ansible/inventory/hosts.yml).

Routes live in routes.yaml next to this script (or -f FILE). Each route maps
hostnames to backend servers:

  TLS ports (e.g. 443, 6443): routed by the TLS SNI name and passed through
  untouched (no TLS termination), so backends keep their own certificates.
  Port 80: routed by the HTTP Host header (plain HTTP, e.g. ACME challenges).
  Unknown names are refused.

  lbctl.py render              print the HAProxy configuration
  lbctl.py ports               print the ports HAProxy binds or connects to
  lbctl.py apply               validate on the LB, install it, reload HAProxy
  lbctl.py status              show the health of every backend server

apply keeps the LB's "userlist controller" (the Data Plane API's users).
"""

from __future__ import annotations

import argparse
import functools
import ipaddress
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
# The LB's SSH key lives here: $INFRA_SECRETS/lb (default /root/lab/lab-secrets/lb).
SECRETS = Path(os.environ.get("INFRA_SECRETS", "/root/lab/lab-secrets")) / "lb"


@functools.cache
def lb_host() -> str:
    """Return the LB host: $LB_HOST, else the first host of group lb in ansible/inventory/hosts.yml.

    Exits with a message when neither gives one. Resolved on first use, so the
    commands that don't reach the LB (render, ports, --help) don't need it.
    """
    if os.environ.get("LB_HOST"):
        return os.environ["LB_HOST"]
    try:
        inv = yaml.safe_load((HERE / "ansible" / "inventory" / "hosts.yml").read_text()) or {}
        return next(iter(inv["lb"]["hosts"]))
    except (OSError, KeyError, TypeError, StopIteration, yaml.YAMLError):
        sys.exit("lbctl.py: set LB_HOST, or list the LB under 'lb' in ansible/inventory/hosts.yml")




def ssh_cmd() -> list[str]:
    """Return the ssh command line to the LB as almalinux, non-interactive; the LB's host key is not checked."""
    return ["ssh", "-i", str(SECRETS / "id_ed25519"), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "BatchMode=yes", f"almalinux@{lb_host()}"]

HTTP_PORT = 80
DEFAULT_TLS_PORTS = (443, 6443)
# A lower-case host name with at least two labels, the last alphabetic, optionally
# prefixed with "*." (a wildcard).
HOST_RE = re.compile(r"(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}")


class LbError(Exception):
    """An error to report as "ERROR: ..." with exit status 1."""
    pass


def load_routes(path: Path) -> list[dict]:
    """Read and check the routes file; return each route as name, hosts, port, backends, check, http.

    backends and http are (ip, port) pairs; port defaults to 443 and check to
    "tcp". Raises LbError for unknown keys, invalid or duplicate names, host
    names, ports or endpoints, and for a host routed twice on the same port
    (port 80 counts for routes with http backends).
    """
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise LbError(f"cannot read {path}: {e}") from e
    unknown = set(data) - {"routes"}
    if unknown:
        raise LbError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")
    routes, names, claimed = [], set(), {}
    for i, r in enumerate(data.get("routes") or []):
        where = f"routes[{i}]"
        if not isinstance(r, dict):
            raise LbError(f"{where}: expected a mapping")
        extra = set(r) - {"name", "hosts", "port", "backends", "healthCheck", "http"}
        if extra:
            raise LbError(f"{where}: unknown key(s): {', '.join(sorted(extra))}")
        name = r.get("name", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(name)) or name in names:
            raise LbError(f"{where}: 'name' must be a unique [a-z0-9-] name")
        names.add(name)
        hosts = r.get("hosts") or []
        if not hosts or not all(isinstance(h, str) and HOST_RE.fullmatch(h) for h in hosts):
            raise LbError(f"{name}: 'hosts' must list host names (exact, or *.domain wildcards)")
        port = r.get("port", 443)
        if not isinstance(port, int) or not 1 <= port <= 65535 or port == HTTP_PORT:
            raise LbError(f"{name}: 'port' must be a TLS port (not {HTTP_PORT})")
        for h in hosts:
            if (port, h) in claimed:
                raise LbError(f"{name}: host {h} on port {port} is already routed by {claimed[(port, h)]}")
            claimed[(port, h)] = name
        check = r.get("healthCheck", "tcp")
        if check not in ("tcp", "readyz"):
            raise LbError(f"{name}: 'healthCheck' must be tcp or readyz")
        backends = [_endpoint(b, f"{name}.backends") for b in r.get("backends") or []]
        if not backends:
            raise LbError(f"{name}: 'backends' must list at least one ip:port")
        http = [_endpoint(b, f"{name}.http") for b in r.get("http") or []]
        if http:
            for h in hosts:
                if (HTTP_PORT, h) in claimed:
                    raise LbError(f"{name}: host {h} on port 80 is already routed by {claimed[(HTTP_PORT, h)]}")
                claimed[(HTTP_PORT, h)] = name
        routes.append({"name": name, "hosts": hosts, "port": port, "backends": backends, "check": check, "http": http})
    return routes


def _endpoint(value, where: str) -> tuple[str, int]:
    """Parse "ip:port" (IPv4) into (ip, port); raise LbError naming where otherwise.

    """
    m = re.fullmatch(r"([0-9.]+):(\d+)", str(value))
    try:
        ipaddress.IPv4Address(m.group(1))
        port = int(m.group(2))
    except (AttributeError, ValueError):
        raise LbError(f"{where}: '{value}' must be ip:port") from None
    if not 1 <= port <= 65535:
        raise LbError(f"{where}: '{value}' must be ip:port")
    return m.group(1), port


def _sni_acl(host: str) -> str:
    """The HAProxy condition matching host as TLS SNI name, case-insensitively.

    A wildcard *.d matches names ending in ".d" (any depth below d, not d itself).
    """
    if host.startswith("*."):
        return f"req.ssl_sni -i -m end {host[1:]}"
    return f"req.ssl_sni -i {host}"


def _host_acl(host: str) -> str:
    """The HAProxy condition matching host as HTTP Host header (port stripped, lower-cased).

    A wildcard *.d matches names ending in ".d", as in _sni_acl().
    """
    expr = "hdr(host),field(1,:),lower"
    if host.startswith("*."):
        return f"{expr} -m end {host[1:]}"
    return f"{expr} -m str {host}"


def render(routes: list[dict]) -> str:
    """Return the complete HAProxy configuration for routes.

    One TCP frontend per TLS port (443 and 6443 always, plus the routes'),
    routing on SNI; one HTTP frontend on port 80, routing on Host, unknown
    hosts answered 404; one backend per route (tls_<name>, and http_<name>
    when it has http servers), with health checks every second.
    """
    out = [textwrap.dedent("""\
        # Generated by lbctl.py from routes.yaml - do not edit on the LB.
        global
            log /dev/log local0 info
            user haproxy
            group haproxy
            maxconn 20000
            stats socket /run/haproxy/admin.sock mode 660 level admin
            stats timeout 30s

        defaults
            log global
            option dontlognull
            # A connection that can't reach its server (e.g. one that just died,
            # before the health check notices) is retried on another server.
            retries 2
            option redispatch 1
            # LAN backends: a dead server doesn't answer at all, so keep the
            # connect timeout short (then retry elsewhere) and detect it fast.
            timeout connect 1s
            timeout check 1s
            timeout client 1h
            timeout server 1h
            timeout tunnel 1h
            timeout client-fin 30s
            timeout server-fin 30s
        """)]
    ports = sorted(set(DEFAULT_TLS_PORTS) | {r["port"] for r in routes})
    for port in ports:
        out.append(textwrap.dedent(f"""
            frontend tls_{port}
                mode tcp
                option tcplog
                bind :{port}
                # Wait for the TLS ClientHello, then route on its SNI name.
                tcp-request inspect-delay 5s
                tcp-request content reject unless {{ req.ssl_hello_type 1 }}"""))
        for r in routes:
            if r["port"] == port:
                for h in r["hosts"]:
                    out.append(f"    use_backend tls_{r['name']} if {{ {_sni_acl(h)} }}")
        out.append("    # Unknown or missing SNI: no backend, connection closed.")
    out.append(textwrap.dedent("""
        frontend http_80
            mode http
            option httplog
            bind :80
            timeout http-request 10s"""))
    for r in routes:
        for h in (r["hosts"] if r["http"] else []):
            out.append(f"    use_backend http_{r['name']} if {{ {_host_acl(h)} }}")
    # http-request rules run before backend selection, so unknown hosts are
    # answered by a default backend rather than a frontend deny rule.
    out.append("    default_backend http_unknown_host")
    out.append("\nbackend http_unknown_host\n    mode http\n"
               '    http-request return status 404 content-type text/plain string "unknown host"')
    for r in routes:
        check = ("    option httpchk GET /readyz\n    http-check expect status 200\n"
                 "    default-server check check-ssl verify none inter 1s fall 2 rise 2"
                 if r["check"] == "readyz" else "    default-server check inter 1s fall 2 rise 2")
        servers = "\n".join(f"    server s{i} {ip}:{port}" for i, (ip, port) in enumerate(r["backends"], 1))
        out.append(f"\nbackend tls_{r['name']}\n    mode tcp\n    balance leastconn\n{check}\n{servers}")
        if r["http"]:
            servers = "\n".join(f"    server s{i} {ip}:{port}" for i, (ip, port) in enumerate(r["http"], 1))
            out.append(f"\nbackend http_{r['name']}\n    mode http\n    balance roundrobin\n"
                       f"    default-server check inter 1s fall 2 rise 2\n{servers}")
    return "\n".join(out) + "\n"


def ssh(cmd: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run cmd on the LB over ssh, input on its stdin; return the result with output captured.

    With check, a non-zero exit raises LbError carrying the output.
    """
    p = subprocess.run(ssh_cmd() + [cmd], input=input, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise LbError((p.stdout + p.stderr).strip() or f"remote command failed: {cmd}")
    return p


def ports(routes: list[dict]) -> list[int]:
    """Ports HAProxy binds or connects to: SELinux must label them http_port_t."""
    return sorted(set(DEFAULT_TLS_PORTS) | {r["port"] for r in routes} | {HTTP_PORT}
                  | {p for r in routes for _, p in r["backends"] + r["http"]})


def apply(routes: list[dict]) -> None:
    """Install the configuration of routes on the LB and reload (or start) HAProxy.

    Runs one script as root over ssh: the new configuration plus the current
    "userlist controller" block is validated first (exit 3: LbError, nothing
    changed); the ports get the SELinux type http_port_t; the old file is kept
    as haproxy.cfg.prev. Raises LbError on any failure.
    """
    cfg = render(routes)
    # The remote script reads the configuration on stdin. awk appends the
    # current "userlist controller" block (to the first blank line). For each
    # port, semanage -a adds the label unless already there, and -m modifies a
    # port that another type already holds.
    script = textwrap.dedent(f"""\
        set -euo pipefail
        tmp=$(mktemp); cat > "$tmp"
        # The Data Plane API's users (provisioned by the infra playbooks) stay.
        if [ -f /etc/haproxy/haproxy.cfg ]; then
            awk '/^userlist controller/ {{ f = 1; print "" }} f && /^$/ {{ f = 0 }} f' /etc/haproxy/haproxy.cfg >> "$tmp"
        fi
        if ! haproxy -c -q -f "$tmp"; then haproxy -c -f "$tmp" 2>&1 | tail -5; rm -f "$tmp"; exit 3; fi
        for p in {' '.join(map(str, ports(routes)))}; do
            semanage port -l | grep -E '^http_port_t\\s' | grep -qw "$p" \\
                || semanage port -a -t http_port_t -p tcp "$p" 2>/dev/null || semanage port -m -t http_port_t -p tcp "$p"
        done
        [ -f /etc/haproxy/haproxy.cfg ] && cp -p /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.prev
        install -m 640 -o root -g haproxy "$tmp" /etc/haproxy/haproxy.cfg; rm -f "$tmp"
        restorecon /etc/haproxy/haproxy.cfg
        if systemctl is-active -q haproxy; then systemctl reload haproxy; else systemctl start haproxy; fi
        sleep 1; systemctl is-active haproxy
        """)
    p = ssh(f"sudo bash -c {sh_quote(script)}", input=cfg, check=False)
    if p.returncode == 3:
        raise LbError("HAProxy rejected the configuration (nothing changed):\n" + p.stdout.strip())
    if p.returncode != 0:
        raise LbError((p.stdout + p.stderr).strip())
    print(f"Applied {len(routes)} route(s) to {lb_host()}; haproxy {p.stdout.strip().splitlines()[-1]}")


def status() -> None:
    """Print each backend server's address, status, last check and current sessions,
    from HAProxy's "show stat" CSV on the LB's admin socket."""
    p = ssh("echo 'show stat' | sudo socat stdio /run/haproxy/admin.sock")
    rows = [l.split(",") for l in p.stdout.splitlines() if l and not l.startswith("#")]
    print(f"{'BACKEND':<22} {'SERVER':<8} {'ADDRESS':<22} {'STATUS':<10} {'CHECK':<22} SESSIONS")
    for r in rows:
        pxname, svname = r[0], r[1]
        if svname in ("FRONTEND", "BACKEND"):
            continue
        # CSV fields: 4 scur, 17 status, 36 check_status, 37 check_code, 73 addr
        # (shorter rows, without addr, leave it empty).
        addr, st, check, cur = r[73] if len(r) > 73 else "", r[17], f"{r[36]} {r[37]}".strip(), r[4]
        print(f"{pxname:<22} {svname:<8} {addr:<22} {st:<10} {check:<22} {cur}")


def sh_quote(s: str) -> str:
    """Quote s as one single-quoted shell word (each ' becomes '"'"')."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main() -> int:
    """Run the command given on the command line; return 0, or 1 after printing an LbError."""
    ap = argparse.ArgumentParser(prog="lbctl.py", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    ap.add_argument("command", choices=("render", "ports", "apply", "status"))
    ap.add_argument("-f", "--file", type=Path, default=HERE / "routes.yaml", help="routes file (default: routes.yaml)")
    args = ap.parse_args()
    try:
        if args.command == "status":
            status()
            return 0
        routes = load_routes(args.file)
        if args.command == "render":
            sys.stdout.write(render(routes))
        elif args.command == "ports":
            print(" ".join(map(str, ports(routes))))
        else:
            apply(routes)
    except LbError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
