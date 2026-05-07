from app.services.vision_chat_parser import (
    extract_vision_meta, extract_vision_doc, strip_fenced_blocks,
)


def test_extract_vision_meta_finds_block():
    text = (
        "Hi! Tell me about your project.\n\n"
        "```vision-meta\n"
        '{"phase": "freeform", "covered": ["problem"], "ready_to_assemble": false}\n'
        "```\n"
    )
    meta = extract_vision_meta(text)
    assert meta == {"phase": "freeform", "covered": ["problem"], "ready_to_assemble": False}


def test_extract_vision_meta_returns_none_when_missing():
    assert extract_vision_meta("just prose, no fence") is None


def test_extract_vision_meta_returns_none_on_malformed_json():
    bad = "```vision-meta\n{not json}\n```"
    assert extract_vision_meta(bad) is None


def test_extract_vision_doc_finds_block():
    text = (
        "Here is the assembled vision:\n"
        "```vision-doc\n"
        '{"problem": "P", "users": "U", "end_state": "E",\n'
        ' "non_goals": "N", "principles": "Pr",\n'
        ' "horizons": "H", "anti_patterns": "A"}\n'
        "```\n"
    )
    doc = extract_vision_doc(text)
    assert doc["problem"] == "P"
    assert set(doc.keys()) == {"problem", "users", "end_state", "non_goals",
                               "principles", "horizons", "anti_patterns"}


def test_extract_vision_doc_rejects_missing_required_keys():
    bad = '```vision-doc\n{"problem": "P"}\n```'
    assert extract_vision_doc(bad) is None


def test_strip_fenced_blocks_removes_meta_and_doc_fences():
    text = (
        "Hi.\n\n"
        "```vision-meta\n{}\n```\n\n"
        "More prose.\n"
        "```vision-doc\n{}\n```\n"
    )
    out = strip_fenced_blocks(text)
    assert "vision-meta" not in out
    assert "vision-doc" not in out
    assert "Hi." in out
    assert "More prose." in out
