#!/bin/bash
#
# PA4 Automated Migration Monitor for Team2
# Monitors Spark job latencies and automatically triggers migration
# when 90th percentile threshold is consistently violated
#
# Usage: ./automated_migration_monitor.sh <results_csv> <threshold_sec> <violation_count>
#
# Example: ./automated_migration_monitor.sh pa4_results.csv 5.0 5
#   (Migrate if exec_time > 5.0s for 5 consecutive iterations)
#

set -e

RESULTS_CSV=${1:-"pa4_results.csv"}
THRESHOLD=${2:-5.0}
VIOLATION_COUNT=${3:-5}

NAMESPACE="team2"
DEPLOYMENT="spark-driver-deploy"

echo "=========================================="
echo "PA4: Automated Migration Monitor"
echo "=========================================="
echo "Monitoring: $RESULTS_CSV"
echo "Threshold: ${THRESHOLD}s"
echo "Violation trigger: $VIOLATION_COUNT consecutive violations"
echo "Namespace: $NAMESPACE"
echo "=========================================="
echo ""

consecutive_violations=0
migrated=false

# Monitor the results file as it's being written
tail -f "$RESULTS_CSV" 2>/dev/null | while read line; do
    # Skip empty lines
    if [ -z "$line" ]; then
        continue
    fi

    # Parse CSV: iteration, exec_time, response_time
    exec_time=$(echo "$line" | cut -d',' -f2 | tr -d ' ')

    # Skip if not a number
    if ! [[ "$exec_time" =~ ^[0-9.]+$ ]]; then
        continue
    fi

    # Check if exec_time exceeds threshold
    if (( $(echo "$exec_time > $THRESHOLD" | bc -l) )); then
        consecutive_violations=$((consecutive_violations + 1))
        echo "[$(date)] Violation detected: exec_time=${exec_time}s > ${THRESHOLD}s (count: $consecutive_violations)"

        # Trigger migration if threshold reached and not already migrated
        if [ $consecutive_violations -ge $VIOLATION_COUNT ] && [ "$migrated" = false ]; then
            echo ""
            echo "=========================================="
            echo "THRESHOLD EXCEEDED! Triggering migration..."
            echo "=========================================="

            # Perform migration
            kubectl -n $NAMESPACE rollout restart deployment/$DEPLOYMENT

            echo "Migration initiated. Waiting for rollout..."
            kubectl -n $NAMESPACE rollout status deployment/$DEPLOYMENT

            echo ""
            echo "New pod location:"
            kubectl -n $NAMESPACE get pods -l app=sparkDriverApp -o wide

            migrated=true
            consecutive_violations=0
            echo "=========================================="
            echo "Migration complete. Continuing monitoring..."
            echo "=========================================="
            echo ""
        fi
    else
        # Reset counter if latency is good
        if [ $consecutive_violations -gt 0 ]; then
            echo "[$(date)] Latency recovered: exec_time=${exec_time}s (resetting counter)"
        fi
        consecutive_violations=0
    fi
done
