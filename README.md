Here’s a clean, **full drop-in README** with all your requested updates and the rest left intact.

---

# PA2 K8s Deployment – Current Status

This repository contains the automation and application code we used to bring up
the PA2 data pipeline. Docker/Kubernetes bootstrap (tasks a–c) is stable, the
private registry plus image rollout (task d/h/i) is in place, and the pipeline
pods can be redeployed end-to-end from our Ansible playbooks.

All paths below assume you are inside the project directory (for example,
`PROJECT_DIR=~/cloud-pa2`). Replace `~/.ssh/team2_key.pem` with your key path if
it differs. The commands work the same on macOS, Linux, or Windows (use Git Bash
or WSL so that `ssh`, `kubectl`, and `ansible-playbook` are available).

---

## Quick Summary

| Task                                 | Status | Notes                                                                                                                     |
| ------------------------------------ | ------ | ------------------------------------------------------------------------------------------------------------------------- |
| (a) Docker & K8s install via Ansible | ✅      | `install_docker_k8s.yml` run on vm1–vm5                                                                                   |
| (b) Firewall updates                 | ✅      | `configure_firewall.yml` applied                                                                                          |
| (c) K8s cluster creation             | ✅      | `setup_k8s_cluster.yml` stages CNI dirs, applies Flannel, joins workers; cluster verified `Ready`                         |
| (d) Docker images & K8s deploy       | ✅      | Build + push publisher/consumer/flask images, roll out via K8s                                                            |
| (e) Extend publisher logic           | ✅      | Implemented in `./publisher.py`; generates multi-sensor data, configurable rate and duration             |
| (f) Extend subscriber logic          | ✅      | Implemented in `./docker/consumer/consumer.py`; supports regex topic fan-out, batching, and scaling                       |
| (g) Extend Flask web server          | ✅      | Implemented in `./docker/flask/flask_server.py`; supports `/bulk_update`, `/readyz`, `/healthz`, `/last`, behind gunicorn |
| (h) Private registry                 | ✅      | Local registry on vm1 (`setup_registry.yml`)                                                                              |
| (i) K8s YAML layout                  | ✅      | Templates under `templates/k8s/` (job/deploy/service mix)                                                                 |

---

## Demo Checklist (showing the graders everything works)

These are the exact commands we record when we need a fresh proof. Run them from
`PROJECT_DIR` with the SSH tunnels up (`./start_tunnels.sh`) and the Ansible
virtualenv activated. Each block notes what “good” output looks like.

### 0. Laptop publisher setup (Imran + Layne)

Before running the demo, **both laptops** must create Kafka tunnels to connect to the cloud brokers.

Run on **both laptops** (Imran and Layne):

```bash
ssh -N \
  -i ~/.ssh/team2_key.pem \
  -L 192.168.5.21:9092:192.168.5.21:9092 \
  -L 192.168.5.70:9092:192.168.5.70:9092 \
  cc@127.0.0.1 -p 2201
```

If you get “Cannot assign requested address,” run this once after every reboot (see **Rebuild or Heal** section below):

```bash
# WSL / Linux
sudo ip addr add 192.168.5.21/32 dev lo
sudo ip addr add 192.168.5.70/32 dev lo
```

```bash
# macOS
sudo ifconfig lo0 alias 192.168.5.21
sudo ifconfig lo0 alias 192.168.5.70
```

Then start your **local publishers** (each on their own laptop):

**Imran’s laptop**

```bash
python3 publisher.py \
  --brokers 192.168.5.21:9092,192.168.5.70:9092 \
  --config ./profile.json \
  --topic-mode shared \
  --topic sensors \
  --device-id device-imran \
  --source laptop-imran \
  --key-strategy random \
  --rate-multiplier 1.0 \
  --duration-sec 180 \
  --log-every 1
```

**Layne’s laptop**

```bash
python3 publisher.py \
  --brokers 192.168.5.21:9092,192.168.5.70:9092 \
  --config ./profile.json \
  --topic-mode shared \
  --topic sensors \
  --device-id device-layne \
  --source laptop-layne \
  --key-strategy random \
  --rate-multiplier 1.0 \
  --duration-sec 180 \
  --log-every 1
```

These two publishers will produce sensor data **simultaneously** from two external laptops into the in-cloud Kafka brokers.

---

### 1. Cluster health

```bash
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get nodes"

ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get pods -A"
```

*Expectation:* every node `Ready`; kube-system pods (kube-proxy, CoreDNS, Flannel) reporting `Running`.

---

### 2. Pipeline pods & publisher jobs

```bash
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get pods -n pipeline"

ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get jobs -n pipeline"
```

*Expectation:* the consumer and Flask deployments show as `Running`; any K8s publisher jobs you applied report `Completed 1/1`.

---

### 3. End-to-end data flow

```bash
# consumer tail (one pod chosen automatically)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf logs -n pipeline deploy/consumer-svc --tail=10"
# consumer stream
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf logs -n pipeline deploy/consumer-svc | \
   grep 'INFO Consumed'"

# query Flask inside the cluster (returns latest 5 docs)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf exec deploy/flask-web -n pipeline -- \
   python -c \"import requests; print(requests.get('http://localhost:5000/last?n=5').text)\""
```

*Expectation:* consumer log lines include `Flask POST ok (batch size=20)` and the `/last` call returns fresh JSON documents from Mongo.

---

### 4. Manual scaling demo

