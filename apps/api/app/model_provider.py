from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import Settings
from .models import PatchFile
from .repository_service import RepositoryError


def _relative_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if not path.parts or path.is_absolute() or ".." in path.parts or PureWindowsPath(path.as_posix()).drive or "\x00" in value:
        raise ValueError("Paths must be repository-relative and cannot contain '..'.")
    return path.as_posix()


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["search", "read", "run"]
    query: str | None = Field(default=None, min_length=1, max_length=500)
    path: str | None = Field(default=None, min_length=1, max_length=4_096)
    command: list[str] | None = Field(default=None, min_length=1, max_length=32)
    start_line: int | None = Field(default=None, ge=1, strict=True)
    end_line: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def validate_action(self) -> ContextRequest:
        if self.start_line is not None or self.end_line is not None:
            if self.action != "read" or self.start_line is None or self.end_line is None or self.end_line < self.start_line:
                raise ValueError("Only read requests can select a range, with 1 <= start_line <= end_line.")
        if self.action == "run":
            if not self.command or self.query is not None or self.path is not None:
                raise ValueError("A run request requires a command and cannot include a query or path.")
            if not self.command[0].strip() or any(len(arg) > 1_000 or "\x00" in arg for arg in self.command):
                raise ValueError("Command arguments must be strings of at most 1000 characters with a nonempty program.")
            return self
        if self.command is not None:
            raise ValueError("Only run requests can include a command.")
        if self.action == "search":
            if not self.query or not self.query.strip() or self.path is not None:
                raise ValueError("A search request requires a query and cannot include a path.")
            self.query = self.query.strip()
        else:
            if self.path is None or self.query is not None:
                raise ValueError("A read request requires a path and cannot include a query.")
            self.path = _relative_path(self.path)
        return self


@dataclass(slots=True)
class PatchProposal:
    files: list[PatchFile] = field(default_factory=list)
    rationale: str = ""
    context_requests: list[ContextRequest] = field(default_factory=list)
    no_changes: bool = False


