"""Gateway state machine with validated transitions and SAFE_FREEZE enforcement."""

from enum import StrEnum


class GatewayStatus(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RECOVERY_MODE = "RECOVERY_MODE"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    SAFE_FREEZE = "SAFE_FREEZE"
    STOPPING = "STOPPING"


_VALID_TRANSITIONS: dict[GatewayStatus, set[GatewayStatus]] = {
    GatewayStatus.STOPPED: {GatewayStatus.STARTING},
    GatewayStatus.STARTING: {GatewayStatus.RECOVERY_MODE},
    GatewayStatus.RECOVERY_MODE: {GatewayStatus.RUNNING, GatewayStatus.SAFE_FREEZE},
    GatewayStatus.RUNNING: {GatewayStatus.DEGRADED, GatewayStatus.SAFE_FREEZE, GatewayStatus.STOPPING},
    GatewayStatus.DEGRADED: {GatewayStatus.RUNNING, GatewayStatus.SAFE_FREEZE},
    GatewayStatus.SAFE_FREEZE: {GatewayStatus.RECOVERY_MODE},
    GatewayStatus.STOPPING: {GatewayStatus.STOPPED},
}

_FREEZE_BLOCKED_ACTIONS = frozenset([
    "new_open_position",
    "increase_position",
    "create_live_intent",
    "modify_hard_risk",
    "modify_live_unlock",
])

_FREEZE_ALLOWED_ACTIONS = frozenset([
    "query_account",
    "query_orders",
    "query_positions",
    "sync_fills",
    "update_local_state",
    "cancel_order",
    "reduce_position_with_confirmation",
])


class InvalidTransitionError(Exception):
    def __init__(self, current: GatewayStatus, target: GatewayStatus):
        self.current = current
        self.target = target
        super().__init__(f"Invalid gateway transition: {current} -> {target}")


class FreezeBlockedError(Exception):
    def __init__(self, action: str):
        self.action = action
        super().__init__(f"Action blocked in SAFE_FREEZE: {action}")


class GatewayStateMachine:
    """Validates gateway state transitions and enforces SAFE_FREEZE rules."""

    def __init__(self, initial: GatewayStatus = GatewayStatus.STOPPED):
        self._status = initial

    @property
    def status(self) -> GatewayStatus:
        return self._status

    @property
    def is_frozen(self) -> bool:
        return self._status == GatewayStatus.SAFE_FREEZE

    @property
    def is_running(self) -> bool:
        return self._status == GatewayStatus.RUNNING

    @property
    def can_submit_orders(self) -> bool:
        return self._status in (GatewayStatus.RUNNING, GatewayStatus.DEGRADED)

    def transition_to(self, target: GatewayStatus) -> GatewayStatus:
        """Transition to a new state. Raises InvalidTransitionError if not allowed."""
        allowed = _VALID_TRANSITIONS.get(self._status, set())
        if target not in allowed:
            raise InvalidTransitionError(self._status, target)
        self._status = target
        return self._status

    def check_action_allowed(self, action: str) -> None:
        """Raise FreezeBlockedError if action is blocked in current state."""
        if self._status == GatewayStatus.SAFE_FREEZE and action in _FREEZE_BLOCKED_ACTIONS:
            raise FreezeBlockedError(action)

    def enter_freeze(self, reason: str) -> str:
        """Convenience: transition to SAFE_FREEZE from any valid predecessor."""
        self.transition_to(GatewayStatus.SAFE_FREEZE)
        return reason
