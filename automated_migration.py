#!/usr/bin/env python3
"""
PA4 Automated Migration Script
Monitors Spark job latencies and automatically migrates driver when tail latencies are violated
"""

import subprocess
import time
import csv
import sys
from datetime import datetime

# Configuration
NAMESPACE = "team2"
DRIVER_DEPLOYMENT = "spark-driver-deploy"
BASELINE_P90 = 5.5  # From baseline experiment (adjust if needed)
VIOLATION_THRESHOLD = 5  # Number of consecutive violations before migration
TOTAL_ITERATIONS = 100
SPARK_MASTER = "spark://spark-master-svc:7077"

# Spark configuration
SPARK_JARS = "/tmp/jars/mongo-spark-connector_2.12-10.3.0.jar,/tmp/jars/mongodb-driver-sync-4.11.1.jar,/tmp/jars/mongodb-driver-core-4.11.1.jar,/tmp/jars/bson-4.11.1.jar"
SPARK_SCRIPT = "/opt/spark/work-dir/app/smart_house_mapreduce_rdd.py"
COLLECTIONS = "readings_shard1,readings_shard2,readings_shard3,readings_shard4,readings_shard5"
M = 10
R = 2

# Results tracking
results = []
violation_count = 0
migration_performed = False
migration_iteration = None


def get_driver_pod():
    """Get current driver pod name"""
    cmd = f"kubectl -n {NAMESPACE} get pod -l app=sparkDriverApp -o jsonpath='{{.items[0].metadata.name}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()


def get_driver_ip(pod_name):
    """Get driver pod IP"""
    cmd = f"kubectl -n {NAMESPACE} get pod {pod_name} -o jsonpath='{{.status.podIP}}'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()


def run_spark_iteration(pod_name, driver_ip):
    """Run a single Spark iteration and return the latency"""
    print(f"  Running Spark job on pod {pod_name}...")

    spark_cmd = f"""
    /spark-3.1.1-bin-hadoop3.2/bin/spark-submit \
      --master {SPARK_MASTER} \
      --conf spark.driver.host={driver_ip} \
      --conf spark.driver.port=7076 \
      --conf spark.blockManager.port=7079 \
      --conf spark.dynamicAllocation.enabled=false \
      --conf spark.executor.instances=2 \
      --conf spark.executor.cores=1 \
      --conf spark.executor.memory=2g \
      --conf spark.cores.max=2 \
      --jars {SPARK_JARS} \
      {SPARK_SCRIPT} \
        --collections "{COLLECTIONS}" \
        --M {M} --R {R} --iters 1 --writeMode append
    """

    start_time = time.time()
    cmd = f'kubectl -n {NAMESPACE} exec {pod_name} -- bash -lc "{spark_cmd}"'
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    end_time = time.time()

    latency = end_time - start_time

    if result.returncode != 0:
        print(f"  WARNING: Spark job failed: {result.stderr[:200]}")
        return None

    return latency


def setup_driver_pod(pod_name):
    """Copy JARs and script to driver pod"""
    print(f"Setting up driver pod {pod_name}...")

    # Create directories
    cmd = f"kubectl -n {NAMESPACE} exec {pod_name} -- mkdir -p /tmp/jars /opt/spark/work-dir/app"
    subprocess.run(cmd, shell=True, capture_output=True)

    # Copy JARs
    jars = [
        "mongo-spark-connector_2.12-10.3.0.jar",
        "mongodb-driver-sync-4.11.1.jar",
        "mongodb-driver-core-4.11.1.jar",
        "bson-4.11.1.jar"
    ]

    for jar in jars:
        src = f"/home/cc/team2/pa3-scaffold/jars/{jar}"
        dst = f"{pod_name}:/tmp/jars/"
        cmd = f"kubectl -n {NAMESPACE} cp {src} {dst}"
        subprocess.run(cmd, shell=True, capture_output=True)

    # Copy Python script
    cmd = f"kubectl -n {NAMESPACE} cp /home/cc/team2/smart_house_mapreduce_rdd.py {pod_name}:/opt/spark/work-dir/app/"
    subprocess.run(cmd, shell=True, capture_output=True)

    print("  ✅ JARs and script copied to new driver pod")


