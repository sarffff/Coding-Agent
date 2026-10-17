from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from .config import Settings
from .models import PatchFile
from .repository_service import RepositoryError


@dataclass(slots=True)
class PatchProposal:
    files: list[PatchFile]
    rationale: str = ""


_SYSTEM_PROMPT = (
    "You are a coding agent that edits files in a user's repository. Given a goal and the "
    "current content of relevant files, produce the minimal set of whole-file edits that "
    "achieve it. Respond with ONLY a single JSON object, no prose and no markdown fences:\n"
    '{"rationale": "<one short paragraph>", "files": [{"path": "<repo-relative path>", '
    '"operation": "create|update|delete", "content": "<COMPLETE new file content; omit for delete>"}]}\n'
    "Rules: only touch files the goal needs; `content` is the whole file, never a diff; never "
    "write secrets or files under .env/keys/vendor/build dirs; keep the existing code style. "
    "Only update or delete files whose complete contents are supplied in the context; otherwise only create new files."
)

_CHAT_SYSTEM_PROMPT = (
    "You are a helpful pair-programming assistant chatting with a developer. Answer conversationally "
    "in plain text (markdown is fine). You may be given a short summary of the user's local folder for "
    "context — reason about it, but you cannot edit files or run commands in this chat."
)


def _compose_chat_system(extra: str | None) -> str:
    """Base chat instructions, plus any per-conversation context (e.g. an attached folder summary)."""
    return f"{_CHAT_SYSTEM_PROMPT}\n\n{extra.strip()}" if extra and extra.strip() else _CHAT_SYSTEM_PROMPT


def extract_proposal_json(text: str) -> dict:
    """Pull the proposal JSON object out of a model reply.

    Tolerates ```json fences and surrounding prose by decoding the first `{...}` that
    parses as an object with a `files` key. Uses json.raw_decode (not a brace counter)
    so braces inside the generated file content never confuse the span boundaries.
    """
    if not text or not text.strip():
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model returned an empty response.")
    decoder = json.JSONDecoder()
    starts = [match.start() for match in re.finditer(r"\{", text)][:50]
    for index in starts:
        try:
            parsed, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "files" in parsed:
            return parsed
    raise RepositoryError("MODEL_INVALID_OUTPUT", "The model response did not contain a valid patch proposal.")

def _proposal_from_payload(payload: dict) -> PatchProposal:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model proposal did not list any file edits.")
    files: list[PatchFile] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise RepositoryError("MODEL_INVALID_OUTPUT", "Each proposed edit must be a JSON object.")
        try:
            files.append(PatchFile.model_validate(
                {"path": item.get("path"), "operation": item.get("operation", "update"), "content": item.get("content")}
            ))
        except Exception as exc:  # pydantic validation of the untrusted model output
            raise RepositoryError("MODEL_INVALID_OUTPUT", f"Invalid proposed edit: {exc}") from exc
    rationale = payload.get("rationale")
    return PatchProposal(files=files, rationale=rationale if isinstance(rationale, str) else "")


def _build_prompt(goal: str, context_files: list[tuple[str, str]], failure: str | None) -> str:
    parts = [f"# Goal\n{goal.strip()}"]
    if failure:
        parts.append(f"# Latest test failure\n{failure.strip()}")
    if context_files:
        parts.append("# Current files")
        parts += [f"## {path}\n```\n{content}\n```" for path, content in context_files]
    else:
        parts.append("# Current files\n(None supplied; create the files the goal needs.)")
    return "\n\n".join(parts)


