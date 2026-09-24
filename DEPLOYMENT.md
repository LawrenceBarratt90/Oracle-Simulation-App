# Deployment Guide

Steps to deploy this simulator (and its Dynatrace dashboards) on a fresh server or workstation for a new customer/tenant.

## 1. Prerequisites

- Python 3.9+
- Network access to your Dynatrace environment
- A Dynatrace **API token**, either:
  - **Classic access token** (`dt0c01...`) with scopes: `openTelemetryTrace.ingest`, `logs.ingest`, `bizevents.ingest`, or
  - **Platform token** (`dt0s16...`) with scopes: `openpipeline:traces:ingest`, `openpipeline:logs:ingest`, `openpipeline:bizevents:ingest`

The simulator auto-detects the token type from its prefix and sends the correct `Authorization` header (`Api-Token` vs `Bearer`) — no config needed beyond pasting the token in.

**Tenant URL gotcha:** use the plain ingest API domain, not the browser/UI domain. If your browser address bar shows `https://<id>.apps.dynatrace.com` or `https://<id>.apps.dynatracelabs.com`, the ingest API is at `https://<id>.dynatrace.com` / `https://<id>.dynatracelabs.com` (drop `apps.`). Classic SaaS tenants (`https://<id>.live.dynatrace.com`) are used as-is.

## 2. Get the code

```bash
git clone https://github.com/LawrenceBarratt90/Oracle-Simulation-App.git
cd Oracle-Simulation-App
```

## 3. Set up the Python environment

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Configure your customer/tenant

```bash
cp customers/example.env.example customers/<yourcustomer>.env
```

Edit `customers/<yourcustomer>.env`:

```
DT_ENV_URL=https://<your-tenant-id>.dynatrace.com
DT_API_TOKEN=<your-token>
CUSTOMER_NAME=<Your Customer Name>
```

Never commit this file — it's already excluded via `.gitignore`.

## 5. Run the simulator

**Quick foreground test:**

```bash
python simulator.py --env-file customers/<yourcustomer>.env --rate 5 --duration 5
```

**Background, on Linux/macOS, via the control script:**

```bash
./simulator_ctl.sh start customers/<yourcustomer>.env -- --rate 1.5 --failure-rate 0.1
./simulator_ctl.sh status customers/<yourcustomer>.env
./simulator_ctl.sh stop customers/<yourcustomer>.env
```

**Persistent server deployment (systemd, Linux):** create `/etc/systemd/system/oracle-nfr-demo.service`:

```ini
[Unit]
Description=Oracle NFR Demo Simulator
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/Oracle-Simulation-App
ExecStart=/opt/Oracle-Simulation-App/venv/bin/python simulator.py --env-file customers/<yourcustomer>.env --rate 1.5 --failure-rate 0.1 --batch-every 15
Restart=on-failure
RestartSec=5
User=<service-user>

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oracle-nfr-demo
sudo systemctl status oracle-nfr-demo
```

**Windows:** run in the foreground, or register as a scheduled task ("Run whether user is logged on or not") pointing at `venv\Scripts\python.exe simulator.py --env-file customers\<yourcustomer>.env --rate 1.5`.

Let it run continuously for at least a few days before a live demo so Davis has a baseline to detect anomalies against.

## 6. Install dtctl (CLI for dashboards/queries)

```bash
# macOS/Linux
curl -fsSL https://raw.githubusercontent.com/dynatrace-oss/dtctl/main/install.sh | sh
# Windows PowerShell
irm https://raw.githubusercontent.com/dynatrace-oss/dtctl/main/install.ps1 | iex
```

Authenticate (opens a browser, no token handling needed):

```bash
dtctl auth login --context <yourcustomer> --environment "https://<your-tenant-id>.apps.dynatrace.com"
```

Note: `dtctl auth login` uses the **`apps.`** platform URL (the browser one), which is the opposite of the ingest URL used in step 4. Verify with `dtctl doctor`.

## 7. Deploy the dashboards

```bash
dtctl apply -f dashboards/business-process-health.yaml --write-id
dtctl apply -f dashboards/transaction-journey.yaml --write-id
dtctl apply -f dashboards/failure-correlation.yaml --write-id
dtctl apply -f dashboards/root-cause-drilldown.yaml --write-id
```

`--write-id` stamps the newly-created dashboard ID back into each YAML file so future re-applies (`dtctl apply -f dashboards/*.yaml`) update the same dashboard instead of creating duplicates. Each `apply` prints a direct URL to the created dashboard.

## 8. Verify

```bash
# Confirm data is arriving
dtctl query 'fetch bizevents | filter event.provider == "oracle-nfr-demo-simulator" | limit 5'

# Confirm the 5 simulated services registered as Smartscape entities
dtctl query 'smartscapeNodes "SERVICE" | fields id, name | limit 20'
```

Open each dashboard URL from step 7 and confirm tiles are populated (allow a few minutes after first data lands for Service entities and tiles to populate).
