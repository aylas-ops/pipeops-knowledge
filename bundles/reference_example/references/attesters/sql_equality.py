"""Deterministic attester for Attested Computations with `runtime: postgres`.

Checks two things about a receipt produced by
`/references/skills/run-on-postgres.md`:

  1. **Provenance** -- the SQL that actually ran equals the concept's sanctioned
     computation, after canonicalising whitespace, comments and keyword case.
     Bind variables are compared symbolically; their values are the executor's
     responsibility, not this module's.

  2. **Fidelity** -- the value the caller is about to show equals the first cell
     of the receipt's result, rather than something the agent retyped.

No LLM. No network. No filesystem. Safe to run consumer-side, which is the
whole point: an attester the producer runs proves nothing to the consumer.
"""

from __future__ import annotations

import re
from typing import Any

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_WHITESPACE = re.compile(r"\s+")
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

_KEYWORDS = frozenset(
    """
    SELECT FROM WHERE GROUP BY ORDER HAVING JOIN LEFT RIGHT INNER OUTER FULL
    CROSS ON AS AND OR NOT NULL IS IN EXISTS BETWEEN LIKE ILIKE CASE WHEN THEN
    ELSE END WITH UNION ALL DISTINCT LIMIT OFFSET FILTER OVER PARTITION
    COUNT SUM AVG MIN MAX COALESCE NULLIF CAST INTERVAL NOW TRUE FALSE
    """.split()
)


def canonicalize(sql: str) -> str:
    """Normalise SQL so formatting differences do not read as tampering."""
    text = _COMMENT_BLOCK.sub(" ", sql)
    text = _COMMENT_LINE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    def upper_keyword(match: re.Match[str]) -> str:
        word = match.group(0)
        return word.upper() if word.upper() in _KEYWORDS else word

    return _WORD.sub(upper_keyword, text)


def attest(
    *,
    sanctioned_sql: str,
    receipt: dict[str, Any],
    claimed_value: Any = None,
) -> dict[str, Any]:
    """Return ``{"ok": bool, "reason": str | None, "details": dict}``.

    Callers MUST refuse to display ``claimed_value`` when ``ok`` is False.
    """
    executed = receipt.get("executed_sql")
    if not executed:
        return {
            "ok": False,
            "reason": "receipt is missing executed_sql",
            "details": {"receipt_keys": sorted(receipt)},
        }

    expected = canonicalize(sanctioned_sql)
    actual = canonicalize(executed)
    if expected != actual:
        return {
            "ok": False,
            "reason": "executed SQL does not match the sanctioned computation",
            "details": {"expected": expected, "actual": actual},
        }

    result = receipt.get("result")
    if result is None:
        return {
            "ok": False,
            "reason": "receipt carries no result",
            "details": {"error": receipt.get("error")},
        }

    if claimed_value is not None:
        first = result[0] if isinstance(result, (list, tuple)) and result else result
        if str(first) != str(claimed_value):
            return {
                "ok": False,
                "reason": "displayed value does not match the receipt",
                "details": {"receipt_value": first, "claimed_value": claimed_value},
            }

    return {
        "ok": True,
        "reason": None,
        "details": {"statement_id": receipt.get("statement_id")},
    }
