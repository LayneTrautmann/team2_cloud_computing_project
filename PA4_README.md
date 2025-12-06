# PA4

### New/Updated Files
`smart_house_mapreduce_rdd.py`, `pa4-stressng-job.yaml`,  `migrate_spark_driver.sh`, `automated_migration_monitor.sh` 

ssh c1m819381



#Runs a PA4-style baseline (many iterations, no stress)

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
```

📥 Step 1 — Copy baseline data from cluster → your laptop
```bash
scp -r c1m819381:/home/cc/team2/pa4_results ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_iter_total_cdf.png ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_mapreduce_cdf.png ./pa4_results_baseline_local/
scp c1m819381:/home/cc/team2/pa4_baseline_iter_total_percentiles.csv ./pa4_results_baseline_local/
```





#PHASE 2 — Stress Run (with stress-ng active on same node as Spark driver)
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
```

📥 Step 1 — Copy stress data from cluster → your laptop
```bash
scp -r c1m819381:/home/cc/team2/pa4_results ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_iter_total_cdf.png ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_mapreduce_cdf.png ./pa4_results_stress_local/
scp c1m819381:/home/cc/team2/pa4_stress_iter_total_percentiles.csv ./pa4_results_stress_local/
```


### Experiment 3a: Manual Migration

```bash
# 1. Start experiment with stress (as above)

# 2. Monitor latencies in real-time
kubectl -n team2 exec $POD -- tail -f /opt/spark/work-dir/pa4_results.csv

# 3. When there are violations (>5 iterations above baseline 90th percentile):
./migrate_spark_driver.sh

# 4. There should be improvment

# 5. Copy results
kubectl -n team2 cp $POD:/opt/spark/work-dir/pa4_results.csv /home/cc/team2/results_manual_migration.csv
```

### Experiment 3b: Automated Migration

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

