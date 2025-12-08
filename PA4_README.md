# PA4

### New/Updated Files
`smart_house_mapreduce_rdd.py`, `pa4-stressng-job.yaml`,  `migrate_spark_driver.sh`, `automated_migration_monitor.sh` 

ssh c1m819381



# Runs a PA4-style baseline (many iterations, no stress)

Leaves results ready for plotting with plot_pa3_cdf.py

I’ll assume you're already SSH’d into:
```bash
cc@cloud-c1-m1:~$
```

and your files are in ~/team2.

0️⃣ Go to the PA4 directory
```bash
cd ~/team2
```

1️⃣ Make sure Spark master & workers are up

From ~/team2/pa3-scaffold/scaffolding_code:
```bash
cd ~/team2/pa3-scaffold/scaffolding_code

# (re)apply master and workers, idempotent
kubectl -n team2 apply -f spark-master-deploy.yaml
kubectl -n team2 apply -f spark-worker-deploy.yaml

# check pods
kubectl -n team2 get pods -l app=sparkMasterApp -o wide
kubectl -n team2 get pods -l app=sparkWorkerApp -o wide


# Optional sanity check that workers are registered with the master:

curl -sS -L http://localhost:30008/json | jq '.aliveworkers'
```

You want a positive number (e.g., 5).

2️⃣ Get the Spark driver pod & IP

Back in ~/team2 is fine:
```bash
cd ~/team2

NAMESPACE=team2

DRIVER_POD=$(kubectl -n $NAMESPACE get pod -l app=sparkDriverApp -o jsonpath='{.items[0].metadata.name}')
DRIVER_IP=$(kubectl -n $NAMESPACE get pod "$DRIVER_POD" -o jsonpath='{.status.podIP}')

echo "Driver pod: $DRIVER_POD"
echo "Driver IP:  $DRIVER_IP"
```

If the driver pod name changed, this will pick up the new one.

3️⃣ Ensure Mongo Spark JARs are in the driver

You already did this once, but if the driver pod ever restarts, the files vanish, so we treat this as part of the baseline ritual:
```bash
kubectl -n $NAMESPACE exec -it "$DRIVER_POD" -- mkdir -p /tmp/jars

kubectl -n $NAMESPACE cp ~/team2/pa3-scaffold/jars/mongo-spark-connector_2.12-10.3.0.jar "$DRIVER_POD":/tmp/jars/
kubectl -n $NAMESPACE cp ~/team2/pa3-scaffold/jars/mongodb-driver-sync-4.11.1.jar        "$DRIVER_POD":/tmp/jars/
kubectl -n $NAMESPACE cp ~/team2/pa3-scaffold/jars/mongodb-driver-core-4.11.1.jar        "$DRIVER_POD":/tmp/jars/
kubectl -n $NAMESPACE cp ~/team2/pa3-scaffold/jars/bson-4.11.1.jar                       "$DRIVER_POD":/tmp/jars/

# sanity check in the pod:
kubectl -n $NAMESPACE exec -it "$DRIVER_POD" -- ls -l /tmp/jars
```

You should see all four JARs there.

4️⃣ Copy your PA4-ready script into the driver

You’ve got smart_house_mapreduce_rdd.py in ~/team2. Put it where your working command expects it:
```bash
kubectl -n $NAMESPACE exec -it "$DRIVER_POD" -- mkdir -p /opt/spark/work-dir/app

kubectl -n $NAMESPACE cp \
  ~/team2/smart_house_mapreduce_rdd.py \
  "$DRIVER_POD":/opt/spark/work-dir/app/smart_house_mapreduce_rdd.py


# Optional check:

kubectl -n $NAMESPACE exec -it "$DRIVER_POD" -- ls -l /opt/spark/work-dir/app
```
5️⃣ Run the PA4 baseline (many iterations, no stress)

Now we reuse your working spark-submit layout, just with PA4-ish parameters.
Example: M=50, R=10, iters=100 for a decent baseline.
```bash
cd ~/team2

kubectl -n team2 exec -it "$DRIVER_POD" -- bash -lc "
  /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=$DRIVER_IP \
    --conf spark.driver.port=7076 \
    --conf spark.blockManager.port=7079 \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=2 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=2 \
    --jars /tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/app/smart_house_mapreduce_rdd.py \
      --collections \"readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5\" \
      --M 10 \
      --R 2 \
      --iters 100 \
      --writeMode append
"

```


This will:

Use the Mongo connector JARs you copied.

Run your PA4-ready script.

Preload RDDs once and then run 100 iterations.

Write a CSV like:
results_M50_R10_iters100_YYYYMMDD_HHMMSS.csv
in /opt/spark/work-dir/app inside the driver.

6️⃣ Copy the baseline CSV out of the driver

