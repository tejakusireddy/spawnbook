import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from core import AgentListing, TaskResult
from core.credits import CreditLedger
from core.registry import Registry

logger = logging.getLogger(__name__)


class Marketplace:
    """Runtime coordination layer connecting registry, credits, and execution.

    register_agent() publishes an agent with its executor.
    hire() is the single call agents use — discovery, payment, and execution
    happen inside, in the protocol-defined order. No caller touches the
    registry or ledger directly.
    """

    def __init__(self, registry: Registry, ledger: CreditLedger) -> None:
        self._registry = registry
        self._ledger = ledger
        self._executors: Dict[str, Callable[[dict], dict]] = {}

    # ------------------------------------------------------------------
    # Public interface — signatures are locked
    # ------------------------------------------------------------------

    def register_agent(self, listing: AgentListing, executor: Callable) -> None:
        """Register an agent and its executor in the marketplace.

        The listing is stored in the registry for discovery.
        The executor is retained for in-process invocation by hire().
        """
        self._registry.register(listing)
        self._executors[listing.agent_id] = executor
        logger.info("Marketplace registered agent %s", listing.agent_id)

    def hire(
        self,
        hiring_agent_id: str,
        capability_needed: str,
        task_input: Any,
        max_budget: Optional[int] = None,
    ) -> Optional[Any]:
        """Discover, pay, and execute an agent in one call.

        Protocol sequence: DISCOVER → RESERVE → EXECUTE → TRANSFER/REFUND.

        Returns TaskResult on execution (success or failure).
        Returns None if no suitable agent exists or credits are insufficient.
        """
        # -- Step 1: Discovery -----------------------------------------------
        agent = self._registry.find_best_for(capability_needed, max_budget)
        if agent is None:
            logger.info(
                "No agent found: capability=%s budget=%s",
                capability_needed,
                max_budget,
            )
            return None

        executor = self._executors.get(agent.agent_id)
        if executor is None:
            logger.error(
                "Agent %s found in registry but no executor registered",
                agent.agent_id,
            )
            return None

        cost = agent.cost_per_run
        task_id = str(uuid.uuid4())
        reason = f"hire:{agent.agent_id}:{capability_needed}:{task_id}"

        # -- Step 2: Reserve credits -----------------------------------------
        if not self._ledger.reserve(hiring_agent_id, cost):
            logger.info(
                "Insufficient credits: %s cannot afford %d for %s",
                hiring_agent_id,
                cost,
                agent.agent_id,
            )
            return None

        # -- Step 3: Execute -------------------------------------------------
        start_ns = time.monotonic_ns()
        try:
            output = executor(task_input)
            elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)

            # -- Step 4a: Success → transfer + record outcome ----------------
            self._ledger.transfer(hiring_agent_id, agent.agent_id, cost, reason)
            self._registry.record_outcome(agent.agent_id, success=True)

            logger.info(
                "Hire succeeded: %s hired %s for %s (%d credits, %dms)",
                hiring_agent_id,
                agent.agent_id,
                capability_needed,
                cost,
                elapsed_ms,
            )
            return TaskResult(
                task_id=task_id,
                status="success",
                output=output,
                execution_time_ms=elapsed_ms,
                credits_used=cost,
            )

        except Exception as exc:
            elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)

            # -- Step 4b: Failure → refund + record outcome ------------------
            self._ledger.refund(hiring_agent_id, cost, reason)
            self._registry.record_outcome(agent.agent_id, success=False)

            logger.error(
                "Hire failed: %s hired %s for %s — %s (%dms)",
                hiring_agent_id,
                agent.agent_id,
                capability_needed,
                exc,
                elapsed_ms,
            )
            return TaskResult(
                task_id=task_id,
                status="failure",
                output={"error": str(exc)},
                execution_time_ms=elapsed_ms,
                credits_used=0,
            )
