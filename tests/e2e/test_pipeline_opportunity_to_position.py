"""Opportunity producer fields remain readable by downstream consumers."""

from aletheia.storage.models import Opportunity
from aletheia.storage import repositories as repo


class TestOpportunityReadFields:
    """An independent producer persists its opportunity evidence."""

    def test_opportunity_fields_survive_repository_read(self, e2e_db):
        """Status, confidence, direction and eligibility survive the repository read."""
        opp = Opportunity(
            symbol="ETH/USDT",
            market="spot",
            direction_bias="short",
            confidence="high",
            confidence_score=78,
            source_agent="research_worker",
            asset_class="crypto",
            execution_eligible=True,
            scanner_family="momentum",
        )
        repo.insert_opportunity(opp)

        from aletheia.storage.repositories import list_opportunities
        result = list_opportunities(limit=10)
        found = [o for o in result if o["symbol"] == "ETH/USDT"]
        assert len(found) == 1
        assert found[0]["status"] == "open"
        assert found[0]["confidence"] == "high"
        assert found[0]["confidence_score"] == 78
        assert found[0]["direction_bias"] == "short"
        assert found[0]["execution_eligible"] == 1
