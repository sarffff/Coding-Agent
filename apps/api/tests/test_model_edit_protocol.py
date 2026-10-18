import asyncio
import json
import os

import pytest

from app import model_provider as mp
from app.config import Settings
from app.model_provider import AnthropicModelProvider, OpenAIModelProvider, _proposal_from_payload, extract_proposal_json
from app.repository_service import RepositoryError


def test_context_requests_extract_and_validate_without_proposing_edits():
    payload = {"rationale": "Find callers first", "context_requests": [
        {"action": "search", "query": "  calculate_total  "},
        {"action": "read", "path": ".\\src\\pricing.py"},
    ]}
    proposal = _proposal_from_payload(extract_proposal_json(f"```json\n{json.dumps(payload)}\n```"))
    assert proposal.files == []
    assert proposal.rationale == "Find callers first"
    assert [(item.action, item.query, item.path) for item in proposal.context_requests] == [
        ("search", "calculate_total", None), ("read", None, "src/pricing.py"),
    ]


@pytest.mark.parametrize("context_request", [
    {"action": "search"}, {"action": "search", "query": " "},
    {"action": "search", "query": "x" * 501}, {"action": "search", "query": "x", "path": "a.py"},
    {"action": "read"}, {"action": "read", "path": "a.py", "query": "x"},
    {"action": "read", "path": "x" * 4097}, {"action": "execute", "query": "anything"},
    {"action": "read", "path": "a.py", "extra": True}, "a.py",
])
def test_invalid_context_requests_are_rejected(context_request):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"context_requests": [context_request]})
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("payload", [
    {}, {"files": []}, {"context_requests": []}, {"context_requests": None},
    {"context_requests": [{"action": "search", "query": "x"}] * 9},
    {"files": [{"path": "new.py", "operation": "create", "content": "x"}],
     "context_requests": [{"action": "read", "path": "a.py"}]},
])
def test_context_and_edit_phases_are_exclusive_and_bounded(payload):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(payload)
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("path", ["../app.py", "src/../app.py", "/app.py", "C:/app.py", "C:app.py", ".\\C:\\app.py", "\\\\server\\app.py", ".", "a\x00.py"])
def test_context_reads_and_file_edits_reject_unsafe_paths(path):
    for payload in (
        {"context_requests": [{"action": "read", "path": path}]},
        {"files": [{"path": path, "operation": "create", "content": "x"}]},
    ):
        with pytest.raises(RepositoryError) as error:
            _proposal_from_payload(payload)
        assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("path", ["./src/app.py", ".\\src\\app.py", pytest.param("SRC/APP.PY", marks=pytest.mark.skipif(os.name != "nt", reason="Windows path case"))])
def test_sequential_text_edits_preserve_large_file_and_resolve_context_aliases(path):
    prefix = "# unchanged\r\n" * 10_000
    original = prefix + "value = 1\r\n"
    proposal = _proposal_from_payload({"files": [{"path": path, "edits": [
        {"old_text": "value = 1", "new_text": "value = 2"},
        {"old_text": "value = 2", "new_text": "value = 3"},
    ]}]}, [("src/app.py", original)])
    assert proposal.files[0].path == "src/app.py"
    assert proposal.files[0].content == prefix + "value = 3\r\n"


@pytest.mark.parametrize("content,old_text", [("answer = 1", "missing"), ("same\nsame\n", "same"), ("aaa", "aa")])
def test_text_edits_require_one_unambiguous_match(content, old_text):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"files": [{"path": "a.py", "edits": [{"old_text": old_text, "new_text": "changed"}]}]}, [("a.py", content)])
    assert error.value.code == "MODEL_EDIT_CONFLICT"


@pytest.mark.parametrize("edit", [{"old_text": "", "new_text": "x"}, {"old_text": "a"}, {"old_text": "a", "new_text": 1}, "a"])
def test_invalid_text_replacements_are_rejected(edit):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"files": [{"path": "a.py", "edits": [edit]}]}, [("a.py", "a")])
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("item", [
    {"content": "b", "edits": [{"old_text": "a", "new_text": "b"}]},
    {"operation": "create", "edits": [{"old_text": "a", "new_text": "b"}]},
    {"operation": "delete", "edits": [{"old_text": "a", "new_text": "b"}]},
    {"edits": []}, {"edits": None},
])
def test_text_edits_cannot_mix_with_whole_content_or_create_delete(item):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"files": [{"path": "a.py", **item}]}, [("a.py", "a")])
    assert error.value.code == "MODEL_INVALID_OUTPUT"


def test_text_edit_limit_applies_across_files_and_allows_deleting_text():
    edits = [{"old_text": f"line {index}\n", "new_text": ""} for index in range(100)]
    payload = {"files": [{"path": "a.py", "edits": edits}]}
    context = [("a.py", "".join(edit["old_text"] for edit in edits)), ("b.py", "x")]
    assert _proposal_from_payload(payload, context).files[0].content == ""
    payload["files"].append({"path": "b.py", "edits": [{"old_text": "x", "new_text": "y"}]})
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(payload, context)
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("item", [
    {"operation": "update", "content": "x"}, {"operation": "delete"},
    {"edits": [{"old_text": "a", "new_text": "b"}]},
])
def test_existing_file_modifications_require_full_context(item):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"files": [{"path": "unread.py", **item}]}, [("other.py", "a")])
    assert error.value.code == "MODEL_CONTEXT_REQUIRED"


@pytest.mark.parametrize("provider", [AnthropicModelProvider, OpenAIModelProvider])
def test_providers_return_context_requests_then_expand_local_edits(monkeypatch, provider):
    replies = [
        {"context_requests": [{"action": "read", "path": "a.py"}]},
        {"files": [{"path": "a.py", "edits": [{"old_text": "return 1", "new_text": "return 2"}]}]},
    ]

    async def respond(_settings, url, headers, body):
        text = json.dumps(replies.pop(0))
        return {"content": [{"type": "text", "text": text}], "choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(mp, "_post_json", respond)
    model = provider(Settings(anthropic_api_key="test", openai_api_key="test"))
    request = asyncio.run(model.propose_patch("fix", [], None))
    assert request.context_requests[0].path == "a.py" and not request.files
    proposal = asyncio.run(model.propose_patch("fix", [("a.py", "def value():\n    return 1\n")], None))
    assert proposal.files[0].content == "def value():\n    return 2\n"
    assert not proposal.context_requests
