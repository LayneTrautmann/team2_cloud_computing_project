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






### What was run for the manual migration

For migration I used  15 iterations instead of 100+



**Experiment 1: Driver WITH Stress (Collocated)**

```bash
# 1. Setup driver pod and stress pod on same node (w3)
DRIVER_POD=spark-driver-deploy-7c6b89d76c-cpkvx
STRESS_POD=pa4-stressng-7vz9f

# Both on cloud-c1-w3 (pod affinity working)

# 2. Start stress
kubectl -n team2 exec $STRESS_POD -- bash -c "stress-ng --cpu 2 --vm 1 --vm-bytes 512M --timeout 900s &" &

# 3. Run 15-iteration experiment
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
      --M 10 --R 2 --iters 15 --writeMode append
" > exp1_with_stress.log 2>&1 &

# 4. Results: Average latency ~6.0 seconds (iterations 11-15: 5.73s-6.12s)
```

**Migration Step**

```bash
# Delete stress pod
kubectl -n team2 delete job pa4-stressng

# Restart driver deployment (moves to different node)
kubectl -n team2 rollout restart deployment/spark-driver-deploy
kubectl -n team2 rollout status deployment/spark-driver-deploy

# Result: Driver moved from cloud-c1-w3 → cloud-c1-w6
```

**Experiment 2: Driver AFTER Migration (Separated from stress)**

```bash
# 1. New driver pod on w6 (no stress)
DRIVER_POD=spark-driver-deploy-788454b886-rrfjq

# 2. Copy JARs and script to new pod
kubectl -n team2 exec $DRIVER_POD -- mkdir -p /tmp/jars /opt/spark/work-dir/app
kubectl -n team2 cp ~/team2/pa3-scaffold/jars/*.jar "$DRIVER_POD":/tmp/jars/
kubectl -n team2 cp ~/team2/smart_house_mapreduce_rdd.py "$DRIVER_POD":/opt/spark/work-dir/app/

# 3. Run 15-iteration experiment (same command as Exp1)
# Results: Average latency ~5.3 seconds (iterations 11-15: 5.03s-5.48s)
```

### Results Summary

**Latency Comparison (iterations 11-15):**
- **Before Migration (w3 with stress):** 5.73s, 5.74s, 6.12s, 5.96s, 5.99s → Avg: **~5.9s**
- **After Migration (w6 no stress):** 5.38s, 5.48s, 5.23s, 5.28s, 5.03s → Avg: **~5.3s**
- **Improvement: 10-15%** ✅

### CDF Plots Generated

```bash
# Combined both 15-iteration experiments into migration comparison
cd ~/team2
source venv/bin/activate

python3 plot_pa3_cdf.py ./pa4_results/migration_comparison pa4_migration_comparison

deactivate

# Generated files:
# - pa4_migration_comparison_iter_total_cdf.png
# - pa4_migration_comparison_mapreduce_cdf.png
# - pa4_migration_comparison_iter_total_percentiles.csv
```

### Files in pa4_results/

```
pa4_results/
├── manual_migration/
│   ├── exp1_with_stress_collocated.csv (15 iterations, ~6.0s avg)
│   └── exp2_after_migration_separated.csv (15 iterations, ~5.3s avg)
└── migration_comparison/
    ├── results_before_migration.csv
    └── results_after_migration.csv
```

### Conclusion

Manual migration successfully demonstrated tail tolerance:
- Moved Spark driver from stressed node (w3) to clean node (w6)
- Achieved 10-15% latency improvement
- Proved migration concept works for reducing tail latency









# Experiment 3b: Automated Migration (Did not run)

```bash
# 1. Start monitoring script in separate terminal
./automated_migration_monitor.sh /home/cc/team2/pa4_results.csv 5.0 5

# 2. Start experiment with stress (in another terminal)
# ... (repeat Experiment 2 steps)

# 3. Monitor script will automatically trigger migration when threshold violated

# 4. Copy results
kubectl -n team2 cp $POD:/opt/spark/work-dir/pa4_results.csv /home/cc/team2/results_auto_migration.csv
```

## Generate CDF Plots

```bash
# On master node or laptop
python3 Scaffolding/cdf_box_plots.py -r results_baseline.csv
python3 Scaffolding/cdf_box_plots.py -r results_stress.csv
python3 Scaffolding/cdf_box_plots.py -r results_manual_migration.csv
python3 Scaffolding/cdf_box_plots.py -r results_auto_migration.csv
```

This will generate:
- `exec_time.png` - CDF with 90th/95th/99th percentiles
- `response_time.png` - Box plot with std dev


## Cleanup

```bash
# Delete stress pod
kubectl -n team2 delete job pa4-stressng

# Verify
kubectl -n team2 get pods
```

---