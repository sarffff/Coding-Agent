import asyncio
import json

import httpx
import pytest

from app import model_provider as mp
from app.config import Settings
from app.model_provider import AnthropicModelProvider, OpenAIModelProvider
from app.repository_service import RepositoryError

REAL_CLIENT = httpx.AsyncClient


def frame(payload):
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def install_stream(monkeypatch, text, status=200):
    requests = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            content = text.encode("utf-8")
            for index in range(0, len(content), 7):
                yield content[index:index + 7]

    def respond(request):
        requests.append(request)
        return httpx.Response(status, stream=Body(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(mp.httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    return requests


def chunks(provider, finish="stop"):
    if provider == "openai":
        return (frame({"choices": [{"index": 0, "delta": {"content": "你"}, "finish_reason": None}]})
                + frame({"choices": [{"index": 0, "delta": {"content": "好"}, "finish_reason": None}]})
                + (frame({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}) + "data: [DONE]\n\n" if finish else ""))
    return (frame({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你"}})
            + frame({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "好"}})
            + (frame({"type": "message_delta", "delta": {"stop_reason": finish}})
               + frame({"type": "message_stop"}) if finish else ""))


def provider_for(name):
    settings = Settings(_env_file=None, openai_api_key="test-key", anthropic_api_key="test-key")
    return OpenAIModelProvider(settings) if name == "openai" else AnthropicModelProvider(settings)


@pytest.mark.parametrize("name", ["openai", "anthropic"])
def test_real_provider_consumes_unicode_sse_deltas(monkeypatch, name):
    requests = install_stream(monkeypatch, chunks(name))

    async def collect():
        return [text async for text in provider_for(name).chat_stream([("user", "hello")], "context")]

    assert asyncio.run(collect()) == ["你", "好"]
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True
    assert len(requests) == 1
    assert payload["messages"][-1] == {"role": "user", "content": "hello"}


@pytest.mark.parametrize("name", ["openai", "anthropic"])
@pytest.mark.parametrize("truncated", [False, True])
def test_partial_or_truncated_provider_stream_is_not_successful(monkeypatch, name, truncated):
    reason = "length" if name == "openai" else "max_tokens"
    install_stream(monkeypatch, chunks(name, reason if truncated else None))
    received = []

    async def collect():
        async for text in provider_for(name).chat_stream([("user", "hello")]):
            received.append(text)

    with pytest.raises(RepositoryError) as error:
        asyncio.run(collect())
    assert error.value.code == ("MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_NETWORK_ERROR")
    assert received == ["你", "好"]


def test_done_marker_without_finish_reason_is_incomplete(monkeypatch):
    install_stream(monkeypatch, chunks("openai", None) + "data: [DONE]\n\n")

    async def collect():
        return [text async for text in provider_for("openai").chat_stream([("user", "hello")])]

    with pytest.raises(RepositoryError) as error:
        asyncio.run(collect())
    assert error.value.code == "MODEL_NETWORK_ERROR"


def test_anthropic_stop_event_requires_completion_reason(monkeypatch):
    install_stream(monkeypatch, chunks("anthropic", None) + frame({"type": "message_stop"}))

    async def collect():
        return [text async for text in provider_for("anthropic").chat_stream([("user", "hello")])]

    with pytest.raises(RepositoryError) as error:
        asyncio.run(collect())
    assert error.value.code == "MODEL_NETWORK_ERROR"


@pytest.mark.parametrize("status,code", [(401, "MODEL_AUTH_FAILED"), (429, "MODEL_RATE_LIMITED"), (502, "MODEL_ERROR")])
def test_provider_http_failures_map_to_existing_errors(monkeypatch, status, code):
    install_stream(monkeypatch, "", status)

    async def collect():
        return [text async for text in provider_for("openai").chat_stream([("user", "hello")])]

    with pytest.raises(RepositoryError) as error:
        asyncio.run(collect())
    assert error.value.code == code
