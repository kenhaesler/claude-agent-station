"""Pinning test for the vision-doc parser's required-keys contract.

Issue #335 added tech_stack and runtime_target as OPTIONAL fields on
VisionDoc with empty-string defaults. The chat parser's
``_REQUIRED_DOC_KEYS`` set, however, must stay at the original seven so
that vision-doc JSON blocks predating the change (e.g. resumed from
cache, or hand-edited by an operator) keep validating. If the two new
keys are ever added here, old chats fail with "missing required key" —
silently breaking the back-compat we promised.
"""

from app.services.vision_chat_parser import _REQUIRED_DOC_KEYS


def test_required_doc_keys_is_exactly_seven_original_fields():
    assert _REQUIRED_DOC_KEYS == {
        "problem", "users", "end_state",
        "non_goals", "principles", "horizons", "anti_patterns",
    }, (
        f"REGRESSION (#335): _REQUIRED_DOC_KEYS changed to {_REQUIRED_DOC_KEYS}. "
        "tech_stack and runtime_target must remain optional in the chat "
        "parser to preserve back-compat with pre-#335 chat payloads."
    )
