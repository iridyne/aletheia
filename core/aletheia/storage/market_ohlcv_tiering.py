"""Format-neutral hot/cold lifecycle for OHLCV history.

SQLite remains the indexed hot authority.  This module decides which immutable
closed bars are eligible for a cold tier and coordinates verified copy-before-
delete receipts.  It intentionally does not choose whether cold chunks are
SQLite BLOBs, external files, or compressed frames.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Protocol


TIERING_CONTRACT_VERSION = "ohlcv-hot-cold-v1"


@dataclass(frozen=True, slots=True)
class OHLCVSeriesIdentity:
    exchange: str
    symbol: str
    market_type: str
    timeframe: str


@dataclass(frozen=True, slots=True)
class OHLCVTieringPolicy:
    """Keep a query-efficient tail hot and archive only complete chunks."""

    hot_bars: int = 2_048
    cold_chunk_bars: int = 4_096

    def __post_init__(self) -> None:
        if self.hot_bars < 64:
            raise ValueError("hot_bars must be at least 64")
        if self.cold_chunk_bars < 64:
            raise ValueError("cold_chunk_bars must be at least 64")


@dataclass(frozen=True, slots=True)
class OHLCVColdChunk:
    contract_version: str
    identity: OHLCVSeriesIdentity
    first_ts: int
    last_ts: int
    row_count: int
    content_hash: str
    rows: tuple[tuple[int, float, float, float, float, float, str], ...]

    @property
    def chunk_id(self) -> str:
        return (
            f"{self.identity.exchange}:{self.identity.symbol}:"
            f"{self.identity.market_type}:{self.identity.timeframe}:"
            f"{self.first_ts}-{self.last_ts}:{self.content_hash[:16]}"
        )


@dataclass(frozen=True, slots=True)
class ColdWriteReceipt:
    chunk_id: str
    content_hash: str
    row_count: int
    durable: bool


class ColdOHLCVBackend(Protocol):
    """The deliberately undecided BLOB/file/object-storage boundary."""

    def put(self, chunk: OHLCVColdChunk) -> ColdWriteReceipt: ...

    def get(self, chunk_id: str) -> OHLCVColdChunk | None: ...


@dataclass(frozen=True, slots=True)
class OHLCVTieringPlan:
    identity: OHLCVSeriesIdentity
    hot_rows: tuple[tuple[int, float, float, float, float, float, str], ...]
    cold_chunks: tuple[OHLCVColdChunk, ...]
    deferred_cold_rows: int

    @property
    def eligible_cold_rows(self) -> int:
        return sum(chunk.row_count for chunk in self.cold_chunks)


def _canonical_row(row: Any) -> tuple[int, float, float, float, float, float, str]:
    if isinstance(row, dict):
        return (
            int(row["ts"]), float(row["open"]), float(row["high"]),
            float(row["low"]), float(row["close"]), float(row["volume"]),
            str(row.get("provider") or "unknown"),
        )
    return (
        int(row[0]), float(row[1]), float(row[2]), float(row[3]),
        float(row[4]), float(row[5]), str(row[6] if len(row) > 6 else "unknown"),
    )


def _content_hash(rows: tuple[tuple[int, float, float, float, float, float, str], ...]) -> str:
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


class OHLCVTierManager:
    """Plan and verify cold copies without deleting the SQLite hot authority."""

    def __init__(self, policy: OHLCVTieringPolicy | None = None):
        self.policy = policy or OHLCVTieringPolicy()

    def plan(
        self,
        identity: OHLCVSeriesIdentity,
        rows: Iterable[Any],
    ) -> OHLCVTieringPlan:
        by_ts: dict[int, tuple[int, float, float, float, float, float, str]] = {}
        for row in rows:
            canonical = _canonical_row(row)
            by_ts[canonical[0]] = canonical
        ordered = tuple(by_ts[ts] for ts in sorted(by_ts))
        hot_count = min(len(ordered), self.policy.hot_bars)
        hot_tail = ordered[-hot_count:] if hot_count else ()
        cold_candidates = ordered[:-hot_count] if hot_count else ordered
        complete_count = (
            len(cold_candidates) // self.policy.cold_chunk_bars
        ) * self.policy.cold_chunk_bars
        complete_rows = cold_candidates[:complete_count]
        deferred_rows = cold_candidates[complete_count:]
        # Until a full immutable chunk exists, the remainder stays queryable in
        # the hot authority.  Hot storage may therefore temporarily exceed the
        # configured tail by at most cold_chunk_bars-1 rows.
        hot_rows = (*deferred_rows, *hot_tail)
        chunks: list[OHLCVColdChunk] = []
        for offset in range(0, complete_count, self.policy.cold_chunk_bars):
            chunk_rows = complete_rows[offset : offset + self.policy.cold_chunk_bars]
            chunks.append(
                OHLCVColdChunk(
                    contract_version=TIERING_CONTRACT_VERSION,
                    identity=identity,
                    first_ts=chunk_rows[0][0],
                    last_ts=chunk_rows[-1][0],
                    row_count=len(chunk_rows),
                    content_hash=_content_hash(chunk_rows),
                    rows=chunk_rows,
                )
            )
        return OHLCVTieringPlan(
            identity=identity,
            hot_rows=hot_rows,
            cold_chunks=tuple(chunks),
            deferred_cold_rows=len(deferred_rows),
        )

    def copy_to_cold(
        self,
        plan: OHLCVTieringPlan,
        backend: ColdOHLCVBackend,
    ) -> tuple[ColdWriteReceipt, ...]:
        """Copy and read-back verify every chunk; never delete hot rows here."""

        receipts: list[ColdWriteReceipt] = []
        for chunk in plan.cold_chunks:
            receipt = backend.put(chunk)
            if (
                not receipt.durable
                or receipt.chunk_id != chunk.chunk_id
                or receipt.content_hash != chunk.content_hash
                or receipt.row_count != chunk.row_count
            ):
                raise RuntimeError(f"cold write receipt mismatch for {chunk.chunk_id}")
            restored = backend.get(chunk.chunk_id)
            if restored is None or restored.content_hash != chunk.content_hash or restored.rows != chunk.rows:
                raise RuntimeError(f"cold read-back verification failed for {chunk.chunk_id}")
            receipts.append(receipt)
        return tuple(receipts)

    @staticmethod
    def deletion_cutoff(receipts: Iterable[ColdWriteReceipt], plan: OHLCVTieringPlan) -> int | None:
        """Return a safe inclusive ``delete through ts`` only after proof."""

        receipt_map = {receipt.chunk_id: receipt for receipt in receipts if receipt.durable}
        if any(chunk.chunk_id not in receipt_map for chunk in plan.cold_chunks):
            return None
        return max((chunk.last_ts for chunk in plan.cold_chunks), default=None)


class MemoryColdOHLCVBackend:
    """Deterministic test/reference backend, not a production format choice."""

    def __init__(self) -> None:
        self._chunks: dict[str, OHLCVColdChunk] = {}

    def put(self, chunk: OHLCVColdChunk) -> ColdWriteReceipt:
        self._chunks[chunk.chunk_id] = chunk
        return ColdWriteReceipt(chunk.chunk_id, chunk.content_hash, chunk.row_count, True)

    def get(self, chunk_id: str) -> OHLCVColdChunk | None:
        return self._chunks.get(chunk_id)
