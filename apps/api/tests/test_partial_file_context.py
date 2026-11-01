import asyncio
import hashlib
import json

import pytest

from app import model_provider as mp
from app.config import Settings
from app.context_service import clone_context, esm_source_candidates, nested_instruction_paths, read_context
from app.model_provider import AnthropicModelProvider, ContextRequest, OpenAIModelProvider, _proposal_from_payload
from app.repository_service import RepositoryError


def partial(content, first=2, last=2):
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return read_context([], "src/app.py", content, digest, first, last)


def edit(old_text, new_text):
    return {"files": [{"path": "src/app.py", "edits": [{"old_text": old_text, "new_text": new_text}]}]}


def test_range_edit_targets_the_seen_duplicate_and_preserves_unread_crlf():
    original = "value = 1\r\nvalue = 1\r\nvalue = 1\r\n"
    context = partial(original)
    proposal = _proposal_from_payload(edit("value = 1", "value = 2"), context)
    assert context == [("src/app.py", "value = 1\r\n")]
    assert proposal.files[0].content == "value = 1\r\nvalue = 2\r\nvalue = 1\r\n"
    assert context.partial_files["src/app.py"].source_content == original


def test_range_offsets_preserve_multibyte_text_and_resolve_relative_path_aliases():
    context = partial("# 中文前缀\r\nvalue = 1\r\n# 中文后缀")
    payload = edit("value = 1", "value = 2")
    payload["files"][0]["path"] = ".\\src\\app.py"
    proposal = _proposal_from_payload(payload, context)
    assert proposal.files[0].path == "src/app.py"
    assert proposal.files[0].content == "# 中文前缀\r\nvalue = 2\r\n# 中文后缀"


@pytest.mark.parametrize("old_text", ["prefix", "suffix", "prefix\nvalue", "1\nsuffix"])
def test_range_edits_cannot_modify_unread_text_or_cross_a_boundary(old_text):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(edit(old_text, "changed"), partial("prefix\nvalue = 1\nsuffix\n"))
    assert error.value.code == "MODEL_EDIT_CONFLICT"


def test_sequential_range_edits_keep_their_offsets_after_length_changes():
    context = partial("prefix\r\nvalue = 1\r\nsuffix\r\n")
    payload = edit("value = 1", "value = 222\r\nextra = 3")
    payload["files"][0]["edits"].append({"old_text": "extra = 3", "new_text": "extra = 4"})
    proposal = _proposal_from_payload(payload, context)
    assert proposal.files[0].content == "prefix\r\nvalue = 222\r\nextra = 4\r\nsuffix\r\n"
    payload["files"][0]["edits"].append({"old_text": "suffix", "new_text": "changed"})
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(payload, context)
    assert error.value.code == "MODEL_EDIT_CONFLICT"


@pytest.mark.parametrize("item", [{"content": "replacement"}, {"operation": "delete"}])
def test_range_context_rejects_whole_file_replacement_and_deletion(item):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"files": [{"path": "src/app.py", **item}]}, partial("prefix\nvalue = 1\nsuffix\n"))
    assert error.value.code == "MODEL_CONTEXT_REQUIRED"


def test_range_edit_requires_unique_text_within_the_seen_lines():
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(edit("value = 1", "value = 2"), partial("prefix\nvalue = 1\nvalue = 1\nsuffix", 2, 3))
    assert error.value.code == "MODEL_EDIT_CONFLICT"


def test_range_prompt_exposes_only_the_slice_and_labels_instruction_scope():
    context = partial("unread_prefix\r\nvalue = 1\r\nunread_suffix\r\n")
    context.append(("src/AGENTS.md", "Use explicit imports."))
    prompt = mp._build_prompt("fix", context, None)
    assert "lines 2-2 of 3" in prompt and "CRLF" in prompt
    assert "unread_prefix" not in prompt and "unread_suffix" not in prompt
    assert "Instruction scope: src/ and its descendants." in prompt
    assert "content and delete are forbidden" in prompt


