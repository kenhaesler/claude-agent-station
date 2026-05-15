"""Pre-dispatch heuristics for the issue splitter (#391).

Default policy: don't split. The five triggers below escalate an issue to a
"split candidate" — the SDK splitter still makes the final call. Triggers:

1. Long body (crude token-estimate threshold).
2. Four or more acceptance criteria checkboxes in the body.
3. Cross-cutting label sets (e.g. backend + frontend + db-migration).
4. Operator opt-in label ``split-me``.
5. Operator opt-out label ``do-not-split`` — the only hard veto. It beats
   ``split-me`` and every other trigger so an operator always has an escape
   hatch when the heuristic misjudges.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Whitespace-token threshold (call them words; one whitespace token ≈ 0.75
# LLM tokens, so 1200 words ≈ 900-1000 LLM tokens — well past the point
# where a single specialist run starts dropping context mid-implementation).
LONG_BODY_WORDS = 1200
ACCEPTANCE_COUNT_THRESHOLD = 4
CROSS_CUTTING_TRIPLES: tuple[frozenset[str], ...] = (
    frozenset({"backend", "frontend", "db-migration"}),
    frozenset({"backend", "frontend", "infra"}),
)

OPT_IN_LABEL = "split-me"
OPT_OUT_LABEL = "do-not-split"


@dataclass(frozen=True, slots=True)
class HeuristicResult:
    should_split: bool
    reasons: tuple[str, ...]


_BULLET_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]", re.MULTILINE)


def _acceptance_count(body: str) -> int:
    return len(_BULLET_RE.findall(body or ""))


def _body_word_count(body: str) -> int:
    # Whitespace-split count is a deliberate proxy for "context size";
    # pulling in tiktoken for one threshold isn't worth the extra
    # dependency on the splitter's hot path.
    return len((body or "").split())


def maybe_split(issue: dict) -> HeuristicResult:
    labels = set(issue.get("labels") or ())
    if OPT_OUT_LABEL in labels:
        return HeuristicResult(should_split=False, reasons=("opt_out",))
    if OPT_IN_LABEL in labels:
        return HeuristicResult(should_split=True, reasons=("opt_in",))

    reasons: list[str] = []
    body = issue.get("body") or ""

    if _body_word_count(body) > LONG_BODY_WORDS:
        reasons.append("body_length")

    if _acceptance_count(body) >= ACCEPTANCE_COUNT_THRESHOLD:
        reasons.append("acceptance_count")

    if any(triple <= labels for triple in CROSS_CUTTING_TRIPLES):
        reasons.append("cross_cutting")

    return HeuristicResult(should_split=bool(reasons), reasons=tuple(reasons))
