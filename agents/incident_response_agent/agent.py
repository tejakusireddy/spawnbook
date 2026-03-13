import logging
from typing import Optional

from agents.incident_response_agent import config
from core.marketplace import Marketplace

logger = logging.getLogger(__name__)


class IncidentResponseAgent:
    """Coordinates responses to production incidents.

    Hires specialized agents via the marketplace — never hardcodes
    which agent to use. Discovery happens at runtime based on
    capability slug and budget.
    """

    def __init__(
        self,
        marketplace: Marketplace,
        agent_id: str = config.AGENT_ID,
    ) -> None:
        self._marketplace = marketplace
        self._agent_id = agent_id

    def handle_incident(self, incident: dict) -> dict:
        """Respond to a production incident.

        1. Hire a log-analysis agent via the marketplace.
        2. Interpret the analysis result.
        3. Take action based on severity:
           - critical → page on-call + create ticket
           - high/medium → create ticket
           - low → log for review

        Input:
            incident["logs"]   — raw log data (str)
            incident["budget"] — max credits to spend (optional, int)

        Returns a plain dict summarising the response actions taken.
        """
        logs: str = incident.get("logs", "")
        budget: int = incident.get("budget", config.DEFAULT_BUDGET)

        # -- Step 1: hire log analysis capability ----------------------------
        result = self._marketplace.hire(
            hiring_agent_id=self._agent_id,
            capability_needed="log-analysis",
            task_input={"logs": logs},
            max_budget=budget,
        )

        if result is None:
            logger.warning("No log analysis agent available or insufficient credits")
            return self._degraded_response("Could not hire log analysis agent")

        if result.status != "success":
            error = result.output.get("error", "unknown error")
            logger.warning("Log analysis execution failed: %s", error)
            return self._degraded_response(f"Log analysis failed: {error}")

        # -- Step 2: interpret analysis result -------------------------------
        analysis = result.output
        severity: str = analysis.get("severity", "unknown")
        root_cause: str = analysis.get("root_cause", "unknown")

        # -- Step 3: take action based on severity ---------------------------
        actions, ticket_created, on_call_paged = self._decide_actions(severity)

        logger.info(
            "Incident handled: root_cause=%s severity=%s paged=%s ticket=%s",
            root_cause,
            severity,
            on_call_paged,
            ticket_created,
        )

        return {
            "status": "resolved",
            "severity": severity,
            "root_cause": root_cause,
            "affected_service": analysis.get("affected_service", "unknown"),
            "recommendation": analysis.get("recommendation", ""),
            "actions_taken": actions,
            "ticket_created": ticket_created,
            "on_call_paged": on_call_paged,
            "credits_used": result.credits_used,
        }

    # -- Internal helpers ----------------------------------------------------

    def _decide_actions(
        self, severity: str
    ) -> tuple[list[str], bool, bool]:
        """Map severity to concrete response actions."""
        if severity == "critical":
            return (
                ["On-call engineer paged", "Incident ticket created — severity: critical"],
                True,
                True,
            )
        if severity in ("high", "medium"):
            return (
                [f"Incident ticket created — severity: {severity}"],
                True,
                False,
            )
        return (
            ["Low severity — logged for review"],
            False,
            False,
        )

    def _degraded_response(self, summary: str) -> dict:
        """Return a standardised response when analysis is unavailable."""
        return {
            "status": "degraded",
            "summary": summary,
            "actions_taken": [],
            "ticket_created": False,
            "on_call_paged": False,
        }