def test_context_copy_keeps_only_retained_metadata_and_a_read_replaces_the_slice():
    original = "prefix\nvalue = 1\nsuffix\n"
    context = partial(original)
    context.append(("other.py", "untouched"))
    assert "src/app.py" in clone_context(context).partial_files
    assert clone_context(context, [("other.py", "untouched")]).partial_files == {}
    source = context.partial_files["src/app.py"]
    replacement = read_context(context, "src/app.py", original, source.content_hash, 3, 3)
    assert dict(replacement)["src/app.py"] == "suffix\n"
    assert dict(context)["src/app.py"] == "value = 1\n"
    complete = read_context(replacement, "src/app.py", original, source.content_hash)
    assert complete.partial_files == {}
    assert _proposal_from_payload({"files": [{"path": "src/app.py", "content": "complete replacement"}]}, complete).files[0].content == "complete replacement"


@pytest.mark.parametrize("first,last", [(0, 1), (2, 1), (1, 4), (None, 1), (1, None)])
def test_read_context_rejects_nonexistent_or_incomplete_ranges(first, last):
    with pytest.raises(RepositoryError) as error:
        partial("first\nsecond\nthird", first, last)
    assert error.value.code == "FILE_RANGE_INVALID"


@pytest.mark.parametrize("context_request", [
    {"action": "read", "path": "a.py", "start_line": 1},
    {"action": "read", "path": "a.py", "end_line": 2},
    {"action": "read", "path": "a.py", "start_line": 2, "end_line": 1},
    {"action": "read", "path": "a.py", "start_line": True, "end_line": 2},
    {"action": "read", "path": "a.py", "start_line": "1", "end_line": 2},
    {"action": "read", "path": "a.py", "start_line": 0, "end_line": 2},
    {"action": "read", "path": "a.py", "start_line": 1, "end_line": 2, "content_hash": "forged"},
    {"action": "search", "query": "value", "start_line": 1, "end_line": 2},
    {"action": "run", "command": ["python"], "start_line": 1, "end_line": 2},
])
def test_model_range_request_validation_rejects_invalid_or_untrusted_fields(context_request):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload({"context_requests": [context_request]})
    assert error.value.code == "MODEL_INVALID_OUTPUT"
    assert ContextRequest(action="read", path="a.py", start_line=1, end_line=2).start_line == 1


def test_partial_metadata_must_match_the_complete_source_hash_and_visible_content():
    context = read_context([], "src/app.py", "prefix\nvalue = 1\nsuffix\n", "forged", 2, 2)
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(edit("value = 1", "value = 2"), context)
    assert error.value.code == "PATCH_CONFLICT"
    context = partial("prefix\nvalue = 1\nsuffix\n")
    context[0] = ("src/app.py", "different visible text")
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(edit("value = 1", "value = 2"), context)
    assert error.value.code == "PATCH_CONFLICT"


def test_nested_instruction_paths_include_only_the_target_ancestors_in_order():
    assert nested_instruction_paths("apps/api/src/app.py") == ["AGENTS.md", "apps/AGENTS.md", "apps/api/AGENTS.md", "apps/api/src/AGENTS.md"]
    assert nested_instruction_paths("src\\app.py") == ["AGENTS.md", "src/AGENTS.md"]
    assert nested_instruction_paths("app.py") == ["AGENTS.md"]
    assert nested_instruction_paths("../outside.py") == []
    assert nested_instruction_paths("C:/outside.py") == []


def test_esm_source_fallback_preserves_the_import_basename():
    assert esm_source_candidates("src/value.js") == ["src/value.ts", "src/value.tsx"]
    assert esm_source_candidates("src/view.jsx") == ["src/view.tsx", "src/view.ts"]
    assert esm_source_candidates("src/value.mjs") == ["src/value.mts"]
    assert esm_source_candidates("src/value.cjs") == ["src/value.cts"]
    assert esm_source_candidates("src/value.ts") == []


@pytest.mark.parametrize("provider", [AnthropicModelProvider, OpenAIModelProvider])
def test_providers_expand_range_edits_locally_without_sending_unread_content(monkeypatch, provider):
    original = "unread_prefix\nvalue = 1\nunread_suffix\n"

    async def respond(_settings, url, headers, body):
        prompt = json.dumps(body)
        assert "unread_prefix" not in prompt and "unread_suffix" not in prompt
        assert "lines 2-2 of 3" in prompt
        text = json.dumps(edit("value = 1", "value = 2"))
        return {"content": [{"type": "text", "text": text}], "choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(mp, "_post_json", respond)
    model = provider(Settings(_env_file=None, anthropic_api_key="test", openai_api_key="test"))
    proposal = asyncio.run(model.propose_patch("fix", partial(original), None))
    assert proposal.files[0].content == "unread_prefix\nvalue = 2\nunread_suffix\n"
