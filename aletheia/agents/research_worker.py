"""Research Worker — background execution of Serenity Research Runs.

Multi-stage pipeline with partial persistence:
  1. load_context — profile, market state, existing themes
  2. collect_evidence — LLM evidence collection → persist immediately
  3. extract_claims — LLM claim extraction → persist immediately
  4. cluster_themes — LLM theme clustering → persist themes + links
  5. score_themes — score themes (confidence, novelty, actionability)
  6. generate_opportunities — optional opportunity candidates
  7. persist_summary — finalize artifact counts
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    SerenityRunEvent, SerenityEvidence, SerenityTheme, SerenityClaim,
    SerenityOpportunity,
)
from aletheia.trading.serenity_evidence import (
    assess_evidence_tier, assess_opportunity_tier, assess_researchability, normalize_tier,
)

logger = logging.getLogger("aletheia.research_worker")

_EXECUTOR_LOCK = threading.Lock()

# Crypto-only allowlist: the quote currency must be one of these for a serenity
# opportunity to be persisted.  Blocks LLM hallucinations like MSTR/USD, SPY,
# GBP/USD, ALTCOINS etc. from reaching the trading decision chain.
_ALLOWED_QUOTE_CURRENCIES = frozenset({"USDT", "USDC", "BTC", "ETH", "BUSD"})


def _is_tradeable_crypto_symbol(symbol: str) -> bool:
    """Return True only for canonical crypto pairs with an allowed quote currency."""
    if not symbol or "/" not in symbol:
        return False
    parts = symbol.split("/")
    if len(parts) != 2:
        return False
    base, quote = parts[0].strip(), parts[1].strip()
    # quote must be an allowed crypto currency
    if quote not in _ALLOWED_QUOTE_CURRENCIES:
        return False
    # base must look like a crypto ticker (letters/digits, 2-10 chars)
    if not base or not base.isalnum() or len(base) > 10:
        return False
    return True


def _get_recent_news_for_research() -> str:
    """Fetch recent high-relevance analyzed news for injection into research context."""
    try:
        from aletheia.storage.sqlite import get_db
        import json as _json
        db = get_db()
        rows = db.execute(
            "SELECT title, source, impact_summary, sentiment, symbols_mentioned "
            "FROM news_items WHERE status = 'analyzed' AND relevance_score >= 0.6 "
            "AND fetched_at >= datetime('now', '-24 hours') "
            "ORDER BY relevance_score DESC LIMIT 20"
        ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            syms = ""
            try:
                s = _json.loads(r["symbols_mentioned"] or "[]")
                if s:
                    syms = f" [{', '.join(s)}]"
            except (ValueError, TypeError):
                pass
            impact = r["impact_summary"] or ""
            lines.append(f"- [{r['source']}] {r['title']} — {impact} ({r['sentiment']}{syms})")
        return "\n\nRecent News Headlines (last 24h, auto-analyzed):\n" + "\n".join(lines)
    except Exception as e:
        logger.debug("news injection into research context failed: %s", e)
        return ""


def _get_equity_context_block() -> str:
    """Build a short equity context block for injection into the research prompt.

    Reads the configured stock tokens and recently observed equity opportunities
    to give Serenity lenses awareness of the observed US equity universe.
    Returns an empty string when no equities are configured (zero-overhead).
    """
    try:
        from aletheia.config.loader import load_config
        from aletheia.data.symbols import stock_token_to_ticker, is_stock_token

        cfg = load_config()
        tokens = list(cfg.market_data.observed_stock_tokens or [])
        tokens += list(cfg.market_data.observed_xstocks or [])

        tickers = sorted({
            stock_token_to_ticker(t)
            for t in tokens
            if is_stock_token(t) and stock_token_to_ticker(t)
        })
        if not tickers:
            return ""

        lines = [
            "\n\nObserved US equity universe (exchange stock tokens — observation only, not traded):",
        ]
        lines.append(f"Tickers: {', '.join(tickers)}")
        lines.append(
            "These are available as r-series tokens (rNVDA/USDT, rTSLA/USDT, etc.) "
            "and/or ON-series tokens (NVDAON/USDT) on Bitget. "
            "Focus supply-chain and thematic research on these names and their ecosystems."
        )
        return "\n".join(lines)
    except Exception:
        return ""


# --- Stage prompts ---

_EVIDENCE_SYSTEM = (
    "You are a research evidence collector for a trading research system. "
    "Given the research context, identify relevant evidence from your knowledge. "
    "Return ONLY a JSON array of evidence objects. Each object must have: "
    '"title" (string), "source" (string: the source type like "market_data", "news", "research"), '
    '"source_ref" (string: specific reference), "summary" (string: 1-2 sentences), '
    '"symbol" (string or null: related asset symbol like "BTC/USDT"), '
    '"tier" (string, your PROPOSED evidence grade: one of "disclosed", "high_confidence", "likely", "speculative" '
    '— "disclosed" only for officially disclosed/filed facts with a checkable external URL/reference; '
    'an independent evaluator re-grades this, so propose honestly), '
    '"confidence" (string: same scale as tier), "supports" (boolean: supports thesis). '
    "Return 5-15 evidence items. Return raw JSON array, no markdown."
)

# S1 (2026-06-28, AI Berkshire 4-lens integration): four research perspectives
# that reduce mono-directional bias. Each lens is a specialized researcher with
# a distinct information diet and mandate.

_LENS_TECHNICAL_SYSTEM = (
    "You are a TECHNICAL ANALYSIS researcher for a crypto trading system. "
    "Your mandate: analyze PRICE ACTION and CHART PATTERNS only. "
    "Focus on: trend direction (MA alignment), support/resistance levels, "
    "momentum indicators (RSI/MACD), volume profile, chart patterns (breakout/reversal), "
    "and whether price is above/below key moving averages. "
    "Return ONLY a JSON array of evidence objects. Each must have: "
    '"title" (string), "source" (string: use "market_data" or "price_action"), '
    '"source_ref" (string: e.g. "BTC/USDT 4h chart, MA50 crossover"), '
    '"summary" (string: 1-2 sentences — SPECIFIC numbers or levels, not vague), '
    '"symbol" (string or null), '
    '"tier" (string: almost always "speculative" — TA heuristics are not disclosed facts), '
    '"confidence" (string: "speculative"), "supports" (boolean). '
    "Return 4-8 technical evidence items. Focus on actionable, specific observations. "
    "Return raw JSON array, no markdown."
)

_LENS_FLOW_SYSTEM = (
    "You are a MARKET FLOW researcher for a crypto trading system. "
    "Your mandate: analyze DERIVATIVES and CAPITAL FLOW signals only. "
    "Focus on: funding rates (positive = leveraged long crowding, negative = short crowding), "
    "open interest changes (increasing OI = conviction, decreasing = deleverage), "
    "liquidation clusters, long/short ratio, spot vs futures premium, "
    "large wallet movements, exchange inflows/outflows. "
    "Return ONLY a JSON array of evidence objects. Each must have: "
    '"title" (string), "source" (string: use "derivatives_data" or "on_chain"), '
    '"source_ref" (string: e.g. "BTC perpetual funding rate, Binance"), '
    '"summary" (string: 1-2 sentences — SPECIFIC signals, not generic descriptions), '
    '"symbol" (string or null), '
    '"tier" (string: "likely" if the signal is observable, "speculative" if inferred), '
    '"confidence" (string: same scale as tier), "supports" (boolean). '
    "Return 4-8 flow evidence items. Return raw JSON array, no markdown."
)

_LENS_SENTIMENT_SYSTEM = (
    "You are a MARKET SENTIMENT researcher for a crypto trading system. "
    "Your mandate: analyze SENTIMENT, NEWS, and NARRATIVE signals only. "
    "Focus on: recent news catalysts (regulatory, technical, macro), "
    "social media sentiment trends, Fear & Greed Index, "
    "narrative shifts (risk-on vs risk-off), "
    "institutional announcements, and any events in the last 7-14 days. "
    "Return ONLY a JSON array of evidence objects. Each must have: "
    '"title" (string), "source" (string: use "news" or "social" or "macro"), '
    '"source_ref" (string: e.g. "Reuters, 2026-06-25 — BTC ETF news"), '
    '"summary" (string: 1-2 sentences — WHO did WHAT and WHEN, not vague sentiment), '
    '"symbol" (string or null), '
    '"tier" (string: "likely" for verifiable news events, "speculative" for sentiment inference), '
    '"confidence" (string: same scale as tier), "supports" (boolean). '
    "Return 4-8 sentiment evidence items. Return raw JSON array, no markdown."
)

_LENS_MACRO_SYSTEM = (
    "You are a MACRO CONTEXT researcher for a crypto trading system. "
    "Your mandate: analyze MACRO and CYCLE POSITION signals only. "
    "Focus on: BTC halving cycle position and historical analogues, "
    "US dollar strength (DXY trend), Fed policy and rate expectations, "
    "global risk appetite (S&P 500 / VIX direction), "
    "crypto market cycle stage (accumulation/markup/distribution/markdown), "
    "and correlation of crypto-equity proxies (COIN, MSTR, HOOD) to BTC. "
    "Return ONLY a JSON array of evidence objects. Each must have: "
    '"title" (string), "source" (string: use "macro" or "research"), '
    '"source_ref" (string: e.g. "BTC halving cycle analysis, 2024 analogue"), '
    '"summary" (string: 1-2 sentences — SPECIFIC cycle position or macro signal), '
    '"symbol" (string or null), '
    '"tier" (string: "likely" for established historical patterns, "speculative" for projections), '
    '"confidence" (string: same scale as tier), "supports" (boolean). '
    "Return 4-8 macro evidence items. Return raw JSON array, no markdown."
)

_LENS_EQUITY_CHAIN_SYSTEM = (
    "You are an EQUITY SUPPLY-CHAIN RESEARCHER for a trading research system. "
    "Your mandate: analyse US-listed companies accessible via exchange-traded stock tokens "
    "(Bitget r-series: rNVDA, rTSLA, rAAPL, rAMD, rMSFT, etc.) with a focus on "
    "industrial supply-chain dynamics, competitive positioning, and near-term catalysts. "
    "Think like an investor looking for HIDDEN ALPHA in the value chain: "
    "  • Upstream suppliers that will benefit BEFORE the headline name does "
    "  • Downstream customers whose order flow signals demand acceleration or contraction "
    "  • Adjacent competitors/substitutes gaining or losing share "
    "  • Capital-expenditure announcements that ripple through equipment/materials suppliers "
    "  • Regulatory / patent / geopolitical factors that shift competitive advantage "
    "Focus especially on SMALL and MID-CAP names available as r-series tokens — these often "
    "have less coverage and more mispricing. Provide concrete, actionable signals with "
    "supporting reasoning; avoid generic macro commentary already covered by the macro lens. "
    "Return ONLY a JSON array of evidence objects. Each must have: "
    '"title" (string: company/theme name + key finding), '
    '"source" (string: use "research", "news", or "market_data"), '
    '"source_ref" (string: e.g. "NVDA Q1 2026 earnings, supply-chain channel checks"), '
    '"summary" (string: 2-3 sentences — WHO, WHAT signal, and WHY it matters for price), '
    '"symbol" (string or null: the stock token canonical symbol, e.g. "rNVDA/USDT" or '
    '"rPLD/USDT"; prefer the r-series form; null if the insight is theme-level), '
    '"tier" (string: "likely" for analyst/news-backed claims; "speculative" for inferred; '
    '"high_confidence" only if there is an external verifiable URL/filing you can cite), '
    '"confidence" (string: same scale as tier), "supports" (boolean). '
    "Return 5-12 evidence items covering a diverse set of companies and themes. "
    "Return raw JSON array, no markdown."
)

# Registry of all standard research lenses. Order matters: technical first for
# price anchoring, then flow (derivatives context), sentiment (narrative), macro (cycle).
# equity_chain lens runs last (longest context, most LLM budget).
# Lenses are run sequentially to stay within Databricks token-per-minute rate limits.
# equity_chain is appended only when equity tickers are configured (see _run_pipeline_inner).
RESEARCH_LENSES = [
    ("technical", _LENS_TECHNICAL_SYSTEM),
    ("flow", _LENS_FLOW_SYSTEM),
    ("sentiment", _LENS_SENTIMENT_SYSTEM),
    ("macro", _LENS_MACRO_SYSTEM),
]

# The equity_chain lens is added dynamically when observed_equities/observed_stock_tokens
# are non-empty. Kept separate so standard crypto-only runs are unaffected.
_EQUITY_CHAIN_LENS = ("equity_chain", _LENS_EQUITY_CHAIN_SYSTEM)

_CLAIMS_SYSTEM = (
    "You are a structured claim extractor. Given evidence summaries, extract atomic claims. "
    "Return ONLY a JSON array of claim objects. Each claim must have: "
    '"claim_text" (string: one clear statement), "subject" (string), "predicate" (string: action/state), '
    '"object" (string), "confidence" (float 0-1), "price_relevant" (boolean), '
    '"support_level" (string: "strong", "medium", "weak", "unsupported"). '
    "Extract 5-20 claims. Return raw JSON array, no markdown."
)

_THEMES_SYSTEM = (
    "You are a research theme clusterer. Given structured claims, group them into investment themes. "
    "Return ONLY a JSON array of theme objects. Each theme must have: "
    '"name" (string: short theme title), "thesis" (string: 1-2 sentence thesis), '
    '"description" (string: longer explanation), "confidence" (float 0-1), '
    '"novelty_score" (float 0-1), "actionability_score" (float 0-1), '
    '"evidence_strength" (float 0-1), "related_assets" (array of symbol strings), '
    '"related_topics" (array of topic strings), "market_types" (array: "crypto", "equity", etc.), '
    '"claim_indices" (array of integers: indices into the claims list that support this theme). '
    "Return 1-5 themes. Return raw JSON array, no markdown."
)

_OPPORTUNITIES_SYSTEM = (
    "You are an opportunity identifier for a crypto derivatives trading system. "
    "Given research themes with evidence, identify potential opportunities. "
    "CRITICAL: only output symbols that are actual crypto perpetual/spot pairs tradeable on OKX or Bitget, "
    "such as BTC/USDT, ETH/USDT, SOL/USDT. "
    "NEVER output stock tickers (MSTR, SPY, GBTC), forex pairs (GBP/USD), "
    "macro aggregates (ALTCOINS, STABLECOIN_SUPPLY_AGGREGATE), or any non-USDT quoted crypto pairs. "
    "Return ONLY a JSON array of opportunity objects. Each must have: "
    '"symbol" (string: canonical crypto format, quote must be USDT — e.g. "BTC/USDT"), '
    '"direction" (string: "long" or "short"), '
    '"thesis" (string: why this is an opportunity), '
    '"evidence_tier" (string, your PROPOSED grade: one of "disclosed", "high_confidence", "likely", "speculative" '
    '— an independent evaluator assigns the authoritative tier from the supporting evidence), '
    '"confidence_score" (integer 0-100), "risk_flags" (array of strings), '
    '"verdict" (object, REQUIRED — MUST be non-null, no hedging allowed): '
    '{ "recommendation": one of "long"|"short"|"watch"|"avoid" (REQUIRED, pick one — no "depends"), '
    '"conviction": integer 0-100 (REQUIRED), '
    '"bull_case": string — specific supporting argument (REQUIRED, ≥1 sentence), '
    '"bear_case": string — specific counter-argument (REQUIRED, ≥1 sentence, do NOT leave empty even if bullish), '
    '"key_assumption": string — the ONE most critical premise this opportunity depends on (REQUIRED), '
    '"invalidation": string — exact condition that would invalidate the key_assumption (REQUIRED), '
    '"price_target": number or null }. '
    "Return 0-5 opportunities. Return empty array if no clear opportunities. Return raw JSON array, no markdown."
)

_ICT_METHODOLOGY_SYSTEM = (
    "You are a trading methodology analyst specializing in ICT (Inner Circle Trader) and SMC (Smart Money Concepts) "
    "for crypto futures markets. Your job is to produce ACTIONABLE trading methodology knowledge. "
    "Return ONLY a JSON array of evidence objects. Each object must have: "
    '"title" (string: concept name like "Order Block Entry"), '
    '"source" (string: "ict_methodology" or "smc_methodology" or "price_action"), '
    '"source_ref" (string: specific concept, e.g. "ICT Order Block / BOS"), '
    '"summary" (string: specific entry criteria, invalidation conditions, optimal timeframe, and R:R expectation), '
    '"symbol" (string or null: most applicable asset, e.g. "BTC/USDT"), '
    '"tier" (string: "speculative" — methodology heuristics are not disclosed facts), '
    '"confidence" (string: "speculative"), "supports" (boolean: true). '
    "Cover these concepts: order blocks, fair value gaps (FVG), liquidity sweeps, market structure shifts (MSS/BOS/CHoCH), "
    "breaker blocks, power of 3 (accumulation/manipulation/distribution), optimal trade entry (OTE), "
    "equal highs/lows as liquidity targets, and displacement/imbalance. "
    "For each concept include: setup condition, entry trigger, invalidation level, preferred timeframe (15m/1h/4h), "
    "and typical risk/reward ratio. Return 8-12 evidence items. Return raw JSON array, no markdown."
)


def enqueue_research_run(run_id: str) -> None:
    """Enqueue a research run for background execution."""
    def _execute():
        try:
            asyncio.run(_process_research_run(run_id))
        except Exception as e:
            logger.error("Research run %s crashed: %s", run_id, e)
            try:
                _mark_failed(run_id, "unknown_error", str(e), None)
            except Exception:
                pass

    t = threading.Thread(target=_execute, name=f"research-run-{run_id}", daemon=True)
    t.start()


async def _process_research_run(run_id: str) -> None:
    """Main execution pipeline for a research run."""
    run = repo.get_serenity_research_run(run_id)
    if not run:
        logger.error("Research run %s not found", run_id)
        return

    if run["status"] != "queued":
        logger.warning("Research run %s not in queued state (is %s), skipping", run_id, run["status"])
        return

    now = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_research_run(run_id, status="running", started_at=now, updated_at=now)
    _emit_event(run_id, "run.started", message="Research run started")

    try:
        await _run_pipeline(run_id, run)
    except _CancelledError:
        logger.info("Research run %s cancelled", run_id)
    except _RateLimitError as e:
        _handle_rate_limit(run_id, run, str(e))
    except Exception as e:
        logger.error("Research run %s failed at stage: %s", run_id, e)
        run_refreshed = repo.get_serenity_research_run(run_id)
        stage = (run_refreshed or run).get("stage")
        _mark_failed(run_id, _classify_error(e), str(e)[:500], stage)


async def _run_pipeline(run_id: str, run: dict) -> None:
    """Execute the research pipeline stages with lock."""
    lock_key = "serenity:research:standard"
    if not repo.acquire_serenity_lock(lock_key, run_id, ttl_seconds=1800):
        _mark_failed(run_id, "lock_unavailable", "Another research run holds the lock", None)
        return

    try:
        await _run_pipeline_inner(run_id, run)
    finally:
        repo.release_serenity_lock(lock_key, run_id)


async def _run_pipeline_inner(run_id: str, run: dict) -> None:
    """Multi-stage research pipeline with partial persistence."""
    from aletheia.agents.helpers import llm_complete

    # Build context from profile/brief
    brief = run.get("brief_snapshot") or ""
    profile_context = ""
    if run.get("profile_snapshot_id"):
        snap = repo.get_serenity_profile_snapshot(run["profile_snapshot_id"])
        if snap:
            profile_context = (
                f"Include topics: {snap.get('include_topics', '[]')}\n"
                f"Exclude topics: {snap.get('exclude_topics', '[]')}\n"
                f"Time horizon: {snap.get('time_horizon') or 'unspecified'}\n"
            )

    existing_themes = repo.list_serenity_themes(status="active", limit=20)
    existing_context = ""
    if existing_themes:
        existing_context = "Existing active themes: " + ", ".join(
            t["name"] for t in existing_themes[:10]
        )

    news_block = _get_recent_news_for_research()

    # Inject equity context when stock tokens are configured, so all lenses are
    # aware of the observed universe and the equity_chain lens gets a rich brief.
    equity_block = _get_equity_context_block()
    has_equity_context = bool(equity_block)

    research_context = (
        f"Research profile:\n{profile_context}\n"
        f"Brief: {brief}\n"
        f"{existing_context}\n"
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        f"{news_block}"
        f"{equity_block}"
    )

    # Build active lens list: standard 4 lenses + equity_chain when equities present.
    active_lenses = list(RESEARCH_LENSES)
    if has_equity_context and run.get("mode") != "methodology":
        active_lenses.append(_EQUITY_CHAIN_LENS)

    # --- Stage 1: Collect Evidence (S1: multi-lens parallel research) ---
    _check_cancellation(run_id)
    _update_stage(run_id, "collect_evidence")
    _emit_event(run_id, "stage.started", stage="collect_evidence", message="Collecting evidence")

    # ICT methodology mode: single specialized prompt, no multi-lens.
    # Standard mode: iterate over lenses sequentially (rate-limit safe).
    persisted_evidence_ids: list[str] = []
    all_evidence_items: list[dict] = []  # all items across all lenses (for Stage 2)
    lens_summaries: dict[str, int] = {}  # lens → count (for synthesis event)

    if run.get("mode") == "methodology":
        evidence_text, err = await llm_complete(
            system=_ICT_METHODOLOGY_SYSTEM,
            user=f"Research context:\n{research_context}\n\nCollect relevant evidence.",
            max_tokens=16384,
        )
        if err:
            if "429" in err or "rate" in err.lower():
                raise _RateLimitError(err)
            raise Exception(f"Evidence collection failed: {err}")
        evidence_items = _parse_json_array(evidence_text, "evidence")
        all_evidence_items = evidence_items
        lens_summaries["methodology"] = len(evidence_items)
    else:
        # S1: sequential lens research — technical, flow, sentiment, macro, and optionally
        # equity_chain. Sequential (not asyncio.gather) to stay within rate limits.
        for lens_name, lens_system in active_lenses:
            _check_cancellation(run_id)
            lens_text, lens_err = await llm_complete(
                system=lens_system,
                user=f"Research context:\n{research_context}\n\nCollect relevant evidence from your {lens_name} perspective.",
                max_tokens=8192,
            )
            if lens_err:
                if "429" in lens_err or "rate" in lens_err.lower():
                    raise _RateLimitError(lens_err)
                logger.warning("Lens %s evidence collection failed: %s", lens_name, lens_err)
                lens_summaries[lens_name] = 0
                continue
            lens_items = _parse_json_array(lens_text, f"evidence[{lens_name}]")
            for item in lens_items:
                item["_lens"] = lens_name  # tag for persistence
            all_evidence_items.extend(lens_items)
            lens_summaries[lens_name] = len(lens_items)
            logger.debug("Lens %s: %d evidence items", lens_name, len(lens_items))
        evidence_items = all_evidence_items  # unified list for Stage 2 compatibility

    theme_id_default = run.get("focus_theme_id") or (existing_themes[0]["id"] if existing_themes else "")
    for item in all_evidence_items:
        ev = SerenityEvidence(
            theme_id=theme_id_default,
            run_id=run_id,
            symbol=item.get("symbol"),
            source=item.get("source", "research"),
            source_ref=item.get("source_ref", ""),
            title=item.get("title", "Untitled evidence"),
            summary=item.get("summary", ""),
            # `tier` is the generator's self-proposal (normalized into the canonical ladder).
            # The independent evaluator assigns the authoritative `assessed_tier` that gates scoring.
            tier=normalize_tier(item.get("tier")),
            supports=item.get("supports", True),
            confidence=normalize_tier(item.get("confidence")),
            # S1: which lens produced this evidence record.
            agent_lens=item.get("_lens"),
        )
        ev.assessed_tier, ev.assessed_by = assess_evidence_tier(ev.model_dump())
        try:
            repo.insert_serenity_evidence(ev)
            persisted_evidence_ids.append(ev.id)
        except Exception as e:
            logger.warning("Failed to persist evidence: %s", e)

    # S1: emit lens synthesis event summarising cross-lens coverage.
    _emit_event(
        run_id, "lens_synthesis", stage="collect_evidence",
        message=f"Multi-lens collection complete: {lens_summaries} total={len(persisted_evidence_ids)}",
    )

    _emit_event(run_id, "stage.completed", stage="collect_evidence",
                message=f"Collected {len(persisted_evidence_ids)} evidence items")

    # --- Stage 2: Extract Claims ---
    _check_cancellation(run_id)
    _update_stage(run_id, "extract_claims")
    _emit_event(run_id, "stage.started", stage="extract_claims", message="Extracting claims")

    evidence_summary = "\n".join(
        f"- [{item.get('tier', '?')}] {item.get('title', '')}: {item.get('summary', '')}"
        for item in evidence_items[:15]
    )

    claims_text, err = await llm_complete(
        system=_CLAIMS_SYSTEM,
        user=f"Evidence:\n{evidence_summary}\n\nExtract structured claims.",
        max_tokens=8192,
    )
    if err:
        if "429" in err or "rate" in err.lower():
            raise _RateLimitError(err)
        raise Exception(f"Claim extraction failed: {err}")

    claim_items = _parse_json_array(claims_text, "claims")
    persisted_claims: list[SerenityClaim] = []
    for item in claim_items:
        claim = SerenityClaim(
            run_id=run_id,
            claim_text=item.get("claim_text", ""),
            subject=item.get("subject"),
            predicate=item.get("predicate"),
            object=item.get("object"),
            confidence=float(item.get("confidence", 0.5)),
            price_relevant=bool(item.get("price_relevant", False)),
            support_level=item.get("support_level"),
        )
        if claim.claim_text:
            try:
                repo.insert_serenity_claim(claim)
                persisted_claims.append(claim)
            except Exception as e:
                logger.warning("Failed to persist claim: %s", e)

    _emit_event(run_id, "stage.completed", stage="extract_claims",
                message=f"Extracted {len(persisted_claims)} claims")

    # --- Stage 3: Cluster Themes ---
    _check_cancellation(run_id)
    _update_stage(run_id, "cluster_themes")
    _emit_event(run_id, "stage.started", stage="cluster_themes", message="Clustering into themes")

    claims_for_prompt = "\n".join(
        f"[{i}] {c.claim_text} (confidence={c.confidence}, price_relevant={c.price_relevant})"
        for i, c in enumerate(persisted_claims[:20])
    )

    themes_text, err = await llm_complete(
        system=_THEMES_SYSTEM,
        user=f"Claims:\n{claims_for_prompt}\n\nCluster into investment research themes.",
        max_tokens=8192,
    )
    if err:
        if "429" in err or "rate" in err.lower():
            raise _RateLimitError(err)
        raise Exception(f"Theme clustering failed: {err}")

    theme_items = _parse_json_array(themes_text, "themes")
    persisted_themes: list[str] = []
    for item in theme_items:
        theme = SerenityTheme(
            name=item.get("name", "Unnamed Theme"),
            run_id=run_id,
            thesis=item.get("thesis"),
            description=item.get("description"),
            status="active",
            confidence=item.get("confidence"),
            novelty_score=item.get("novelty_score"),
            actionability_score=item.get("actionability_score"),
            evidence_strength=item.get("evidence_strength"),
            related_assets=json.dumps(item.get("related_assets", [])),
            related_topics=json.dumps(item.get("related_topics", [])),
            market_types=json.dumps(item.get("market_types", [])),
        )
        try:
            repo.insert_serenity_theme(theme)
            persisted_themes.append(theme.id)

            # Link claims to theme
            claim_indices = item.get("claim_indices", [])
            links = []
            for idx in claim_indices:
                if 0 <= idx < len(persisted_claims):
                    links.append((theme.id, persisted_claims[idx].id, None))
            if links:
                repo.insert_serenity_theme_claims_bulk(links)
        except Exception as e:
            logger.warning("Failed to persist theme: %s", e)

    _emit_event(run_id, "stage.completed", stage="cluster_themes",
                message=f"Created {len(persisted_themes)} themes")

    # --- Stage 4: Generate Opportunities (optional) ---
    _check_cancellation(run_id)
    _update_stage(run_id, "generate_opportunities")
    _emit_event(run_id, "stage.started", stage="generate_opportunities", message="Generating opportunities")

    persisted_opps: list[str] = []
    if persisted_themes and run.get("mode") != "quick_refresh":
        themes_for_prompt = "\n".join(
            f"- {item.get('name', '')}: {item.get('thesis', '')}"
            for item in theme_items[:5]
        )

        opps_text, err = await llm_complete(
            system=_OPPORTUNITIES_SYSTEM,
            user=f"Research themes:\n{themes_for_prompt}\n\nIdentify potential opportunities.",
            max_tokens=8192,
        )
        if not err:
            opp_items = _parse_json_array(opps_text, "opportunities")
            for i, item in enumerate(opp_items[:5]):
                raw_sym = (item.get("symbol") or "").strip()
                if not _is_tradeable_crypto_symbol(raw_sym):
                    logger.info(
                        "Skipping non-tradeable symbol from LLM: %r — only USDT-quoted crypto pairs allowed",
                        raw_sym,
                    )
                    continue
                theme_id = persisted_themes[min(i, len(persisted_themes) - 1)] if persisted_themes else None
                if theme_id:
                    # The opportunity's authoritative tier is derived from the strongest
                    # INDEPENDENTLY ASSESSED supporting evidence under this theme, not the
                    # LLM's self-graded `evidence_tier` (which is kept only as a proposal).
                    theme_evidence = repo.list_serenity_evidence(theme_id=theme_id, limit=200)
                    assessed_tier, assessed_by = assess_opportunity_tier({}, theme_evidence)
                    r_grade, _ = assess_researchability(theme_evidence)
                    # S4: extract and validate forced verdict from LLM output.
                    raw_verdict = item.get("verdict") or {}
                    if not isinstance(raw_verdict, dict):
                        raw_verdict = {}
                    verdict_obj = {
                        "recommendation": raw_verdict.get("recommendation") or "watch",
                        "conviction": int(raw_verdict.get("conviction") or 0),
                        "bull_case": str(raw_verdict.get("bull_case") or ""),
                        "bear_case": str(raw_verdict.get("bear_case") or ""),
                        "key_assumption": str(raw_verdict.get("key_assumption") or ""),
                        "invalidation": str(raw_verdict.get("invalidation") or ""),
                        "price_target": raw_verdict.get("price_target"),
                    }
                    opp = SerenityOpportunity(
                        theme_id=theme_id,
                        symbol=item.get("symbol", "BTC/USDT"),
                        direction=item.get("direction", "long"),
                        thesis=item.get("thesis"),
                        evidence_tier=normalize_tier(item.get("evidence_tier")),
                        assessed_tier=assessed_tier,
                        assessed_by=assessed_by,
                        confidence_score=item.get("confidence_score"),
                        risk_flags=json.dumps(item.get("risk_flags", [])),
                        verdict_json=json.dumps(verdict_obj),
                        researchability_grade=r_grade,
                    )
                    try:
                        repo.insert_serenity_opportunity(opp)
                        persisted_opps.append(opp.id)
                    except Exception as e:
                        logger.warning("Failed to persist opportunity: %s", e)

    _emit_event(run_id, "stage.completed", stage="generate_opportunities",
                message=f"Generated {len(persisted_opps)} opportunities")

    # --- Stage 5: Persist Summary ---
    _check_cancellation(run_id)
    _update_stage(run_id, "persist_summary")

    counts = {
        "themes": len(persisted_themes),
        "evidence": len(persisted_evidence_ids),
        "claims": len(persisted_claims),
        "opportunities": len(persisted_opps),
    }

    now = datetime.now(timezone.utc).isoformat()
    final_status = "completed" if counts["themes"] > 0 else "completed_empty"
    repo.update_serenity_research_run(
        run_id,
        status=final_status,
        stage=None,
        finished_at=now,
        updated_at=now,
        artifact_counts=json.dumps(counts),
    )
    _emit_event(run_id, f"run.{final_status}",
                message=f"Run {final_status}: {counts['themes']} themes, {counts['evidence']} evidence, "
                        f"{counts['claims']} claims, {counts['opportunities']} opportunities")


def _parse_json_array(text: str | None, label: str) -> list[dict]:
    """Parse LLM output as a JSON array, with fallback for markdown-wrapped and truncated output."""
    if not text:
        return []
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and label in result:
            return result[label]
        return []
    except json.JSONDecodeError:
        pass

    # Truncation repair: find last complete object in array and close it
    repaired = _repair_truncated_json_array(text)
    if repaired:
        logger.info("Repaired truncated %s JSON: %d items recovered", label, len(repaired))
        return repaired

    logger.warning("Failed to parse %s JSON (len=%d, tail=...%s)", label, len(text), text[-60:])
    return []


def _repair_truncated_json_array(text: str) -> list[dict] | None:
    """Attempt to recover items from a truncated JSON array."""
    if not text.startswith("["):
        return None
    # Find the last complete "}" that closes an object in the array
    last_close = text.rfind("}")
    if last_close < 1:
        return None
    # Try closing the array after the last complete object
    candidate = text[:last_close + 1] + "]"
    try:
        result = json.loads(candidate)
        if isinstance(result, list) and all(isinstance(x, dict) for x in result):
            return result
    except json.JSONDecodeError:
        pass
    # More aggressive: find last "},\n" or "}\n" pattern
    import re
    for m in re.finditer(r'\}\s*,?\s*$', text[:last_close + 1], re.MULTILINE):
        candidate = text[:m.end()].rstrip().rstrip(",") + "]"
        try:
            result = json.loads(candidate)
            if isinstance(result, list) and all(isinstance(x, dict) for x in result):
                return result
        except json.JSONDecodeError:
            continue
    return None


def _update_stage(run_id: str, stage: str) -> None:
    repo.update_serenity_research_run(run_id, stage=stage,
                                      updated_at=datetime.now(timezone.utc).isoformat())


def _emit_event(run_id: str, event_type: str, stage: str | None = None,
                message: str | None = None) -> None:
    try:
        repo.insert_serenity_run_event(SerenityRunEvent(
            run_id=run_id, event_type=event_type, stage=stage, message=message))
    except Exception as e:
        logger.warning("Failed to emit event for run %s: %s", run_id, e)


def _mark_failed(run_id: str, error_type: str, error_message: str, error_stage: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    repo.update_serenity_research_run(
        run_id,
        status="failed",
        error_type=error_type,
        error_message=error_message[:500] if error_message else None,
        error_stage=error_stage,
        finished_at=now,
        updated_at=now,
    )
    _emit_event(run_id, "run.failed", stage=error_stage,
                message=f"{error_type}: {error_message[:200] if error_message else ''}")


def _handle_rate_limit(run_id: str, run: dict, error_message: str) -> None:
    from datetime import timedelta
    import random

    now = datetime.now(timezone.utc)
    retry_count = (run.get("retry_count") or 0)
    max_retries = 3 if run.get("trigger_type") == "manual" else 2

    # Compute next retry with exponential backoff + jitter
    backoff_seconds = [60, 180, 600]
    next_retry_at = None
    if retry_count < max_retries:
        delay = backoff_seconds[min(retry_count, len(backoff_seconds) - 1)]
        jitter = random.randint(0, delay // 4)
        next_retry_at = (now + timedelta(seconds=delay + jitter)).isoformat()

    repo.update_serenity_research_run(
        run_id,
        status="rate_limited",
        error_type="provider_rate_limit",
        error_message=error_message[:500],
        error_stage=run.get("stage"),
        finished_at=now.isoformat(),
        updated_at=now.isoformat(),
        next_retry_at=next_retry_at,
    )
    _emit_event(run_id, "run.rate_limited",
                message=f"Rate limited: {error_message[:200]}"
                        + (f" (next retry at {next_retry_at})" if next_retry_at else " (retries exhausted)"))

    # Schedule automatic retry if retries remain
    if next_retry_at and retry_count < max_retries:
        _schedule_auto_retry(run_id, run, retry_count)


def _schedule_auto_retry(run_id: str, run: dict, retry_count: int) -> None:
    """Schedule an automatic retry via APScheduler one-shot job."""
    try:
        from aletheia.agent_runtime.scheduler import _scheduler
        if _scheduler and _scheduler.running:
            from datetime import timedelta
            import random
            backoff_seconds = [60, 180, 600]
            delay = backoff_seconds[min(retry_count, len(backoff_seconds) - 1)]
            jitter = random.randint(0, delay // 4)
            run_time = datetime.now(timezone.utc) + timedelta(seconds=delay + jitter)

            def _retry_job():
                from aletheia.storage.models import SerenityResearchRun
                new_run = SerenityResearchRun(
                    trigger_type=run.get("trigger_type", "manual"),
                    mode=run.get("mode", "standard"),
                    status="queued",
                    parent_run_id=run_id,
                    profile_snapshot_id=run.get("profile_snapshot_id"),
                    brief_snapshot=run.get("brief_snapshot"),
                    focus_theme_id=run.get("focus_theme_id"),
                    retry_count=retry_count + 1,
                )
                repo.insert_serenity_research_run(new_run)
                enqueue_research_run(new_run.id)

            _scheduler.add_job(
                _retry_job, "date", run_date=run_time,
                id=f"serenity_retry_{run_id}",
                misfire_grace_time=120,
                replace_existing=True,
            )
            logger.info("Scheduled auto-retry for run %s at %s", run_id, run_time.isoformat())
    except Exception as e:
        logger.warning("Failed to schedule auto-retry for run %s: %s", run_id, e)


def _classify_error(e: Exception) -> str:
    msg = str(e).lower()
    if "429" in msg or "rate" in msg:
        return "provider_rate_limit"
    if "timeout" in msg:
        return "provider_timeout"
    if "auth" in msg or "401" in msg or "403" in msg:
        return "provider_auth_error"
    if "network" in msg or "connection" in msg:
        return "network_error"
    if "json" in msg or "schema" in msg or "parse" in msg:
        return "schema_validation_error"
    return "unknown_error"


class _RateLimitError(Exception):
    pass


class _CancelledError(Exception):
    pass


def _should_cancel(run_id: str) -> bool:
    run = repo.get_serenity_research_run(run_id)
    return run is not None and run["status"] == "cancelled"


def _check_cancellation(run_id: str) -> None:
    if _should_cancel(run_id):
        raise _CancelledError("Run cancelled by user")
