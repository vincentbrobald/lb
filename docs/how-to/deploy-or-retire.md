# Deploy or retire the load balancer

> How-to · [Documentation](../../README.md#documentation)

Create the VM and deploy HAProxy, its routes and the Data Plane API, or take
it away again. The VM doesn't exist while nothing needs it: why,
[Design decisions](../explanation/design-decisions.md).

## Deploy

The playbook uses the site inventory and shared roles of `/root/lab/infra/ansible`
(group_vars, the `site_secret` lookup, `site_guard`, `site_ca`, `libvirt_vm`);
`ansible/inventory/hosts.yml` here only adds the `lb` host. Credentials stay
in `/root/lab/lab-secrets/lb/` (SSH key, Data Plane API password). The API's
certificate is issued on `pki`, so `pki.yml` of `infra` must have run.

```sh
export PATH=~/.venvs/ansible/bin:$PATH
cd /root/lab/lb/ansible
ansible-playbook lb.yml --check --diff
ansible-playbook lb.yml
```

The VM comes back with the same MAC, so the router gives it 192.0.2.167
again, and with its SSH key and API password from `/root/lab/lab-secrets/lb/`.

## Retire

```sh
virsh destroy lb; virsh undefine lb --remove-all-storage
```

The code, the playbook and `/root/lab/lab-secrets/lb/` stay.
