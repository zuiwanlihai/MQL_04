"""
Structured error codes for the strategy sandbox.

These are returned to the strategy as part of order results
and resource limit violations. The strategy can catch these
and take appropriate action (log, retry, stop, etc.).

Error codes are divided into two categories:
- SAFETY: lead to strategy termination
- BUSINESS: returned as structured errors, strategy continues
"""

from enum import Enum


class ErrorCode(str, Enum):
    """Closed enum of all possible error codes returned by the SDK/platform."""

    # === Safety errors (strategy terminated) ===
    RESOURCE_TIMEOUT = "RESOURCE_TIMEOUT"        # Callback exceeded time limit
    RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"       # Memory exceeded limit
    SANDBOX_VIOLATION = "SANDBOX_VIOLATION"       # Attempted forbidden import/operation
    RUNTIME_ERROR = "RUNTIME_ERROR"               # Unhandled exception in strategy
    ILLEGAL_IMPORT = "ILLEGAL_IMPORT"             # Blacklisted module import attempted

    # === Business errors (strategy continues) ===
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"   # Not enough margin for order
    RATE_LIMITED = "RATE_LIMITED"                  # Too many orders in one bar
    MARKET_CLOSED = "MARKET_CLOSED"                # Outside trading hours
    INVALID_VOLUME = "INVALID_VOLUME"              # Volume out of allowed range
    INVALID_SYMBOL = "INVALID_SYMBOL"              # Symbol not declared or not available
    RISK_BLOCKED = "RISK_BLOCKED"                  # Blocked by risk control rules
    STORAGE_FULL = "STORAGE_FULL"                  # ctx.storage exceeded 1MB quota
    REQUOTE = "REQUOTE"                            # Price changed, need requote
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"            # Referenced order doesn't exist
    POSITION_NOT_FOUND = "POSITION_NOT_FOUND"      # Referenced position doesn't exist
    STRATEGY_STOPPED = "STRATEGY_STOPPED"          # Strategy has been stopped
    WARMUP_PERIOD = "WARMUP_PERIOD"                # Order rejected during warmup

    # === General ===
    UNKNOWN_ERROR = "UNKNOWN_ERROR"                # Catch-all for unexpected errors


# Grouping for UI/log display
SAFETY_ERRORS = frozenset({
    ErrorCode.RESOURCE_TIMEOUT,
    ErrorCode.RESOURCE_EXCEEDED,
    ErrorCode.SANDBOX_VIOLATION,
    ErrorCode.RUNTIME_ERROR,
    ErrorCode.ILLEGAL_IMPORT,
})

BUSINESS_ERRORS = frozenset({
    ErrorCode.INSUFFICIENT_MARGIN,
    ErrorCode.RATE_LIMITED,
    ErrorCode.MARKET_CLOSED,
    ErrorCode.INVALID_VOLUME,
    ErrorCode.INVALID_SYMBOL,
    ErrorCode.RISK_BLOCKED,
    ErrorCode.STORAGE_FULL,
    ErrorCode.REQUOTE,
    ErrorCode.ORDER_NOT_FOUND,
    ErrorCode.POSITION_NOT_FOUND,
    ErrorCode.STRATEGY_STOPPED,
    ErrorCode.WARMUP_PERIOD,
})


def is_safety_error(code: ErrorCode) -> bool:
    """Check if an error code leads to strategy termination."""
    return code in SAFETY_ERRORS


def is_business_error(code: ErrorCode) -> bool:
    """Check if an error code is a business rejection (strategy continues)."""
    return code in BUSINESS_ERRORS