"""Lock the SkillStore interface — FORBIDDEN_LAYERS and CRUD contract.

Phase 4b: skills.py is reserved (no consumers), but the safety-critical
FORBIDDEN_LAYERS set and the CRUD interface are locked by these specs so
future Tier 2/3 activation starts from a known-good contract.
"""

import pytest

from aletheia.learning.skills import SkillStore, SkillPermissionError, FORBIDDEN_LAYERS


class TestForbiddenLayers:
    """FORBIDDEN_LAYERS must include all trust-boundary layers."""

    def test_risk_limits_is_forbidden(self):
        assert "risk_limits" in FORBIDDEN_LAYERS

    def test_execution_gateway_is_forbidden(self):
        assert "execution_gateway" in FORBIDDEN_LAYERS

    def test_credentials_is_forbidden(self):
        assert "credentials" in FORBIDDEN_LAYERS

    def test_live_mode_is_forbidden(self):
        assert "live_mode" in FORBIDDEN_LAYERS

    def test_order_submission_is_forbidden(self):
        assert "order_submission" in FORBIDDEN_LAYERS

    def test_forbidden_layers_is_frozenset(self):
        """Immutable — callers cannot mutate the trust boundary at runtime."""
        assert isinstance(FORBIDDEN_LAYERS, frozenset)


class TestSkillStoreRejection:
    """SkillStore.create() must reject skills targeting forbidden layers."""

    def test_reject_skill_targeting_risk_limits(self):
        store = SkillStore()
        with pytest.raises(SkillPermissionError):
            store.create(
                name="bad_risk",
                scope="execution_helper",
                description="tries to modify risk",
                allowed_layers=["risk_limits"],
            )

    def test_reject_skill_targeting_credentials(self):
        store = SkillStore()
        with pytest.raises(SkillPermissionError):
            store.create(
                name="bad_creds",
                scope="execution_helper",
                description="tries to access credentials",
                allowed_layers=["credentials"],
            )

    def test_reject_skill_targeting_multiple_forbidden(self):
        store = SkillStore()
        with pytest.raises(SkillPermissionError):
            store.create(
                name="bad_multi",
                scope="execution_helper",
                description="touches multiple forbidden layers",
                allowed_layers=["risk_limits", "execution_gateway"],
            )
