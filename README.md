# Oracle Simulation App — Dynatrace business & technical observability demo

A fully synthetic demo of end-to-end transaction tracing, log correlation,
and business-to-technical drill-down across a representative Oracle estate
(OIC, Fusion SaaS, WebLogic, Oracle DB, batch), built for any Dynatrace
tenant. No Oracle infrastructure, WebLogic, or database required — every
layer is emitted as OpenTelemetry telemetry from one Python script, with
four ready-to-deploy Dynatrace dashboards to visualize it.

**What this is:** a concept demo showing how Dynatrace correlates business
impact, cross-system transaction tracing, and technical root cause in one
platform — with each simulated layer registering as its own Dynatrace
Service entity, linked on one shared distributed trace.

**What this is not:** a production integration, or a claim that Dynatrace
auto-discovers Oracle CC&B/Fusion/OIC. Every correlation ID, span name, and
event mapping here is representative configuration you'd repeat (against
real systems) in an actual engagement.

---

## What's in the repo

```
├── simulator.py                    the whole demo — traces, logs, business events
├── simulator_ctl.sh                start / stop / restart / status (Linux/macOS)
├── requirements.txt
├── DEPLOYMENT.md                   full step-by-step server deployment guide
├── customers/
│   └── example.env.example         copy this per tenant/customer
└── dashboards/                     4 ready-to-deploy Dynatrace dashboards (dtctl apply)
    ├── business-process-health.yaml
    ├── transaction-journey.yaml
    ├── failure-correlation.yaml
    └── root-cause-drilldown.yaml
```

For full install/deploy instructions (including systemd/server setup and dashboard
deployment), see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## What gets created in the target tenant

- Distributed traces spanning **five separate Dynatrace Service entities**
  per transaction — OIC Integration, Fusion SaaS Billing, WebLogic CCB Bill
  Run, Oracle DB, and Batch Scheduler — all linked on one shared trace via
  explicit OpenTelemetry context propagation across per-service resources.
- Every span tagged `instrumentation.simulated_type` = `opentelemetry`
  (OIC, Fusion — SaaS layers with no host access for any agent, ever) or
  `oneagent` (WebLogic, DB, batch — layers that would be auto-instrumented
  by OneAgent in a real deployment). Nothing here actually runs OneAgent —
  this is a metadata label so you can filter, color, and narrate the real
  vs SaaS-only instrumentation boundary even though the whole build is
  synthetic.
- Log lines matching representative Oracle log formats, tagged with the
  same correlation ID.
- Business events at each milestone (`Bill Generation Started/Completed/
  Failed`, `Batch Run Started/Completed/Failed`).
- Configurable failure injection so Davis has real anomalies to detect,
  not scripted ones.

All correlated by one shared field: `dt.demo.correlation_id`.

---

## Quick start

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp customers/example.env.example customers/acme.env
# edit customers/acme.env: DT_ENV_URL, DT_API_TOKEN, CUSTOMER_NAME