class ModelProvider(ABC):
    """Abstract interface for the code-drafting model backend."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    async def propose_patch(self, goal: str, context_files: list[tuple[str, str]], failure: str | None) -> PatchProposal:
        ...

    @abstractmethod
    async def chat(self, messages: list[tuple[str, str]], system: str | None = None) -> str:
        """Plain conversational turn: (role, content) history in, assistant text out. No file edits."""
        ...


async def _post_json(settings: Settings, url: str, headers: dict[str, str], payload: dict) -> dict:
    """One HTTP round-trip to a model API, with the error mapping both real providers share."""
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise RepositoryError("MODEL_TIMEOUT", "The model request did not finish in time.") from exc
        except httpx.RequestError as exc:
            raise RepositoryError("MODEL_NETWORK_ERROR", f"Failed to reach the model API: {exc}") from exc
    if resp.status_code == 401:
        raise RepositoryError("MODEL_AUTH_FAILED", "The model API key is invalid or missing.")
    if resp.status_code == 429:
        raise RepositoryError("MODEL_RATE_LIMITED", "The model API rate limit was exceeded.")
    if resp.status_code >= 400:
        raise RepositoryError("MODEL_ERROR", f"Model API error: {resp.status_code}")
    return resp.json()


class AnthropicModelProvider(ModelProvider):
    """Anthropic Messages API over httpx — no extra SDK dependency."""

    async def propose_patch(self, goal: str, context_files: list[tuple[str, str]], failure: str | None) -> PatchProposal:
        if not self.settings.anthropic_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_ANTHROPIC_API_KEY is not set.")
        data = await _post_json(
            self.settings,
            f"{self.settings.anthropic_api_url.rstrip('/')}/v1/messages",
            {"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens, "system": _SYSTEM_PROMPT,
             "messages": [{"role": "user", "content": _build_prompt(goal, context_files, failure)}]},
        )
        blocks = data.get("content") or []
        if data.get("stop_reason") == "max_tokens":
            raise RepositoryError("MODEL_OUTPUT_TRUNCATED", "The model reached its output limit. Reduce the edit scope or increase FORGE_LLM_MAX_TOKENS before retrying.")
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
        return _proposal_from_payload(extract_proposal_json(text))

    async def chat(self, messages: list[tuple[str, str]], system: str | None = None) -> str:
        if not self.settings.anthropic_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_ANTHROPIC_API_KEY is not set.")
        data = await _post_json(
            self.settings,
            f"{self.settings.anthropic_api_url.rstrip('/')}/v1/messages",
            {"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens, "system": _compose_chat_system(system),
             "messages": [{"role": role, "content": content} for role, content in messages]},
        )
        blocks = data.get("content") or []
        return "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text").strip()


class OpenAIModelProvider(ModelProvider):
    """OpenAI (and OpenAI-compatible) Chat Completions API over httpx.

    Point FORGE_OPENAI_API_URL at any OpenAI-compatible server; set FORGE_LLM_MODEL to a
    model that server serves. JSON-only output is enforced by the prompt (no response_format,
    so OpenAI-compatible servers that lack it still work); extract_proposal_json tolerates fences.
    """

    async def propose_patch(self, goal: str, context_files: list[tuple[str, str]], failure: str | None) -> PatchProposal:
        if not self.settings.openai_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_OPENAI_API_KEY is not set.")
        data = await _post_json(
            self.settings,
            f"{self.settings.openai_api_url.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {self.settings.openai_api_key}", "content-type": "application/json"},
            {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens,
             "messages": [{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": _build_prompt(goal, context_files, failure)}]},
        )
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason") == "length":
            raise RepositoryError("MODEL_OUTPUT_TRUNCATED", "The model reached its output limit. Reduce the edit scope or increase FORGE_LLM_MAX_TOKENS before retrying.")
        text = choices[0].get("message", {}).get("content", "") if choices and isinstance(choices[0], dict) else ""
        return _proposal_from_payload(extract_proposal_json(text))

    async def chat(self, messages: list[tuple[str, str]], system: str | None = None) -> str:
        if not self.settings.openai_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_OPENAI_API_KEY is not set.")
        payload_messages = [{"role": "system", "content": _compose_chat_system(system)}]
        payload_messages += [{"role": role, "content": content} for role, content in messages]
        data = await _post_json(
            self.settings,
            f"{self.settings.openai_api_url.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {self.settings.openai_api_key}", "content-type": "application/json"},
            {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens, "messages": payload_messages},
        )
        choices = data.get("choices") or []
        return (choices[0].get("message", {}).get("content", "") if choices and isinstance(choices[0], dict) else "").strip()


class FakeModelProvider(ModelProvider):
    """Deterministic in-memory provider for offline tests and demos.

    Seed proposals ahead of time; propose_patch returns them FIFO. An unseeded call
    raises MODEL_NO_RESPONSE — the fake is a test double, not a real model. State is
    class-level (mirrors FakeRemoteProvider); reset() clears it between tests.
    """

    _queue: list[PatchProposal] = []
    _calls: list[tuple[str, list[tuple[str, str]], str | None]] = []
    _chat_queue: list[str] = []
    _chat_calls: list[tuple[list[tuple[str, str]], str | None]] = []

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.queue = FakeModelProvider._queue

    @classmethod
    def reset(cls) -> None:
        cls._queue.clear()
        cls._calls.clear()
        cls._chat_queue.clear()
        cls._chat_calls.clear()

    @classmethod
    def seed(cls, files: list[PatchFile], rationale: str = "fake proposal") -> None:
        cls._queue.append(PatchProposal(files=list(files), rationale=rationale))

    @classmethod
    def seed_chat(cls, reply: str) -> None:
        cls._chat_queue.append(reply)

    async def propose_patch(self, goal: str, context_files: list[tuple[str, str]], failure: str | None) -> PatchProposal:
        FakeModelProvider._calls.append((goal, context_files, failure))
        if not self.queue:
            raise RepositoryError("MODEL_NO_RESPONSE", "No fake model proposal was seeded.")
        return self.queue.pop(0)

    async def chat(self, messages: list[tuple[str, str]], system: str | None = None) -> str:
        FakeModelProvider._chat_calls.append((list(messages), system))
        if FakeModelProvider._chat_queue:
            return FakeModelProvider._chat_queue.pop(0)
        last_user = next((content for role, content in reversed(messages) if role == "user"), "")
        return f"(fake reply) {last_user}"[:2000]


def build_model_provider(settings: Settings, provider: ModelProvider | None = None) -> ModelProvider:
    if provider is not None:
        return provider
    if settings.llm_provider == "fake":
        return FakeModelProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIModelProvider(settings)
    return AnthropicModelProvider(settings)
