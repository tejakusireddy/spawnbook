import pytest

from core import AgentListing, TaskResult
from core.credits import CreditLedger
from core.marketplace import Marketplace
from core.registry import Registry


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def registry() -> Registry:
    return Registry(db_path=":memory:")


@pytest.fixture
def ledger() -> CreditLedger:
    return CreditLedger(db_path=":memory:")


@pytest.fixture
def marketplace(registry: Registry, ledger: CreditLedger) -> Marketplace:
    return Marketplace(registry=registry, ledger=ledger)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_listing(**overrides: object) -> AgentListing:
    defaults = dict(
        agent_id="log-agent-v1",
        name="Log Analysis Agent",
        description="Analyzes logs for root cause",
        capability_tags=["log-analysis"],
        cost_per_run=50,
        reputation_score=95.0,
        total_runs=0,
        successful_runs=0,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)  # type: ignore[arg-type]


def _success_executor(task_input: dict) -> dict:
    return {"root_cause": "connection pool exhausted", "severity": "high"}


def _failing_executor(task_input: dict) -> dict:
    raise RuntimeError("Agent crashed during analysis")


# ------------------------------------------------------------------
# Successful hire
# ------------------------------------------------------------------


class TestHireSuccess:
    def test_returns_task_result_with_success_status(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(_make_listing(), _success_executor)
        ledger.fund("hirer", 1000)

        result = marketplace.hire("hirer", "log-analysis", {"logs": "error data"})

        assert isinstance(result, TaskResult)
        assert result.status == "success"
        assert result.output["root_cause"] == "connection pool exhausted"
        assert result.credits_used == 50
        assert result.execution_time_ms >= 0
        assert result.task_id  # non-empty UUID

    def test_credits_transfer_to_hired_agent(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(_make_listing(), _success_executor)
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert ledger.balance("hirer") == 950
        assert ledger.balance("log-agent-v1") == 50

    def test_successful_outcome_recorded_in_registry(
        self,
        marketplace: Marketplace,
        registry: Registry,
        ledger: CreditLedger,
    ) -> None:
        marketplace.register_agent(_make_listing(), _success_executor)
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        agent = registry.get("log-agent-v1")
        assert agent is not None
        assert agent.total_runs == 1
        assert agent.successful_runs == 1
        assert agent.reputation_score == 100.0

    def test_task_input_passed_to_executor(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        received = {}

        def capturing_executor(task_input: dict) -> dict:
            received.update(task_input)
            return {"status": "done"}

        marketplace.register_agent(_make_listing(), capturing_executor)
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "log-analysis", {"key": "value", "n": 42})

        assert received == {"key": "value", "n": 42}

    def test_budget_respected_in_discovery(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(agent_id="cheap", cost_per_run=20, reputation_score=80.0),
            _success_executor,
        )
        marketplace.register_agent(
            _make_listing(agent_id="expensive", cost_per_run=200, reputation_score=99.0),
            _success_executor,
        )
        ledger.fund("hirer", 1000)

        result = marketplace.hire("hirer", "log-analysis", {}, max_budget=50)

        assert result is not None
        assert result.credits_used == 20


# ------------------------------------------------------------------
# Failed execution
# ------------------------------------------------------------------


class TestHireFailure:
    def test_returns_task_result_with_failure_status(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(_make_listing(), _failing_executor)
        ledger.fund("hirer", 1000)

        result = marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert isinstance(result, TaskResult)
        assert result.status == "failure"
        assert "error" in result.output
        assert result.credits_used == 0

    def test_full_refund_on_failure(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(_make_listing(), _failing_executor)
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert ledger.balance("hirer") == 1000
        assert ledger.balance("log-agent-v1") == 0

    def test_failure_outcome_recorded_in_registry(
        self,
        marketplace: Marketplace,
        registry: Registry,
        ledger: CreditLedger,
    ) -> None:
        marketplace.register_agent(_make_listing(), _failing_executor)
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        agent = registry.get("log-agent-v1")
        assert agent is not None
        assert agent.total_runs == 1
        assert agent.successful_runs == 0
        assert agent.reputation_score == 0.0

    def test_error_message_captured_in_output(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(_make_listing(), _failing_executor)
        ledger.fund("hirer", 1000)

        result = marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert result is not None
        assert "crashed" in result.output["error"].lower()


# ------------------------------------------------------------------
# Insufficient credits
# ------------------------------------------------------------------


class TestHireInsufficientCredits:
    def test_returns_none(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=100), _success_executor
        )
        ledger.fund("hirer", 50)

        result = marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert result is None

    def test_executor_never_called(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        call_log: list[int] = []

        def tracking_executor(task_input: dict) -> dict:
            call_log.append(1)
            return {}

        marketplace.register_agent(
            _make_listing(cost_per_run=100), tracking_executor
        )
        ledger.fund("hirer", 50)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert len(call_log) == 0

    def test_balance_unchanged(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=100), _success_executor
        )
        ledger.fund("hirer", 50)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        assert ledger.balance("hirer") == 50

    def test_no_outcome_recorded(
        self,
        marketplace: Marketplace,
        registry: Registry,
        ledger: CreditLedger,
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=100), _success_executor
        )
        ledger.fund("hirer", 50)

        marketplace.hire("hirer", "log-analysis", {"logs": "data"})

        agent = registry.get("log-agent-v1")
        assert agent is not None
        assert agent.total_runs == 0


# ------------------------------------------------------------------
# No agent found
# ------------------------------------------------------------------


class TestHireNoAgentFound:
    def test_returns_none_for_unknown_capability(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        ledger.fund("hirer", 1000)

        result = marketplace.hire("hirer", "web-scraping", {"url": "https://x.com"})

        assert result is None

    def test_returns_none_when_budget_too_low(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=100), _success_executor
        )
        ledger.fund("hirer", 1000)

        result = marketplace.hire(
            "hirer", "log-analysis", {"logs": "data"}, max_budget=10
        )

        assert result is None

    def test_returns_none_when_registry_empty(
        self, marketplace: Marketplace
    ) -> None:
        result = marketplace.hire("hirer", "log-analysis", {})
        assert result is None

    def test_no_credits_reserved_when_no_agent(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        ledger.fund("hirer", 1000)

        marketplace.hire("hirer", "web-scraping", {})

        assert ledger.balance("hirer") == 1000


# ------------------------------------------------------------------
# Integration: multiple hires, reputation evolution
# ------------------------------------------------------------------


class TestMultipleHires:
    def test_sequential_hires_deduct_correctly(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=50), _success_executor
        )
        ledger.fund("hirer", 200)

        r1 = marketplace.hire("hirer", "log-analysis", {})
        r2 = marketplace.hire("hirer", "log-analysis", {})

        assert r1 is not None and r1.status == "success"
        assert r2 is not None and r2.status == "success"
        assert ledger.balance("hirer") == 100
        assert ledger.balance("log-agent-v1") == 100

    def test_reputation_degrades_after_failures(
        self,
        marketplace: Marketplace,
        registry: Registry,
        ledger: CreditLedger,
    ) -> None:
        marketplace.register_agent(_make_listing(), _failing_executor)
        ledger.fund("hirer", 10000)

        marketplace.hire("hirer", "log-analysis", {})
        marketplace.hire("hirer", "log-analysis", {})

        agent = registry.get("log-agent-v1")
        assert agent is not None
        assert agent.total_runs == 2
        assert agent.successful_runs == 0
        assert agent.reputation_score == 0.0

    def test_hire_until_broke(
        self, marketplace: Marketplace, ledger: CreditLedger
    ) -> None:
        marketplace.register_agent(
            _make_listing(cost_per_run=60), _success_executor
        )
        ledger.fund("hirer", 100)

        r1 = marketplace.hire("hirer", "log-analysis", {})
        r2 = marketplace.hire("hirer", "log-analysis", {})

        assert r1 is not None and r1.status == "success"
        assert r2 is None  # insufficient credits for second hire
        assert ledger.balance("hirer") == 40
