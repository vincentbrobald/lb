# Design decisions

> Explanation · [Documentation](../../README.md#documentation)

The choices the SNI load balancer is built on, and why it isn't running.

### Not running, kept as code

**Decision.** The VM is destroyed; the code and playbook stay, and
`ansible-playbook lb.yml` re-creates it.
**Why.** Clusters in LAN mode have their own addresses (the API VIP, the
ingress gateway's LoadBalancer address), so nothing on the site needs a load
balancer in front of them.
**Revisit if** clusters without LAN mode must be reached from the LAN, or one
public entry point must serve several clusters by name.

### SNI passthrough, no TLS termination

**Decision.** HAProxy routes TLS connections by their SNI name without
decrypting; plain HTTP on port 80 by `Host`.
**Why.** Backends keep their own certificates (a Kubernetes API its cluster
CA, a gateway its step-ca certificate); the load balancer holds no keys.
**Cost.** Backends see the load balancer's address, not the client's (no
PROXY protocol).

### Routes as data, applied by `lbctl.py`

**Decision.** Routes are declared in `routes.yaml`; `lbctl.py` renders the
configuration, copies it over SSH, validates it with `haproxy -c` on the VM,
installs it and reloads HAProxy without dropping connections. HAProxy's Data
Plane API is installed beside it, for tools that prefer an API.
**Why.** An invalid configuration changes nothing, and the previous one is
kept for comparison; one rendered file is easier to review than a series of
API calls.

### HAProxy from the distribution

**Decision.** HAProxy 3.0 from AlmaLinux AppStream, confined by SELinux.
**Why.** Security updates with the OS; no extra repository.

### Shared roles from infra

**Decision.** The playbook uses infra's inventory, roles and credential lookup;
this repository only adds the `lb` host and its role.
**Why.** One way to build VMs on the site.

### One VM

**Accepted limit.** A single VM is a single point of failure; a second one
with a shared VIP (keepalived) would make it redundant.
