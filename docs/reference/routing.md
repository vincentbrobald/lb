# Routing

> Reference · [Documentation](../../README.md#documentation)

What the load balancer does with a connection, and its limits.

HAProxy 3.0, from AlmaLinux AppStream. It routes connections by host name to
backend servers.

- **TLS ports** (443, 6443, plus any port used in a route): routed by the TLS
  **SNI** name and **passed through** without decrypting. Backends keep their own
  certificates. For example, a Kubernetes API answers with its own cluster CA,
  and a Gateway with its step-ca certificate.
- **Port 80:** plain HTTP, routed by the `Host` header (useful for HTTP→HTTPS
  redirects and ACME http-01).
- **Unknown names** are refused: the TLS connection is closed, and HTTP gets a
  404.
- **Health checks** every second (`/readyz` for Kubernetes API servers, TCP
  otherwise). A server is marked down after 2 failures and back up after 2
  successes.
- **Failover:** with a 1 s connect timeout and `option redispatch`, a connection
  that can't reach a dead server is retried on another one.
  - Tested by hard-powering-off each of 3 control planes in turn:
    498/498 API requests through the LB succeeded.

- **Routes:** `routes.yaml`, applied by `lbctl.py`
  ([Manage routes](../how-to/manage-routes.md)).
- **SELinux:** HAProxy runs confined. `apply` labels the ports it uses as
  `http_port_t`.
- **Client addresses:** backends see the LB's address, not the client's (no
  PROXY protocol).
- **Single point of failure:** this is a single VM. A second one with a shared
  VIP (keepalived) would make it redundant.

## Data Plane API

The HAProxy Data Plane API v3.4.4 (Apache-2.0, release RPM checked against the
release `checksums.txt`; the RPM itself is unsigned) runs on the LB as service
`dataplaneapi`:
- **URL:** `https://lb.example.org:5555/v3`.
- **Auth:** user `admin`; the password is in `/root/lab/lab-secrets/lb/dataplaneapi-password`.
  The VM only stores a SHA-512 hash, in the HAProxy userlist `controller`.
- **TLS:** a step-ca certificate, renewed on the VM by
  `dataplaneapi-cert-renew.service` (`step ca renew --daemon`).
- **Deployment:** `ansible/lb.yml` (below), with HAProxy and the routes.
- **The API rewrites `haproxy.cfg`** in its own layout as soon as the file
  changes. It writes through temporary files in `/etc/haproxy/.dataplaneapi-tmp`
  (from `/tmp` the file would keep the `tmp_t` SELinux label, which HAProxy
  may not read), and reloads through `/usr/local/sbin/haproxy-ctl`, which
  restores the label first.
- **Not used by the tools:** `lbctl.py apply` writes `haproxy.cfg` directly
  (over SSH) and keeps the `userlist controller` the API needs;
  `deploy-talos.py` doesn't register routes.
