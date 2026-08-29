from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aletheia.uta.contract import UTA_CONTRACT_VERSION
from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import (
    AccountBalance,
    AccountSnapshot,
    CommandEvidence,
    FillListReport,
    FundingLedgerEntry,
    FundingLedgerReport,
    InstrumentIdentity,
    OrderQuery,
    PositionListReport,
    PositionStatusReport,
    QueryScope,
    ReadCompleteness,
    SubmissionReport,
    SubmitOrderCommand,
    UTAAccountIdentity,
    UTAHealth,
    UTAHealthReport,
    UTAReach,
    VerifiedExitReport,
    VerifiedExitState,
)


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


@pytest.fixture
def identity():
    return UTAAccountIdentity(
        account_id="binance-testnet-primary",
        execution_mode="binance_testnet",
        venue="binance",
        environment="testnet",
        profile="default",
        credential_generation=3,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version="4.5.56",
    )


@pytest.fixture
def instrument():
    return InstrumentIdentity(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures",
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
        contract_size="0.001",
        linear=True,
    )


def test_contract_models_are_immutable_decimal_safe_and_identity_explicit(identity, instrument):
    assert UTA_CONTRACT_VERSION == 1
    assert instrument.contract_size == Decimal("0.001")
    with pytest.raises(ValidationError):
        instrument.contract_size = Decimal("1")
    with pytest.raises(ValidationError, match="raw binary floats"):
        InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            contract_size=0.001,
        )


def test_complete_empty_is_distinct_from_unavailable(identity):
    complete = AccountSnapshot(
        identity=identity,
        completeness=ReadCompleteness.COMPLETE,
        scope=QueryScope(),
        received_at=NOW,
        profile_version="1",
        settlement_currency="USDT",
        equity="0",
        available="0",
        balances=(AccountBalance(currency="USDT", total="0", available="0"),),
    )
    unavailable = AccountSnapshot(
        identity=identity,
        completeness=ReadCompleteness.UNAVAILABLE,
        scope=QueryScope(),
        received_at=NOW,
        reason_code="private_account_timeout",
        profile_version="1",
        settlement_currency="USDT",
    )
    assert complete.equity == Decimal("0")
    assert unavailable.equity is None
    with pytest.raises(ValidationError, match="reason_code"):
        AccountSnapshot(
            identity=identity,
            completeness=ReadCompleteness.PARTIAL,
            scope=QueryScope(),
            received_at=NOW,
            profile_version="1",
            settlement_currency="USDT",
        )


def test_account_snapshot_rejects_duplicate_asset_balance(identity):
    with pytest.raises(ValidationError, match="currencies must be unique"):
        AccountSnapshot(
            identity=identity,
            completeness="complete",
            scope=QueryScope(),
            received_at=NOW,
            profile_version="1",
            settlement_currency="USDT",
            balances=(
                AccountBalance(currency="BTC", total="1"),
                AccountBalance(currency="BTC", total="2"),
            ),
        )


def test_command_identity_is_separate_and_account_bound(identity, instrument):
    command = SubmitOrderCommand(
        command_id="cmd-entry-1",
        client_order_id="venue-entry-1",
        expected_account=identity,
        instrument=instrument,
        purpose="entry",
        proposal_id="proposal-1",
        side="buy",
        quantity="0.01",
        order_type="market",
        stop_loss_price="99.5",
        take_profit_price="102.25",
    )
    assert command.command_id != command.client_order_id != command.proposal_id
    assert command.expected_account.credential_generation == 3
    assert command.stop_loss_price == Decimal("99.5")
    assert command.take_profit_price == Decimal("102.25")
    assert set(CommandEvidence) == {
        CommandEvidence.NOT_SENT,
        CommandEvidence.VENUE_REJECTED,
        CommandEvidence.ACKNOWLEDGED,
        CommandEvidence.AMBIGUOUS,
    }

    with pytest.raises(ValidationError, match="greater than 0"):
        SubmitOrderCommand(
            command_id="cmd-invalid",
            expected_account=identity,
            instrument=instrument,
            purpose="entry",
            side="buy",
            quantity="0",
            order_type="market",
        )


def test_ambiguous_errors_never_retry_and_reason_is_secret_safe():
    with pytest.raises(ValueError, match="cannot be retryable"):
        UTAError(
            UTAErrorCode.TIMEOUT,
            "possible_send_timeout",
            command_evidence=CommandEvidence.AMBIGUOUS,
            retryable=True,
        )
    with pytest.raises(ValueError, match="stable reason code"):
        UTAError(UTAErrorCode.AUTH, "request https://user:secret@example.test")


