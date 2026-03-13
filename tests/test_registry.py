import pytest

from core import AgentListing
from core.registry import Registry


@pytest.fixture
def registry() -> Registry:
    return Registry(db_path=":memory:")


def _make_listing(**overrides: object) -> AgentListing:
    defaults = dict(
        agent_id="agent-1",
        name="Test Agent",
        description="A test agent",
        capability_tags=["log-analysis"],
        cost_per_run=50,
        reputation_score=90.0,
        total_runs=0,
        successful_runs=0,
    )
    defaults.update(overrides)
    return AgentListing(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# register + get
# ------------------------------------------------------------------


class TestRegister:
    def test_register_and_get(self, registry: Registry) -> None:
        listing = _make_listing()
        registry.register(listing)
        result = registry.get("agent-1")

        assert result is not None
        assert result.agent_id == "agent-1"
        assert result.name == "Test Agent"
        assert result.capability_tags == ["log-analysis"]
        assert result.cost_per_run == 50
        assert result.reputation_score == 90.0

    def test_register_overwrites_existing(self, registry: Registry) -> None:
        registry.register(_make_listing(name="Version 1"))
        registry.register(_make_listing(name="Version 2"))

        result = registry.get("agent-1")
        assert result is not None
        assert result.name == "Version 2"

    def test_register_updates_capabilities(self, registry: Registry) -> None:
        registry.register(_make_listing(capability_tags=["log-analysis"]))
        registry.register(
            _make_listing(capability_tags=["log-analysis", "metric-analysis"])
        )

        result = registry.get("agent-1")
        assert result is not None
        assert sorted(result.capability_tags) == ["log-analysis", "metric-analysis"]

    def test_register_multiple_agents(self, registry: Registry) -> None:
        registry.register(_make_listing(agent_id="a1"))
        registry.register(_make_listing(agent_id="a2"))

        assert registry.get("a1") is not None
        assert registry.get("a2") is not None


# ------------------------------------------------------------------
# get
# ------------------------------------------------------------------


class TestGet:
    def test_get_nonexistent_returns_none(self, registry: Registry) -> None:
        assert registry.get("does-not-exist") is None

    def test_get_returns_all_fields(self, registry: Registry) -> None:
        registry.register(
            _make_listing(
                agent_id="full",
                total_runs=10,
                successful_runs=8,
                reputation_score=80.0,
            )
        )
        result = registry.get("full")
        assert result is not None
        assert result.total_runs == 10
        assert result.successful_runs == 8
        assert result.reputation_score == 80.0


# ------------------------------------------------------------------
# find_best_for
# ------------------------------------------------------------------


class TestFindBestFor:
    def test_finds_matching_agent(self, registry: Registry) -> None:
        registry.register(_make_listing())
        result = registry.find_best_for("log-analysis")

        assert result is not None
        assert result.agent_id == "agent-1"

    def test_returns_none_for_unknown_capability(self, registry: Registry) -> None:
        registry.register(_make_listing(capability_tags=["log-analysis"]))
        assert registry.find_best_for("web-scraping") is None

    def test_returns_none_when_registry_empty(self, registry: Registry) -> None:
        assert registry.find_best_for("log-analysis") is None

    def test_prefers_higher_reputation(self, registry: Registry) -> None:
        registry.register(
            _make_listing(agent_id="low", reputation_score=60.0)
        )
        registry.register(
            _make_listing(agent_id="high", reputation_score=95.0)
        )
        result = registry.find_best_for("log-analysis")

        assert result is not None
        assert result.agent_id == "high"

    def test_budget_filters_expensive_agents(self, registry: Registry) -> None:
        registry.register(_make_listing(agent_id="cheap", cost_per_run=30))
        registry.register(_make_listing(agent_id="expensive", cost_per_run=200))

        result = registry.find_best_for("log-analysis", budget=50)
        assert result is not None
        assert result.agent_id == "cheap"

    def test_budget_returns_none_when_all_too_expensive(
        self, registry: Registry
    ) -> None:
        registry.register(_make_listing(cost_per_run=100))
        assert registry.find_best_for("log-analysis", budget=10) is None

    def test_no_budget_ignores_cost(self, registry: Registry) -> None:
        registry.register(_make_listing(cost_per_run=99999))
        result = registry.find_best_for("log-analysis")
        assert result is not None

    def test_tiebreak_lower_cost(self, registry: Registry) -> None:
        registry.register(
            _make_listing(agent_id="costly", cost_per_run=100, reputation_score=90.0)
        )
        registry.register(
            _make_listing(agent_id="cheap", cost_per_run=30, reputation_score=90.0)
        )

        result = registry.find_best_for("log-analysis")
        assert result is not None
        assert result.agent_id == "cheap"

    def test_multi_capability_agent(self, registry: Registry) -> None:
        registry.register(
            _make_listing(capability_tags=["log-analysis", "metric-analysis"])
        )
        assert registry.find_best_for("log-analysis") is not None
        assert registry.find_best_for("metric-analysis") is not None


# ------------------------------------------------------------------
# record_outcome
# ------------------------------------------------------------------


class TestRecordOutcome:
    def test_success_increments_both_counters(self, registry: Registry) -> None:
        registry.register(_make_listing())
        registry.record_outcome("agent-1", success=True)

        result = registry.get("agent-1")
        assert result is not None
        assert result.total_runs == 1
        assert result.successful_runs == 1
        assert result.reputation_score == 100.0

    def test_failure_increments_total_only(self, registry: Registry) -> None:
        registry.register(_make_listing())
        registry.record_outcome("agent-1", success=False)

        result = registry.get("agent-1")
        assert result is not None
        assert result.total_runs == 1
        assert result.successful_runs == 0
        assert result.reputation_score == 0.0

    def test_mixed_outcomes_calculate_reputation(self, registry: Registry) -> None:
        registry.register(_make_listing())
        registry.record_outcome("agent-1", success=True)
        registry.record_outcome("agent-1", success=True)
        registry.record_outcome("agent-1", success=False)

        result = registry.get("agent-1")
        assert result is not None
        assert result.total_runs == 3
        assert result.successful_runs == 2
        assert result.reputation_score == pytest.approx(66.666, abs=0.01)

    def test_nonexistent_agent_raises(self, registry: Registry) -> None:
        with pytest.raises(ValueError, match="not found"):
            registry.record_outcome("ghost", success=True)

    def test_outcome_affects_find_best_for_ranking(self, registry: Registry) -> None:
        registry.register(
            _make_listing(agent_id="reliable", reputation_score=80.0)
        )
        registry.register(
            _make_listing(agent_id="flaky", reputation_score=95.0)
        )

        # flaky agent fails twice, reliable succeeds twice
        registry.record_outcome("flaky", success=False)
        registry.record_outcome("flaky", success=False)
        registry.record_outcome("reliable", success=True)
        registry.record_outcome("reliable", success=True)

        result = registry.find_best_for("log-analysis")
        assert result is not None
        assert result.agent_id == "reliable"
