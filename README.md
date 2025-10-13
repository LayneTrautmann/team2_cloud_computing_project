# PA2 K8s Deployment – Current Status

This repository contains the automation and application code we used to bring up
the PA2 data pipeline.  Tasks (a) and (b) from the assignment are complete, and
task (c) is *almost* done—only the CNI plug‑in needs to be fixed so that all
Kubernetes nodes report `Ready`.

---

## Quick Summary

| Task | Status | Notes |
| ---- | ------ | ----- |
| (a) Docker & K8s install via Ansible | ✅ | `install_docker_k8s.yml` run on vm1–vm5 |
| (b) Firewall updates | ✅ | `configure_firewall.yml` applied |
| (c) K8s cluster creation | ✅ | `setup_k8s_cluster.yml` stages CNI dirs, applies Flannel, joins workers; cluster verified `Ready` |
| (d) Private registry | ⏳ | `setup_registry.yml`, `build_push_images.yml` ready to use |
| (e–i) App extensions & K8s manifests | ⏳ | Deployables under `templates/k8s/`, `deploy_k8s_apps.yml` |

---

## Prerequisites

1. **SSH tunnels**
   ```bash
   cd <projet directory>
   ./start_tunnels.sh
   ```

2. **Python virtualenv (for Ansible)**
   ```bash
   source ~/ansible_project/.venv/bin/activate
   ```

3. **OpenStack credentials**
   ```bash
   export OS_CLOUD=CH-822922      # change if your clouds.yaml entry uses a different name
   ```

4. **Optional:** reapply security‑group rules if Chameleon wipes them
   ```bash
   ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
     ansible-playbook -i inventory.ini create_vms_portfwd.yml --tags sg_rules
   ```

---

## Rebuild or Heal the Cluster (task c)

Any time you need a fresh cluster (or after pulling new playbook changes):

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
  ansible-playbook -i inventory.ini setup_k8s_cluster.yml

ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig /home/cc/.kube/config get nodes -o wide"
```

The playbook ensures the CNI directories exist on every node, applies the
Flannel manifest (with the proper host mount locations), and rejoins the
workers. All nodes should report `Ready` within a minute and will show up as
`Ready` when you run the verification command.

---

## Next Work Items

1. **Private registry** *(task d)*  
   - Use `setup_registry.yml` to start a registry on vm1.  
   - `build_push_images.yml` builds publisher/consumer/flask images and pushes
     them to the registry.

2. **Extended publisher/subscriber/web logic** *(tasks e–g)*  
   - Implement the requirements in `publisher.py`, `consumer.py`,
     `flask_server.py`.  
   - When the new images are ready, redeploy via Kubernetes using
     `deploy_k8s_apps.yml`.

3. **K8s rollout** *(task i)*  
   - Customize the manifests under `templates/k8s/`; ensure workloads pull from
     the private registry and expose the required services/NodePorts.

---

## Helpful Commands

```bash
# Reapply Docker/K8s prerequisites (idempotent)
ansible-playbook -i inventory.ini install_docker_k8s.yml

# Check kubelet logs on a worker (example vm2)
ssh -p 2202 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo journalctl -u kubelet -n 50 --no-pager"

# Watch pod status (vm1)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig /home/cc/.kube/config get pods -A -w"
```
