"""Skill store — learned agent capabilities with permission enforcement.

Skills are created as draft artifacts from applied lessons. They are not
activated automatically; a human/operator route must review them before use.
"""

import json
from datetime import datetime, timezone

from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.models import PSSkill, new_id
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Hard-coded layers that skills can NEVER modify (plan §1.7)
FORBIDDEN_LAYERS = frozenset({
    "risk_limits",
    "execution_gateway",
    "credentials",
    "live_mode",
    "order_submission",
})


class SkillPermissionError(Exception):
    """Raised when a skill attempts to touch a forbidden layer."""
    pass


class SkillStore:
    """CRUD for agent-learned skills with permission enforcement."""

    def create(
        self,
        name: str,
        scope: str,
        description: str,
        allowed_layers: list[str] | None = None,
        inputs: list[str] | None = None,
        outputs: list[str] | None = None,
        risk_level: str = "low",
    ) -> PSSkill:
        """Create a new skill. Raises SkillPermissionError if targeting forbidden layers."""
        targets = allowed_layers or []
        violations = set(targets) & FORBIDDEN_LAYERS
        if violations:
            raise SkillPermissionError(
                f"Skill '{name}' targets forbidden layers: {violations}"
            )

        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        skill_id = new_id()

        skill = PSSkill(
            skill_id=skill_id,
            name=name,
            scope=scope,
            description=description,
            inputs_json=json.dumps(inputs or [], ensure_ascii=False),
            outputs_json=json.dumps(outputs or [], ensure_ascii=False),
            risk_level=risk_level,
            allowed_layers_json=json.dumps(targets, ensure_ascii=False),
            forbidden_layers_json=json.dumps(list(FORBIDDEN_LAYERS), ensure_ascii=False),
            status="draft",
            created_at=now,
        )

        db.execute(
            "INSERT INTO ps_skills (skill_id, name, scope, description, "
            "inputs_json, outputs_json, risk_level, allowed_layers_json, "
            "forbidden_layers_json, version, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (skill_id, name, scope, description,
             skill.inputs_json, skill.outputs_json, risk_level,
             skill.allowed_layers_json, skill.forbidden_layers_json, 1, "draft", now),
        )
        if not was_in_tx:
            db.commit()

        get_event_store().append(make_event(
            "SKILL_CREATED", "skill", skill_id,
            payload={"name": name, "scope": scope, "targets": targets},
        ))

        return skill

    def create_from_lesson(self, lesson, *, policy_version_id: str | None = None) -> PSSkill | None:
        """Create a draft skill candidate from an applied lesson.

        Idempotent per lesson_id. The skill is a reviewable long-term capability
        proposal, not an executable permission grant.
        """
        db = get_db()
        lesson_id = getattr(lesson, "lesson_id", None)
        if not lesson_id:
            return None
        existing = db.execute(
            "SELECT * FROM ps_skills WHERE name = ? LIMIT 1",
            (f"lesson:{lesson_id}",),
        ).fetchone()
        if existing:
            return PSSkill(**dict(existing))

        evidence = {}
        recommendation = {}
        try:
            evidence = json.loads(getattr(lesson, "evidence_json", "") or "{}")
        except (TypeError, ValueError):
            evidence = {}
        try:
            recommendation = json.loads(getattr(lesson, "recommendation_json", "") or "{}")
        except (TypeError, ValueError):
            recommendation = {}

        inputs = [
            "applied_lesson",
            getattr(lesson, "source_type", "") or "unknown_source",
        ]
        if evidence.get("symbol"):
            inputs.append("symbol")
        if evidence.get("scanner_family"):
            inputs.append("scanner_family")

        outputs = ["operator_prompt_context", "ranking_advisory"]
        if recommendation.get("action"):
            outputs.append(f"recommendation:{recommendation['action']}")

        description = (
            f"Draft skill from applied lesson {lesson_id}: "
            f"{getattr(lesson, 'summary', '')}"
        )[:500]
        if policy_version_id:
            description += f" (policy_version={policy_version_id})"

        return self.create(
            name=f"lesson:{lesson_id}",
            scope="learning_advisory",
            description=description,
            allowed_layers=["knowledge", "prompt_context", "ranking_advisory"],
            inputs=inputs,
            outputs=outputs,
            risk_level="low",
        )

    def get(self, skill_id: str) -> PSSkill | None:
        db = get_db()
        row = db.execute("SELECT * FROM ps_skills WHERE skill_id = ?", (skill_id,)).fetchone()
        if row is None:
            return None
        return PSSkill(**dict(row))

    def list_all(self, scope: str | None = None, status: str | None = None, limit: int = 100) -> list[PSSkill]:
        db = get_db()
        sql = "SELECT * FROM ps_skills WHERE 1=1"
        params: list = []
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        return [PSSkill(**dict(r)) for r in rows]

    def activate(self, skill_id: str) -> None:
        """Activate a skill after review."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_skills SET status = 'active' WHERE skill_id = ?",
            (skill_id,),
        )
        if not was_in_tx:
            db.commit()

    def deprecate(self, skill_id: str) -> None:
        """Deprecate a skill."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_skills SET status = 'deprecated' WHERE skill_id = ?",
            (skill_id,),
        )
        if not was_in_tx:
            db.commit()