> **Note:** Kafka consumer parallelism is capped by the **number of partitions** in a topic. If your shared topic `sensors` has 3 partitions, only 3 consumer replicas can actively read in parallel. Increase partitions first if you plan to scale consumers beyond that.

#### 4.1 Ensure sufficient Kafka partitions (optional but recommended)

```bash
# Describe current partitions (run on VM1 path shown below)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo /opt/kafka_2.13-3.7.0/bin/kafka-topics.sh --bootstrap-server 192.168.5.21:9092 \
   --describe --topic sensors"

# If partitions < 4 (for example), increase to 6 for the demo:
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo /opt/kafka_2.13-3.7.0/bin/kafka-topics.sh --bootstrap-server 192.168.5.21:9092 \
   --alter --topic sensors --partitions 6"

# Re-describe to confirm:
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo /opt/kafka_2.13-3.7.0/bin/kafka-topics.sh --bootstrap-server 192.168.5.21:9092 \
   --describe --topic sensors"
```

(You can also watch consumer progress/lag live:)

```bash
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "watch -n 2 'sudo /opt/kafka_2.13-3.7.0/bin/kafka-consumer-groups.sh \
   --bootstrap-server 192.168.5.21:9092 --group k8s-consumers --describe'"
```

#### 4.2 Scale consumer and Flask Deployments

```bash
# scale consumers up
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/consumer-svc -n pipeline --replicas=4"

# scale flask up
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/flask-web -n pipeline --replicas=3"

# verify pods
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get pods -n pipeline -o wide"
```

#### 4.3 (Optional) Demonstrate batching to Flask for higher throughput

```bash
# Check that consumer env includes POST_BATCH_SIZE>1 and FLASK_URL_BULK=/bulk_update
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get deploy/consumer-svc -n pipeline -o yaml | grep -A2 POST_BATCH_SIZE"

# Watch consumer post logs while publishers are active:
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf logs -n pipeline deploy/consumer-svc -f"
```

#### 4.4 Scale back down

```bash
# scale consumers down
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/consumer-svc -n pipeline --replicas=2"

# scale flask down
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/flask-web -n pipeline --replicas=2"

# verify
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf get pods -n pipeline -o wide"
```

If you need fresh publisher runs (e.g., you just rebooted and the Jobs already show `Completed`), redeploy them first:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
  ansible-playbook -i inventory.ini deploy_k8s_apps.yml
```

---

## Prerequisites

1. **SSH tunnels**

   ```bash
   cd PROJECT_DIR
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

4. **Optional:** reapply security-group rules if Chameleon wipes them

   ```bash
   ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ANSIBLE_REMOTE_TEMP=/tmp \
     ansible-playbook -i inventory.ini create_vms_portfwd.yml --tags sg_rules
   ```

---

## Rebuild or Heal the Cluster (task c)

Run this when you want a clean control plane/worker set or after pulling major
changes:

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

**If you plan to run publishers from laptops (outside the cluster), also add loopback aliases after each laptop reboot:**

```bash
# WSL / Linux (run on the laptop)
sudo ip addr add 192.168.5.21/32 dev lo
sudo ip addr add 192.168.5.70/32 dev lo
```

```bash
# macOS (run on the laptop)
sudo ifconfig lo0 alias 192.168.5.21
sudo ifconfig lo0 alias 192.168.5.70
```

(Then open SSH tunnels as shown in the Demo Checklist before running `publisher.py`.)

---

## Private Registry & Image Build (tasks d/h/i)

Use this sequence to refresh images or validate the registry:

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

---

## Manual publisher bursts & scaling demos (tasks e/f/g)

The playbook now renders one manifest per publisher under
`/tmp/pipeline-manifests/02-<job-name>.yaml`; the source list lives in
`k8s/publisher-jobs.yml`. Duplicate the sample block there to add extra
publishers (raise the `rate_multiplier`, change the `device_id`, etc.), then
rerun the deploy playbook:

```bash
# edit k8s/publisher-jobs.yml locally first
ansible-playbook -i inventory.ini deploy_k8s_apps.yml

# start just the new publisher job without touching the rest (vm1):
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig ~/.kube/config apply -f /tmp/pipeline-manifests/02-your-new-job.yaml"

# watch the publisher pod(s) come up
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "kubectl --kubeconfig ~/.kube/config get pods -n pipeline -l app=publisher -w"
```

Scaling the long-running deployments is a manual `kubectl scale` away. These
commands bump the subscribers to 4 replicas and the gunicorn frontends to 3:

```bash
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/consumer-svc -n pipeline --replicas=4"

ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf scale deploy/flask-web -n pipeline --replicas=3"
```

Re-run the same command with a different `--replicas` value to dial them back
down. To make that the new default in the playbook, pass the counts as extra
vars (for example,
`ansible-playbook -i inventory.ini deploy_k8s_apps.yml -e consumer_replicas=4 -e flask_replicas=3`).

---

## Nice-to-have Follow-ups

* **Gunicorn tuning:** adjust `GUNICORN_WORKERS`, `GUNICORN_ACCESS_LOG`, or `GUNICORN_ERROR_LOG` env vars in `deploy_k8s_apps.yml` if you need different concurrency/logging.
* **Repo cleanup:** remove or ignore the large `docker/*-dev.tar.gz` archives if they are not needed.
* **Logging polish:** quiet the consumer rebalance noise or lower Flask’s request logging if it gets chatty.

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

---
