# lb — SNI load balancer

HAProxy on `lb.example.org` (192.0.2.167; the documents use `example.org`
for the site's domain, whose real value is in the inventories), routing TLS connections by
their SNI name to backend servers, plus HAProxy's Data Plane API. The site doesn't
need it today: clusters in LAN mode have their own addresses (API VIP,
Gateway LoadBalancer IPs), so the VM doesn't exist; the playbook re-creates
it. Use it for internal-only clusters reachable from the LAN, or one public
entry point routed by name to several clusters.

| Path | What |
|---|---|
| `lbctl.py`, `routes.yaml` | The routes and the tool that renders, applies and monitors them; the LB's host name comes from `ansible/inventory/hosts.yml` (or `$LB_HOST`) |
| `ansible/lb.yml` | Creates the VM when missing (role `libvirt_vm`) and deploys HAProxy, the routes and the Data Plane API (role `haproxy_lb`) |

The playbook is one more host of the site: it uses the site inventory, shared
roles and credential lookup of
[infra](https://github.com/vincentbrobald/infra), checked out next to this
repository (`/root/lab/infra`, see `ansible/ansible.cfg`).

## Documentation

The documentation follows [Diátaxis](https://diataxis.fr/): how-to guides for
tasks, reference to look things up, explanation to understand.

**How-to guides**
- [Deploy or retire the load balancer](docs/how-to/deploy-or-retire.md)
- [Manage routes](docs/how-to/manage-routes.md): a host name to its backends, DNS, certificates

**Reference**
- [Routing](docs/reference/routing.md): ports, health checks, failover, limits, the Data Plane API

**Explanation**
- [Design decisions](docs/explanation/design-decisions.md): SNI passthrough, why it isn't running

## License

MIT (LICENSE), © 2026 Vincent Brobald.
