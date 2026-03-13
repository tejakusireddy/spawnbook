import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# -- Error patterns ----------------------------------------------------------
#
# Each entry: (compiled regex, root cause, severity, recommendation).
# Ordered roughly by severity so the most critical patterns appear first,
# but final ranking is done explicitly via _SEVERITY_RANK at query time.

_PATTERNS: List[Tuple[re.Pattern[str], str, str, str]] = [
    # Database / connection pool
    (
        re.compile(r"connection pool (exhausted|full|exceeded|depleted)", re.IGNORECASE),
        "Database connection pool exhausted",
        "critical",
        "Increase connection pool size or investigate connection leaks",
    ),
    (
        re.compile(r"(OutOfMemory|OOM|memory (exhausted|exceeded|limit))", re.IGNORECASE),
        "Out of memory",
        "critical",
        "Increase memory allocation or investigate memory leaks",
    ),
    (
        re.compile(r"(disk space|no space left|filesystem full)", re.IGNORECASE),
        "Disk space exhausted",
        "critical",
        "Free disk space or increase volume size",
    ),
    # Database connectivity
    (
        re.compile(r"(database|db).*(timeout|timed out|unreachable)", re.IGNORECASE),
        "Database connection timeout",
        "high",
        "Check database server health and network connectivity",
    ),
    # Generic timeouts
    (
        re.compile(r"(request|connection|gateway) timeout", re.IGNORECASE),
        "Request timeout",
        "high",
        "Investigate upstream service latency or increase timeout thresholds",
    ),
    # HTTP 5xx
    (
        re.compile(r"(HTTP[/ ]?5\d{2}|status[=: ]*5\d{2}|Internal Server Error)", re.IGNORECASE),
        "Internal server error",
        "high",
        "Investigate application error logs and recent deployments",
    ),
    # Auth failures
    (
        re.compile(r"(authentication|auth) (fail|failure|denied|unauthorized)", re.IGNORECASE),
        "Authentication failure",
        "medium",
        "Check credentials and authentication configuration",
    ),
    # Rate limiting
    (
        re.compile(r"(rate limit|throttl|429|too many requests)", re.IGNORECASE),
        "Rate limiting triggered",
        "medium",
        "Implement backoff strategy or request rate limit increase",
    ),
    # Null / type errors
    (
        re.compile(r"(NullPointerException|null reference|TypeError.*None|AttributeError)", re.IGNORECASE),
        "Null reference error",
        "medium",
        "Fix null handling in application code",
    ),
    # Permission
    (
        re.compile(r"(permission denied|access denied|forbidden|403)", re.IGNORECASE),
        "Permission denied",
        "medium",
        "Review IAM policies and service account permissions",
    ),
]

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_TIMESTAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"|(\d{2}:\d{2}:\d{2})"
)

_SERVICE_RE = re.compile(
    r"\[([a-zA-Z][a-zA-Z0-9_-]+)\]"
    r"|service[=: ]+([a-zA-Z][a-zA-Z0-9_-]+)"
)


# -- Public executor ---------------------------------------------------------

def execute(input: dict) -> dict:
    """Analyze logs for root causes using pattern matching.

    Input:  {"logs": "<raw log string>"}
    Output: structured analysis dict.

    v1 uses regex pattern matching. LLM reasoning is planned for v2.
    """
    logs: str = input.get("logs", "")
    if not logs or not logs.strip():
        return {
            "root_cause": "No logs provided",
            "severity": "unknown",
            "affected_service": "unknown",
            "first_occurrence": None,
            "occurrence_count": 0,
            "recommendation": "Provide log data for analysis",
        }

    lines = logs.strip().split("\n")
    matches = _find_matches(lines)

    if not matches:
        return {
            "root_cause": "No known error patterns detected",
            "severity": "low",
            "affected_service": _extract_service(logs),
            "first_occurrence": _extract_first_timestamp(logs),
            "occurrence_count": 0,
            "recommendation": "Manual investigation recommended",
        }

    matches.sort(key=lambda m: _SEVERITY_RANK.get(m[2], 99))
    _, root_cause, severity, recommendation = matches[0]

    logger.info(
        "Log analysis complete: root_cause=%s severity=%s matches=%d",
        root_cause,
        severity,
        len(matches),
    )

    return {
        "root_cause": root_cause,
        "severity": severity,
        "affected_service": _extract_service(logs),
        "first_occurrence": _extract_first_timestamp(logs),
        "occurrence_count": len(matches),
        "recommendation": recommendation,
    }


# -- Internal helpers --------------------------------------------------------

def _find_matches(lines: List[str]) -> List[Tuple[str, str, str, str]]:
    """Return (line, root_cause, severity, recommendation) for every match."""
    matches: List[Tuple[str, str, str, str]] = []
    for line in lines:
        for pattern, root_cause, severity, recommendation in _PATTERNS:
            if pattern.search(line):
                matches.append((line, root_cause, severity, recommendation))
                break
    return matches


def _extract_first_timestamp(logs: str) -> Optional[str]:
    """Return the first timestamp found in the raw logs, or None."""
    match = _TIMESTAMP_RE.search(logs)
    if match:
        return match.group(1) or match.group(2)
    return None


def _extract_service(logs: str) -> str:
    """Return the first service name found, or 'unknown'."""
    match = _SERVICE_RE.search(logs)
    if match:
        return match.group(1) or match.group(2)
    return "unknown"
