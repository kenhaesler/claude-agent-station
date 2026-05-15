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

    warnings: list[str] = []
    items = data
    if len(items) > MAX_PROPOSALS:
        warnings.append(f"truncated {len(items)} proposals to {MAX_PROPOSALS}")
        items = items[:MAX_PROPOSALS]

    proposals: list[SubIssueProposal] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise SplitterError(f"item {i} is not an object")
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise SplitterError(f"item {i} missing fields: {missing}")
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
