#!/usr/bin/env python3
"""Spawnbook end-to-end demo.

Runs a complete incident response scenario:
  1. Marketplace initializes with a 10% platform fee
  2. Log Analysis Agent registers (cost=50, reputation=98%)
  3. Incident Response Agent is funded with 500 credits
  4. A production incident fires — connection pool exhaustion
  5. The agent hires log-analysis at runtime, credits flow autonomously
  6. Full transaction ledger prints at the end

Run:
    python3 examples/run_demo.py

No API keys. No external dependencies. Pure Python stdlib.
"""

import logging
import os
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Path setup — works regardless of the caller's working directory
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# Suppress internal module logging — the demo has its own output.
logging.basicConfig(level=logging.WARNING)

from core import AgentListing
from core.credits import CreditLedger
from core.marketplace import Marketplace
from core.registry import Registry
from agents.log_analysis_agent.agent import execute as log_execute
from agents.log_analysis_agent import config as log_cfg
from agents.incident_response_agent import config as ira_cfg

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PLATFORM_FEE_PCT = 0.10
INITIAL_CREDITS = 500
INCIDENT_BUDGET = 100

# ---------------------------------------------------------------------------
# Simulated production logs — realistic enough that an SRE would believe them
# ---------------------------------------------------------------------------

SIMULATED_LOGS = """\
2026-03-13T03:28:41.302Z [api-gateway] INFO  Health check passed — all upstreams healthy
2026-03-13T03:29:12.107Z [order-service] INFO  Processing order #ORD-2847291 for user_id=18472
2026-03-13T03:30:01.884Z [user-service] INFO  GET /api/v1/users/18472 — 200 OK (34ms)
2026-03-13T03:30:55.841Z [api-gateway] WARN  Slow response from order-service: 2847ms (threshold: 2000ms)
2026-03-13T03:31:02.193Z [api-gateway] WARN  Slow response from order-service: 4201ms (threshold: 2000ms)
2026-03-13T03:31:14.557Z [user-service] WARN  Connection checkout took 8012ms — pool pressure increasing
2026-03-13T03:31:28.041Z [pg-bouncer] ERROR connection pool exhausted — max_connections=100 active=100 waiting=34
2026-03-13T03:31:28.044Z [order-service] ERROR Database connection timeout after 30000ms — host=db-primary.internal:5432
2026-03-13T03:31:28.091Z [api-gateway] ERROR HTTP 503 Service Unavailable — POST /api/v1/orders
2026-03-13T03:31:29.107Z [pg-bouncer] ERROR connection pool exhausted — max_connections=100 active=100 waiting=41
2026-03-13T03:31:29.203Z [user-service] ERROR Database connection timeout after 30000ms — host=db-primary.internal:5432
2026-03-13T03:31:30.004Z [api-gateway] ERROR HTTP 500 Internal Server Error — GET /api/v1/users/18472
2026-03-13T03:31:30.891Z [order-service] ERROR Failed to commit transaction: connection pool exhausted
2026-03-13T03:31:31.247Z [api-gateway] ERROR HTTP 503 Service Unavailable — POST /api/v1/checkout
2026-03-13T03:31:32.018Z [pg-bouncer] ERROR connection pool exhausted — max_connections=100 active=100 waiting=57
2026-03-13T03:31:33.441Z [api-gateway] ERROR HTTP 500 Internal Server Error — GET /api/v1/inventory/SKU-8291
2026-03-13T03:31:34.102Z [payment-service] WARN  Upstream timeout: order-service not responding
2026-03-13T03:31:35.203Z [api-gateway] ERROR HTTP 503 Service Unavailable — POST /api/v1/orders
2026-03-13T03:31:36.891Z [pg-bouncer] ERROR connection pool exhausted — max_connections=100 active=100 waiting=72
2026-03-13T03:31:37.003Z [order-service] ERROR Database connection timeout after 30000ms — host=db-primary.internal:5432
2026-03-13T03:31:38.247Z [api-gateway] ERROR HTTP 500 Internal Server Error — POST /api/v1/orders
2026-03-13T03:31:39.018Z [monitoring] CRITICAL Error rate 94.7% exceeds threshold (5.0%) — paging incident response"""

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _emit(msg: str) -> None:
    print(f"  {_ts()}  {msg}")


def _divider() -> None:
    print()
    print("  " + "─" * 62)


