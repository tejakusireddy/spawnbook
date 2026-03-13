import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class CreditLedger:
    """Credit ledger implementing the full transaction lifecycle.

    Protocol: RESERVE → EXECUTE → TRANSFER (success) or REFUND (failure).
    Credits are integers. Transfers are atomic. Every operation is logged.
    SQLite is the storage backend; it never leaks outside this class.
    """

    def __init__(
        self, db_path: str = ":memory:", platform_fee_pct: float = 0.0
    ) -> None:
        if not 0.0 <= platform_fee_pct < 1.0:
            raise ValueError(
                f"platform_fee_pct must be in [0.0, 1.0), got {platform_fee_pct}"
            )
        self._conn = sqlite3.connect(db_path)
        self._platform_fee_pct = platform_fee_pct
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                balance    INTEGER NOT NULL DEFAULT 0,
                reserved   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                from_account TEXT,
                to_account   TEXT,
                amount       INTEGER NOT NULL,
                fee          INTEGER NOT NULL DEFAULT 0,
                reason       TEXT    NOT NULL,
                outcome      TEXT    NOT NULL
            );
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public interface — signatures are locked
    # ------------------------------------------------------------------

    def fund(self, account_id: str, amount: int) -> None:
        """Load credits into an account. Creates the account if needed."""
        if amount <= 0:
            raise ValueError(f"Fund amount must be positive, got {amount}")

        self._ensure_account(account_id)
        self._conn.execute(
            "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
            (amount, account_id),
        )
        self._log_tx(
            from_account=None,
            to_account=account_id,
            amount=amount,
            fee=0,
            reason="fund",
            outcome="success",
        )
        self._conn.commit()
        logger.info("Funded %s with %d credits", account_id, amount)

    def reserve(self, account_id: str, amount: int) -> bool:
        """Hold credits before execution.

        Returns True if the reservation succeeded.
        Returns False if available balance is insufficient — no exception,
        because insufficient funds is an expected business outcome.
        """
        if amount <= 0:
            raise ValueError(f"Reserve amount must be positive, got {amount}")

        self._ensure_account(account_id)

        row = self._conn.execute(
            "SELECT balance, reserved FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()

        available = row[0] - row[1]
        if available < amount:
            self._log_tx(
                from_account=account_id,
                to_account=None,
                amount=amount,
                fee=0,
                reason="reserve",
                outcome="insufficient_balance",
            )
            self._conn.commit()
            logger.info(
                "Reserve failed for %s: requested=%d available=%d",
                account_id,
                amount,
                available,
            )
            return False

        self._conn.execute(
            "UPDATE accounts SET reserved = reserved + ? WHERE account_id = ?",
            (amount, account_id),
        )
        self._log_tx(
            from_account=account_id,
            to_account=None,
            amount=amount,
            fee=0,
            reason="reserve",
            outcome="success",
        )
        self._conn.commit()
        logger.info("Reserved %d credits from %s", amount, account_id)
        return True

    def transfer(self, from_id: str, to_id: str, amount: int, reason: str) -> bool:
        """Transfer credits on successful execution.

        Deducts from the sender's balance and reservation.
        Credits the receiver with (amount - platform fee).
        Atomic: all-or-nothing, partial transfers do not exist.
        """
        if amount <= 0:
            raise ValueError(f"Transfer amount must be positive, got {amount}")

        try:
            row = self._conn.execute(
                "SELECT balance, reserved FROM accounts WHERE account_id = ?",
                (from_id,),
            ).fetchone()

            if row is None:
                self._log_tx(from_id, to_id, amount, 0, reason, "sender_not_found")
                self._conn.commit()
                logger.error("Transfer failed: sender %s not found", from_id)
                return False

            balance, reserved = row
            if balance < amount or reserved < amount:
                self._log_tx(from_id, to_id, amount, 0, reason, "transfer_failed")
                self._conn.commit()
                logger.error(
                    "Transfer failed: from=%s amount=%d balance=%d reserved=%d",
                    from_id,
                    amount,
                    balance,
                    reserved,
                )
                return False

            fee = int(amount * self._platform_fee_pct)
            agent_receives = amount - fee

            self._ensure_account(to_id)
            self._conn.execute(
                "UPDATE accounts SET balance = balance - ?, reserved = reserved - ? WHERE account_id = ?",
                (amount, amount, from_id),
            )
            self._conn.execute(
                "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
                (agent_receives, to_id),
            )
            self._log_tx(from_id, to_id, amount, fee, reason, "success")
            self._conn.commit()
            logger.info(
                "Transferred %d credits %s → %s (fee=%d, reason=%s)",
                amount,
                from_id,
                to_id,
                fee,
                reason,
            )
            return True
        except Exception:
            self._conn.rollback()
            logger.error(
                "Transfer rollback: %d credits %s → %s", amount, from_id, to_id
            )
            raise

    def refund(self, account_id: str, amount: int, reason: str) -> None:
        """Release reserved credits on failure or timeout. No fee charged."""
        if amount <= 0:
            raise ValueError(f"Refund amount must be positive, got {amount}")

        row = self._conn.execute(
            "SELECT reserved FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Cannot refund: account '{account_id}' not found")

        if row[0] < amount:
            raise ValueError(
                f"Refund of {amount} exceeds reserved balance of {row[0]} for '{account_id}'"
            )

        self._conn.execute(
            "UPDATE accounts SET reserved = reserved - ? WHERE account_id = ?",
            (amount, account_id),
        )
        self._log_tx(
            from_account=None,
            to_account=account_id,
            amount=amount,
            fee=0,
            reason=reason,
            outcome="refund",
        )
        self._conn.commit()
        logger.info("Refunded %d credits to %s (reason=%s)", amount, account_id, reason)

    def balance(self, account_id: str) -> int:
        """Return the current balance for an account. Zero if unknown."""
        row = self._conn.execute(
            "SELECT balance FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()

        if row is None:
            return 0

        return row[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_account(self, account_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO accounts (account_id, balance, reserved) VALUES (?, 0, 0)",
            (account_id,),
        )

    def _log_tx(
        self,
        from_account: Optional[str],
        to_account: Optional[str],
        amount: int,
        fee: int,
        reason: str,
        outcome: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO transactions
                (timestamp, from_account, to_account, amount, fee, reason, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                from_account,
                to_account,
                amount,
                fee,
                reason,
                outcome,
            ),
        )