def trigger_migration():
    """Trigger driver migration via kubectl rollout restart"""
    print("\n" + "="*80)
    print("🚨 MIGRATION TRIGGERED - 5 consecutive violations detected!")
    print("="*80)

    # Get old pod name to know when it changes
    old_pod = get_driver_pod()
    print(f"Old driver pod: {old_pod}")

    # Delete stress pod to remove the stressor
    print("Deleting stress pod to remove stressor...")
    cmd = f"kubectl -n {NAMESPACE} delete job pa4-stressng"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"  {result.stdout.strip()}")

    # Restart deployment
    cmd = f"kubectl -n {NAMESPACE} rollout restart deployment/{DRIVER_DEPLOYMENT}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"Restarting deployment: {result.stdout}")

    # Wait for rollout to complete
    print("Waiting for new driver pod to be ready...")
    cmd = f"kubectl -n {NAMESPACE} rollout status deployment/{DRIVER_DEPLOYMENT}"
    subprocess.run(cmd, shell=True)

    # Wait for old pod to be replaced with new pod
    print("Waiting for new driver pod to appear...")
    max_retries = 30
    new_pod = None
    for i in range(max_retries):
        time.sleep(2)
        candidate_pod = get_driver_pod()

        # Check if it's actually a new pod (different name)
        if candidate_pod and candidate_pod != old_pod:
            # Verify pod is running
            cmd = f"kubectl -n {NAMESPACE} get pod {candidate_pod} -o jsonpath='{{.status.phase}}'"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.stdout.strip() == "Running":
                new_pod = candidate_pod
                print(f"  Found new pod: {new_pod}")
                break

        print(f"  Retry {i+1}/{max_retries}...")

    if not new_pod or new_pod == old_pod:
        print(f"ERROR: Failed to get new driver pod after migration!")
        return old_pod, get_driver_ip(old_pod)  # Fallback to old pod

    # Get new pod info
    new_ip = get_driver_ip(new_pod)

    cmd = f"kubectl -n {NAMESPACE} get pod {new_pod} -o wide"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"\nNew driver pod: {new_pod}")
    print(f"New driver IP: {new_ip}")
    print(result.stdout)

    # Setup new driver pod with JARs and script
    setup_driver_pod(new_pod)

    print("="*80 + "\n")

    return new_pod, new_ip


def save_results(filename):
    """Save results to CSV"""
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['iteration', 'latency_s', 'violated', 'migration_triggered'])
        writer.writerows(results)
    print(f"\nResults saved to: {filename}")


def main():
    print("="*80)
    print("PA4 AUTOMATED MIGRATION EXPERIMENT")
    print("="*80)
    print(f"Baseline p90 threshold: {BASELINE_P90}s")
    print(f"Violation threshold: {VIOLATION_THRESHOLD} consecutive iterations")
    print(f"Total iterations: {TOTAL_ITERATIONS}")
    print("="*80 + "\n")

    global violation_count, migration_performed, migration_iteration

    # Get initial driver pod
    driver_pod = get_driver_pod()
    driver_ip = get_driver_ip(driver_pod)
    print(f"Starting with driver pod: {driver_pod}")
    print(f"Driver IP: {driver_ip}\n")

    # Run iterations
    for i in range(1, TOTAL_ITERATIONS + 1):
        print(f"\n[Iteration {i}/{TOTAL_ITERATIONS}]")

        # Run Spark job
        latency = run_spark_iteration(driver_pod, driver_ip)

        if latency is None:
            print("  Skipping iteration due to error")
            continue

        # Check for violation
        violated = latency > BASELINE_P90
        migration_triggered = False

        if violated:
            violation_count += 1
            print(f"  ⚠️  Latency: {latency:.3f}s (VIOLATED - threshold: {BASELINE_P90}s)")
            print(f"  Violation count: {violation_count}/{VIOLATION_THRESHOLD}")
        else:
            print(f"  ✅ Latency: {latency:.3f}s (OK)")
            violation_count = 0  # Reset on success

        # Trigger migration if threshold reached
        if violation_count >= VIOLATION_THRESHOLD and not migration_performed:
            migration_triggered = True
            migration_performed = True
            migration_iteration = i

            driver_pod, driver_ip = trigger_migration()
            violation_count = 0  # Reset after migration

        # Record result
        results.append([i, f"{latency:.3f}", violated, migration_triggered])

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"automated_migration_results_{timestamp}.csv"
    save_results(filename)

    # Print summary
    print("\n" + "="*80)
    print("EXPERIMENT SUMMARY")
    print("="*80)
    if migration_performed:
        print(f"✅ Migration performed at iteration {migration_iteration}")
    else:
        print("❌ No migration was triggered (no violations detected)")

    total_violations = sum(1 for r in results if r[2])
    print(f"Total violations: {total_violations}/{TOTAL_ITERATIONS}")

    avg_latency = sum(float(r[1]) for r in results) / len(results)
    print(f"Average latency: {avg_latency:.3f}s")
    print("="*80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExperiment interrupted by user")
        if results:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"automated_migration_results_interrupted_{timestamp}.csv"
            save_results(filename)
        sys.exit(1)
