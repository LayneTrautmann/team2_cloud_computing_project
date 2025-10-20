# PA2 K8s Deployment – Current Status

This repository contains the automation and application code we used to bring up
the PA2 data pipeline.  Docker/Kubernetes bootstrap (tasks a–c) is stable, the
private registry plus image rollout (task d/h/i) is in place, and the pipeline
pods can be redeployed end‑to‑end from our Ansible playbooks.

---

## Quick Summary

| Task | Status | Notes |
| ---- | ------ | ----- |
| (a) Docker & K8s install via Ansible | ✅ | `install_docker_k8s.yml` run on vm1–vm5 |
| (b) Firewall updates | ✅ | `configure_firewall.yml` applied |
| (c) K8s cluster creation | ✅ | `setup_k8s_cluster.yml` stages CNI dirs, applies Flannel, joins workers; cluster verified `Ready` |
| (d) Docker images & K8s deploy | ✅ | Build + push publisher/consumer/flask images, roll out via K8s |
| (e) Extend publisher logic | ✅ | Publisher sends the new sensor mix, 3‑minute job controlled through the profile ConfigMap |
| (f) Extend subscriber logic | ✅ | Consumer handles regex topic fan-out, batches to `/bulk_update`, recovers from pattern conflicts |
| (g) Extend Flask web server | ✅ | `/bulk_update`, `/readyz`, `/healthz`, `/last` live; still using the Flask dev server (switching to gunicorn is optional polish) |
| (h) Private registry | ✅ | Local registry on vm1 (`setup_registry.yml`) |
| (i) K8s YAML layout | ✅ | Templates under `templates/k8s/` (job/deploy/service mix) |

---

## Prerequisites

1. **SSH tunnels**
   ```bash
   cd /Users/laynetrautmann/Desktop/Cloud\ Computing/Pa1   # or your clone path
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

## What We Just Verified (simple version)

1. **Cluster heal:** `install_docker_k8s.yml` → `configure_firewall.yml` (firewalld now disabled so kube-proxy stays up) → `setup_k8s_cluster.yml`.
2. **Registry + images:** `setup_registry.yml`, then `build_push_images.yml` to rebuild publisher/consumer/flask and push `dev/latest`.
3. **Deploy pipeline:** `deploy_k8s_apps.yml` applies namespace, ConfigMap, job, deployments, and service.
4. **Smoke checks:**
   - `kubectl get pods -n pipeline` shows 2× consumers, 2× flask pods Running, publisher job `Completed` once it times out at 180 s.
   - `kubectl logs -n pipeline deploy/consumer-svc` prints `Flask POST ok (batch size=20)`.
   - `kubectl exec -n pipeline deploy/flask-web -- python -c "…/last?n=5"` returns fresh documents from Mongo.

If any pod restarts after a node hiccup, re-run steps 2–3 and the health checks.

---

## Private Registry & Image Build (tasks d/h/i)

Run these whenever you need fresh images or want to validate the registry:

```bash
# 1) make sure tunnels + virtualenv are active (see prerequisites above)

# 2) start/refresh the registry container on vm1
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
  ansible-playbook -i inventory.ini setup_registry.yml

# 3) build publisher/consumer/flask images locally on vm1 and push to the registry
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
  ansible-playbook -i inventory.ini build_push_images.yml

# 4) verify the registry and node access
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "curl -s http://localhost:5000/v2/_catalog"

# (optional) confirm a worker can pull from the registry
ssh -p 2203 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock --image-endpoint unix:///run/containerd/containerd.sock pull 192.168.5.21:5000/consumer:latest"
```

To deploy the pipeline components (namespace, ConfigMap, publisher job, consumer
deployment, flask deployment/service) against the cluster:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
  ansible-playbook -i inventory.ini deploy_k8s_apps.yml

ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig ~/.kube/config get pods -n pipeline"
```

The pods should progress to `Running` within a few moments. The consumer and
flask logs are handy sanity checks:

```bash
# consumer log stream (one pod example)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig ~/.kube/config logs -n pipeline deploy/consumer-svc -f"

# flask ingestion log
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig ~/.kube/config logs -n pipeline deploy/flask-web -f"
```

## Nice-to-have Follow-ups

- **Flask runtime:** swap the built-in dev server for gunicorn/uwsgi so the pod no longer restarts after readiness probes (functionally optional).
- **Repo cleanup:** remove or ignore the large `docker/*-dev.tar.gz` archives if they are not needed.
- **Logging polish:** quiet the consumer rebalance noise or lower Flask’s request logging if it gets chatty.
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
