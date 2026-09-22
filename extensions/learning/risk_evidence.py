"""Optional Learning treatment evidence; Risk retains rule interpretation."""


def authorize_lesson_treatment(lesson, proposal, recommendation: dict) -> bool:
    source_type = str(getattr(lesson, "source_type", "") or "").strip().lower()
    proposal_id = str(getattr(proposal, "id", "") or "").strip()
    from extensions.learning.experiments import apply_boolean_treatment
    decision = apply_boolean_treatment(
        path_id="risk_lesson_constraint",
        scope={
            "lesson_id": lesson.lesson_id,
            "source_type": source_type,
            "category": getattr(lesson, "category", None),
        },
        action={
            "type": "proposal_filter",
            "recommendation": recommendation,
        },
        source_cohort=f"ps_lessons:{source_type or 'unknown'}",
        target_horizon="4h",
        candidate_key=f"risk_lesson:{proposal_id}:{lesson.lesson_id}",
        consumer="risk_engine",
        consumer_stage="proposal_filter",
        # record_risk_filter_decision() creates this exact fixed-horizon candidate
        # for every authoritative Proposal.  The horizon suffix is required by
        # the Learning cohort join; bare ``proposal:<id>`` would never settle.
        outcome_candidate_key=f"proposal:{proposal_id}:4h",
        opportunity_id=getattr(proposal, "opportunity_id", None),
        manifest={
            "proposal_id": proposal_id,
            "lesson_id": lesson.lesson_id,
            "static_owner_rule": False,
        },
    )
    return bool(decision.applied and decision.value >= 1.0)
