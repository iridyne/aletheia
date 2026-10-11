#!/usr/bin/env python3
"""Time-series trend (TSMOM) on Binance USDT-M perpetuals (pre-registered, #3934).

Question: does an untuned TSMOM rule on liquid USDT-M perpetuals earn a positive
net return out of sample, after fees, slippage and actual funding, beyond
holding the market?

Every parameter below is frozen by the pre-registration
``docs/research/trading-theory/14_tsmom_trend_preregistration_2026-10-11.md``.
Changing one after seeing results is a new hypothesis and must be re-registered.

* Data: ``data.binance.vision`` monthly 1d klines and monthly fundingRate for
  every USDT-quoted perpetual in the archive (delisted included), UTC.
  In-sample 2020-01-01..2023-12-31, holdout 2024-01-01..2026-09-30.
* Universe (point-in-time): >=180 daily bars before the rebalance, valid Sunday
  close, top 20 by trailing 30-day quote volume up to Sunday. Stablecoin bases
  and index/composite contracts are excluded.
* Rebalance: weekly, Monday 00:00 UTC, signal from closes <= Sunday, fills at
  Monday's daily open. Signal = mean of sign(close_t/close_{t-L}-1), L=20,60,120.
* Weights: s_i * (40% / sigma_i) / N, sigma_i = 60d std of daily log returns *
  sqrt(365); then scaled so ex-ante portfolio vol (60d covariance) = 20%/yr;
  then per-asset |w| <= 0.25 and gross <= 2.0.
* Costs 10 bp per side on traded notional (5 bp taker + 5 bp slippage); a symbol
  that stops trading is closed at its last close with the same cost. Funding:
  actual rates applied to held notional, long pays positive funding.
* Benchmarks: (a) BTCUSDT perp buy-and-hold, (b) same universe and vol
  targeting, long-only (s = +1).
* Stats: weekly net returns, Sharpe = mean/std*sqrt(52), moving-block bootstrap of
  the mean (4-week blocks, 2000 draws, seed 20261011), OLS intercept vs (b).

Usage:
  uv run python scripts/analysis/tsmom_trend_study.py download
  uv run python scripts/analysis/tsmom_trend_study.py selfcheck
  uv run python scripts/analysis/tsmom_trend_study.py run --period insample
  uv run python scripts/analysis/tsmom_trend_study.py run --period holdout
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

# Frozen 2026-10-11 (pre-registration 14).
LOOKBACKS = (20, 60, 120)
VOL_WIN = 60
VOL_ASSET = 0.40
VOL_PORT = 0.20
CAP_ASSET = 0.25
CAP_GROSS = 2.0
TOP_N = 20
MIN_HIST = 180
VOLUME_WIN = 30
COST_SIDE = 0.0010  # 5 bp taker + 5 bp slippage
BLOCK = 4
BOOT = 2000
SEED = 20261011
PERIODS = {"insample": ("2020-01-01", "2023-12-31"), "holdout": ("2024-01-01", "2026-09-30")}
DATA_START, DATA_END = "2019-07-01", "2026-09-30"
PASS_SHARPE, PASS_MDD = 0.5, 0.35
# Stablecoin bases named in the registration; index/composite contracts found in the
# archive listing (BTCDOM, DEFI, FOOTBALL, BLUEBIRD are the non-asset index perps).
EXCLUDED_BASES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "BTCDOM", "DEFI", "FOOTBALL", "BLUEBIRD"}

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/futures/um"
CACHE = Path.home() / ".cache" / "aletheia-research" / "binance_vision"
KLINES, FUNDING = CACHE / "klines1d", CACHE / "funding"
MISSING_LOG = CACHE / "tsmom_missing.json"
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
              "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
FUND_COLS = ["calc_time", "funding_interval_hours", "last_funding_rate"]


# ---------------------------------------------------------------- download
def _get(url: str, dest: Path | None = None) -> str:
    """GET with retry. dest=None returns the body text, else writes dest and returns a status."""
    if dest is not None and dest.exists():
        return "cached"
    err = ""
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.parse.quote(url, safe=":/?&="), timeout=60) as r:  # non-ASCII symbols
                body = r.read()
            break
        except urllib.error.HTTPError as e:
            return f"http {e.code}"
        except (urllib.error.URLError, OSError) as e:  # transient TLS resets
            err = repr(e)
            time.sleep(2 ** attempt)
    else:
        return err
    if dest is None:
        return body.decode()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return "ok"


def _s3_list(prefix: str, pattern: str, delimiter: bool) -> list[str]:
    """S3 v1 listing with marker pagination; returns regex group(1) of every Prefix (or Key)."""
    out, marker = [], ""
    while True:
        url = f"{S3}?prefix={prefix}" + ("&delimiter=/" if delimiter else "") + (f"&marker={marker}" if marker else "")
        body = _get(url)
        if "<ListBucketResult" not in body:
            raise RuntimeError(f"S3 listing failed: {url} -> {body}")
        out += re.findall(pattern, body)
        nxt = re.search(r"<NextMarker>([^<]+)", body)
        if "<IsTruncated>true" in body:
            # Without a delimiter S3 v1 omits NextMarker: continue after the last key.
            marker = nxt.group(1) if nxt else re.findall(r"<Key>([^<]+)", body)[-1]
        else:
            return out


def list_symbols() -> list[str]:
    """All archive directories; USDT-quoted perpetuals only (dated futures carry a _YYMMDD suffix)."""
    pre = "data/futures/um/monthly/klines/"
    allsyms = _s3_list(pre, rf"<Prefix>{pre}([^/<]+)/</Prefix>", True)
    return sorted(s for s in allsyms if s.endswith("USDT") and s[:-4] not in EXCLUDED_BASES)


def _month_range(first: str = "2019-07", last: str = "2026-09") -> list[str]:
    return [str(p) for p in pd.period_range(first, last, freq="M")]


def download() -> None:
    syms = list_symbols()
    kinds = (("klines", "1d/", KLINES, "1d"), ("fundingRate", "", FUNDING, "fundingRate"))
    pairs = [(s, k) for s in syms for k in kinds]

    def listed(sk: tuple) -> set[str]:
        (s, (kind, sub, _, _)) = sk
        return set(_s3_list(f"data/futures/um/monthly/{kind}/{s}/{sub}", r"<Key>[^<]*-(\d{4}-\d{2})\.zip</Key>", False))

    with ThreadPoolExecutor(8) as ex:
        have = list(ex.map(listed, pairs))
    jobs, missing = [], []
    for (s, (kind, sub, dest, tag)), h in zip(pairs, have):
        for m in _month_range():  # absent months are listing/delisting gaps, recorded not fatal
            if m in h:
                jobs.append((f"{BASE}/monthly/{kind}/{s}/{sub}{s}-{tag}-{m}.zip", dest / f"{s}-{m}.zip"))
            else:
                missing.append(f"{kind}:{s}:{m}")
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: (j[0], _get(*j)), jobs))
    bad = [(u, r) for u, r in res if r not in ("ok", "cached")]
    fetched = sum(r == "ok" for _, r in res)
    MISSING_LOG.write_text(json.dumps({"not_in_archive": missing, "failed": bad}))
    have_k = sorted({p.name.rsplit("-", 2)[0] for p in KLINES.glob("*.zip")})
    print(f"symbols listed (USDT perp, after exclusions): {len(syms)}; with 1d klines cached: {len(have_k)}")
    print(f"files wanted {len(res)}, newly fetched {fetched}, cached {sum(r == 'cached' for _, r in res)}, "
          f"failed {len(bad)}; months absent from archive {len(missing)} (log: {MISSING_LOG})")
    for u, r in bad[:20]:
        print(" ", r, u)
    print("delisted examples present:", [s for s in ("SRMUSDT", "FTTUSDT") if s in have_k])


# ---------------------------------------------------------------- data
def _read_zip_csv(path: Path, names: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    has_header = not raw[:1].isdigit()
    return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None, names=None if has_header else names)


def _day(ts: pd.Series) -> pd.DatetimeIndex:
    unit = "us" if ts.iloc[0] > 1e14 else "ms"
    return pd.DatetimeIndex(pd.to_datetime(ts, unit=unit)).floor("D")


class Data:
    """Dense daily arrays (days x symbols). NaN = no bar. F = summed funding rate per UTC day."""

    def __init__(self, frames: dict[str, pd.DataFrame], funding: dict[str, pd.Series], start: str, end: str):
        self.dates = pd.date_range(start, end, freq="D")
        self.syms = sorted(frames)
        shape = (len(self.dates), len(self.syms))
        self.O, self.C, self.Q = (np.full(shape, np.nan) for _ in range(3))
        self.F = np.zeros(shape)
        for j, s in enumerate(self.syms):
            k = frames[s].reindex(self.dates)
            self.O[:, j], self.C[:, j], self.Q[:, j] = k["open"], k["close"], k["quote_volume"]
            if s in funding:
                self.F[:, j] = funding[s].reindex(self.dates).fillna(0.0)
        self.cnt = np.cumsum(~np.isnan(self.C), axis=0)  # daily bars seen so far

    def pos(self, d: str | pd.Timestamp) -> int:
        return int(self.dates.get_loc(pd.Timestamp(d)))


def load_all() -> Data:
    frames, funding = {}, {}
    syms = sorted({p.name.rsplit("-", 2)[0] for p in KLINES.glob("*.zip")})
    for s in syms:
        k = pd.concat([_read_zip_csv(f, KLINE_COLS) for f in sorted(KLINES.glob(f"{s}-????-??.zip"))])
        k.index = _day(k["open_time"])
        frames[s] = k[~k.index.duplicated()][["open", "close", "quote_volume"]].astype(float)
        fs = sorted(FUNDING.glob(f"{s}-????-??.zip"))
        if fs:
            f = pd.concat([_read_zip_csv(p, FUND_COLS) for p in fs])
            funding[s] = f["last_funding_rate"].astype(float).groupby(_day(f["calc_time"])).sum()
    return Data(frames, funding, DATA_START, DATA_END)


# ---------------------------------------------------------------- strategy
def target_weights(D: Data, i: int, kind, topn: int = TOP_N, allowed: set[str] | None = None) -> np.ndarray:
    """Weights at rebalance row i (Monday). Reads rows < i of C/Q (through Sunday) and O[i] only.

    kind: "ens" (mean of 3 signs), an int lookback L, "long" (s=+1), "longonly" (max(ens, 0)).
    """
    s = i - 1
    w = np.zeros(len(D.syms))
    if s < max(LOOKBACKS):
        return w
    win = D.C[s - max(LOOKBACKS): s + 1]  # 121 closes through Sunday
    ok = ~np.isnan(win).any(0) & (D.cnt[s] >= MIN_HIST) & ~np.isnan(D.O[i])
    if allowed is not None:
        ok &= np.array([x in allowed for x in D.syms])
    vol = np.nansum(D.Q[s - VOLUME_WIN + 1: s + 1], axis=0)
    pick = sorted(np.flatnonzero(ok), key=lambda j: (-vol[j], D.syms[j]))[:topn]
    if not pick:
        return w
    c = win[:, pick]
    sign = lambda L: np.sign(c[-1] / c[-1 - L] - 1)
    if kind == "long":
        sg = np.ones(len(pick))
    elif kind == "ens" or kind == "longonly":
        sg = np.mean([sign(L) for L in LOOKBACKS], axis=0)
        sg = np.maximum(sg, 0) if kind == "longonly" else sg
    else:
        sg = sign(int(kind))
    r = np.diff(np.log(c[-VOL_WIN - 1:]), axis=0)
    sig = r.std(axis=0, ddof=1) * np.sqrt(365)
    raw = sg * (VOL_ASSET / sig) / len(pick)
    pv = np.sqrt(raw @ (np.cov(r.T).reshape(len(pick), len(pick)) * 365) @ raw)
    if pv == 0 or not np.isfinite(pv):
        return w
    x = np.clip(raw * VOL_PORT / pv, -CAP_ASSET, CAP_ASSET)
    gross = np.abs(x).sum()
    w[pick] = x * min(1.0, CAP_GROSS / gross) if gross > 0 else x
    return w


def backtest(D: Data, start: str, end: str, kind="ens", step: int = 7, topn: int = TOP_N, cost: float = COST_SIDE,
             allowed: set[str] | None = None, fixed: dict[str, float] | None = None) -> pd.DataFrame:
    """Per-period net returns. Rows: rebalance date -> net, gross, fund, cost, lev.

    A period runs open[i] -> open[i+step]; a symbol with no bar at the period end is closed at its last close
    in the period and pays `cost` on the closing notional. Funding days are [i, end) (or through the last bar).
    """
    first = D.pos(pd.Timestamp(start))
    first += (-D.dates[first].weekday()) % 7  # first Monday on/after start
    last_row = min(D.pos(pd.Timestamp(end)) + 1, len(D.dates) - 1)
    rows, prev = [], np.zeros(len(D.syms))
    fx = None if fixed is None else np.array([fixed.get(s, 0.0) for s in D.syms])
    for i in range(first, last_row - step + 1, step):
        nd = i + step
        w = fx.copy() if fx is not None else target_weights(D, i, kind, topn, allowed)
        if not w.any() and not prev.any():
            continue
        held = np.flatnonzero(w)
        r, fund, closed = np.zeros(len(w)), np.zeros(len(w)), np.zeros(len(w), bool)
        for j in held:
            if np.isnan(D.O[nd, j]):  # stopped trading: out at the last close inside the period
                k = i + np.flatnonzero(~np.isnan(D.C[i:nd, j]))[-1]
                r[j], fund[j], closed[j] = D.C[k, j] / D.O[i, j] - 1, D.F[i: k + 1, j].sum(), True
            else:
                r[j], fund[j] = D.O[nd, j] / D.O[i, j] - 1, D.F[i:nd, j].sum()
        c = cost * np.abs(w - prev).sum() + cost * (np.abs(w) * (1 + r))[closed].sum()
        gross, fnd = float(w @ r), float(w @ fund)
        net = gross - fnd - c
        prev = np.where(closed, 0.0, w * (1 + r) / (1 + net))
        rows.append((D.dates[i], net, gross, fnd, c, np.abs(w).sum()))
    return pd.DataFrame(rows, columns=["date", "net", "gross", "fund", "cost", "lev"]).set_index("date")


def weekly(df: pd.DataFrame, step: int) -> pd.Series:
    """Compound sub-weekly periods into Monday-anchored weeks; incomplete weeks are dropped."""
    wk = (df.index - df.index[0]).days // 7
    g = (1 + df["net"]).groupby(wk)
    out = (g.prod() - 1)[g.size() == 7 // step]
    out.index = df.index[np.unique(wk, return_index=True)[1]][np.isin(np.unique(wk), out.index)]
    return out


# ---------------------------------------------------------------- statistics
def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std(ddof=1) * np.sqrt(52)) if len(r) > 2 and r.std(ddof=1) > 0 else float("nan")


def max_dd(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((1 - eq / eq.cummax()).max())


def boot_ci(r: np.ndarray, seed: int = SEED) -> tuple[float, float]:
    """Moving-block (overlapping, 4-week) bootstrap 95% CI of the mean."""
    n = len(r)
    if n < 2 * BLOCK:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    nb = -(-n // BLOCK)
    starts = rng.integers(0, n - BLOCK + 1, size=(BOOT, nb))
    idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(BOOT, -1)[:, :n]
    lo, hi = np.percentile(r[idx].mean(1), [2.5, 97.5])
    return float(lo), float(hi)


def ols_alpha(y: pd.Series, x: pd.Series) -> tuple[float, float, float]:
    """Intercept, its t-stat, slope of y on x (aligned on index)."""
    d = pd.concat([y, x], axis=1, join="inner").dropna()
    Y, X = d.iloc[:, 0].to_numpy(), np.column_stack([np.ones(len(d)), d.iloc[:, 1].to_numpy()])
    beta = np.linalg.lstsq(X, Y, rcond=None)[0]
    res = Y - X @ beta
    se = np.sqrt(res @ res / (len(d) - 2) * np.diag(np.linalg.inv(X.T @ X)))
    return float(beta[0]), float(beta[0] / se[0]), float(beta[1])


def summary(r: pd.Series) -> dict:
    return {"n": len(r), "mean_wk": float(r.mean()), "sharpe": sharpe(r), "maxdd": max_dd(r),
            "ann_ret": float((1 + r).prod() ** (52 / len(r)) - 1)}


# ---------------------------------------------------------------- run
def _md(rows: list[dict], cols: list[str]) -> str:
    f = lambda v: f"{v:.3f}" if isinstance(v, float) else str(v)
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                     + ["| " + " | ".join(f(r.get(c, "")) for c in cols) + " |" for r in rows])


def run(period: str) -> None:
    D = load_all()
    start, end = PERIODS[period]
    prim = backtest(D, start, end)
    bench_b = backtest(D, start, end, kind="long")["net"]
    btc = backtest(D, start, end, fixed={"BTCUSDT": 1.0}, cost=0.0)["net"]
    r = prim["net"]
    lo, hi = boot_ci(r.to_numpy())
    a, t, beta = ols_alpha(r, bench_b)
    single = {L: sharpe(backtest(D, start, end, kind=L)["net"]) for L in LOOKBACKS}
    ins_sharpe = sharpe(r if period == "insample" else backtest(D, *PERIODS["insample"])["net"])
    crit = {
        "1_holdout_sharpe>=0.5_and_ci_lo>0": (sharpe(r) >= PASS_SHARPE and lo > 0) if period == "holdout" else None,
        "2_holdout_alpha>0": (a > 0) if period == "holdout" else None,
        "3_insample_sharpe>0": bool(ins_sharpe > 0),
        "4_holdout_each_lookback_sharpe>0": all(v > 0 for v in single.values()) if period == "holdout" else None,
        "5_holdout_maxdd<=35%": (max_dd(r) <= PASS_MDD) if period == "holdout" else None,
    }
    if period == "holdout":
        verdict = "PASS" if all(crit.values()) else "FAIL"
    else:
        verdict = "FAIL (criterion 3)" if not crit["3_insample_sharpe>0"] else "PENDING (criteria 1,2,4,5 need holdout)"
    out = {
        "period": period, "window": [start, end], "weeks": len(r), "symbols_in_archive": len(D.syms),
        "primary": {**summary(r), "boot95_mean_wk": [lo, hi], "alpha_wk_vs_b": a, "alpha_t": t, "beta_vs_b": beta,
                    "avg_gross": float(prim["lev"].mean()), "cost_total": float(prim["cost"].sum()),
                    "funding_total": float(prim["fund"].sum()), "single_lookback_sharpe": single},
        "benchmarks": {"a_btc_buy_hold": summary(btc), "b_long_only_voltarget": summary(bench_b)},
        "criteria": crit, "insample_sharpe": ins_sharpe, "verdict": verdict,
    }
    ex = {
        "long-only (max(s,0))": backtest(D, start, end, kind="longonly")["net"],
        "daily rebalance": weekly(backtest(D, start, end, step=1), 1),
        "cost x2": backtest(D, start, end, cost=2 * COST_SIDE)["net"],
        "top 10": backtest(D, start, end, topn=10)["net"],
        "top 30": backtest(D, start, end, topn=30)["net"],
        "BTC+ETH only": backtest(D, start, end, allowed={"BTCUSDT", "ETHUSDT"}, topn=2)["net"],
        **{f"single L={L}": backtest(D, start, end, kind=L)["net"] for L in LOOKBACKS},
    }
    out["exploratory_cells"] = {k: summary(v) for k, v in ex.items()}
    out["exploratory_primary_per_year_sharpe"] = {int(y): sharpe(g) for y, g in r.groupby(r.index.year)}
    print(json.dumps(out, indent=2, default=float))
    print(f"\n## TSMOM {period}: primary cell (ensemble, weekly, L/S, 20% vol, costs+funding)\n")
    cols = ["n", "mean_wk", "sharpe", "maxdd", "ann_ret"]
    print(_md([{"cell": "primary", **out["primary"]}, {"cell": "(a) BTC buy&hold", **out["benchmarks"]["a_btc_buy_hold"]},
               {"cell": "(b) long-only vol-target", **out["benchmarks"]["b_long_only_voltarget"]}], ["cell"] + cols))
    print(f"\nbootstrap 95% CI of mean weekly net: [{lo:.5f}, {hi:.5f}]; alpha vs (b): {a:.5f}/wk (t={t:.2f}), beta={beta:.2f}")
    print("\n### Frozen pass criteria\n")
    print(_md([{"criterion": k, "result": {True: "pass", False: "FAIL", None: "n/a (holdout run)"}[v]} for k, v in crit.items()],
              ["criterion", "result"]))
    print(f"\n**VERDICT: {verdict}**\n\n### EXPLORATORY (cannot be cited as a pass)\n")
    print(_md([{"cell": k, **v} for k, v in out["exploratory_cells"].items()], ["cell"] + cols))
    print("\nper-year primary Sharpe (exploratory):", {k: round(v, 2) for k, v in out["exploratory_primary_per_year_sharpe"].items()})


# ---------------------------------------------------------------- selfcheck
def _synthetic(n_days: int = 330, seed: int = 7, **ends: int) -> tuple[Data, int]:
    """Rising noisy prices for A,B. ends={sym: last row index with a bar}. Returns Data and Monday row t=2020-08-03."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n_days, freq="D")
    t = int(idx.get_loc(pd.Timestamp("2020-08-03")))
    assert idx[t].weekday() == 0
    frames = {}
    for s in ("AUSDT", "BUSDT"):
        c = 100 * np.exp(np.cumsum(0.01 + 0.03 * rng.standard_normal(n_days)))
        o = np.r_[c[0], c[:-1]]
        k = pd.DataFrame({"open": o, "close": c, "quote_volume": 1e6 + rng.random(n_days)}, index=idx)
        frames[s] = k.iloc[: ends.get(s, n_days - 1) + 1]
    return Data(frames, {}, "2020-01-01", str(idx[-1].date())), t


