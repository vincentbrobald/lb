# Manage routes

> How-to · [Documentation](../../README.md#documentation)

Send a host name through the load balancer to its backends. What a route
does: [Routing](../reference/routing.md).

1. Add the route to `routes.yaml` (its comments have an example for a
   cluster's API and `*.apps`).
2. See, then apply it:

   ```sh
   /root/lab/lb/lbctl.py render    # show the generated haproxy.cfg
   /root/lab/lb/lbctl.py apply     # validate on the LB, install, reload (no dropped connections)
   ```

   `apply` validates the configuration on the LB first: an invalid one
   changes nothing. The previous one is kept as `/etc/haproxy/haproxy.cfg.prev`.
3. Point the names at the load balancer: the router only registers DHCP host
   names, so add the records (CNAME to `lb.example.org`) on the router,
   e.g. `api.<cluster>.example.org` and `*.apps.<cluster>.example.org`.
4. For a Kubernetes API, put the routed name in the API server's certificate
   (Talos `certExtraSANs`), or `kubectl` refuses it.
5. Check:

   ```sh
   /root/lab/lb/lbctl.py status    # health of every backend server
   ```