python simulator.py --env-file customers/acme.env --rate 5 --duration 5
```

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for background/server deployment and
dashboard installation via [dtctl](https://github.com/dynatrace-oss/dtctl).

---

## Re-skinning for a new customer

`CUSTOMER_NAME` in the env file is the only thing you need to change to
re-skin the whole demo for a new customer — it's woven into every span
name, log line, and business event. Nothing in `simulator.py` needs
touching. Keep as many customer files as you like (`customers/acme.env`,
`customers/beta-co.env`, ...) and run more than one at once — the control
script tracks each by its env file, so they don't collide.

```bash
./simulator_ctl.sh start   customers/acme.env
./simulator_ctl.sh status  customers/acme.env
./simulator_ctl.sh stop    customers/acme.env
./simulator_ctl.sh restart customers/acme.env
```

Pass extra simulator flags through the control script with `--`:

```bash
./simulator_ctl.sh start customers/acme.env -- --rate 8 --failure-rate 0.15
```

| Flag | Default | Meaning |
|---|---|---|
| `--rate` | 5 | Target transactions per minute |
| `--duration` | 0 | Stop automatically after N minutes (0 = run forever) |
| `--failure-rate` | 0.1 | Probability (0–1) each transaction fails |
| `--batch-every` | 15 | Run a simulated batch job every N transactions |
| `--env-file` | `.env` | Which customer/tenant config to use |

---

## The 4 dashboards

Deployed via `dtctl apply -f dashboards/<file>.yaml --write-id` (see
[DEPLOYMENT.md](DEPLOYMENT.md)):

1. **Business Process Health** — success rate, transaction volume, business
   event trend, instrumentation split (OTel-only vs OneAgent-equivalent),
   span volume/kind by service.
2. **Transaction Journey** — recent milestones + full span-by-span journey
   across OIC → Fusion → WebLogic → Oracle DB, duration percentiles
   (p50/p90/p99) and trends by service, duration heatmap.
3. **Failure Correlation** — failed transaction count, breakdown by failing
   stage, failure-rate trend, failing spans by service and instrumentation
   type.
4. **Root-Cause Drilldown** — failing spans next to their correlated error
   logs, log volume/level trends, failed-vs-successful duration comparison.

All 4 filter only on `event.provider == "oracle-nfr-demo-simulator"` and
`dt.demo.correlation_id` — no customer-specific config needed, they work
for any customer env file you point the simulator at.

---

## Verifying it's working

1. **Traces**: Dynatrace → Distributed Traces → search for `OIC –
   <Customer Name> Bill Generation Integration`.
2. **Services**: `dtctl query 'smartscapeNodes "SERVICE" | fields id, name'`
   — should list the 5 simulated services per customer.
3. **The OneAgent/OTel split**, once you have some data:
   ```
   fetch spans
   | summarize count(), by:{instrumentation.simulated_type}
   ```
4. **Business events**:
   ```
   fetch bizevents
   | filter event.provider == "oracle-nfr-demo-simulator"
   | sort timestamp desc
   | limit 50
   ```
5. **A single transaction's full story** (grab a correlation ID from any
   of the above):
   ```
   fetch bizevents | filter dt.demo.correlation_id == "<id>" | sort timestamp asc
   fetch spans     | filter dt.demo.correlation_id == "<id>" | sort start_time asc
   fetch logs      | filter dt.demo.correlation_id == "<id>" | sort timestamp asc
   ```

---

## Honest framing for stakeholders

- Business Flow, distributed tracing, bizevents, OpenPipeline, and Davis
  are real, current, shipping Dynatrace capabilities — not built for this
  demo.
- There is no Oracle CC&B/Fusion/OIC-specific auto-discovery; every
  mapping here is configuration that would be repeated (against real
  systems) in a production engagement.
- OIC and Fusion SaaS have no host-level access for any vendor's agent,
  ever — OpenTelemetry is the correct and permanent approach for those
  layers in production too, not a demo shortcut.
- This build has no OneAgent installed anywhere — the `oneagent` metadata
  label marks which layers *would* be OneAgent-covered in a real
  deployment. If asked directly, say the whole trace is synthetic and
  explain the label's purpose — it doesn't weaken the story, since the
  story is about what Dynatrace does with the data.

---

## Troubleshooting

- **No traces appearing**: check the token has the right ingest scopes
  (see [DEPLOYMENT.md](DEPLOYMENT.md) §1) and `DT_ENV_URL` has no
  `apps.` in the domain and no trailing slash.
- **401/403 errors**: token scope mismatch or wrong auth scheme —
  classic tokens (`dt0c01...`) use `Api-Token`, platform tokens
  (`dt0s16...`) use `Bearer`. The simulator auto-detects this from the
  token prefix.
- **`simulator_ctl.sh start` says "Env file not found"**: copy
  `customers/example.env.example` to your customer's filename first.
- **Two customers' data mixing together**: make sure each has a distinct
  `CUSTOMER_NAME` — it's the only thing separating them in the tenant if
  you're sending multiple customers into the same tenant.
- **Davis not detecting anything**: needs a baseline — run continuously
  for at least a few days before expecting reliable anomaly detection.
- **No Service entities in Smartscape**: this is expected to take some
  time after first ingest — Dynatrace builds topology via a separate
  processing pipeline from raw span ingestion.

