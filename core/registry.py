import logging
import sqlite3
from typing import Optional

from core import AgentListing

logger = logging.getLogger(__name__)


class Registry:
    """Agent discovery registry.

    Stores agent listings and resolves capability queries at runtime.
    SQLite is the storage backend; it never leaks outside this class.
    Callers interact only through the public interface.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id        TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                description     TEXT NOT NULL,
                cost_per_run    INTEGER NOT NULL,
                reputation_score REAL NOT NULL,
                total_runs      INTEGER NOT NULL DEFAULT 0,
                successful_runs INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS agent_capabilities (
                agent_id   TEXT NOT NULL,
                capability TEXT NOT NULL,
                PRIMARY KEY (agent_id, capability),
                FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public interface — signatures are locked
    # ------------------------------------------------------------------

    def register(self, manifest: AgentListing) -> None:
        """Register or update an agent listing."""
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agents
                    (agent_id, name, description, cost_per_run,
                     reputation_score, total_runs, successful_runs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.agent_id,
                    manifest.name,
                    manifest.description,
                    manifest.cost_per_run,
                    manifest.reputation_score,
                    manifest.total_runs,
                    manifest.successful_runs,
                ),
            )
            self._conn.execute(
                "DELETE FROM agent_capabilities WHERE agent_id = ?",
                (manifest.agent_id,),
            )
            for slug in manifest.capability_tags:
                self._conn.execute(
                    "INSERT INTO agent_capabilities (agent_id, capability) VALUES (?, ?)",
                    (manifest.agent_id, slug),
                )
            self._conn.commit()
            logger.info(
                "Registered agent %s with capabilities %s",
                manifest.agent_id,
                manifest.capability_tags,
            )
        except Exception:
            self._conn.rollback()
            logger.error("Failed to register agent %s", manifest.agent_id)
            raise

    def find_best_for(
        self, capability: str, budget: Optional[int] = None
    ) -> Optional[AgentListing]:
        """Find the highest-reputation agent for a capability within budget.

        Selection logic lives here — callers never reimplement it.
        Tie-break: lower cost first, then agent_id for determinism.
        """
        if budget is not None:
            row = self._conn.execute(
                """
                SELECT a.agent_id, a.name, a.description, a.cost_per_run,
                       a.reputation_score, a.total_runs, a.successful_runs
                FROM agents a
                JOIN agent_capabilities ac ON a.agent_id = ac.agent_id
                WHERE ac.capability = ? AND a.cost_per_run <= ?
                ORDER BY a.reputation_score DESC, a.cost_per_run ASC, a.agent_id ASC
                LIMIT 1
                """,
                (capability, budget),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT a.agent_id, a.name, a.description, a.cost_per_run,
                       a.reputation_score, a.total_runs, a.successful_runs
                FROM agents a
                JOIN agent_capabilities ac ON a.agent_id = ac.agent_id
                WHERE ac.capability = ?
                ORDER BY a.reputation_score DESC, a.cost_per_run ASC, a.agent_id ASC
                LIMIT 1
                """,
                (capability,),
            ).fetchone()

        if row is None:
            logger.debug(
                "No agent found for capability=%s budget=%s", capability, budget
            )
            return None

        return self._row_to_listing(row)

    def record_outcome(self, agent_id: str, success: bool) -> None:
        """Record an execution outcome and recalculate reputation."""
        row = self._conn.execute(
            "SELECT total_runs, successful_runs FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Cannot record outcome: agent '{agent_id}' not found")

        total_runs = row[0] + 1
        successful_runs = row[1] + (1 if success else 0)
        reputation_score = (successful_runs / total_runs) * 100.0

        self._conn.execute(
            """
            UPDATE agents
            SET total_runs = ?, successful_runs = ?, reputation_score = ?
            WHERE agent_id = ?
            """,
            (total_runs, successful_runs, reputation_score, agent_id),
        )
        self._conn.commit()
        logger.info(
            "Recorded outcome for %s: success=%s reputation=%.1f",
            agent_id,
            success,
            reputation_score,
        )

    def get(self, agent_id: str) -> Optional[AgentListing]:
        """Retrieve an agent listing by ID, or None if not found."""
        row = self._conn.execute(
            """
            SELECT agent_id, name, description, cost_per_run,
                   reputation_score, total_runs, successful_runs
            FROM agents
            WHERE agent_id = ?
            """,
            (agent_id,),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_listing(row)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_listing(self, row: tuple) -> AgentListing:
        caps = [
            r[0]
            for r in self._conn.execute(
                "SELECT capability FROM agent_capabilities WHERE agent_id = ? ORDER BY capability",
                (row[0],),
            ).fetchall()
        ]
        return AgentListing(
            agent_id=row[0],
            name=row[1],
            description=row[2],
            capability_tags=caps,
            cost_per_run=row[3],
            reputation_score=row[4],
            total_runs=row[5],
            successful_runs=row[6],
        )
