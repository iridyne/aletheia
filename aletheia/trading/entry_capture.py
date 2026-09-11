"""Finite Compiler-owned capture policies; no execution or mutable state."""
from typing import Literal, NamedTuple

CapturePriority = Literal["PRICE_FIRST", "MAKER_PREFERRED", "FILL_FIRST"]


class CapturePolicy(NamedTuple):
    policy_id: str
    passive_attempt_timeout_sec: int


CAPTURE_POLICIES = {
    "PRICE_FIRST": CapturePolicy("exact-anchor-passive-until-plan-terminal/v1", 0),
    "MAKER_PREFERRED": CapturePolicy("exact-anchor-passive-then-bounded-ioc/v1", 60),
    "FILL_FIRST": CapturePolicy("bounded-opposing-bbo-ioc/v1", 0),
}
