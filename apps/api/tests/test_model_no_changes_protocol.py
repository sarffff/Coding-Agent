import asyncio
import json

import pytest

from app import model_provider as mp
from app.config import Settings
from app.model_provider import AnthropicModelProvider, OpenAIModelProvider, _proposal_from_payload, extract_proposal_json
from app.repository_service import RepositoryError


def test_explicit_no_changes_returns_evidence_without_a_patch():
    payload = {"no_changes": True, "rationale": "The requested guard already rejects empty input."}
    assert extract_proposal_json(json.dumps(payload)) == payload
    proposal = _proposal_from_payload(payload)
    assert proposal.no_changes and not proposal.files and not proposal.context_requests
    assert proposal.rationale == payload["rationale"]


@pytest.mark.parametrize("payload", [
    {"no_changes": True}, {"no_changes": True, "rationale": "  "},
    {"no_changes": True, "rationale": 1}, {"no_changes": "true", "rationale": "done"},
    {"no_changes": 1, "rationale": "done"}, {"no_changes": False, "rationale": "done"},
    {"no_changes": True, "rationale": "done", "files": [{"path": "a.py", "operation": "create", "content": "x"}]},
    {"no_changes": True, "rationale": "done", "context_requests": [{"action": "read", "path": "a.py"}]},
])
def test_no_changes_requires_an_explicit_boolean_evidence_and_an_exclusive_phase(payload):
    with pytest.raises(RepositoryError) as error:
        _proposal_from_payload(payload)
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("provider", [AnthropicModelProvider, OpenAIModelProvider])
def test_both_providers_support_a_no_changes_reply(monkeypatch, provider):
    async def respond(_settings, url, headers, body):
        text = json.dumps({"no_changes": True, "rationale": "Existing implementation already meets the goal; run required checks."})
        return {"content": [{"type": "text", "text": text}], "choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(mp, "_post_json", respond)
    model = provider(Settings(_env_file=None, anthropic_api_key="test", openai_api_key="test"))
    proposal = asyncio.run(model.propose_patch("verify", [("a.py", "value = 2\n")], None))
    assert proposal.no_changes
    assert "required checks and human completion evidence" in mp._SYSTEM_PROMPT