Back on cloud-c1-m1, grab the newest results_*.csv:
```bash
cd ~/team2
mkdir -p pa4_results

LATEST=$(kubectl -n team2 exec "$DRIVER_POD" -- bash -lc "ls -1t /opt/spark/work-dir/app/results_M*_R*_iters* | head -n 1")
echo "Found in pod: $LATEST"

kubectl -n team2 cp "$DRIVER_POD":"$LATEST" ./pa4_results/$(basename "$LATEST")
```

✅ Run plotting script correctly

From inside ~/team2:
```bash
cd ~/team2
source venv/bin/activate          # 🔥 MUST ACTIVATE venv

pip install pandas numpy matplotlib --quiet   # only needed once

python3 plot_pa3_cdf.py ./pa4_results pa4_baseline

deactivate
```

📥 Step 1 — Copy baseline data from cluster → your laptop
```bash
scp -r c1m819381:/home/cc/team2/pa4_results ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_iter_total_cdf.png ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_mapreduce_cdf.png ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_iter_total_percentiles.csv ./pa4_results_baseline_local/
```





# PHASE 2 — Stress Run (with stress-ng active on same node as Spark driver)
Terminal A — Deploy the stress workload
```bash
cd ~/team2

# 1. Launch stress pod
kubectl -n team2 apply -f pa4-stressng-job.yaml

# 2. Confirm both driver + stress are on SAME NODE
kubectl -n team2 get pods -o wide | grep -E "spark|stress"
```

If they are NOT on same node → we will pin them manually (I will help if needed).

3. Capture stress pod name
```bash
STRESS_POD=$(kubectl -n team2 get pod -l app=pa4-stressng -o jsonpath='{.items[0].metadata.name}')
echo $STRESS_POD
```
4. Start system stress (CPU+MEM+IO load) — 20 minutes
```bash
kubectl -n team2 exec $STRESS_POD -- \
    stress-ng --cpu 4 --io 2 --vm 2 --vm-bytes 1G --timeout 1200s &
```
Terminal B — Run Spark PA4 workload under stress

When you run stress workloads, store into separate folders:
```bash
mkdir -p ~/team2/pa4_results/stress
kubectl -n team2 cp "$DRIVER_POD":/opt/spark/work-dir/app/results_M10_R2_iters100_* \
    ~/team2/pa4_results/stress/
```

Use the same working baseline command but change --iters (100+ recommended under stress):
```bash
/kubectl -n team2 exec -it "$DRIVER_POD" -- bash -lc "
  /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=$DRIVER_IP \
    --conf spark.driver.port=7076 \
    --conf spark.blockManager.port=7079 \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=2 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=2 \
    --jars /tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/app/smart_house_mapreduce_rdd.py \
      --collections \"readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5\" \
      --M 10 --R 2 --iters 100 --writeMode append
"
```
✅ Run plotting script correctly

From inside ~/team2:
```bash
cd ~/team2
source venv/bin/activate          # 🔥 MUST ACTIVATE venv

pip install pandas numpy matplotlib --quiet   # only needed once

python3 plot_pa3_cdf.py ./pa4_results/stress pa4_stress

deactivate
```

📥 Step 1 — Copy stress data from cluster → your laptop
```bash
scp -r c1m819381:/home/cc/team2/pa4_results ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_iter_total_cdf.png ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_mapreduce_cdf.png ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_iter_total_percentiles.csv ./pa4_results_stress_local/
```






## Manual Migration Experiment (100 Iterations)

**Experiment 1: Driver WITH Stress (Collocated)**

```bash
# 1. Setup driver pod and stress pod on same node (w6)
DRIVER_POD=spark-driver-deploy-788454b886-rrfjq
DRIVER_IP=10.244.15.125
STRESS_POD=pa4-stressng-sl62t

# Both on cloud-c1-w6 (pod affinity working)

# 2. Start stress
kubectl -n team2 exec $STRESS_POD -- bash -c "stress-ng --cpu 2 --vm 1 --vm-bytes 512M --timeout 1800s &" &

# 3. Run 100-iteration experiment
nohup kubectl -n team2 exec "$DRIVER_POD" -- bash -lc "
  /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=$DRIVER_IP \
    --conf spark.driver.port=7076 \
    --conf spark.blockManager.port=7079 \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=2 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=2 \
    --jars /tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/app/smart_house_mapreduce_rdd.py \
      --collections \"readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5\" \
      --M 10 --R 2 --iters 100 --writeMode append
" > results_100iter_with_stress.log 2>&1 &

# 4. Results saved to: results_100iter_before_migration.csv
```

**Migration Step**

```bash
# Delete stress pod
kubectl -n team2 delete job pa4-stressng

# Restart driver deployment (moves to different node)
kubectl -n team2 rollout restart deployment/spark-driver-deploy
kubectl -n team2 rollout status deployment/spark-driver-deploy

# Result: Driver moved from cloud-c1-w6 → cloud-c1-w17
```

