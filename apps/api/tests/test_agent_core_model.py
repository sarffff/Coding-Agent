import asyncio
import json

import httpx
import pytest

from app import model_provider as mp
from app.config import Settings
from app.repository_service import RepositoryError


_REAL_ASYNC_CLIENT = httpx.AsyncClient
_PROPOSAL = json.dumps({"files": [{"path": "new.py", "operation": "create", "content": "value = 2\n"}]})


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>upstream unavailable</html>"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json="unexpected"),
    httpx.Response(200, content=b"null"),
])
def test_successful_http_response_still_requires_a_json_object(monkeypatch, response):
    monkeypatch.setattr(mp.httpx, "AsyncClient", lambda **kwargs: _REAL_ASYNC_CLIENT(
        transport=httpx.MockTransport(lambda request: response), **kwargs,
    ))
    with pytest.raises(RepositoryError) as error:
        asyncio.run(mp._post_json(Settings(_env_file=None), "https://model.invalid/api", {}, {}))
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("provider,response", [
    (mp.OpenAIModelProvider, {"choices": "invalid"}),
    (mp.OpenAIModelProvider, {"choices": [{"message": None}]}),
    (mp.OpenAIModelProvider, {"choices": [{"message": {"content": 42}}]}),
    (mp.AnthropicModelProvider, {"content": "invalid"}),
    (mp.AnthropicModelProvider, {"content": [{"type": "text", "text": None}]}),
])
@pytest.mark.parametrize("operation", ["propose_patch", "chat"])
def test_malformed_response_content_is_a_retryable_protocol_error(monkeypatch, provider, response, operation):
    async def respond(*args):
        return response

    monkeypatch.setattr(mp, "_post_json", respond)
    model = provider(Settings(_env_file=None, anthropic_api_key="test", openai_api_key="test"))
    call = model.propose_patch("fix", [], None) if operation == "propose_patch" else model.chat([("user", "fix")])
    with pytest.raises(RepositoryError) as error:
        asyncio.run(call)
    assert error.value.code == "MODEL_INVALID_OUTPUT"


@pytest.mark.parametrize("provider,response", [
    (mp.OpenAIModelProvider, {"choices": [{"finish_reason": reason, "message": {"content": _PROPOSAL}}]})
    for reason in ("content_filter", "tool_calls", "function_call", "unexpected")
] + [
    (mp.AnthropicModelProvider, {"stop_reason": reason, "content": [{"type": "text", "text": _PROPOSAL}]})
    for reason in ("refusal", "pause_turn", "tool_use", "unexpected")
])
def test_unfinished_completion_cannot_become_a_patch(monkeypatch, provider, response):
    async def respond(*args):
        return response

    monkeypatch.setattr(mp, "_post_json", respond)
    model = provider(Settings(_env_file=None, anthropic_api_key="test", openai_api_key="test"))
    with pytest.raises(RepositoryError) as error:
        asyncio.run(model.propose_patch("fix", [], None))
    assert error.value.code == "MODEL_INVALID_OUTPUT"
