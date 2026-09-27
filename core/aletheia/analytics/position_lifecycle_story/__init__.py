"""Read-only Position lifecycle explanation and its public rendering API.

service builds structured facts from persisted evidence; render consumes the
finished Story without database access. Neither grants trading authority.
"""

from .render import render_position_lifecycle_story
from .service import (
    MEANINGFUL_EXCURSION_RULE_VERSION,
    POSITION_LIFECYCLE_EVENT_TYPES,
    STORY_CONTRACT_VERSION,
    build_position_lifecycle_story,
)

__all__ = [
    "MEANINGFUL_EXCURSION_RULE_VERSION",
    "POSITION_LIFECYCLE_EVENT_TYPES",
    "STORY_CONTRACT_VERSION",
    "build_position_lifecycle_story",
    "render_position_lifecycle_story",
]
