from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aletheia.uta.contract import UTA_CONTRACT_VERSION
from aletheia.uta.models import (
    AccountSnapshot,
    CommandEvidence,
    InstrumentIdentity,
    QueryScope,
    ReadCompleteness,
    SubmitOrderCommand,
    UTAAccountIdentity,
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
    )
    assert command.command_id != command.client_order_id != command.proposal_id
    assert command.expected_account.credential_generation == 3
    assert set(CommandEvidence) == {
        CommandEvidence.NOT_SENT,
        CommandEvidence.VENUE_REJECTED,
        CommandEvidence.ACKNOWLEDGED,
        CommandEvidence.AMBIGUOUS,
    }
