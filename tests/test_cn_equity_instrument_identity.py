import sqlite3

import pytest
from pydantic import TypeAdapter, ValidationError

from aletheia.uta.models import CNEquityInstrumentIdentityV2


@pytest.mark.parametrize(
    "fields",
    [
        {"venue": "XSHE", "canonical_symbol": "600000"},
        {"venue": "XSHG", "canonical_symbol": "900901"},
        {"venue": "XSHG", "canonical_symbol": "60000"},
        {"venue": "XSHG", "canonical_symbol": "600000", "currency": "USD"},
        {"venue": "XSHG", "canonical_symbol": "600000", "instrument_class": "fund"},
        {"venue": "XNYS", "canonical_symbol": "600000"},
    ],
)
def test_cn_equity_identity_rejects_unsupported_boundaries(fields):
    with pytest.raises(ValidationError):
        CNEquityInstrumentIdentityV2(**fields)


@pytest.mark.parametrize(
    ("venue", "canonical_symbol"),
    [("XSHG", "688001"), ("XSHE", "300001")],
)
def test_observation_only_boards_retain_identity(venue, canonical_symbol):
    identity = CNEquityInstrumentIdentityV2(
        venue=venue,
        canonical_symbol=canonical_symbol,
    )
    assert identity.instrument_key == f"cn_equity:{venue}:{canonical_symbol}:equity"


def test_cn_equity_identity_is_exact_and_versioned():
    shanghai = CNEquityInstrumentIdentityV2(venue="XSHG", canonical_symbol="600000")
    shenzhen = CNEquityInstrumentIdentityV2(venue="XSHE", canonical_symbol="000001")

    assert shanghai.instrument_key == "cn_equity:XSHG:600000:equity"
    assert shenzhen.instrument_key == "cn_equity:XSHE:000001:equity"
    assert shanghai.instrument_key != shenzhen.instrument_key
    assert shanghai.storage_projection() == {
        "market_id": "cn_equity",
        "instrument_key": "cn_equity:XSHG:600000:equity",
        "instrument_identity_version": "instrument-identity/v2",
    }


def test_cn_equity_identity_is_consumable_at_uta_report_boundaries():
    from aletheia.uta.models import (
        FillReport,
        FundingLedgerEntry,
        OrderStatusReport,
        PositionStatusReport,
    )

    identity = CNEquityInstrumentIdentityV2(venue="XSHG", canonical_symbol="600000")
    for model in (PositionStatusReport, OrderStatusReport, FillReport, FundingLedgerEntry):
        annotation = model.model_fields["instrument"].annotation
        assert TypeAdapter(annotation).validate_python(identity.model_dump()) == identity


def test_cn_equity_identity_is_rejected_by_crypto_only_uta_requests():
    from aletheia.uta.models import MutationCommand, QueryScope, VerifiedExitQuery

    payload = CNEquityInstrumentIdentityV2(
        venue="XSHG", canonical_symbol="600000"
    ).model_dump()
    for model in (QueryScope, VerifiedExitQuery, MutationCommand):
        annotation = model.model_fields["instrument"].annotation
        with pytest.raises(ValidationError):
            TypeAdapter(annotation).validate_python(payload)


def test_uta_report_identity_reader_requires_cn_discriminator_and_accepts_crypto_v1():
    from aletheia.uta.models import CryptoInstrumentIdentityV1, PositionStatusReport

    annotation = PositionStatusReport.model_fields["instrument"].annotation
    adapter = TypeAdapter(annotation)
    with pytest.raises(ValidationError):
        adapter.validate_python({"venue": "XSHG", "canonical_symbol": "600000"})

    crypto = CryptoInstrumentIdentityV1(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTC/USDT",
        market_type="spot",
        base_currency="BTC",
        quote_currency="USDT",
    )
    assert adapter.validate_python(crypto.model_dump()) == crypto

def test_cn_equity_report_identity_is_consumable_by_shared_uta_aggregation():
    from decimal import Decimal
    from types import SimpleNamespace

    from aletheia.uta.manager import _shadow_differences

    identity = CNEquityInstrumentIdentityV2(venue="XSHG", canonical_symbol="600000")
    account = SimpleNamespace(
        equity_usdt=100.0,
        available_usdt=80.0,
        unrealized_pnl=0.0,
    )
    uta_account = SimpleNamespace(
        equity=Decimal("100"),
        available=Decimal("80"),
        unrealized_pnl=Decimal("0"),
    )
    facts = {
        "side": "long",
        "quantity": Decimal("100"),
        "entry_price": Decimal("10"),
        "mark_price": Decimal("11"),
        "leverage": None,
        "unrealized_pnl": Decimal("100"),
    }
    legacy = SimpleNamespace(
        symbol=identity.canonical_symbol,
        market_type="cn_equity",
        **facts,
    )
    uta = SimpleNamespace(instrument=identity, **facts)

    assert _shadow_differences(
        account,
        [legacy],
        uta_account,
        SimpleNamespace(positions=[uta]),
    ) == []