def test_query_windows_are_aware_ordered_and_open_order_scope_is_allowed(instrument):
    scoped = OrderQuery(scope=QueryScope(instrument=instrument))
    with pytest.raises(ValueError, match="requires client_order_id"):
        scoped.require_exact()
    assert OrderQuery(scope=QueryScope(), venue_order_id="venue-1").require_exact()
    with pytest.raises(ValidationError, match="timezone-aware"):
        QueryScope(start_at=datetime(2026, 8, 27), end_at=NOW)
    with pytest.raises(ValidationError, match="must not follow"):
        QueryScope(
            start_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            end_at=NOW,
        )


def test_health_cannot_claim_healthy_down_or_hide_failure_reason(identity):
    with pytest.raises(ValidationError, match="reason_code"):
        UTAHealthReport(
            identity=identity,
            health=UTAHealth.DEGRADED,
            reach=UTAReach.CONNECTED,
            completeness=ReadCompleteness.PARTIAL,
            received_at=NOW,
            profile_version="1",
        )
    with pytest.raises(ValidationError, match="contradicts"):
        UTAHealthReport(
            identity=identity,
            health=UTAHealth.HEALTHY,
            reach=UTAReach.DOWN,
            completeness=ReadCompleteness.COMPLETE,
            received_at=NOW,
            profile_version="1",
        )


def _position(identity, instrument):
    return PositionStatusReport(
        identity=identity,
        instrument=instrument,
        side="long",
        quantity="0.01",
        received_at=NOW,
    )


def test_nested_report_evidence_must_match_exact_account(identity, instrument):
    other = identity.model_copy(update={"account_id": "other-account"})
    with pytest.raises(ValidationError, match="different account identity"):
        PositionListReport(
            identity=identity,
            completeness="complete",
            scope=QueryScope(),
            received_at=NOW,
            profile_version="1",
            positions=(_position(other, instrument),),
        )
    assert FillListReport(
        identity=identity,
        completeness="complete",
        scope=QueryScope(),
        received_at=NOW,
        profile_version="1",
    ).fills == ()


def test_conclusive_verified_exit_requires_complete_matching_payload(identity, instrument):
    with pytest.raises(ValidationError, match="complete evidence"):
        VerifiedExitReport(
            identity=identity,
            completeness="partial",
            scope=QueryScope(),
            received_at=NOW,
            reason_code="history_page_missing",
            profile_version="1",
            state=VerifiedExitState.CONFIRMED_ABSENT_WITHOUT_FILL,
            local_position_id="position-1",
        )
    with pytest.raises(ValidationError, match="Fill evidence"):
        VerifiedExitReport(
            identity=identity,
            completeness="complete",
            scope=QueryScope(),
            received_at=NOW,
            profile_version="1",
            state=VerifiedExitState.VERIFIED_FILL_EXIT,
            local_position_id="position-1",
        )
    report = VerifiedExitReport(
        identity=identity,
        completeness="complete",
        scope=QueryScope(),
        received_at=NOW,
        profile_version="1",
        state=VerifiedExitState.POSITION_PRESENT,
        local_position_id="position-1",
        current_position=_position(identity, instrument),
    )
    assert report.state is VerifiedExitState.POSITION_PRESENT


def test_acknowledged_submission_requires_reconcilable_order_identity(identity):
    with pytest.raises(ValidationError, match="stable external order identity"):
        SubmissionReport(
            identity=identity,
            command_id="cmd-1",
            evidence=CommandEvidence.ACKNOWLEDGED,
            received_at=NOW,
            profile_version="1",
        )
    report = SubmissionReport(
        identity=identity,
        command_id="cmd-1",
        evidence=CommandEvidence.ACKNOWLEDGED,
        received_at=NOW,
        venue_order_id="venue-1",
        profile_version="1",
    )
    assert report.venue_order_id == "venue-1"


def test_funding_entries_require_private_ledger_provenance(identity, instrument):
    entry = FundingLedgerEntry(
        identity=identity,
        instrument=instrument,
        settlement_at=NOW,
        amount="-0.25",
        currency="USDT",
        source="broker_ledger",
        source_provenance="binance_private_income_history",
    )
    report = FundingLedgerReport(
        identity=identity,
        completeness="complete",
        scope=QueryScope(instrument=instrument),
        received_at=NOW,
        profile_version="1",
        entries=(entry,),
    )
    assert report.entries[0].source == "broker_ledger"


def test_report_reason_codes_cannot_embed_raw_exception_text(identity):
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        AccountSnapshot(
            identity=identity,
            completeness="unavailable",
            scope=QueryScope(),
            received_at=NOW,
            reason_code="GET https://secret.example",
            profile_version="1",
            settlement_currency="USDT",
        )
