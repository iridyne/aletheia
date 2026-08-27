"""Stable, sanitized UTA error taxonomy."""

from enum import Enum
import re

from aletheia.uta.models import CommandEvidence


class UTAErrorCode(str, Enum):
    CONFIG = "UTA_CONFIG"
    AUTH = "UTA_AUTH"
    PERMISSION = "UTA_PERMISSION"
    NETWORK = "UTA_NETWORK"
    TIMEOUT = "UTA_TIMEOUT"
    RATE_LIMIT = "UTA_RATE_LIMIT"
    NOT_SUPPORTED = "UTA_NOT_SUPPORTED"
    INVALID_INSTRUMENT = "UTA_INVALID_INSTRUMENT"
    INVALID_ORDER = "UTA_INVALID_ORDER"
    INSUFFICIENT_FUNDS = "UTA_INSUFFICIENT_FUNDS"
    MARKET_CLOSED = "UTA_MARKET_CLOSED"
    VENUE_REJECTED = "UTA_VENUE_REJECTED"
    PARSE = "UTA_PARSE"
    INCOMPLETE_READ = "UTA_INCOMPLETE_READ"
    AMBIGUOUS_COMMAND = "UTA_AMBIGUOUS_COMMAND"
    ACCOUNT_IDENTITY_MISMATCH = "UTA_ACCOUNT_IDENTITY_MISMATCH"
    UNKNOWN = "UTA_UNKNOWN"


class UTAError(Exception):
    """Contract/invariant failure carrying only sanitized boundary evidence."""

    def __init__(
        self,
        code: UTAErrorCode,
        reason: str,
        *,
        command_evidence: CommandEvidence | None = None,
        retryable: bool = False,
    ) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason):
            raise ValueError("UTA error reason must be a bounded stable reason code")
        if command_evidence is CommandEvidence.AMBIGUOUS and retryable:
            raise ValueError("ambiguous UTA commands cannot be retryable")
        self.code = code
        self.reason = reason
        self.command_evidence = command_evidence
        self.retryable = retryable
        super().__init__(f"{code.value}: {reason}")
