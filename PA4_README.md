# PA4

### New/Updated Files
`smart_house_mapreduce_rdd.py`, `pa4-stressng-job.yaml`,  `migrate_spark_driver.sh`, `automated_migration_monitor.sh` 



this seems to run a mapreduce job 

```bash
kubectl -n team2 exec -it "$DRIVER_POD" -- bash -lc "
  /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=$DRIVER_IP \
    --conf spark.driver.port=7076 \
    --conf spark.blockManager.port=7079 \
    --conf spark.ui.showConsoleProgress=true \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=5 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=5 \
    --jars /tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/app/smart_house_mapreduce_rdd.py \
      --collections \"readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5\" \
      --M 10 \
      --R 2 \
      --iters 3 \
      --writeMode append
"
```

### 1: Baseline - No Stress

```bash
# 1. SSH into cluster master

# 2. Get Spark driver pod name
POD=$(kubectl -n team2 get pod -l app=sparkDriverApp -o jsonpath='{.items[0].metadata.name}')

# 3. Copy PA4 script to driver pod
kubectl -n team2 cp smart_house_mapreduce_rdd.py $POD:/opt/spark/work-dir/

# 4. Run baseline experiment (100 iterations)
kubectl -n team2 exec -it $POD -- bash -lc "
  POD_IP=\$(hostname -i)
  /opt/spark/bin/spark-submit \
    --master spark://spark-master-svc:7077 \
    --conf spark.driver.host=\$POD_IP \
    --conf spark.driver.port=7078 \
    --conf spark.blockManager.port=7079 \
    --conf spark.ui.showConsoleProgress=false \
    --conf spark.dynamicAllocation.enabled=false \
    --conf spark.executor.instances=5 \
    --conf spark.executor.cores=1 \
    --conf spark.executor.memory=2g \
    --conf spark.cores.max=5 \
    --jars /opt/spark/work-dir/jars/mongo-spark-connector_2.12-10.3.0.jar,/opt/spark/work-dir/jars/mongodb-driver-sync-4.11.1.jar,/opt/spark/work-dir/jars/mongodb-driver-core-4.11.1.jar,/opt/spark/work-dir/jars/bson-4.11.1.jar \
    /opt/spark/work-dir/smart_house_mapreduce_rdd.py \
      --collections readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5 \
      --iters 100 \
      --M 50 \
      --R 5 \
      --writeMode overwrite
"

# 5. Copy results from pod to master
kubectl -n team2 cp $POD:/opt/spark/work-dir/pa4_results.csv /home/cc/team2/results_baseline.csv

# 6. Copy to your laptop
scp c1m819381:/home/cc/team2/results_baseline.csv .
```

### 2: Under Stress 

```bash
# 1. Deploy stress-ng pod 
kubectl -n team2 apply -f pa4-stressng-job.yaml

# 2. Verify stress pod is on same node as driver
kubectl -n team2 get pods -o wide | grep -E "spark-driver|stressng"

# 3. Get stress pod name
STRESS_POD=$(kubectl -n team2 get pod -l app=pa4-stressng -o jsonpath='{.items[0].metadata.name}')

# 4. Start stress workload in background
kubectl -n team2 exec $STRESS_POD -- stress-ng --cpu 4 --io 2 --vm 2 --vm-bytes 1G --timeout 600s &

# 5. Run experiment (same as baseline) (repeat steps from  1)

# 6. Copy results
kubectl -n team2 cp $POD:/opt/spark/work-dir/pa4_results.csv /home/cc/team2/results_stress.csv
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

