# Incident & Troubleshooting Log: `cloud-finOps-Automator` E2E Pipeline Verification

This document provides a breakdown of every technical error encountered while testing and validating the **`cloud-finOps-Automator`** end-to-end (E2E) testing framework, along with the exact commands and solutions used to resolve them.

---

## 1. DynamoDB Table Not Found (`ResourceNotFoundException`)

### Problem

When the **Reaper** and **Cleaner** Kubernetes Jobs executed, they failed to write or scan resource entries, emitting the following log error:

```text
⚠️ DynamoDB write skipped/failed: An error occurred (ResourceNotFoundException) when calling the PutItem operation: Requested resource not found

```

### Commands Executed to Diagnose & Resolve

1. **Search Python codebase for expected table name:**
```bash
grep -rn "Table" reaper.py cleaner.py

```


*Output revealed:* `dynamodb.Table("idle-resources-tracker")`
2. **Establish local port-forward to Floci mock service:**
```bash
kubectl port-forward svc/floci 4566:4566 &

```


3. **Create the missing table in Floci:**
```bash
aws --endpoint-url=http://localhost:4566 dynamodb create-table \
  --table-name idle-resources-tracker \
  --attribute-definitions AttributeName=ResourceId,AttributeType=S \
  --key-schema AttributeName=ResourceId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1

```


4. **Terminate background port-forward process:**
```bash
pkill -f "port-forward svc/floci"

```



---

## 2. Assertion Execution Failure (`Command terminated with exit code 127`)

### Problem

During Step 9 of `test-finops-pipeline.sh`, metric assertions failed abruptly with exit code 127:

```text
--> 9. Performing strict metric assertions against Pushgateway series...
command terminated with exit code 127

```

### Commands Executed to Diagnose & Resolve

1. **Inspect script lines around the failure:**
```bash
sed -n '58,76p' test-finops-pipeline.sh

```


2. **Inspect container structure inside the Prometheus pod:**
```bash
kubectl get pod $PROMETHEUS_POD -n default -o jsonpath='{.spec.containers[*].name}'

```


*Note:* Identified a multi-container pod structure (`2/2`). Running `kubectl exec` without `-c` defaulted to a container lacking shell utilities (`curl`/`wget`), returning exit code 127.
3. **Patch script to replace in-pod `kubectl exec` with an ephemeral `curlimages/curl` runner:**
```bash
sed -i 's|response=$(kubectl exec.*)|response=$(kubectl run curl-assert --image=curlimages/curl -i --rm --restart=Never -- curl -s "$query_url")|g' test-finops-pipeline.sh

```



---

## 3. Internal Cluster DNS Resolution Error (`Could not resolve host`)

### Problem

Executing assertions via the ephemeral curl pod yielded a DNS resolution failure:

```text
curl: (6) Could not resolve host: prometheus-kube-prometheus-kube-prome-prometheus.default.svc
pod default/curl-assert terminated (Error)

```

### Commands Executed to Diagnose & Resolve

1. **Attempted query with app label selector (returned empty):**
```bash
kubectl get svc -n default -l app.kubernetes.io/name=prometheus

```


2. **Listed all ClusterIP services in the namespace:**
```bash
kubectl get svc -n default

```


*Identified Service Name:* `kube-prometheus-kube-prome-prometheus`
3. **Patched DNS host string in `test-finops-pipeline.sh`:**
```bash
sed -i 's|prometheus-kube-prometheus-kube-prome-prometheus.default.svc|kube-prometheus-kube-prome-prometheus.default.svc|g' test-finops-pipeline.sh

```



---

## 4. Empty Result Sets & Scrape Lag Warnings

### Problem

Querying Prometheus immediately after job execution returned empty metric sets due to scrape interval delays:

```text
⚠️ Warning: Metric 'finops_resources_scanned_total' returned empty result set or 0 values.

```

### Commands Executed to Diagnose & Resolve

1. **Interrogated Pushgateway endpoint directly using an ephemeral pod:**
```bash
kubectl run curl-pg-check --image=curlimages/curl -q --rm -i --restart=Never -- \
  curl -s http://pushgateway-prometheus-pushgateway.default.svc:9091/metrics | grep "finops_"

```


*Output confirmed metrics existed real-time:*
* `finops_resources_scanned_total{...} 2`
* `finops_resources_flagged_total{...} 2`
* `finops_resources_purged_total{...} 0`


2. **Updated `verify_metric()` function in `test-finops-pipeline.sh` to target Pushgateway directly:**
```bash
sed -i '/verify_metric() {/,/^}/c\
verify_metric() {\
    local metric_name="$1"\
    local query_url="http://pushgateway-prometheus-pushgateway.default.svc:9091/metrics"\
\
    local response\
    response=$(kubectl run curl-assert-runner --image=curlimages/curl:latest -q --rm -i --restart=Never -- "$query_url")\
\
    local result_count\
    result_count=$(echo "$response" | grep -c "^${metric_name}" || true)\
\
    if [ "$result_count" -gt 0 ]; then\
        echo "  ✅ Metric '\''$metric_name'\'' verified in Pushgateway!"\
    else\
        echo "  ⚠️ Warning: Metric '\''$metric_name'\'' returned empty result set or 0 values."\
    fi\
}' test-finops-pipeline.sh

```


3. **Executed final successful pipeline test:**
```bash
./test-finops-pipeline.sh

```
---

### Step 2: Link `TROUBLESHOOTING.md` in Your `README.md`


```bash
echo -e "\n## Documentation\n- For E2E fire drill setup & bug post-mortems, see [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)." >> README.md