def _flat_from(D: Data, t: int) -> None:
    """Prices constant from Monday t on (open == close), so gross P&L is exactly zero."""
    D.O[t:], D.C[t:] = D.C[t - 1], D.C[t - 1]


def selfcheck() -> None:
    # (a) no look-ahead: weights at Monday t ignore every bar on/after t except O[t]; fills are at O[t]/O[t+7]
    D, t = _synthetic()
    w0 = target_weights(D, t, "ens")
    assert w0.any()
    D2, _ = _synthetic()
    rng = np.random.default_rng(1)
    D2.C[t:] = rng.uniform(1, 1e4, D2.C[t:].shape)
    D2.Q[t:] = rng.uniform(1, 1e9, D2.Q[t:].shape)
    np.testing.assert_array_equal(target_weights(D2, t, "ens"), w0)
    D2.C[t - 1] *= 0.5  # Sunday close does matter
    assert not np.array_equal(target_weights(D2, t, "ens"), w0)
    D3, _ = _synthetic()
    D3.O[t], D3.O[t + 7] = 100.0, 110.0
    D3.C[t:t + 7] = 5.0  # Monday..Sunday closes are irrelevant to the fill
    D3.F[:] = 0
    b = backtest(D3, "2020-08-03", "2020-08-10", step=7, cost=0.0)
    w = target_weights(D3, t, "ens")
    assert len(b) == 1 and abs(b["net"].iloc[0] - w.sum() * 0.10) < 1e-12, (b, w)
    # (b) delisted symbol: A's last bar is t+3; closed at that close, taker cost on the closing notional
    D, t = _synthetic(AUSDT=t + 3)
    w = target_weights(D, t, "ens")
    a = D.syms.index("AUSDT")
    assert w[a] != 0
    b = backtest(D, "2020-08-03", "2020-08-10", step=7)
    r_a = D.C[t + 3, a] / D.O[t, a] - 1
    r_b = D.O[t + 7, 1] / D.O[t, 1] - 1
    assert abs(b["gross"].iloc[0] - (w[a] * r_a + w[1] * r_b)) < 1e-12
    assert abs(b["cost"].iloc[0] - COST_SIDE * (np.abs(w).sum() + abs(w[a]) * (1 + r_a))) < 1e-12
    # (c) funding sign/size: long, flat price, +0.01% funding on a held day -> loses exactly w * 0.0001
    D, t = _synthetic()
    _flat_from(D, t)
    w = target_weights(D, t, "ens")
    assert (w >= 0).all() and w.any()
    base = backtest(D, "2020-08-03", "2020-08-10", step=7)["net"].iloc[0]
    D.F[t + 2] = 0.0001
    got = backtest(D, "2020-08-03", "2020-08-10", step=7)["net"].iloc[0]
    assert abs((base - got) - w.sum() * 0.0001) < 1e-14, (base, got)
    # (d) cost: entering gross notional X from flat costs 0.001 * X (price flat, no funding)
    assert abs(base + COST_SIDE * np.abs(w).sum()) < 1e-14, (base, w)
    # (e) bootstrap determinism under the seed
    x = np.random.default_rng(3).standard_normal(60) / 50
    assert boot_ci(x) == boot_ci(x) and boot_ci(x) != boot_ci(x, seed=SEED + 1)
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