_SYSTEM_PROMPT = (
    "You are a coding agent that edits files in a user's repository. Given a goal and the "
    "current content of relevant files, produce the minimal set of edits that achieve it. "
    "First search and read more files if you need implementation, callers, or tests to understand the change. "
    "Respond with ONLY one JSON object: request context, propose edits, OR explicitly report no changes needed. "
    "To request context (at most 8 requests per response):\n"
    '{"context_requests": [{"action": "search", "query": "<literal text or filename>"}, '
    '{"action": "read", "path": "<repo-relative path>"}, '
    '{"action": "read", "path": "<large file path>", "start_line": 100, "end_line": 180}, '
    '{"action": "run", "command": ["<program>", "<arg>"]}]}\n'
    "`run` executes one program (no shell, repository root, time-limited) and returns its exit code and output; "
    "use it to reproduce a failure or run a single test. It may be unavailable. "
    "Commands you run do not count as validation; the required checks still run after your edits.\n"
    "To propose edits, no prose and no markdown fences:\n"
    '{"rationale": "<one short paragraph>", "files": [{"path": "<repo-relative path>", '
    '"operation": "create|update|delete", "content": "<COMPLETE new file content; omit for delete>"}]}\n'
    "For an update, prefer `edits` instead of `content`: "
    '[{"old_text": "<exact existing text>", "new_text": "<replacement text>"}]. '
    "Edits run in order; each nonempty old_text must match exactly once at that point. "
    "Use the smallest unique old_text without a newline for a single-line change. "
    "For multiline old_text in CRLF files, preserve the exact \\r\\n line endings. "
    "Return a complete JSON object, including all closing braces and brackets. "
    "Use at most 100 edits in total. Do not combine content and edits; create/delete cannot use edits. "
    "Rules: only touch files the goal needs; `content` is the whole file, never a diff; never "
    "write secrets or files under .env/keys/vendor/build dirs; keep the existing code style. "
    "Whole-file replacement or deletion requires the complete file contents in the context; request a read first otherwise. "
    "For large files, read a bounded line range. Only `update` with `edits` is allowed on a range: "
    "old_text must match uniquely inside the currently supplied range; never use content or delete. "
    "Reading another range replaces that file's previous range. Follow each AGENTS.md only within its directory and descendants. "
    "If the goal is already satisfied and no edits are necessary, respond with "
    '{"no_changes": true, "rationale": "<specific evidence that no edits are needed>"}. '
    "Do not combine no_changes with file edits or context requests. This only requests validation; "
    "required checks and human completion evidence still determine whether the task is complete. "
    "All paths must be repository-relative with no '..' components."
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
    parses as an object with a `files`, `context_requests`, or `no_changes` key. Uses json.raw_decode (not a brace counter)
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
        if isinstance(parsed, dict) and ("files" in parsed or "context_requests" in parsed or "no_changes" in parsed):
            return parsed
    raise RepositoryError("MODEL_INVALID_OUTPUT", "The model response did not contain a valid patch proposal.")

def _proposal_from_payload(payload: dict, context_files: list[tuple[str, str]] | None = None) -> PatchProposal:
    raw_files = payload.get("files", [])
    raw_requests = payload.get("context_requests", [])
    no_changes = payload.get("no_changes", False)
    if not isinstance(raw_files, list) or not isinstance(raw_requests, list) or (raw_files and raw_requests):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "Return either file edits or context requests, not both.")
    if not isinstance(no_changes, bool):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "no_changes must be a boolean.")
    rationale = payload.get("rationale")
    rationale = rationale if isinstance(rationale, str) else ""
    if no_changes:
        if raw_files or raw_requests or not rationale.strip():
            raise RepositoryError("MODEL_INVALID_OUTPUT", "An explicit no_changes reply requires a nonempty rationale and cannot include edits or context requests.")
        return PatchProposal(rationale=rationale, no_changes=True)
    if raw_requests:
        if len(raw_requests) > 8:
            raise RepositoryError("MODEL_INVALID_OUTPUT", "Request at most 8 context operations at a time.")
        try:
            requests = [ContextRequest.model_validate(item) for item in raw_requests]
        except ValidationError as exc:
            raise RepositoryError("MODEL_INVALID_OUTPUT", f"Invalid context request: {exc}") from exc
        return PatchProposal(rationale=rationale, context_requests=requests)
    if not raw_files:
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model proposal did not list any file edits.")
    context = {os.path.normcase(_relative_path(path)): (path, content) for path, content in context_files or []}
    partials = {os.path.normcase(_relative_path(path)): source for path, source in getattr(context_files, "partial_files", {}).items()}
    files: list[PatchFile] = []
    edit_count = 0
    for item in raw_files:
        if not isinstance(item, dict):
            raise RepositoryError("MODEL_INVALID_OUTPUT", "Each proposed edit must be a JSON object.")
        try:
            proposed = PatchFile.model_validate(
                {"path": item.get("path"), "operation": item.get("operation", "update"), "content": item.get("content")}
            )
            proposed.path = _relative_path(proposed.path)
        except (ValidationError, ValueError) as exc:
            raise RepositoryError("MODEL_INVALID_OUTPUT", f"Invalid proposed edit: {exc}") from exc
        baseline = context.get(os.path.normcase(proposed.path))
        partial = partials.get(os.path.normcase(proposed.path))
        if "edits" in item:
            edits = item["edits"]
            if proposed.operation != "update" or "content" in item or not isinstance(edits, list) or not edits:
                raise RepositoryError("MODEL_INVALID_OUTPUT", "Only updates can use a nonempty edits list, without content.")
            edit_count += len(edits)
            if edit_count > 100:
                raise RepositoryError("MODEL_INVALID_OUTPUT", "Propose at most 100 text edits at a time.")
        if proposed.operation != "create" and baseline is None:
            raise RepositoryError("MODEL_CONTEXT_REQUIRED", "Read the complete file before proposing changes.", {"path": proposed.path})
        if baseline is not None:
            proposed.path = _relative_path(baseline[0])
        if partial is not None and (hashlib.sha256(partial.source_content.encode("utf-8")).hexdigest() != partial.content_hash or
                                    baseline[1] != partial.source_content[partial.start:partial.end]):
            raise RepositoryError("PATCH_CONFLICT", "The partial context no longer matches its captured complete file.", {"path": proposed.path})
        if partial is not None and (proposed.operation != "update" or "edits" not in item):
            raise RepositoryError("MODEL_CONTEXT_REQUIRED", "A partially read file can only use text edits within its supplied line range. Read the complete file before replacing or deleting it.", {"path": proposed.path})
        if "edits" in item:
            content = partial.source_content if partial is not None else baseline[1]
            start, end = (partial.start, partial.end) if partial is not None else (0, len(content))
            for edit in edits:
                if not isinstance(edit, dict) or not isinstance(edit.get("old_text"), str) or not edit["old_text"] or not isinstance(edit.get("new_text"), str):
                    raise RepositoryError("MODEL_INVALID_OUTPUT", "Each text edit requires nonempty old_text and string new_text.")
                old_text = edit["old_text"]
                visible = content[start:end]
                match = visible.find(old_text)
                if match < 0 or match != visible.rfind(old_text):
                    if partial is not None:
                        raise RepositoryError("MODEL_EDIT_CONFLICT", "The text to replace must appear exactly once within the supplied line range. Read the required range first, use unique old_text, and preserve CRLF.", {"path": proposed.path})
                    raise RepositoryError("MODEL_EDIT_CONFLICT", "The text to replace must appear exactly once in the current file. Use the smallest unique old_text without a newline for a single-line change; preserve CRLF, or provide complete file content.", {"path": proposed.path})
                match += start
                content = content[:match] + edit["new_text"] + content[match + len(old_text):]
                end += len(edit["new_text"]) - len(old_text)
            proposed.content = content
        files.append(proposed)
    return PatchProposal(files=files, rationale=rationale)


