import pytest

from core.credits import CreditLedger


@pytest.fixture
def ledger() -> CreditLedger:
    return CreditLedger(db_path=":memory:")


@pytest.fixture
def ledger_with_fee() -> CreditLedger:
    return CreditLedger(db_path=":memory:", platform_fee_pct=0.10)


# ------------------------------------------------------------------
# fund + balance
# ------------------------------------------------------------------


class TestFund:
    def test_fund_creates_account_and_sets_balance(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 500)
        assert ledger.balance("alice") == 500

    def test_fund_accumulates(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 300)
        ledger.fund("alice", 200)
        assert ledger.balance("alice") == 500

    def test_fund_zero_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.fund("alice", 0)

    def test_fund_negative_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.fund("alice", -100)


class TestBalance:
    def test_unknown_account_returns_zero(self, ledger: CreditLedger) -> None:
        assert ledger.balance("nobody") == 0


# ------------------------------------------------------------------
# reserve
# ------------------------------------------------------------------


class TestReserve:
    def test_reserve_success(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 1000)
        assert ledger.reserve("alice", 400) is True

    def test_reserve_insufficient_balance(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 100)
        assert ledger.reserve("alice", 200) is False

    def test_reserve_unfunded_account(self, ledger: CreditLedger) -> None:
        assert ledger.reserve("alice", 1) is False

    def test_reserve_reduces_available_for_next_reserve(
        self, ledger: CreditLedger
    ) -> None:
        ledger.fund("alice", 100)
        assert ledger.reserve("alice", 60) is True
        assert ledger.reserve("alice", 60) is False
        assert ledger.reserve("alice", 40) is True

    def test_reserve_does_not_change_balance(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 500)
        ledger.reserve("alice", 200)
        assert ledger.balance("alice") == 500

    def test_reserve_zero_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.reserve("alice", 0)

    def test_reserve_negative_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.reserve("alice", -50)

    def test_reserve_exact_balance(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 100)
        assert ledger.reserve("alice", 100) is True


# ------------------------------------------------------------------
# transfer
# ------------------------------------------------------------------


class TestTransfer:
    def test_transfer_success_no_fee(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 1000)
        ledger.reserve("alice", 200)

        assert ledger.transfer("alice", "bob", 200, "job completed") is True
        assert ledger.balance("alice") == 800
        assert ledger.balance("bob") == 200

    def test_transfer_with_platform_fee(
        self, ledger_with_fee: CreditLedger
    ) -> None:
        ledger_with_fee.fund("alice", 1000)
        ledger_with_fee.reserve("alice", 100)

        assert ledger_with_fee.transfer("alice", "bob", 100, "analysis done") is True

        # 10% fee: bob receives 90
        assert ledger_with_fee.balance("alice") == 900
        assert ledger_with_fee.balance("bob") == 90

    def test_transfer_fails_without_reservation(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 1000)
        assert ledger.transfer("alice", "bob", 500, "no reserve") is False
        assert ledger.balance("alice") == 1000

    def test_transfer_fails_sender_not_found(self, ledger: CreditLedger) -> None:
        assert ledger.transfer("ghost", "bob", 100, "no sender") is False

    def test_transfer_zero_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.transfer("alice", "bob", 0, "zero")

    def test_transfer_negative_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.transfer("alice", "bob", -10, "negative")

    def test_transfer_creates_receiver_account(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 500)
        ledger.reserve("alice", 100)
        ledger.transfer("alice", "new-agent", 100, "first job")

        assert ledger.balance("new-agent") == 100

    def test_transfer_is_atomic_balances_consistent(
        self, ledger: CreditLedger
    ) -> None:
        ledger.fund("alice", 300)
        ledger.reserve("alice", 300)
        ledger.transfer("alice", "bob", 300, "all-in")

        assert ledger.balance("alice") == 0
        assert ledger.balance("bob") == 300

    def test_fee_rounds_down(self) -> None:
        ledger = CreditLedger(db_path=":memory:", platform_fee_pct=0.10)
        ledger.fund("alice", 1000)
        ledger.reserve("alice", 33)
        ledger.transfer("alice", "bob", 33, "odd amount")

        # fee = int(33 * 0.1) = int(3.3) = 3
        assert ledger.balance("bob") == 30


# ------------------------------------------------------------------
# refund
# ------------------------------------------------------------------


class TestRefund:
    def test_refund_releases_reservation(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 500)
        ledger.reserve("alice", 200)
        ledger.refund("alice", 200, "agent failed")

        assert ledger.balance("alice") == 500

    def test_refund_makes_credits_available_again(
        self, ledger: CreditLedger
    ) -> None:
        ledger.fund("alice", 300)
        ledger.reserve("alice", 300)

        # all credits reserved — new reserve should fail
        assert ledger.reserve("alice", 1) is False

        ledger.refund("alice", 300, "timeout")

        # credits available again
        assert ledger.reserve("alice", 300) is True

    def test_refund_no_fee_charged(self, ledger_with_fee: CreditLedger) -> None:
        ledger_with_fee.fund("alice", 500)
        ledger_with_fee.reserve("alice", 200)
        ledger_with_fee.refund("alice", 200, "execution error")

        assert ledger_with_fee.balance("alice") == 500

    def test_refund_exceeds_reserved_raises(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 500)
        ledger.reserve("alice", 100)
        with pytest.raises(ValueError, match="exceeds reserved"):
            ledger.refund("alice", 200, "over-refund")

    def test_refund_nonexistent_account_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="not found"):
            ledger.refund("ghost", 100, "no account")

    def test_refund_zero_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.refund("alice", 0, "zero")

    def test_refund_negative_raises(self, ledger: CreditLedger) -> None:
        with pytest.raises(ValueError, match="positive"):
            ledger.refund("alice", -10, "negative")


# ------------------------------------------------------------------
# Full lifecycle
# ------------------------------------------------------------------


class TestFullLifecycle:
    def test_success_flow_end_to_end(self, ledger: CreditLedger) -> None:
        """fund → reserve → transfer: credits move from hirer to provider."""
        ledger.fund("hirer", 1000)

        assert ledger.reserve("hirer", 200) is True
        assert ledger.transfer("hirer", "provider", 200, "log-analysis job") is True

        assert ledger.balance("hirer") == 800
        assert ledger.balance("provider") == 200

    def test_failure_flow_end_to_end(self, ledger: CreditLedger) -> None:
        """fund → reserve → refund: credits return to hirer on failure."""
        ledger.fund("hirer", 1000)

        assert ledger.reserve("hirer", 200) is True
        ledger.refund("hirer", 200, "agent raised exception")

        assert ledger.balance("hirer") == 1000

    def test_multiple_concurrent_reservations(self, ledger: CreditLedger) -> None:
        ledger.fund("hirer", 500)

        assert ledger.reserve("hirer", 200) is True
        assert ledger.reserve("hirer", 200) is True
        assert ledger.reserve("hirer", 200) is False  # only 100 available

        ledger.refund("hirer", 200, "first job failed")
        assert ledger.reserve("hirer", 200) is True

    def test_credits_never_go_negative(self, ledger: CreditLedger) -> None:
        ledger.fund("alice", 100)
        assert ledger.reserve("alice", 101) is False
        assert ledger.balance("alice") == 100

    def test_constructor_rejects_invalid_fee(self) -> None:
        with pytest.raises(ValueError, match="platform_fee_pct"):
            CreditLedger(db_path=":memory:", platform_fee_pct=1.0)
        with pytest.raises(ValueError, match="platform_fee_pct"):
            CreditLedger(db_path=":memory:", platform_fee_pct=-0.1)
