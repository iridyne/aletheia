#!/usr/bin/env python3
"""Forced-flow event study on Binance USDT-M public data (pre-registered, #3934).

Question: after an extreme one-minute taker-selling shock in a thin altcoin
perpetual, does a limit buy resting below the price earn money net of costs?
Mirror: after an extreme taker-buying shock, a limit sell resting above.

Every parameter below is frozen by the pre-registration
``docs/research/trading-theory/12_forced_flow_event_study_preregistration_2026-10-10.md``.
Changing one after seeing results is a new hypothesis and must be re-registered.

* Data: ``data.binance.vision`` monthly 1m klines (``taker_buy_quote_volume``)
  and daily ``metrics`` (5m open interest). Public, no credentials. Liquidation
  snapshots are not published (HTTP 404), so forced flow is proxied by taker
  imbalance and split afterwards by the open-interest change.
* Event (long): net taker sell quote volume of minute t above its trailing
  7-day 99.5th percentile (known before t), red bar; 60-minute declustering.
* Order: placed after bar t closes at ``close_t * exp(-k * sigma_1m * sqrt(15))``
  (sigma = trailing 24h std of 1m log returns), alive for bars t+1..t+30. It
  fills only if a later low trades strictly through the price (touch != fill).
  Fill price = limit (maker). Exit = close of bar fill+H as taker.
* Net return = gross - 14 bp (maker 2 + taker 5 + exit slippage/funding buffer).
* Control: same order rule at random non-event minutes of the same coin.
* CI: day-cluster bootstrap (resample UTC fill days), as in
  ``selection_alpha_table.py``; events from one day share the market move.

Usage:
  uv run python scripts/analysis/forced_flow_event_study.py download
  uv run python scripts/analysis/forced_flow_event_study.py selfcheck
  uv run python scripts/analysis/forced_flow_event_study.py run --period insample
  uv run python scripts/analysis/forced_flow_event_study.py run --period holdout
"""

from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Frozen 2026-10-10: USDT PERPETUAL, underlyingType COIN, onboarded before
# 2026-03-02, 24h quote volume 1M-50M USDT (362 symbols); 30 drawn at evenly
# spaced volume ranks, lowest first.
SYMBOLS = (
    "MAVUSDT KOMAUSDT XVGUSDT 1000XECUSDT XANUSDT ACHUSDT BLESSUSDT TREEUSDT "
    "AGLDUSDT SAFEUSDT CARVUSDT CLOUSDT ARKMUSDT SCRUSDT DIAUSDT AZTECUSDT "
    "CVCUSDT BIOUSDT SYNUSDT JSTUSDT SKYUSDT BULLAUSDT MANTAUSDT LSKUSDT "
    "HUMAUSDT AXSUSDT ALGOUSDT ICPUSDT PYTHUSDT LTCUSDT"
).split()
MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
PERIODS = {"insample": ("2026-04-01", "2026-09-01"), "holdout": ("2026-09-01", "2026-10-01")}

Q = 0.995  # event threshold quantile
THR_WINDOW = 7 * 1440
THR_MIN = 3 * 1440
SIGMA_WINDOW = 1440
DECLUSTER = 60
ORDER_LIFE = 30
KS = (0.0, 1.0, 2.0)
HOLDS = (15, 60, 240)
COST = 0.0014
PRIMARY = ("long", 1.0, 60)
BOOT = 2000
SEED = 20261010

BASE = "https://data.binance.vision/data/futures/um"
CACHE = Path.home() / ".cache" / "aletheia-research" / "binance_vision"


def _fetch(url: str, dest: Path) -> str:
    if dest.exists():
        return "cached"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = r.read()
            break
        except urllib.error.HTTPError as e:
            return f"http {e.code}"
        except (urllib.error.URLError, OSError) as e:  # transient TLS resets
            err = repr(e)
            time.sleep(2 ** attempt)
    else:
        return err
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return "ok"


