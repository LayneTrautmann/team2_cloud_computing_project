# PA4 Manual Migration 
# Migrates the Spark driver deployment to another worker node

# Usage: ./migrate_spark_driver.sh


set -e

NAMESPACE="team2"
DEPLOYMENT="spark-driver-deploy"

echo "Namespace: $NAMESPACE"
echo "Deployment: $DEPLOYMENT"
echo ""

# Show current pod location
echo "Current Spark driver pod location:"
kubectl -n $NAMESPACE get pods -l app=sparkDriverApp -o wide

echo ""
echo "Rollout restarting"
kubectl -n $NAMESPACE rollout restart deployment/$DEPLOYMENT

echo ""
echo "Waiting for rollouts"
kubectl -n $NAMESPACE rollout status deployment/$DEPLOYMENT

echo ""
echo "New Spark driver pod location:"
kubectl -n $NAMESPACE get pods -l app=sparkDriverApp -o wide