def _build_prompt(goal: str, context_files: list[tuple[str, str]], failure: str | None) -> str:
    parts = [f"# Goal\n{goal.strip()}"]
    if failure:
        parts.append(f"# Latest test failure\n{failure.strip()}")
    if context_files:
        parts.append("# Current files")
        for path, content in context_files:
            note = "\nThis file contains CRLF (\\r\\n); preserve \\r\\n in multiline old_text." if "\r\n" in content else ""
            partial = getattr(context_files, "partial_files", {}).get(path)
            if partial is not None:
                note += (f"\nPartial file: lines {partial.start_line}-{partial.end_line} of {partial.total_lines}. "
                         "Only these lines are supplied. Use update with edits within this range; content and delete are forbidden.")
            if PurePosixPath(path).name == "AGENTS.md":
                note += f"\nInstruction scope: {PurePosixPath(path).parent.as_posix()}/ and its descendants."
            parts.append(f"## {path}{note}\n```\n{content}\n```")
    else:
        parts.append("# Current files\n(None supplied; search and read existing files before modifying them.)")
    return "\n\n".join(parts)


def _truncated() -> RepositoryError:
    return RepositoryError(
        "MODEL_OUTPUT_TRUNCATED",
        "The reply hit the output token limit. Use `edits` instead of whole-file `content`, or change fewer files at once.",
    )


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

    async def chat_stream(self, messages: list[tuple[str, str]], system: str | None = None) -> AsyncIterator[str]:
        # Providers without streaming return one complete reply; never simulate typing.
        yield await self.chat(messages, system)


# Backoff before the 2nd and 3rd attempts on 429/5xx (incl. 529 overloaded) or a dropped connection.
_RETRY_DELAYS: tuple[float, ...] = (2.0, 8.0)


async def _post_json(settings: Settings, url: str, headers: dict[str, str], payload: dict) -> dict:
    """One HTTP round-trip to a model API, with the retry and error mapping both real providers share."""
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        for delay in (*_RETRY_DELAYS, None):
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                # Not retried: a long generation that timed out would just time out again.
                raise RepositoryError("MODEL_TIMEOUT", "The model request did not finish in time.") from exc
            except httpx.RequestError as exc:
                if delay is None:
                    raise RepositoryError("MODEL_NETWORK_ERROR", f"Failed to reach the model API: {exc}") from exc
                await asyncio.sleep(delay)
                continue
            if delay is not None and (resp.status_code == 429 or resp.status_code >= 500):
                await asyncio.sleep(delay)
                continue
            break
    if resp.status_code == 401:
        raise RepositoryError("MODEL_AUTH_FAILED", "The model API key is invalid or missing.")
    if resp.status_code == 429:
        raise RepositoryError("MODEL_RATE_LIMITED", "The model API rate limit was exceeded.")
    if resp.status_code >= 400:
        raise RepositoryError("MODEL_ERROR", f"Model API error: {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API response must be a JSON object.")
    return data


def _anthropic_text(data: dict) -> str:
    reason = data.get("stop_reason")
    if reason == "max_tokens":
        raise _truncated()
    if reason not in (None, "end_turn", "stop_sequence"):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model could not complete the reply.")
    blocks = data.get("content")
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API returned invalid content blocks.")
    texts = [block.get("text") for block in blocks if block.get("type") == "text"]
    if any(not isinstance(text, str) for text in texts):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API returned invalid text content.")
    return "".join(texts)


def _openai_text(data: dict) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API returned invalid completion choices.")
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        raise _truncated()
    if choice.get("finish_reason") not in (None, "stop"):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model could not complete the reply.")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model API returned invalid text content.")
    return message["content"]