def test_identity_projection_persists_across_authority_tables(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories
    from aletheia.storage.models import (
        Opportunity,
        Order,
        Position,
        UniverseMember,
        UniverseSnapshot,
    )
    from aletheia.storage.sqlite import get_db

    identity = CNEquityInstrumentIdentityV2(venue="XSHG", canonical_symbol="600000")
    projection = identity.storage_projection()
    snapshot = UniverseSnapshot(market_type="cn_equity")
    repositories.insert_universe_snapshot(snapshot)

    records = {
        "universe_members": UniverseMember(
            snapshot_id=snapshot.id,
            asset_id=identity.instrument_key,
            venue=identity.venue,
            symbol=identity.canonical_symbol,
            market_type="cn_equity",
            rank=1,
            total_score=1.0,
            **projection,
        ),
        "opportunities": Opportunity(
            symbol=identity.canonical_symbol,
            market="cn_equity",
            asset_class="equity",
            asset_subclass="a_share",
            execution_eligible=False,
            **projection,
        ),
        "orders": Order(
            symbol=identity.canonical_symbol,
            market_type="cn_equity",
            direction="buy",
            order_type="limit",
            quantity=100,
            price=10.0,
            execution_mode="paper_internal",
            source="adopted",
            **projection,
        ),
        "positions": Position(
            symbol=identity.canonical_symbol,
            market_type="cn_equity",
            direction="long",
            quantity=100,
            entry_price=10.0,
            execution_mode="paper_internal",
            **projection,
        ),
    }
    repositories.insert_universe_member(records["universe_members"])
    repositories.insert_opportunity(records["opportunities"])
    repositories.insert_order(records["orders"])
    repositories.insert_position(records["positions"])

    db = get_db()
    for table, record in records.items():
        row = db.execute(
            f"SELECT market_id, instrument_key, instrument_identity_version FROM {table} WHERE id=?",
            (record.id,),
        ).fetchone()
        assert tuple(row) == (
            "cn_equity",
            "cn_equity:XSHG:600000:equity",
            "instrument-identity/v2",
        )
        with pytest.raises(sqlite3.IntegrityError, match="instrument_identity_immutable"):
            db.execute(
                f"UPDATE {table} SET instrument_key=? WHERE id=?",
                ("cn_equity:XSHG:601000:equity", record.id),
            )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE opportunities SET asset_class=NULL WHERE id=?",
            (records["opportunities"].id,),
        )


@pytest.mark.parametrize(
    ("market_id", "instrument_key", "version", "symbol", "market"),
    [
        ("cn_equity", None, "instrument-identity/v2", "600000", "cn_equity"),
        ("crypto", "crypto:XSHG:600000:equity", "instrument-identity/v2", "600000", "cn_equity"),
        ("cn_equity", "cn_equity:XSHG:600000:equity", "instrument-identity/v3", "600000", "cn_equity"),
        ("cn_equity", "cn_equity:XSHG:600000:a_share", "instrument-identity/v2", "600000", "cn_equity"),
        ("cn_equity", "cn_equity:XSHG:600000:equity", "instrument-identity/v2", "000001", "cn_equity"),
        ("cn_equity", "cn_equity:XSHG:600000:equity", "instrument-identity/v2", "600000", "spot"),
    ],
)
def test_invalid_or_contradictory_projection_fails(
    tmp_path,
    monkeypatch,
    market_id,
    instrument_key,
    version,
    symbol,
    market,
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories
    from aletheia.storage.models import Opportunity

    row = Opportunity(
        symbol=symbol,
        market=market,
        asset_class="equity",
        execution_eligible=False,
        market_id=market_id,
        instrument_key=instrument_key,
        instrument_identity_version=version,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repositories.insert_opportunity(row)


def test_cn_rows_require_identity_and_consistent_legacy_projections(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories
    from aletheia.storage.models import (
        Opportunity,
        Order,
        Position,
        UniverseMember,
        UniverseSnapshot,
    )

    identity = CNEquityInstrumentIdentityV2(venue="XSHG", canonical_symbol="600000")
    snapshot = UniverseSnapshot(market_type="cn_equity")
    repositories.insert_universe_snapshot(snapshot)
    rows = (
        (
            repositories.insert_universe_member,
            UniverseMember(
                snapshot_id=snapshot.id,
                asset_id=identity.instrument_key,
                venue=identity.venue,
                symbol=identity.canonical_symbol,
                market_type="cn_equity",
                rank=1,
                total_score=1.0,
            ),
        ),
        (
            repositories.insert_opportunity,
            Opportunity(
                symbol=identity.canonical_symbol,
                market="cn_equity",
                asset_class="equity",
                execution_eligible=False,
            ),
        ),
        (
            repositories.insert_order,
            Order(
                symbol=identity.canonical_symbol,
                market_type="cn_equity",
                direction="buy",
                order_type="limit",
                quantity=100,
                price=10.0,
                execution_mode="paper_internal",
                source="adopted",
            ),
        ),
        (
            repositories.insert_position,
            Position(
                symbol=identity.canonical_symbol,
                market_type="cn_equity",
                direction="long",
                quantity=100,
                entry_price=10.0,
                execution_mode="paper_internal",
            ),
        ),
    )
    for writer, row in rows:
        with pytest.raises(sqlite3.IntegrityError):
            writer(row)

    with pytest.raises(sqlite3.IntegrityError):
        repositories.insert_universe_member(
            UniverseMember(
                snapshot_id=snapshot.id,
                asset_id=identity.instrument_key,
                venue=None,
                symbol=identity.canonical_symbol,
                market_type="cn_equity",
                rank=1,
                total_score=1.0,
                **identity.storage_projection(),
            )
        )
    with pytest.raises(sqlite3.IntegrityError):
        repositories.insert_opportunity(
            Opportunity(
                symbol=identity.canonical_symbol,
                market="cn_equity",
                asset_class="crypto",
                execution_eligible=False,
                **identity.storage_projection(),
            )
        )
def test_legacy_crypto_writer_remains_null(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories
    from aletheia.storage.models import Opportunity
    from aletheia.storage.sqlite import get_db

    legacy = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        execution_eligible=False,
    )
    repositories.insert_opportunity(legacy)
    row = get_db().execute(
        "SELECT market_id, instrument_key, instrument_identity_version "
        "FROM opportunities WHERE id=?",
        (legacy.id,),
    ).fetchone()
    assert tuple(row) == (None, None, None)
