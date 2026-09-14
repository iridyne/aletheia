"""Serenity source admission; Core owns Candidate persistence and planning."""

from datetime import datetime, timedelta, timezone
import json
import logging
import threading

from aletheia.storage import repositories as repo


def publish_serenity_candidates(
    theme_id: str, opportunity_ids: list[str], *,
    min_score_for_pi: float | None = None, new_candidates_only: bool = False,
) -> list[str]:
    """Publish research and real PA evidence atomically, never a legacy decision."""
    from aletheia.config.loader import load_config
    from aletheia.config.exchange_alignment import exchange_alignment
    from aletheia.storage.models import UniverseMember, UniverseSnapshot, new_id
    from aletheia.trading.serenity_scoring import score_serenity_opportunity
    from aletheia.universe.candidate_eligibility import evaluate_hard_gate
    from aletheia.universe.candidate_opportunity import fingerprint, serenity_candidate_opportunity_context
    from aletheia.universe.fast_stream import fast_market_data_unavailability_reason

    ordered_ids = list(dict.fromkeys(opportunity_ids))
    if not ordered_ids:
        raise ValueError("Serenity publication requires at least one opportunity")
    if repo.get_db().in_transaction:
        raise ValueError("serenity_preparation_inside_transaction")
    cfg = load_config(persist_migrations=False)
    alignment = exchange_alignment(cfg)
    venue = alignment.execution_exchange or alignment.data_exchange
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    def source_material(opportunity_id: str) -> dict:
        opportunity = repo.get_serenity_opportunity(opportunity_id)
        if opportunity is None or opportunity["theme_id"] != theme_id or opportunity["status"] != "active":
            raise ValueError("serenity_source_ineligible")
        evidence = repo._select_all("serenity_evidence", where="theme_id=?", params=(theme_id,), order="id", limit=None)
        gates = repo._select_all("serenity_risk_gates", where="opportunity_id=?", params=(opportunity_id,), order="id", limit=None)
        if not any(item["supports"] for item in evidence) or any(gate["status"] == "fail" for gate in gates):
            raise ValueError("serenity_source_evidence_unavailable")
        return {"opportunity": {key: value for key, value in opportunity.items()
                                if key not in {"updated_at", "action_intent"}},
                "evidence": evidence, "risk_gates": gates}

    prepared = []
    for opportunity_id in ordered_ids:
        material = source_material(opportunity_id)
        opportunity = material["opportunity"]
        score = score_serenity_opportunity(opportunity, evidence=material["evidence"], risk_gates=material["risk_gates"])["score"]
        if min_score_for_pi is not None and score < min_score_for_pi:
            continue
        source_hash = fingerprint(material)
        existing = repo.get_db().execute(
            "SELECT um.id FROM universe_members um JOIN candidate_planning_generations pg ON (pg.candidate_id=um.id OR pg.last_candidate_id=um.id) "
            "WHERE json_extract(um.candidate_opportunity_context_json,'$.source.kind')='serenity' "
            "AND json_extract(um.candidate_opportunity_context_json,'$.source.id')=? "
            "AND json_extract(um.candidate_opportunity_context_json,'$.candidate_selection.source_record_hash')=? "
            "ORDER BY um.rowid LIMIT 1", (opportunity_id, source_hash),
        ).fetchone()
        if existing:
            prepared.append((opportunity_id, source_hash, existing["id"], None, None))
            continue
        asset = repo.get_db().execute(
            "SELECT * FROM universe_registry WHERE venue=? AND symbol=? AND market_type=? AND status='active'",
            (venue, opportunity["symbol"], opportunity["market_type"]),
        ).fetchone()
        if asset is None:
            raise ValueError("serenity_exact_instrument_unavailable")
        feature = repo.get_latest_feature_snapshot(asset["id"])
        if feature is None:
            raise ValueError("serenity_pa_unavailable")
        candles = repo.get_ohlcv_candles(opportunity["symbol"], "1h", 1, venue, opportunity["market_type"], int(now.timestamp()*1000)-3_600_000+1)
        feature.update({"asset_id": asset["id"], "venue": venue, "symbol": asset["symbol"],
                        "market_type": asset["market_type"], "registry_status": asset["status"],
                        "feature_snapshot_id": feature["id"], "feature_snapshot_ts": feature["ts"],
                        "feature_source": "universe_feature_snapshot/v1",
                        "data_freshness_sec": (now - repo._normalized_utc_datetime(feature["ts"])).total_seconds(),
                        "last_price": candles[-1]["close"] if candles else None})
        candidate_id = new_id()
        gate = evaluate_hard_gate(feature, cfg.universe, candidate_key=candidate_id, evaluated_at=timestamp,
            action_route_reason=fast_market_data_unavailability_reason(cfg, venue, opportunity["market_type"]))
        if gate.status != "eligible" or gate.actionability != "actionable":
            raise ValueError("serenity_instrument_ineligible")
        context = serenity_candidate_opportunity_context(candidate_id=candidate_id, opportunity=opportunity,
            feature=feature, source_record_hash=source_hash, detected_at=timestamp)
        snapshot = UniverseSnapshot(source_kind="serenity", created_at=timestamp, policy_id="serenity_research_v1",
            expires_at=(now + timedelta(hours=cfg.universe.snapshot_ttl_hours)).isoformat())
        member = UniverseMember(id=candidate_id, snapshot_id=snapshot.id, asset_id=asset["id"], venue=venue,
            symbol=asset["symbol"], market_type=asset["market_type"], rank=len(prepared)+1, total_score=score,
            estimated_cost=(2.0 * cfg.trading.execution_fee_pct
                + (float(feature["spread_bps"]) + 2.0 * cfg.trading.slippage_bps) / 100.0),
            spread_bps=feature["spread_bps"], quote_volume_24h=feature["quote_volume_24h"],
            scanner_family=context.opportunity["source_component_id"], scanner_direction=opportunity["direction"],
            entry_price_usd=feature["last_price"], expires_at=snapshot.expires_at,
            candidate_selection_policy="serenity_research_v1", candidate_selection_policy_epoch="serenity_research_v1",
            opportunity_mechanism=context.opportunity["primary_mechanism"],
            opportunity_mechanism_version=context.opportunity["mechanism_version"],
            opportunity_source_component=context.opportunity["source_component_id"], opportunity_source_cutoff_at=timestamp,
            opportunity_evidence_hash=source_hash, candidate_opportunity_context_version=context.version,
            candidate_opportunity_context_json=context.model_dump_json(), candidate_opportunity_context_hash=context.context_hash)
        proof = repo.prepare_candidate_requalification(member.model_dump())
        if proof is None:
            raise ValueError("serenity_pa_requalification_unavailable")
        prepared.append((opportunity_id, source_hash, member, snapshot, proof))

    with repo.transaction(immediate=True):
        theme = repo.get_serenity_theme(theme_id)
        if theme is None or theme["status"] not in {"active", "promoted"}:
            raise ValueError("serenity_theme_ineligible")
        result, created = [], []
        for opportunity_id, source_hash, member, snapshot, proof in prepared:
            if fingerprint(source_material(opportunity_id)) != source_hash:
                raise ValueError("serenity_source_changed")
            durable = repo.get_db().execute(
                "SELECT um.id FROM universe_members um JOIN candidate_planning_generations pg ON (pg.candidate_id=um.id OR pg.last_candidate_id=um.id) "
                "WHERE json_extract(um.candidate_opportunity_context_json,'$.source.kind')='serenity' "
                "AND json_extract(um.candidate_opportunity_context_json,'$.source.id')=? "
                "AND json_extract(um.candidate_opportunity_context_json,'$.candidate_selection.source_record_hash')=? "
                "ORDER BY um.rowid LIMIT 1", (opportunity_id, source_hash),
            ).fetchone()
            if durable is not None:
                result.append(durable["id"])
                continue
            repo.publish_candidate_snapshot(snapshot, [member])
            generation, generation_created = repo.ensure_candidate_planning_generation(member.id, now=now, requalification=proof)
            if generation_created and json.loads(generation["requalification_source_json"]).get("birth_material") != proof:
                raise ValueError("serenity_pa_requalification_changed")
            result.append(member.id)
            created.append(member.id)
        if result:
            repo.update_serenity_theme(theme_id, status="promoted", promoted_at=timestamp, updated_at=timestamp)
        return created if new_candidates_only else result




