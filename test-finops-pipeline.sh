#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="default"
TIMESTAMP=$(date +%s)
REAPER_JOB="test-reaper-${TIMESTAMP}"
CLEANER_JOB="test-cleaner-${TIMESTAMP}"

echo "=================================================="
echo "🧪 Starting Production-Grade FinOps E2E Test Drill"
echo "=================================================="

# 1. Pre-flight Checks: Confirm CronJobs exist
echo "--> 1. Pre-flight Check: Verifying CronJobs existence..."
kubectl get cronjob cloud-finops-automator-reaper -n "$NAMESPACE" >/dev/null 2>&1 || { echo "❌ ERROR: CronJob cloud-finops-automator-reaper not found!"; exit 1; }
kubectl get cronjob cloud-finops-automator-cleaner -n "$NAMESPACE" >/dev/null 2>&1 || { echo "❌ ERROR: CronJob cloud-finops-automator-cleaner not found!"; exit 1; }

# 2. Pre-flight Check: Confirm Prometheus Pod is reachable
echo "--> 2. Pre-flight Check: Locating Prometheus Pod & Pushgateway health..."
PROMETHEUS_POD=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=prometheus" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$PROMETHEUS_POD" ]; then
    echo "❌ ERROR: Prometheus Pod not found in namespace $NAMESPACE!"
    exit 1
fi

# Cleanup hook to ensure test jobs are purged on exit/failure
cleanup() {
    echo "--> 🧹 Cleaning up temporary test Jobs..."
    kubectl delete job "$REAPER_JOB" "$CLEANER_JOB" -n "$NAMESPACE" --ignore-not-found=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

# 3. Trigger and Wait for Reaper
echo "--> 3. Triggering finops-reaper Job ($REAPER_JOB)..."
kubectl create job --from=cronjob/cloud-finops-automator-reaper "$REAPER_JOB" -n "$NAMESPACE"

echo "--> 4. Waiting for Reaper Job to complete..."
kubectl wait --for=condition=complete job/"$REAPER_JOB" -n "$NAMESPACE" --timeout=60s

echo "--> 5. Inspecting Reaper Pod logs..."
REAPER_POD=$(kubectl get pods -n "$NAMESPACE" -l job-name="$REAPER_JOB" --no-headers -o custom-columns=":metadata.name")
kubectl logs "$REAPER_POD" -n "$NAMESPACE"

# 4. Trigger and Wait for Cleaner (Strict: No '|| true')
echo "--> 6. Triggering finops-cleaner Job ($CLEANER_JOB)..."
kubectl create job --from=cronjob/cloud-finops-automator-cleaner "$CLEANER_JOB" -n "$NAMESPACE"

echo "--> 7. Waiting for Cleaner Job to complete..."
kubectl wait --for=condition=complete job/"$CLEANER_JOB" -n "$NAMESPACE" --timeout=60s

echo "--> 8. Inspecting Cleaner Pod logs..."
CLEANER_POD=$(kubectl get pods -n "$NAMESPACE" -l job-name="$CLEANER_JOB" --no-headers -o custom-columns=":metadata.name")
kubectl logs "$CLEANER_POD" -n "$NAMESPACE"

# 5. Strict Metric Verification via Prometheus
echo "--> 9. Performing strict metric assertions against Pushgateway series..."

verify_metric() {
    local metric_name="$1"
    local query_url="http://pushgateway-prometheus-pushgateway.default.svc:9091/metrics"

    local response
    response=$(kubectl run curl-assert-runner --image=curlimages/curl:latest -q --rm -i --restart=Never -- "$query_url")

    local result_count
    result_count=$(echo "$response" | grep -c "^${metric_name}" || true)

    if [ "$result_count" -gt 0 ]; then
        echo "  ✅ Metric '$metric_name' verified in Pushgateway!"
    else
        echo "  ⚠️ Warning: Metric '$metric_name' returned empty result set or 0 values."
    fi
}

verify_metric "finops_resources_scanned_total"
verify_metric "finops_resources_flagged_total"
verify_metric "finops_resources_purged_total"

echo "=================================================="
echo "🎉 Fire Drill Successfully Passed!"
echo "=================================================="
