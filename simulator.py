"""
Oracle CC&B / Fusion / OIC business observability demo simulator.

Fully synthetic: every layer (OIC, Fusion, WebLogic, Oracle DB, batch) is
represented as an OpenTelemetry span emitted directly to a Dynatrace
environment, correlated by a shared business transaction ID. No real
Oracle infrastructure, WebLogic, or database is required.

Each span carries an `instrumentation.simulated_type` attribute of either
"opentelemetry" (OIC/Fusion — layers with no real host access, ever) or
"oneagent" (WebLogic/DB/batch — layers that would be auto-instrumented by
OneAgent in a real deployment). This lets you filter/color by that field
in Dynatrace to show the real vs SaaS-only instrumentation boundary, even
though this build has no OneAgent installed anywhere.

Point it at any tenant and any customer name via a .env file — see
customers/example.env.example. Use simulator_ctl.sh to start/stop/restart
a background run.

Usage:
    python simulator.py --rate 5 --failure-rate 0.1
    python simulator.py --env-file customers/acme.env --rate 8
"""

import argparse
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

CORRELATION_ATTR = "dt.demo.correlation_id"
INSTR_ATTR = "instrumentation.simulated_type"  # "opentelemetry" | "oneagent"

# Each layer gets its own OTel resource/service.name so Dynatrace creates a separate
# Service entity per layer, instead of one service holding every span.
LAYER_SERVICE_NAMES = {
    "oic": "OIC Integration - {customer}",
    "fusion": "Fusion SaaS Billing - {customer}",
    "weblogic": "WebLogic CCB Bill Run - {customer}",
    "db": "Oracle DB - {customer}",
    "batch": "Batch Scheduler - {customer}",
}


def load_config(env_file: str | None) -> dict:
    load_dotenv(env_file or ".env")
    token = os.environ.get("DT_API_TOKEN", "")
    cfg = {
        "DT_ENV_URL": os.environ.get("DT_ENV_URL", "").rstrip("/"),
        "DT_API_TOKEN": token,
        "CUSTOMER_NAME": os.environ.get("CUSTOMER_NAME", "Demo Customer"),
        # Platform tokens (dt0s...) authenticate with Bearer; classic access tokens (dt0c01...) use Api-Token.
        "AUTH_SCHEME": "Bearer" if token.startswith("dt0s") else "Api-Token",
    }
    if not cfg["DT_ENV_URL"] or not cfg["DT_API_TOKEN"]:
        raise SystemExit(
            "DT_ENV_URL and DT_API_TOKEN must be set. Copy customers/example.env.example, "
            "fill it in, and pass it with --env-file, or create a .env in this folder."
        )
    return cfg


LOG_TEMPLATES = {
    "oic": "INFO IntegrationInstance [{cid}] Bill generation flow started for contract account",
    "oic_error": "ERROR IntegrationInstance [{cid}] Timeout invoking Fusion billing interface",
    "fusion": "INFO SOAComposite [{cid}] Billing interface request accepted, processing",
    "weblogic": "INFO [ACTIVE] ExecuteThread [{cid}] Bill run request dispatched to CCB service",
    "db": "INFO OracleDB [{cid}] Bill run committed, rows affected: 1",
    "db_error": "ERROR OracleDB [{cid}] ORA-00060: deadlock detected while waiting for resource",
    "batch": "INFO BatchScheduler [{cid}] Nightly bill run batch job completed",
    "batch_error": "ERROR BatchScheduler [{cid}] Nightly bill run batch job failed, rolling back",
}


