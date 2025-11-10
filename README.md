
---
## PA3 running publishers

# run once per terminal
export BROKERS="127.0.0.1:29091,127.0.0.1:29092"
export TOPIC="debs-sensors"

# Imran runs 3 publishers
python3 publisher.py \
  --brokers "$BROKERS" \
  --input data/shard1.csv \
  --topic "$TOPIC" --topic-mode shared \
  --acks all --create-topic \
  --partitions 5 --replication-factor 1 \
  --device-id device-imran --source laptop-imran \
  --log-every 10000

python3 publisher.py \
  --brokers "$BROKERS" \
  --input data/shard2.csv \
  --topic "$TOPIC" --topic-mode shared \
  --acks all \
  --device-id device-imran --source laptop-imran \
  --log-every 10000

python3 publisher.py \
  --brokers "$BROKERS" \
  --input data/shard3.csv \
  --topic "$TOPIC" --topic-mode shared \
  --acks all \
  --device-id device-imran --source laptop-imran \
  --log-every 10000


# Layne runs 2 publishers
python3 publisher.py \
  --brokers "$BROKERS" \
  --input data/shard4.csv \
  --topic "$TOPIC" --topic-mode shared \
  --acks all \
  --device-id device-layne --source laptop-layne \
  --log-every 10000

python3 publisher.py \
  --brokers "$BROKERS" \
  --input data/shard5.csv \
  --topic "$TOPIC" --topic-mode shared \
  --acks all \
  --device-id device-layne --source laptop-layne \
  --log-every 10000


# Now show mongo shard count
ssh -p 2205 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "mongosh mongodb://sensorapp:CHANGE_ME_STRONG_PASSWORD@localhost:27017/sensors?authSource=sensors \
     --eval 'db.getCollectionNames().filter(n=>n.startsWith(\"readings_shard\")).map(n=>({n,count:db[n].countDocuments()}))'"


# Show the latest records (e.g.sensorType":"33-5-2" corresponds to house 33, household 5, plug 2.)
ssh -p 2201 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf exec deploy/flask-web -n pipeline -- \
   python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:5000/last?n=5').read().decode())\""

# Can show schema
ssh -p 2205 -i ~/.ssh/team2_key.pem cc@127.0.0.1 \
  "mongosh mongodb://sensorapp:CHANGE_ME_STRONG_PASSWORD@localhost:27017/sensors?authSource=sensors \
     --eval 'db.readings_shard1.find({source:\"laptop-layne\"}).sort({_id:-1}).limit(1)'"



## **0. Prerequisites**

Run these once per session on the **master node terminal** (cloud-c1-m1):

```bash
export TEAM_NS=team2
export POD=$(kubectl -n $TEAM_NS get pod -l app=sparkDriverApp -o jsonpath='{.items[0].metadata.name}')
```

Verify Spark is up:

```bash
kubectl -n $TEAM_NS get pods -l app=spark
```

Expected example:

```
spark-master-deploy-xxxxx     Running
spark-worker-deploy-xxxxx     Running (x5)
spark-driver-deploy-xxxxx     Running
```

---

## **1. Cluster Health Check (Show the grader the cluster is alive)**

```bash
kubectl get nodes -o wide
kubectl -n $TEAM_NS get pods -o wide
kubectl -n $TEAM_NS top pods   # if metrics-server installed
```

Expected:

* All nodes Ready
* Workers spread across multiple cloud-c1-wX nodes

---

## **2. Verify Mongo Shards (Input Data Exists)**

```bash
kubectl -n $TEAM_NS run mongo-check --rm -i --restart=Never --image=mongo:7.0 -- \
  mongosh "mongodb://mongo-svc:27017/sensors" --eval '
    ["readings_shard1","readings_shard2","readings_shard3","readings_shard4","readings_shard5"]
      .forEach(c => print(c, db[c].countDocuments()))
  '
```

Expected ~100k per shard.

---

## **3. Run MapReduce Analytics (M,R Experiments)**

You will run **three** experiments:

| Experiment | Maps (M) | Reduces (R) | Iterations (n) |
| ---------- | -------- | ----------- | -------------- |
| #1         | 10       | 2           | 10             |
| #2         | 50       | 5           | 10             |
| #3         | 100      | 10          | 10             |

### **Run #1 — M=10, R=2**

```bash
kubectl -n $TEAM_NS exec -i $POD -- sh -lc '
M=10 R=2 ITERS=10 /tmp/run_pa3.sh
'
```

### **Run #2 — M=50, R=5**

```bash
kubectl -n $TEAM_NS exec -i $POD -- sh -lc '
M=50 R=5 ITERS=10 /tmp/run_pa3.sh
'
```

### **Run #3 — M=100, R=10**

```bash
kubectl -n $TEAM_NS exec -i $POD -- sh -lc '
M=100 R=10 ITERS=10 /tmp/run_pa3.sh
'
```

---

## **4. Confirm Job is Using Multiple Workers (Parallelism Proof)**

Run while job is running:

```bash
kubectl -n $TEAM_NS logs $POD -f | grep -E "Starting task|executor|partition"
```

You should see **different node IPs**:

```
Starting task ... on 10.244.7.7 (executor 1)
Starting task ... on 10.244.19.7 (executor 0)
Starting task ... on 10.244.14.8 (executor 3)
Starting task ... on 10.244.20.8 (executor 4)
```

This is **the required proof** that Spark is **actually parallelized** across cloud workers.

---

## **5. View Results in Human Readable Form**

After each experiment ends, summaries are automatically written:

```bash
ls /tmp/pa3_out/
```

Then display summary table:

```bash
cat /tmp/pa3_out/*/summary.txt
```

Expected format:

```
==== SUMMARY: pa3_20251107_045321 ====
Combo        | n   | p50 (s) | p90 (s) | p95 (s) | p99 (s)
-----------------------------------------------------------
M10_R2       | 10  | xx.xxx  | xx.xxx  | xx.xxx  | xx.xxx
M50_R5       | 10  | xx.xxx  | xx.xxx  | xx.xxx  | xx.xxx
M100_R10     | 10  | 18.991  | 19.657  | 19.715  | 19.761
```

This is **exactly what the assignment wants you to report.**

---

## **6. Show Sample Analytical Output (Grader wants to see real values)**

Example: Total work per house (printed by job):

```bash
kubectl -n $TEAM_NS exec -i $POD -- sh -lc '
python3 - <<EOF
import pandas as pd
df = pd.read_csv("/tmp/pa3_out/latest/total_work_per_house.csv")
print(df.head(10).to_string())
EOF
'
```

---

## **7. Restart Spark if Needed**

```bash
kubectl -n $TEAM_NS rollout restart deploy/spark-master-deploy
kubectl -n $TEAM_NS rollout restart deploy/spark-worker-deploy
kubectl -n $TEAM_NS rollout restart deploy/spark-driver-deploy
```

Watch status:

```bash
kubectl -n $TEAM_NS get pods -w
```

---

## **8. If a Job Failed Due to Output Folder Existing**

Spark refuses to overwrite existing output.

Fix:

```bash
kubectl -n $TEAM_NS exec $POD -- rm -rf /tmp/pa3_out/*
```

Then rerun the experiment.









<details>
  <summary>PA2 details. Click to expand</summary>


## PA2 Demo Checklist (showing the graders everything works)

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