async def _stream_data(settings: Settings, url: str, headers: dict[str, str], payload: dict) -> AsyncIterator[str]:
    """Read complete SSE data frames; a failed partial response is never restarted."""
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            async with client.stream("POST", url, headers=headers, json={**payload, "stream": True}) as response:
                if response.status_code == 401:
                    raise RepositoryError("MODEL_AUTH_FAILED", "The model API key is invalid or missing.")
                if response.status_code == 429:
                    raise RepositoryError("MODEL_RATE_LIMITED", "The model API rate limit was exceeded.")
                if response.status_code >= 400:
                    raise RepositoryError("MODEL_ERROR", f"Model API error: {response.status_code}")
                data: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if data:
                            yield "\n".join(data)
                            data = []
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
                if data:
                    yield "\n".join(data)
    except httpx.TimeoutException as exc:
        raise RepositoryError("MODEL_TIMEOUT", "The model request did not finish in time.") from exc
    except httpx.RequestError as exc:
        raise RepositoryError("MODEL_NETWORK_ERROR", "The model stream was interrupted. Retry the request.") from exc


def _stream_json(data: str) -> dict:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model returned an invalid stream event.") from exc
    if not isinstance(parsed, dict):
        raise RepositoryError("MODEL_INVALID_OUTPUT", "The model returned an invalid stream event.")
    return parsed


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
        return _proposal_from_payload(extract_proposal_json(_anthropic_text(data)), context_files)

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
        return _anthropic_text(data).strip()

    async def chat_stream(self, messages: list[tuple[str, str]], system: str | None = None) -> AsyncIterator[str]:
        if not self.settings.anthropic_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_ANTHROPIC_API_KEY is not set.")
        finished = False
        stop_reason = None
        async for data in _stream_data(self.settings, f"{self.settings.anthropic_api_url.rstrip('/')}/v1/messages",
                                       {"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                       {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens,
                                        "system": _compose_chat_system(system), "messages": [{"role": role, "content": content} for role, content in messages]}):
            event = _stream_json(data)
            kind = event.get("type")
            if kind == "error":
                raise RepositoryError("MODEL_ERROR", "The model API reported a streaming error.")
            if kind == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason")
                if stop_reason == "max_tokens":
                    raise _truncated()
            if kind == "message_stop":
                finished = stop_reason is not None
                break
            block = event.get("delta", {}) if kind == "content_block_delta" else event.get("content_block", {}) if kind == "content_block_start" else {}
            content = block.get("text") if block.get("type") in {"text", "text_delta"} else None
            if isinstance(content, str) and content:
                yield content
        if not finished:
            raise RepositoryError("MODEL_NETWORK_ERROR", "The model stream ended before completion. Retry the request.")


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
        return _proposal_from_payload(extract_proposal_json(_openai_text(data)), context_files)

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
        return _openai_text(data).strip()

    async def chat_stream(self, messages: list[tuple[str, str]], system: str | None = None) -> AsyncIterator[str]:
        if not self.settings.openai_api_key:
            raise RepositoryError("MODEL_AUTH_FAILED", "FORGE_OPENAI_API_KEY is not set.")
        finished = False
        async for data in _stream_data(self.settings, f"{self.settings.openai_api_url.rstrip('/')}/chat/completions",
                                       {"Authorization": f"Bearer {self.settings.openai_api_key}", "content-type": "application/json"},
                                       {"model": self.settings.llm_model, "max_tokens": self.settings.llm_max_tokens,
                                        "messages": [{"role": "system", "content": _compose_chat_system(system)},
                                                     *[{"role": role, "content": content} for role, content in messages]]}):
            if data == "[DONE]":
                break
            event = _stream_json(data)
            if "error" in event:
                raise RepositoryError("MODEL_ERROR", "The model API reported a streaming error.")
            choices = event.get("choices") or []
            choice = next((item for item in choices if isinstance(item, dict) and item.get("index", 0) == 0), None)
            if not choice:
                continue
            reason = choice.get("finish_reason")
            if reason == "length":
                raise _truncated()
            if reason == "content_filter":
                raise RepositoryError("MODEL_INVALID_OUTPUT", "The model could not complete the reply.")
            if reason in {"stop", "tool_calls", "function_call"}:
                finished = True
            elif reason is not None:
                raise RepositoryError("MODEL_INVALID_OUTPUT", "The model returned an unknown completion reason.")
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str) and content:
                yield content
        if not finished:
            raise RepositoryError("MODEL_NETWORK_ERROR", "The model stream ended before completion. Retry the request.")


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