**Experiment 2: Driver AFTER Migration (Separated from stress)**

```bash
# 1. New driver pod on w17 (no stress)
DRIVER_POD=spark-driver-deploy-5d7c58d8b-5plnk
DRIVER_IP=10.244.1.247

# 2. Copy JARs and script to new pod
kubectl -n team2 exec $DRIVER_POD -- mkdir -p /tmp/jars /opt/spark/work-dir/app
kubectl -n team2 cp ~/team2/pa3-scaffold/jars/*.jar "$DRIVER_POD":/tmp/jars/
kubectl -n team2 cp ~/team2/smart_house_mapreduce_rdd.py "$DRIVER_POD":/opt/spark/work-dir/app/

# 3. Run 100-iteration experiment (same command as Exp1)
nohup kubectl -n team2 exec "$DRIVER_POD" -- bash -lc "
  /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=$DRIVER_IP \
    --conf spark.driver.port=7076 \
    --conf spark.blockManager.port=7079 \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=2 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=2 \
    --jars /tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/app/smart_house_mapreduce_rdd.py \
      --collections \"readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5\" \
      --M 10 --R 2 --iters 100 --writeMode append
" > results_100iter_after_migration.log 2>&1 &

# 4. Results saved to: results_100iter_after_migration.csv
```

### Results Summary

**Performance Metrics:**

| Metric | Before Migration (w/ stress) | After Migration (clean) | Improvement |
|--------|------------------------------|-------------------------|-------------|
| Average | 5.335s | 5.246s | 1.7% |
| Median (p50) | 5.255s | 5.190s | 1.2% |
| p90 | 5.711s | 5.490s | 3.9% |
| p95 | 5.904s | 5.616s | 4.9% |
| **p99** | **6.611s** | **6.071s** | **8.2%** |
| **Max (tail)** | **8.063s** | **7.374s** | **8.5%** |

**Key Findings:**
- ✅ **Tail latency improved by 8.5%** (max: 8.063s → 7.374s)
- ✅ **p99 improved by 8.2%** (6.611s → 6.071s)
- ✅ **p95 improved by 4.9%** (5.904s → 5.616s)
- ✅ **Tail improvement > Average improvement** (8.5% vs 1.7%)

### CDF Plot Generation

```bash
# Generate CDF plot comparing before and after migration
cd /Users/laynetrautmann/Desktop/Cloud\ Computing/Pa1
source venv/bin/activate

python3 plot_migration_final.py

deactivate

# Generated file: PA4_Manual_Migration_CDF.png
```

### Files in pa4_results/

```
pa4_results/
└── migration_comparison/
    ├── results_100iter_before_migration.csv (100 iterations, cloud-c1-w6 with stress)
    └── results_100iter_after_migration.csv (100 iterations, cloud-c1-w17 clean)
```

### Conclusion

Manual migration successfully with 8.5% improvement.

---

## Automated Migration Experiment

### Implementation

**Script:** `automated_migration.py`

**Key Features:**
- Monitors p90 latency threshold (5.5s baseline)
- Detects consecutive violations (5 iterations)
- Automatically triggers migration:
  1. Deletes stress pod
  2. Restarts driver deployment (`kubectl rollout restart`)
  3. Waits for new pod on different node
  4. Copies JARs and script to new pod
  5. Continues monitoring

### Experiment Execution

**Setup:**
- Driver + stress collocated on cloud-c1-w17
- Baseline p90 threshold: 5.5s
- Violation threshold: 5 consecutive iterations

**Results:**

| Phase | Iterations | Avg Latency | Range |
|-------|-----------|-------------|-------|
| Before Migration (with stress) | 5 | 76.863s | 76.382s - 77.941s |
| After Migration (no stress) | 22 | 62.822s | 62.163s - 63.682s |

**Improvement:** 18.3% average latency reduction

### Automated Migration Flow

1. **Iterations 1-5:** Detected high latency (76-78s) due to stress
2. **Migration Triggered:** After 5 consecutive violations
3. **Actions Performed:**
   - Deleted stress pod (pa4-stressng)
   - Restarted driver deployment
   - New driver spawned on cloud-c1-w17 (clean node after stress removed)
   - Automatically set up new driver with dependencies
4. **Iterations 6-27:** Continued with improved latency (62-63s)

### CDF Plot

See `PA4_Automated_Migration_CDF.png` for visualization:
- Red line: Before migration (with stress, n=5)
- Green line: After migration (no stress, n=22)
- Clear left shift showing improvement

**Log File:** `automated_migration_final.log` contains complete execution trace

**Conclusion:** Automated migration successful 18.3% improvement.








