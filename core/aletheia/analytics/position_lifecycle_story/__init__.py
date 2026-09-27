"""Read-only Position lifecycle explanation and its public rendering API.

service assembles persisted facts; events owns pure vocabulary and projection;
render consumes the finished Story. None grants trading authority.
"""

from .events import POSITION_LIFECYCLE_EVENT_TYPES
from .render import render_position_lifecycle_story
from .service import (
    MEANINGFUL_EXCURSION_RULE_VERSION,
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