def download() -> None:
    jobs = []
    for s in SYMBOLS:
        for m in MONTHS:
            jobs.append((f"{BASE}/monthly/klines/{s}/1m/{s}-1m-{m}.zip", CACHE / "klines" / f"{s}-{m}.zip"))
        d = date(2026, 3, 25)  # 7 days of OI before the window
        while d < date(2026, 10, 1):
            jobs.append((f"{BASE}/daily/metrics/{s}/{s}-metrics-{d}.zip", CACHE / "metrics" / s / f"{d}.zip"))
            d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: (j[0], _fetch(*j)), jobs))
    bad = [(u, r) for u, r in res if r not in ("ok", "cached")]
    print(f"{len(res)} files, {len(bad)} missing")
    for u, r in bad[:20]:
        print(" ", r, u)


def _read_zip_csv(path: Path, names: list[str] | None = None) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    has_header = not raw[:1].isdigit()
    return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None, names=None if has_header else names)


KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
              "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def load_klines(sym: str) -> pd.DataFrame | None:
    files = [CACHE / "klines" / f"{sym}-{m}.zip" for m in MONTHS]
    if not all(f.exists() for f in files):
        return None
    k = pd.concat([_read_zip_csv(f, KLINE_COLS) for f in files])
    k["ts"] = pd.to_datetime(k["open_time"], unit="ms", utc=True)
    k = k.drop_duplicates("ts").set_index("ts").sort_index()
    grid = pd.date_range(k.index[0], k.index[-1], freq="1min")
    k = k.reindex(grid)[["open", "high", "low", "close", "quote_volume", "taker_buy_quote_volume"]]
    return k.astype(float)


def load_oi(sym: str) -> pd.Series | None:
    files = sorted((CACHE / "metrics" / sym).glob("*.zip"))
    if not files:
        return None
    m = pd.concat([_read_zip_csv(f) for f in files])
    m["ts"] = pd.to_datetime(m["create_time"], utc=True)
    return m.drop_duplicates("ts").set_index("ts").sort_index()["sum_open_interest"].astype(float)


def events_for(k: pd.DataFrame, side: str) -> np.ndarray:
    """Bar positions of declustered events; thresholds use only bars before t."""
    net_sell = k["quote_volume"] - 2 * k["taker_buy_quote_volume"]
    flow = net_sell if side == "long" else -net_sell
    thr = flow.rolling(THR_WINDOW, min_periods=THR_MIN).quantile(Q).shift(1)
    bar = k["close"] < k["open"] if side == "long" else k["close"] > k["open"]
    hit = np.flatnonzero(((flow > thr) & (flow > 0) & bar).to_numpy())
    out, last = [], -10**9
    for i in hit:
        if i - last > DECLUSTER:
            out.append(i)
            last = i
    return np.array(out, dtype=int)