logger = logging.getLogger("aletheia.serenity_publication")
_AUTO_PROMOTE_NOTIFY_LOCK = threading.Lock()
_AUTO_PROMOTE_NOTIFY_RETRIES: set[str] = set()


def auto_publish_serenity_candidates(
    opportunity_ids: list[str],
    *,
    min_score_for_pi: float,
) -> int:
    """Atomically publish score-qualified Serenity Candidates by theme."""

    theme_ids: set[str] = set()
    for opportunity_id in opportunity_ids:
        opp = repo.get_serenity_opportunity(opportunity_id)
        if not opp or opp.get("status") != "active":
            continue
        theme_ids.add(str(opp["theme_id"]))

    active_ids_by_theme: dict[str, list[str]] = {}
    for theme_id in theme_ids:
        active_siblings = repo.list_serenity_opportunities(
            theme_id=theme_id, status="active", limit=1000
        )
        active_ids_by_theme[theme_id] = [str(opp["id"]) for opp in active_siblings]

    promoted_count = 0
    newly_queued_themes: set[str] = set()
    for theme_id, active_ids in active_ids_by_theme.items():
        try:
            queued_ids = publish_serenity_candidates(
                theme_id,
                active_ids,
                min_score_for_pi=min_score_for_pi,
                new_candidates_only=True,
            )
        except Exception as exc:
            # A theme is one atomic unit. Keep it retryable while allowing an
            # unrelated theme in the same research run to advance.
            logger.warning(
                "auto_promote theme %s rolled back (%s)",
                theme_id,
                type(exc).__name__,
            )
            continue
        promoted_count += len(queued_ids)
        if queued_ids:
            newly_queued_themes.add(theme_id)

    with _AUTO_PROMOTE_NOTIFY_LOCK:
        retry_themes = set(theme_ids) & _AUTO_PROMOTE_NOTIFY_RETRIES
    notify_themes = newly_queued_themes | retry_themes
    if notify_themes:
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon

        try:
            notify_pi_daemon(source="serenity_auto_promote")
        except Exception:
            with _AUTO_PROMOTE_NOTIFY_LOCK:
                _AUTO_PROMOTE_NOTIFY_RETRIES.update(notify_themes)
            logger.warning("Serenity auto-promote wake failed; retry retained")
        else:
            with _AUTO_PROMOTE_NOTIFY_LOCK:
                _AUTO_PROMOTE_NOTIFY_RETRIES.difference_update(notify_themes)
    return promoted_count
