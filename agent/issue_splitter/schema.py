"""Splitter output schema + parser (#391).

The splitter emits a JSON array of sub-issue proposals. Strict validation
keeps the autonomous flow from acting on malformed output — on any
validation failure the run falls back to single-issue mode and the parent
issue stays untouched.

An empty array (``[]``) is the splitter's explicit signal of "don't split,
run the parent issue as-is". It is intentionally distinct from a validation
error: a clean empty decision is forwarded to the scheduler unchanged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_PROPOSALS = 5
MIN_PROPOSALS = 2

REQUIRED_FIELDS = ("title", "body", "labels", "acceptance")

_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(?P<body>.*?)\n?```\s*$",
    re.DOTALL,
)


class SplitterError(Exception):
    """Raised by :func:`parse_splitter_output` on any validation failure."""


@dataclass(frozen=True, slots=True)
class SubIssueProposal:
    title: str
    body: str
    labels: tuple[str, ...]
    acceptance: tuple[str, ...]
    depends_on: int | None = None  # zero-based index into the proposals tuple


@dataclass(frozen=True, slots=True)
class SplitDecision:
    proposals: tuple[SubIssueProposal, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _strip_markdown_fence(raw: str) -> str:
    """Tolerate ```json fences around the JSON payload.

    Sonnet wraps structured output in a fenced block by default; rejecting
    that would force the prompt to fight the model. The parser stays strict
    once the fence is gone.
    """
    match = _FENCE_RE.match(raw.strip())
    if match is None:
        return raw
    return match.group("body")


def parse_splitter_output(raw: str) -> SplitDecision:
    payload = _strip_markdown_fence(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SplitterError(f"invalid json: {exc}") from exc

    if not isinstance(data, list):
        raise SplitterError("top-level must be a json array")

    if len(data) == 0:
        # Splitter explicitly chose not to split; let the run proceed as-is.
        return SplitDecision(proposals=(), warnings=())

    if len(data) < MIN_PROPOSALS:
        raise SplitterError(
            f"need at least {MIN_PROPOSALS} proposals or an empty array, got {len(data)}"
        )

    # Refuse rather than truncate: silently dropping proposals would
    # invalidate the splitter's depends_on index math (an index pointing
    # at a discarded item would become a separate "out of range" error)
    # and discards work the operator might want to see. The prompt's
    # 2-5 contract is the source of truth — anything outside that is
    # the model violating the contract, not something for us to paper
    # over. Reviewers: see PR #421 finding "truncation silently drops".
    if len(data) > MAX_PROPOSALS:
        raise SplitterError(
            f"at most {MAX_PROPOSALS} proposals allowed, got {len(data)}"
        )

    warnings: list[str] = []
    proposals: list[SubIssueProposal] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise SplitterError(f"item {i} is not an object")
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise SplitterError(f"item {i} missing fields: {missing}")
        # ``labels`` and ``acceptance`` MUST be JSON arrays — strings are
        # iterable in Python, so a bare ``"labels": "backend"`` would
        # otherwise produce per-character labels like ('b','a','c',...)
        # and PR-2's GitHub label-creation step would land seven garbage
        # labels on the issue. Reject explicitly. See PR #421 review.
        for field_name in ("labels", "acceptance"):
            value = item.get(field_name)
            if value is not None and not isinstance(value, list):
                raise SplitterError(
                    f"item {i} {field_name} must be a json array, got {type(value).__name__}"
                )
        proposals.append(
            SubIssueProposal(
                title=str(item["title"]),
                body=str(item["body"]),
                labels=tuple(map(str, item.get("labels") or ())),
                acceptance=tuple(map(str, item.get("acceptance") or ())),
                depends_on=item.get("depends_on"),
            )
        )

    for i, prop in enumerate(proposals):
        if prop.depends_on is None:
            continue
        if not isinstance(prop.depends_on, int) or isinstance(prop.depends_on, bool):
            raise SplitterError(f"item {i} depends_on must be int or null")
        if not 0 <= prop.depends_on < len(proposals):
            raise SplitterError(
                f"item {i} depends_on={prop.depends_on} out of range"
            )
        if prop.depends_on == i:
            raise SplitterError(f"item {i} depends_on itself")

    return SplitDecision(proposals=tuple(proposals), warnings=tuple(warnings))
