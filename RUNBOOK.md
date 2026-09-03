Here is a comprehensive post-mortem and operational runbook documenting the end-to-end implementation, architectural troubleshooting, and event-driven automation of the **Cloud FinOps Automator** pipeline on Minikube.

---

## 1. System Architecture Overview

The system runs locally on Minikube to emulate a production cloud resource lifecycle management and GitOps workflow:

```
[GitHub Repo] --(Push Event Webhook)--> [ngrok Tunnel] 
                                              │
                                              ▼
                                       [ArgoCD Server] (GitOps Sync)
                                              │
                                              ▼
[CronJob / Local App] ──(State Checks)──► [Floci (DynamoDB)]
       │
 (Push Metrics)
       ▼
[Pushgateway] (2Gi PVC) ──(Scrape)──► [Prometheus] ──► [Grafana Dashboards]

```

---

## 2. Issues Encountered & Resolution Matrix

### Issue 1: Telemetry Data Loss Across Pod Restarts

* **Symptom:** Every time the Pushgateway pod restarted or Minikube bounced, all counter metrics (`finops_resources_flagged_total` and `finops_resources_purged_total`) reset to `0`, resulting in `No Data` gaps on Grafana panels.
* **Root Cause:** Standard Pushgateway deployment stores metrics in-memory by default without dynamic storage class binding.
* **Attempted Fix & Failure:**
```bash
helm upgrade pushgateway prometheus-community/prometheus-pushgateway \
  --set persistence.enabled=true --set persistence.size=2Gi -n default

```


Running `kubectl get pvc -n default` returned `No resources found`.
* **Resolution:** The `prometheus-pushgateway` Helm chart structures persistent volume configurations under `persistentVolume.*` instead of `persistence.*`.
```bash
helm upgrade pushgateway prometheus-community/prometheus-pushgateway \
  --set persistentVolume.enabled=true \
  --set persistentVolume.size=2Gi \
  --set persistence.enabled=true \
  --namespace default

```


**Verification:** PVC `pushgateway-prometheus-pushgateway` successfully transitioned to state **`Bound`** (2Gi, `standard` StorageClass).

---

### Issue 2: ArgoCD Polling Latency vs. Immediate GitOps Sync

* **Symptom:** Standard ArgoCD installations operate on a 3-minute poll loop (`timeout.reconcile`), introducing noticeable delays between Git repository commits and cluster updates.
* **Root Cause:** External Git platform webhooks cannot directly reach local Minikube IP blocks (`127.0.0.1` / `192.168.x.x`).
* **Resolution:**
1. Installed and authenticated `ngrok` inside the WSL environment.
2. Provisioned an HTTPS public tunnel directed at the ArgoCD server API service:
```bash
ngrok http https://localhost:8443 --host-header=rewrite

```


3. Configured GitHub Webhook:
* **Payload URL:** `https://<ngrok-subdomain>.ngrok-free.app/api/webhook`
* **Content Type:** `application/json`
* **Triggers:** `Just the push event`


4. Verified payload handling directly in `argocd-server` logs:
```json
{"app-namespace":"argocd","application":"cloud-finops-automator","level":"info","msg":"refreshing app from webhook","project":"default"}

```





---

### Issue 3: Floci DynamoDB Schema Mismatch & Serialization Errors

* **Symptom:** The Reaper and Cleaner jobs failed during scan/deletion queries against local Floci DynamoDB tables.
* **Root Cause:** Key field schema mismatch between code configurations (`resourceId` vs `ResourceId`). DynamoDB key expressions are case-sensitive.
* **Resolution:** Standardized the primary key naming in all Go microservice scripts and local DynamoDB table initialization manifests to PascalCase:
* **Hash Key:** `ResourceId` (String)
* **Table Name:** `idle-resources-tracker`



---

## 3. End-to-End Pipeline Execution Runbook

### Step 1: Cluster & Service Port-Forwards

Maintain these active port-forwarding tunnels during testing:

```bash
# Pushgateway Telemetry Ingestion
kubectl port-forward svc/pushgateway-prometheus-pushgateway 9091:9091 -n default &

# Grafana Dashboard Access
kubectl port-forward svc/grafana 3000:80 -n monitoring &

# ArgoCD API & UI
kubectl port-forward svc/argocd-server 8443:443 -n argocd &

```

### Step 2: Triggering the Lifecycle Job

Run the FinOps scanner and cleaner routines locally or via Kubernetes CronJobs:

```bash
# Execute local binary or trigger job manually
go run main.go --action=scan-and-clean

```

### Step 3: Metric Verification Pipeline

Verify that metrics pushed to Pushgateway correctly propagate to Prometheus and render in Grafana:

```bash
# 1. Query Pushgateway endpoints
curl -s http://localhost:9091/metrics | grep finops_resources

# 2. Check Prometheus target status
curl -s http://localhost:9090/api/v1/targets | grep pushgateway

```

---

## 4. Final Validated State Metrics

| Dashboard Metric | Value | Status |
| --- | --- | --- |
| **Total Resources Scanned** | `2` | Verified |
| **Total Resources Flagged** | `0` | Verified |
| **Total Resources Purged** | `1` | Verified & Rendered |

---

## 5. Next Steps & Production Recommendations

1. **Ingress Controller Automation:** Replace temporary `ngrok` developer tunnels with an Ingress Controller (e.g., NGINX Ingress) paired with `cert-manager` for production Kubernetes deployments.
2. **Prometheus Operator ServiceMonitor:** Convert standalone Pushgateway scraping into a declaratively managed `ServiceMonitor` Custom Resource definition for native Kubernetes lifecycle management.
3. **Dead-Man's Switch / Alertmanager:** Configure Alertmanager webhook routes to notify Slack/Teams if the Cleaner script encounters continuous AWS/Azure API exceptions.