def simulate(k: pd.DataFrame, pos: np.ndarray, side: str, kk: float) -> list[dict]:
    """One row per (event, hold). Unfilled orders keep fill=False."""
    close, low, high = (k[c].to_numpy() for c in ("close", "low", "high"))
    logret = np.log(k["close"]).diff()
    sigma = logret.rolling(SIGMA_WINDOW, min_periods=SIGMA_WINDOW // 2).std().to_numpy()
    n, sgn, rows = len(k), (1 if side == "long" else -1), []
    for t in pos:
        if not np.isfinite(sigma[t]) or not np.isfinite(close[t]):
            continue
        limit = close[t] * np.exp(-sgn * kk * sigma[t] * np.sqrt(15))
        win = slice(t + 1, min(t + 1 + ORDER_LIFE, n))
        through = low[win] < limit if side == "long" else high[win] > limit
        through = np.nan_to_num(through, nan=False).astype(bool)
        j = t + 1 + int(np.argmax(through)) if through.any() else None
        base = {"t": int(t), "ts": k.index[t], "side": side, "k": kk, "sigma": float(sigma[t])}
        for h in HOLDS:
            if j is None:
                rows.append({**base, "hold": h, "fill": False, "gross": np.nan, "mae": np.nan})
                continue
            e = j + h
            if e >= n or not np.isfinite(close[e]):
                continue
            gross = sgn * (close[e] / limit - 1)
            path = low[j:e + 1] if side == "long" else high[j:e + 1]
            mae = np.nanmin(sgn * (path / limit - 1))
            rows.append({**base, "hold": h, "fill": True, "fill_ts": k.index[j], "gross": gross, "mae": mae})
    return rows


def control_positions(k: pd.DataFrame, ev: np.ndarray, size: int, rng: np.random.Generator) -> np.ndarray:
    ok = np.ones(len(k), bool)
    ok[:THR_MIN] = False
    ok[-(ORDER_LIFE + max(HOLDS) + 1):] = False
    for t in ev:
        ok[max(0, t - DECLUSTER):t + DECLUSTER + 1] = False
    cand = np.flatnonzero(ok & np.isfinite(k["close"].to_numpy()))
    return np.sort(rng.choice(cand, size=min(size, len(cand)), replace=False)) if len(cand) else cand


def oi_change(oi: pd.Series | None, ts: pd.Series) -> np.ndarray:
    """OI % change from the last snapshot <= t-5m to the first snapshot in [t+10m, t+30m]."""
    if oi is None:
        return np.full(len(ts), np.nan)
    before = oi.reindex(ts - pd.Timedelta("5min"), method="ffill").to_numpy()
    after_idx = oi.index.searchsorted(ts + pd.Timedelta("10min"))
    after = np.full(len(ts), np.nan)
    for i, (a, t) in enumerate(zip(after_idx, ts)):
        if a < len(oi) and oi.index[a] <= t + pd.Timedelta("30min"):
            after[i] = oi.iloc[a]
    before = np.where(before > 0, before, np.nan)  # a few snapshots report OI = 0
    return after / before - 1


def boot_ci(df: pd.DataFrame, col: str, rng: np.random.Generator) -> tuple[float, float, float, int, int]:
    """Mean and day-cluster bootstrap 95% CI. Days = UTC fill days."""
    d = df.dropna(subset=[col])
    if d.empty:
        return (np.nan,) * 3 + (0, 0)
    g = d.groupby(d["fill_ts"].dt.floor("D"))[col]
    sums, cnts = g.sum().to_numpy(), g.count().to_numpy()
    nd = len(sums)
    if nd < 10:
        return float(d[col].mean()), np.nan, np.nan, len(d), nd
    idx = rng.integers(0, nd, size=(BOOT, nd))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(d[col].mean()), float(lo), float(hi), len(d), nd


def run(period: str) -> None:
    start, end = (pd.Timestamp(x, tz="UTC") for x in PERIODS[period])
    rng = np.random.default_rng(SEED)
    ev_rows, ctl_rows, coverage = [], [], {}
    for rank, sym in enumerate(SYMBOLS):
        k = load_klines(sym)
        if k is None:
            coverage[sym] = "missing klines"
            continue
        oi = load_oi(sym)
        tier = ("thin", "mid", "thick")[rank * 3 // len(SYMBOLS)]
        in_period = (k.index >= start) & (k.index < end)
        for side in ("long", "short"):
            ev_all = events_for(k, side)
            ev = ev_all[in_period[ev_all]]
            ctl = control_positions(k, ev_all, len(ev) * 3, rng)
            ctl = ctl[in_period[ctl]]
            for kk in KS:
                for rows, pos in ((ev_rows, ev), (ctl_rows, ctl)):
                    got = simulate(k, pos, side, kk)
                    for r in got:
                        r.update(symbol=sym, tier=tier)
                    rows.extend(got)
        coverage[sym] = f"{int(in_period.sum() - k['close'][in_period].isna().sum())} bars"
    ev_df, ctl_df = pd.DataFrame(ev_rows), pd.DataFrame(ctl_rows)
    for df in (ev_df, ctl_df):
        df["net"] = df["gross"] - COST
    # OI split once per event bar (independent of k / hold).
    ev_df["oi_chg"] = np.nan
    for sym, g in ev_df.groupby("symbol"):
        ts = g["ts"].drop_duplicates()
        ch = pd.Series(oi_change(load_oi(sym), ts), index=ts.index)
        ev_df.loc[g.index, "oi_chg"] = g["ts"].map(dict(zip(ts, ch)))

    out = {"period": period, "coverage": coverage, "cells": []}
    print(f"# Forced-flow event study — {period} {PERIODS[period][0]}..{PERIODS[period][1]}")
    print("missing:", [s for s, c in coverage.items() if c.startswith("missing")] or "none")
    print("\n| side | k | hold | events | fill% | n filled | days | net mean bp | 95% CI bp | ctl net bp | "
          "evt-ctl bp | 95% CI bp | win% | median MAE bp |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for side in ("long", "short"):
        for kk in KS:
            for h in HOLDS:
                cell = (side, kk, h)
                e = ev_df[(ev_df.side == side) & (ev_df.k == kk) & (ev_df.hold == h)]
                c = ctl_df[(ctl_df.side == side) & (ctl_df.k == kk) & (ctl_df.hold == h)]
                ef, cf = e[e.fill], c[c.fill]
                m, lo, hi, nf, nd = boot_ci(ef, "net", rng)
                cm = cf["net"].mean() if len(cf) else np.nan
                dm, dlo, dhi = _diff_ci(ef, cf, rng)
                row = dict(side=side, k=kk, hold=h, events=len(e), fill_rate=len(ef) / max(len(e), 1),
                           n_filled=nf, days=nd, net=m, lo=lo, hi=hi, ctl_net=cm, diff=dm, dlo=dlo, dhi=dhi,
                           win=float((ef["net"] > 0).mean()) if nf else np.nan,
                           mae=float(ef["mae"].median()) if nf else np.nan, primary=cell == PRIMARY)
                out["cells"].append(row)
                tag = " **(primary)**" if row["primary"] else ""
                print(f"| {side}{tag} | {kk:g} | {h} | {len(e)} | {row['fill_rate']:.0%} | {nf} | {nd} | "
                      f"{_bp(m)} | {_bp(lo)}..{_bp(hi)} | {_bp(cm)} | {_bp(dm)} | {_bp(dlo)}..{_bp(dhi)} | "
                      f"{_pct(row['win'])} | {_bp(row['mae'])} |")

    side, kk, h = PRIMARY
    p = ev_df[(ev_df.side == side) & (ev_df.k == kk) & (ev_df.hold == h)]
    pf = p[p.fill]
    print("\n## Secondary splits of the primary cell (exploratory, not pass criteria)\n")
    print("| split | n filled | days | net mean bp | 95% CI bp |")
    print("|---|---|---|---|---|")
    splits = {f"tier={t}": pf[pf.tier == t] for t in ("thin", "mid", "thick")}
    splits["OI fell (<=-0.5%)"] = pf[pf.oi_chg <= -0.005]
    splits["OI flat/up (>-0.5%)"] = pf[pf.oi_chg > -0.005]
    splits["OI unknown"] = pf[pf.oi_chg.isna()]
    for name, d in splits.items():
        m, lo, hi, nf, nd = boot_ci(d, "net", rng)
        out["cells"].append(dict(split=name, n_filled=nf, days=nd, net=m, lo=lo, hi=hi))
        print(f"| {name} | {nf} | {nd} | {_bp(m)} | {_bp(lo)}..{_bp(hi)} |")
    # Adverse-selection diagnostic: what the unfilled orders missed (move from limit, same hold).
    print("\nevents per symbol (primary):", p.groupby("symbol").size().sort_values(ascending=False).head(10).to_dict())
    top = pf.groupby("fill_ts").size().sort_values(ascending=False).head(5)
    print("busiest fill minutes (cross-coin clustering):", {str(t): int(n) for t, n in top.items()})
    dest = CACHE / f"results-{period}.json"
    dest.write_text(json.dumps(out, default=str, indent=1))
    ev_df.to_parquet(CACHE / f"events-{period}.parquet") if _has_parquet() else ev_df.to_csv(CACHE / f"events-{period}.csv")
    print(f"\nwrote {dest}")


def _diff_ci(ef: pd.DataFrame, cf: pd.DataFrame, rng: np.random.Generator) -> tuple[float, float, float]:
    """Event minus control mean net, bootstrapping days jointly across both sets."""
    if ef.empty or cf.empty:
        return np.nan, np.nan, np.nan
    a = ef.assign(day=ef["fill_ts"].dt.floor("D"))
    b = cf.assign(day=cf["fill_ts"].dt.floor("D"))
    days = np.array(sorted(set(a.day) | set(b.day)))
    if len(days) < 10:
        return float(a.net.mean() - b.net.mean()), np.nan, np.nan
    ga, gb = a.groupby("day")["net"].agg(["sum", "count"]), b.groupby("day")["net"].agg(["sum", "count"])
    ga, gb = ga.reindex(days, fill_value=0), gb.reindex(days, fill_value=0)
    idx = rng.integers(0, len(days), size=(BOOT, len(days)))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = (ga["sum"].to_numpy()[idx].sum(1) / ga["count"].to_numpy()[idx].sum(1)
             - gb["sum"].to_numpy()[idx].sum(1) / gb["count"].to_numpy()[idx].sum(1))
    lo, hi = np.nanpercentile(d, [2.5, 97.5])
    return float(a.net.mean() - b.net.mean()), float(lo), float(hi)


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def _bp(x: float) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x * 1e4:+.1f}"


def _pct(x: float) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.0%}"


def selfcheck() -> None:
    """Synthetic bars: one forced-sell bar at t=5000; assert detection, fill and exit arithmetic."""
    rng = np.random.default_rng(0)
    n, t = 6000, 5000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    k = pd.DataFrame({"close": close, "open": np.r_[close[0], close[:-1]], "quote_volume": 1000.0,
                      "taker_buy_quote_volume": 500.0}, index=pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC"))
    k.iloc[t, k.columns.get_loc("taker_buy_quote_volume")] = 0.0
    k.iloc[t, k.columns.get_loc("open")] = close[t] * 1.01
    k["high"], k["low"] = k[["open", "close"]].max(axis=1) * 1.0002, k[["open", "close"]].min(axis=1) * 0.9998
    k.iloc[t + 3, k.columns.get_loc("low")] = close[t] * 0.9  # trades through any k<=2 limit
    assert events_for(k, "long").tolist() == [t], events_for(k, "long")
    assert events_for(k, "short").size == 0
    rows = {r["hold"]: r for r in simulate(k, np.array([t]), "long", 1.0)}
    r = rows[60]
    limit = close[t] * np.exp(-r["sigma"] * np.sqrt(15))
    assert r["fill"] and r["fill_ts"] == k.index[t + 3], r
    assert abs(r["gross"] - (close[t + 3 + 60] / limit - 1)) < 1e-12
    far = simulate(k, np.array([t]), "long", 1000.0)
    assert not any(x["fill"] for x in far)
    print("selfcheck ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("download")
    sub.add_parser("selfcheck")
    r = sub.add_parser("run")
    r.add_argument("--period", choices=sorted(PERIODS), required=True)
    a = ap.parse_args()
    {"download": download, "selfcheck": selfcheck}.get(a.cmd, lambda: run(a.period))()


if __name__ == "__main__":
    main()
