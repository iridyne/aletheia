"""Decision Core — aggregates agent opinions via weighted voting.

Combines Trend, Macro, News, and Risk agent outputs into a single
direction + confidence.  Detects conflicts between agents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aletheia.agents.committee.base import (
    AgentOpinion,
    AgentDirection,
)

logger = logging.getLogger("aletheia.committee.decision_core")

# Weights: how much each agent contributes to the final decision.
# Risk Agent has veto power (can block), not just a vote.
AGENT_WEIGHTS: dict[str, float] = {
    "Trend": 0.30,
    "Macro": 0.25,
    "News": 0.15,
    "Risk": 0.30,
}


@dataclass
class DecisionResult:
    """Output of the Decision Core."""

    direction: AgentDirection
    confidence: float
    position_size_factor: float
    conflict_detected: bool
    conflict_description: str
    agent_votes: dict[str, dict[str, Any]]

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "confidence": round(self.confidence, 3),
            "position_size_factor": round(self.position_size_factor, 3),
            "conflict_detected": self.conflict_detected,
            "conflict_description": self.conflict_description,
            "agent_votes": self.agent_votes,
        }


def _opinion_to_vote(opinion: AgentOpinion) -> float:
    """Convert an agent opinion to a signed vote (-1 to +1)."""
    sign = {"long": 1.0, "short": -1.0, "neutral": 0.0}[opinion.direction]
    return sign * opinion.confidence * AGENT_WEIGHTS.get(opinion.agent_name, 0.1)


def decide(
    opinions: list[AgentOpinion],
    risk_position_factor: float = 1.0,
) -> DecisionResult:
    """Aggregate agent opinions into a final decision.

    Args:
        opinions: list of AgentOpinion from each committee agent.
        risk_position_factor: 0-1, position size scaling from Risk Agent.

    Returns:
        DecisionResult with final direction, confidence, and conflict info.
    """
    if not opinions:
        return DecisionResult(
            direction="neutral",
            confidence=0.0,
            position_size_factor=0.0,
            conflict_detected=False,
            conflict_description="No agent opinions available",
            agent_votes={},
        )

    # --- Weighted vote ---
    vote_sum = 0.0
    agent_votes: dict[str, dict[str, Any]] = {}
    for op in opinions:
        vote = _opinion_to_vote(op)
        vote_sum += vote
        agent_votes[op.agent_name] = {
            "direction": op.direction,
            "confidence": op.confidence,
            "weight": AGENT_WEIGHTS.get(op.agent_name, 0.1),
            "vote": round(vote, 4),
        }

    # --- Conflict detection ---
    directional_agents = [o for o in opinions if o.direction != "neutral"]
    long_agents = [o for o in directional_agents if o.direction == "long"]
    short_agents = [o for o in directional_agents if o.direction == "short"]

    conflict_detected = False
    conflict_desc = ""

    # Strong disagreement: multiple agents with high confidence in opposite directions
    if long_agents and short_agents:
        long_conf = max(o.confidence for o in long_agents)
        short_conf = max(o.confidence for o in short_agents)
        if long_conf > 0.6 and short_conf > 0.6:
            conflict_detected = True
            long_names = ", ".join(o.agent_name for o in long_agents)
            short_names = ", ".join(o.agent_name for o in short_agents)
            conflict_desc = (
                f"Agent 分歧: [{long_names}] 看多 vs [{short_names}] 看空"
            )

    # Risk veto
    risk_opinion = next((o for o in opinions if o.agent_name == "Risk"), None)
    if risk_opinion and risk_opinion.direction == "neutral" and risk_opinion.confidence > 0.6:
        conflict_detected = True
        conflict_desc = f"Risk Agent 否决: {risk_opinion.reasoning[:100]}"

    # --- Final direction ---
    if vote_sum > 0.15:
        direction = "long"
    elif vote_sum < -0.15:
        direction = "short"
    else:
        direction = "neutral"

    # --- Confidence ---
    # Normalize to 0-1 based on vote magnitude and agent agreement
    raw_confidence = min(abs(vote_sum) * 2, 1.0)
    # Penalize for conflict
    if conflict_detected:
        raw_confidence *= 0.7
    # Floor at 0.1 when direction is non-neutral: a decision was reached
    # (vote_sum exceeded the threshold), so report at least minimal confidence.
    # Pure neutral (vote_sum ~ 0) stays at 0 to signal "no conviction".
    direction_neutral = direction == "neutral"
    floor = 0.0 if direction_neutral else 0.1
    confidence = max(floor, min(raw_confidence, 1.0))

    # --- Position size ---
    # Start with risk agent's position factor
    position_factor = risk_position_factor
    # Reduce for conflict
    if conflict_detected:
        position_factor *= 0.5
    # Scale by confidence
    position_factor *= min(confidence * 1.5, 1.0)
    position_factor = max(0.0, min(position_factor, 1.0))

    return DecisionResult(
        direction=direction,
        confidence=round(confidence, 3),
        position_size_factor=round(position_factor, 3),
        conflict_detected=conflict_detected,
        conflict_description=conflict_desc,
        agent_votes=agent_votes,
    )