def _section(title: str) -> None:
    _divider()
    print()
    _emit(title)
    print()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def main() -> None:
    # ── Banner ─────────────────────────────────────────────────────────
    print()
    print("  ┌────────────────────────────────────────────────────────────┐")
    print("  │  spawnbook v0.1.0                                         │")
    print("  │  Agent-to-agent runtime coordination protocol             │")
    print("  └────────────────────────────────────────────────────────────┘")

    # ── 1. Initialize ──────────────────────────────────────────────────

    _section("Initializing marketplace")

    registry = Registry()
    ledger = CreditLedger(platform_fee_pct=PLATFORM_FEE_PCT)
    marketplace = Marketplace(registry=registry, ledger=ledger)

    _emit(f"Registry     in-memory SQLite")
    _emit(f"Ledger       in-memory SQLite · platform fee {int(PLATFORM_FEE_PCT * 100)}%")
    _emit(f"Marketplace  online")

    # ── 2. Register agents ─────────────────────────────────────────────

    _section("Registering agents")

    log_listing = AgentListing(
        agent_id=log_cfg.AGENT_ID,
        name=log_cfg.AGENT_NAME,
        description=log_cfg.DESCRIPTION,
        capability_tags=[log_cfg.CAPABILITY_SLUG],
        cost_per_run=log_cfg.COST_PER_RUN,
        reputation_score=98.0,
    )
    marketplace.register_agent(log_listing, log_execute)

    _emit(
        f"Registered {log_cfg.AGENT_NAME} v1  "
        f"slug={log_cfg.CAPABILITY_SLUG}  "
        f"cost={log_cfg.COST_PER_RUN}  "
        f"reputation=98.0%"
    )

    # ── 3. Fund accounts ──────────────────────────────────────────────

    _section("Funding accounts")

    ledger.fund(ira_cfg.AGENT_ID, INITIAL_CREDITS)

    _emit(f"Funded {ira_cfg.AGENT_ID} with {INITIAL_CREDITS} credits")

    # ── 4. Incident fires ─────────────────────────────────────────────

    print()
    print()
    print("  ╔════════════════════════════════════════════════════════════╗")
    print("  ║  PRODUCTION INCIDENT                                      ║")
    print("  ╚════════════════════════════════════════════════════════════╝")
    print()

    _emit("Incident Response Agent triggered — error rate 94.7%")
    print()

    log_lines = SIMULATED_LOGS.strip().split("\n")
    error_lines = [l for l in log_lines if "ERROR" in l or "CRITICAL" in l]

    for line in error_lines[:6]:
        ts_end = line.index("]") + 1
        print(f"    {line[:ts_end]}")
        print(f"      {line[ts_end:].strip()}")
    if len(error_lines) > 6:
        print(f"    ... {len(error_lines) - 6} more error lines")

    print()
    _emit(f"Ingested {len(log_lines)} log lines from production")

    # ── 5. Discovery ──────────────────────────────────────────────────

    print()
    agent = registry.find_best_for("log-analysis", budget=INCIDENT_BUDGET)
    if agent is None:
        _emit("ERROR  No agent found for capability=log-analysis")
        return

    _emit(f"→ searching registry: capability=log-analysis, budget={INCIDENT_BUDGET}")
    _emit(
        f"→ matched: {agent.name} v1  |  "
        f"reputation={agent.reputation_score:.1f}%  |  "
        f"cost={agent.cost_per_run} credits"
    )

    # ── 6. Hire via marketplace ───────────────────────────────────────

    _emit(f"→ reserving {agent.cost_per_run} credits from {ira_cfg.AGENT_ID}")
    _emit(f"→ executing {agent.name}...")
    print()

    start_ns = time.monotonic_ns()
    result = marketplace.hire(
        hiring_agent_id=ira_cfg.AGENT_ID,
        capability_needed="log-analysis",
        task_input={"logs": SIMULATED_LOGS},
        max_budget=INCIDENT_BUDGET,
    )
    elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)

    if result is None:
        _emit("ERROR  Hire failed — no agent available or insufficient credits")
        return

    # ── 7. Results ────────────────────────────────────────────────────

    output = result.output
    severity = output.get("severity", "unknown")
    fee = int(result.credits_used * PLATFORM_FEE_PCT)
    received = result.credits_used - fee

    _emit(f"→ execution complete ({elapsed_ms}ms)")
    _emit(f"→ root cause: {output.get('root_cause', 'unknown')}")
    _emit(
        f"→ severity: {severity.upper()}  |  "
        f"first occurrence: {output.get('first_occurrence', 'N/A')}"
    )
    _emit(f"→ affected service: {output.get('affected_service', 'unknown')}")
    _emit(f"→ recommendation: {output.get('recommendation', 'N/A')}")
    print()
    _emit(
        f"→ transferred {result.credits_used} credits to {log_cfg.AGENT_ID}  |  "
        f"platform fee: {fee}  |  "
        f"agent received: {received}"
    )

    # ── 8. Incident response actions ──────────────────────────────────

    print()
    if severity == "critical":
        _emit("→ on-call engineer paged")
        _emit("→ incident ticket created — severity: critical")
    elif severity in ("high", "medium"):
        _emit(f"→ incident ticket created — severity: {severity}")
        _emit("→ on-call not paged")
    else:
        _emit("→ low severity — logged for review")

    # ── 9. Transaction ledger ─────────────────────────────────────────

    _section("Transaction ledger")

    rows = ledger._conn.execute(
        "SELECT id, timestamp, from_account, to_account, amount, fee, reason, outcome "
        "FROM transactions ORDER BY id"
    ).fetchall()

    fmt = "  {:>2}   {:<26}  {:<26}  {:>6}  {:>3}  {}"
    print(fmt.format("#", "From", "To", "Amount", "Fee", "Outcome"))
    print(fmt.format("──", "─" * 26, "─" * 26, "──────", "───", "────────────"))

    for row in rows:
        tx_id, _, from_acc, to_acc, amount, fee_val, reason, outcome = row
        f = from_acc if from_acc else "—"
        t = to_acc if to_acc else "—"

        if outcome == "success" and reason == "fund":
            label = "funded"
        elif outcome == "success" and reason == "reserve":
            label = "reserved"
        elif outcome == "success" and from_acc and to_acc:
            label = "transferred"
        elif outcome == "refund":
            label = "refunded"
        else:
            label = outcome

        print(fmt.format(tx_id, f, t, amount, fee_val, label))

    # ── 10. Final balances ────────────────────────────────────────────

    _section("Final balances")

    ira_bal = ledger.balance(ira_cfg.AGENT_ID)
    log_bal = ledger.balance(log_cfg.AGENT_ID)
    total_fees = sum(r[5] for r in rows)

    fmt_bal = "  {:<34}  {:>6} credits"
    print(fmt_bal.format(ira_cfg.AGENT_ID, ira_bal))
    print(fmt_bal.format(log_cfg.AGENT_ID, log_bal))
    print(fmt_bal.format("platform (fees collected)", total_fees))

    _divider()
    print()


if __name__ == "__main__":
    main()