class Simulator:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.customer = cfg["CUSTOMER_NAME"]
        self.count = 0
        self.lock = threading.Lock()
        self.providers: list[TracerProvider] = []
        self.tracers: dict[str, trace.Tracer] = {}
        for layer, name_template in LAYER_SERVICE_NAMES.items():
            resource = Resource.create({"service.name": name_template.format(customer=self.customer)})
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(
                endpoint=f"{cfg['DT_ENV_URL']}/api/v2/otlp/v1/traces",
                headers={"Authorization": f"{cfg['AUTH_SCHEME']} {cfg['DT_API_TOKEN']}"},
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            self.providers.append(provider)
            self.tracers[layer] = provider.get_tracer(f"oracle.nfr.demo.{layer}")

    def send_log(self, cid: str, template_key: str) -> None:
        line = LOG_TEMPLATES[template_key].format(cid=cid)
        payload = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "content": line,
                CORRELATION_ATTR: cid,
                "customer.name": self.customer,
                "log.source": "oracle-nfr-demo-simulator",
            }
        ]
        try:
            requests.post(
                f"{self.cfg['DT_ENV_URL']}/api/v2/logs/ingest",
                headers={
                    "Authorization": f"{self.cfg['AUTH_SCHEME']} {self.cfg['DT_API_TOKEN']}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
                timeout=5,
            )
        except requests.RequestException as exc:
            print(f"[warn] log ingest failed: {exc}")

    def send_bizevent(self, cid: str, milestone: str, success: bool = True, extra: dict | None = None) -> None:
        payload = {
            "event.type": "com.demo.billing.milestone",
            "event.provider": "oracle-nfr-demo-simulator",
            CORRELATION_ATTR: cid,
            "milestone": milestone,
            "success": success,
            "customer.name": self.customer,
        }
        if extra:
            payload.update(extra)
        try:
            requests.post(
                f"{self.cfg['DT_ENV_URL']}/api/v2/bizevents/ingest",
                headers={
                    "Authorization": f"{self.cfg['AUTH_SCHEME']} {self.cfg['DT_API_TOKEN']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=5,
            )
        except requests.RequestException as exc:
            print(f"[warn] bizevent ingest failed: {exc}")

    def run_transaction(self, failure_rate: float) -> None:
        cid = str(uuid.uuid4())
        c = self.customer
        self.send_bizevent(cid, "Bill Generation Started")

        oic_fails = random.random() < failure_rate
        db_fails = (not oic_fails) and random.random() < failure_rate * 0.5

        with self.tracers["oic"].start_as_current_span(
            f"OIC – {c} Bill Generation Integration", kind=trace.SpanKind.SERVER
        ) as oic_span:
            oic_span.set_attribute(CORRELATION_ATTR, cid)
            oic_span.set_attribute(INSTR_ATTR, "opentelemetry")
            oic_span.set_attribute("integration.name", "CCB_BillGeneration_v2")
            oic_span.set_attribute("customer.name", c)
            time.sleep(random.uniform(0.03, 0.09))

            if oic_fails:
                oic_span.set_status(Status(StatusCode.ERROR, "Timeout invoking Fusion billing interface"))
                oic_span.record_exception(TimeoutError("Fusion billing interface timeout"))
                self.send_log(cid, "oic_error")
                self.send_bizevent(cid, "Bill Generation Failed", success=False, extra={"failure_stage": "oic"})
                return

            self.send_log(cid, "oic")

            # A CLIENT span on the caller's service, bridged into a SERVER span on the
            # callee's own TracerProvider/resource, so each layer shows as its own
            # Dynatrace Service entity while staying on one shared trace.
            with self.tracers["oic"].start_as_current_span(
                "Call Fusion Billing Interface", kind=trace.SpanKind.CLIENT
            ) as oic_call_span:
                oic_call_span.set_attribute(CORRELATION_ATTR, cid)
                oic_call_span.set_attribute(INSTR_ATTR, "opentelemetry")
                fusion_ctx = trace.set_span_in_context(oic_call_span)

                with self.tracers["fusion"].start_as_current_span(
                    f"Fusion SaaS – {c} Billing Interface Call", context=fusion_ctx, kind=trace.SpanKind.SERVER
                ) as fusion_span:
                    fusion_span.set_attribute(CORRELATION_ATTR, cid)
                    fusion_span.set_attribute(INSTR_ATTR, "opentelemetry")
                    fusion_span.set_attribute("interface.name", "BillingInterfaceV1")
                    fusion_span.set_attribute("customer.name", c)
                    time.sleep(random.uniform(0.05, 0.12))
                    self.send_log(cid, "fusion")

                    with self.tracers["fusion"].start_as_current_span(
                        "Call WebLogic CCB Bill Run Service", kind=trace.SpanKind.CLIENT
                    ) as fusion_call_span:
                        fusion_call_span.set_attribute(CORRELATION_ATTR, cid)
                        fusion_call_span.set_attribute(INSTR_ATTR, "opentelemetry")
                        wls_ctx = trace.set_span_in_context(fusion_call_span)

                        with self.tracers["weblogic"].start_as_current_span(
                            f"WebLogic – {c} CCB Bill Run Service", context=wls_ctx, kind=trace.SpanKind.SERVER
                        ) as wls_span:
                            wls_span.set_attribute(CORRELATION_ATTR, cid)
                            wls_span.set_attribute(INSTR_ATTR, "oneagent")
                            wls_span.set_attribute("http.route", "/ccb/bill-run")
                            wls_span.set_attribute("customer.name", c)
                            time.sleep(random.uniform(0.04, 0.10))
                            self.send_log(cid, "weblogic")

                            with self.tracers["weblogic"].start_as_current_span(
                                "Call Oracle DB", kind=trace.SpanKind.CLIENT
                            ) as wls_call_span:
                                wls_call_span.set_attribute(CORRELATION_ATTR, cid)
                                wls_call_span.set_attribute(INSTR_ATTR, "oneagent")
                                db_ctx = trace.set_span_in_context(wls_call_span)

                                with self.tracers["db"].start_as_current_span(
                                    f"Oracle DB – {c} Bill Run Commit", context=db_ctx, kind=trace.SpanKind.SERVER
                                ) as db_span:
                                    db_span.set_attribute(CORRELATION_ATTR, cid)
                                    db_span.set_attribute(INSTR_ATTR, "oneagent")
                                    db_span.set_attribute("db.system", "oracle")
                                    db_span.set_attribute("db.operation", "INSERT")
                                    db_span.set_attribute("customer.name", c)
                                    time.sleep(random.uniform(0.02, 0.06))

                                    if db_fails:
                                        db_span.set_status(Status(StatusCode.ERROR, "ORA-00060: deadlock detected"))
                                        db_span.record_exception(RuntimeError("ORA-00060 deadlock"))
                                        self.send_log(cid, "db_error")
                                        self.send_bizevent(
                                            cid, "Bill Generation Failed", success=False, extra={"failure_stage": "db"}
                                        )
                                        return

                                    self.send_log(cid, "db")

        self.send_bizevent(cid, "Bill Generation Completed")

    def run_batch_job(self, failure_rate: float) -> None:
        cid = str(uuid.uuid4())
        self.send_bizevent(cid, "Batch Run Started")

        with self.tracers["batch"].start_as_current_span(
            f"Batch – {self.customer} Nightly Bill Run", kind=trace.SpanKind.SERVER
        ) as batch_span:
            batch_span.set_attribute(CORRELATION_ATTR, cid)
            batch_span.set_attribute(INSTR_ATTR, "oneagent")
            batch_span.set_attribute("customer.name", self.customer)
            time.sleep(random.uniform(0.2, 0.5))

            if random.random() < failure_rate:
                batch_span.set_status(Status(StatusCode.ERROR, "Batch job failed"))
                self.send_log(cid, "batch_error")
                self.send_bizevent(cid, "Batch Run Failed", success=False)
                return

            self.send_log(cid, "batch")

        self.send_bizevent(cid, "Batch Run Completed")

    def tick(self, failure_rate: float, batch_every: int) -> None:
        # Fire the transaction on its own thread so overlapping transactions
        # can be in flight at once, like real traffic, rather than strictly
        # serialised one after another.
        threading.Thread(target=self.run_transaction, args=(failure_rate,), daemon=True).start()
        with self.lock:
            self.count += 1
            n = self.count
        if batch_every and n % batch_every == 0:
            threading.Thread(target=self.run_batch_job, args=(failure_rate,), daemon=True).start()
        print(f"[{n}] transaction fired for {self.customer}")

    def shutdown(self) -> None:
        for provider in self.providers:
            provider.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Oracle NFR demo transaction simulator")
    parser.add_argument("--env-file", type=str, default=None, help="Path to a customer .env file")
    parser.add_argument("--rate", type=float, default=5.0, help="Target transactions per minute (default: 5)")
    parser.add_argument("--duration", type=int, default=0, help="Run for N minutes then stop (0 = run forever)")
    parser.add_argument("--failure-rate", type=float, default=0.1, help="Probability (0-1) each transaction fails")
    parser.add_argument("--batch-every", type=int, default=15, help="Run a simulated batch job every N transactions")
    args = parser.parse_args()

    cfg = load_config(args.env_file)
    sim = Simulator(cfg)

    base_interval = 60.0 / max(args.rate, 0.1)
    end_time = time.time() + args.duration * 60 if args.duration else None

    print(f"Customer: {sim.customer}")
    print(f"Tenant:   {cfg['DT_ENV_URL']}")
    print(f"Target rate: {args.rate}/min (~{base_interval:.1f}s between transactions, jittered)")
    print(f"Failure rate: {args.failure_rate:.0%}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            if end_time and time.time() >= end_time:
                print("Duration reached, stopping.")
                break
            sim.tick(args.failure_rate, args.batch_every)
            # +/- 30% jitter so load isn't perfectly metronomic
            time.sleep(base_interval * random.uniform(0.7, 1.3))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sim.shutdown()


if __name__ == "__main__":
    main()
