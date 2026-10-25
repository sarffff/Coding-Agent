from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable

from .config import Settings
from .model_provider import ModelProvider, build_model_provider
from .models import ConfigResponse, ConfigUpdateRequest, ModelProfile, ModelProfileView
from .repository_service import RepositoryError

logger = logging.getLogger(__name__)

# Everything a human can change from the settings page. Retention and budget limits stay env-only
# on purpose: they are constructor arguments of services built once at startup, so accepting an
# edit for them would silently do nothing.
EDITABLE_FIELDS = (
    "llm_provider",
    "llm_model",
    "anthropic_api_key",
    "openai_api_key",
    "anthropic_api_url",
    "openai_api_url",
    "llm_max_tokens",
    "llm_timeout_seconds",
    "llm_max_context_files",
    "llm_max_context_bytes",
    "command_timeout_seconds",
)


MODEL_LIST_KEY = "models"
ACTIVE_MODEL_KEY = "active_model_id"


def apply_model_profile(settings: Settings, profile: ModelProfile) -> None:
    """Point the flat settings at one saved profile; empty base_url/key leave the current value."""
    settings.llm_provider = profile.provider
    settings.llm_model = profile.model
    if profile.provider == "openai":
        if profile.base_url:
            settings.openai_api_url = profile.base_url
        if profile.api_key is not None:
            settings.openai_api_key = profile.api_key
    elif profile.provider == "anthropic":
        if profile.base_url:
            settings.anthropic_api_url = profile.base_url
        if profile.api_key is not None:
            settings.anthropic_api_key = profile.api_key


def _load_saved_models(path: Path) -> tuple[list[ModelProfile], str | None]:
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    models: list[ModelProfile] = []
    for item in data.get(MODEL_LIST_KEY) or []:
        try:
            models.append(ModelProfile(**item))
        except (TypeError, ValueError):
            logger.warning("Skipping an invalid saved model entry in %s", path)
    active = data.get(ACTIVE_MODEL_KEY)
    return models, active if isinstance(active, str) else None


def overrides_path(settings: Settings) -> Path:
    return (settings.state_dir / "config.json").resolve()


def apply_overrides(settings: Settings) -> list[str]:
    """Apply saved overrides before the services are built.

    A corrupt or unreadable file must not stop the API from starting: the env file is still a
    complete configuration, the override layer is only a delta on top of it.
    """
    path = overrides_path(settings)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable config overrides at %s: %s", path, exc)
        return []
    if not isinstance(data, dict):
        return []
    applied: list[str] = []
    for name in EDITABLE_FIELDS:
        value = data.get(name)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            setattr(settings, name, value)
            applied.append(name)
    models, active = _load_saved_models(path)
    profile = next((item for item in models if item.id == active), None)
    if profile is not None:
        apply_model_profile(settings, profile)
        applied.append(ACTIVE_MODEL_KEY)
    return applied


class ConfigService:
    """Reads and updates the runtime configuration; the API keys never leave this object."""

    def __init__(self, settings: Settings, *, on_provider_change: Callable[[ModelProvider], None] | None = None) -> None:
        self.settings = settings
        self._on_provider_change = on_provider_change
        self._restart_required = False
        self._models, self._active_model_id = _load_saved_models(overrides_path(settings))

    def describe(self) -> ConfigResponse:
        s = self.settings
        return ConfigResponse(
            provider=s.llm_provider,
            model=s.llm_model,
            anthropic_api_url=s.anthropic_api_url,
            openai_api_url=s.openai_api_url,
            anthropic_key_configured=bool(s.anthropic_api_key),
            openai_key_configured=bool(s.openai_api_key),
            workspace_root=str(s.workspace_root),
            state_dir=str(s.state_dir),
            overrides_path=str(overrides_path(s)),
            command_timeout_seconds=s.command_timeout_seconds,
            llm_timeout_seconds=s.llm_timeout_seconds,
            llm_max_tokens=s.llm_max_tokens,
            llm_max_context_files=s.llm_max_context_files,
            llm_max_context_bytes=s.llm_max_context_bytes,
            task_max_iterations=s.task_max_iterations,
            task_max_wall_time_seconds=s.task_max_wall_time_seconds,
            task_max_remote_calls=s.task_max_remote_calls,
            task_max_consecutive_failures=s.task_max_consecutive_failures,
            audit_retention_days=s.audit_retention_days,
            test_output_retention_days=s.test_output_retention_days,
            restart_required=self._restart_required,
            models=[
                ModelProfileView(id=item.id, label=item.label, provider=item.provider, model=item.model,
                                 base_url=item.base_url, api_key_configured=bool(item.api_key))
                for item in self._models
            ],
            active_model_id=self._active_model_id,
        )

    def update(self, payload: ConfigUpdateRequest) -> ConfigResponse:
        changes = payload.model_dump(exclude_none=True)
        models = changes.pop(MODEL_LIST_KEY, None)
        active = changes.pop(ACTIVE_MODEL_KEY, None)
        if not changes and models is None and active is None:
            raise RepositoryError("CONFIG_EMPTY", "No setting was provided.")
        for name, value in changes.items():
            if isinstance(value, str) and not value.strip() and name in {"anthropic_api_url", "openai_api_url"}:
                raise RepositoryError("CONFIG_INVALID_VALUE", f"{name} cannot be empty.", {"field": name})

        previous_provider = self.settings.llm_provider
        for name, value in changes.items():
            setattr(self.settings, name, value)

        saved: dict[str, object] = dict(changes)
        if models is not None:
            # The UI never sees stored keys, so an entry without one keeps the key it already had.
            # Sending an explicit empty string is how a key gets cleared.
            existing_keys = {item.id: item.api_key for item in self._models}
            incoming = [ModelProfile(**item) for item in models]
            for item in incoming:
                if item.api_key is None and item.id in existing_keys:
                    item.api_key = existing_keys[item.id]
            self._models = incoming
            saved[MODEL_LIST_KEY] = [item.model_dump() for item in self._models]
            if self._active_model_id not in {item.id for item in self._models}:
                # The active entry was removed (or renamed): fall back to the flat settings.
                self._active_model_id = None
                saved[ACTIVE_MODEL_KEY] = None
        if active is not None:
            if active not in {item.id for item in self._models}:
                raise RepositoryError("CONFIG_INVALID_VALUE", "The selected model is not in the saved list.", {"field": ACTIVE_MODEL_KEY})
            self._active_model_id = active
            saved[ACTIVE_MODEL_KEY] = active

        self._persist(saved)

        profile = next((item for item in self._models if item.id == self._active_model_id), None)
        if profile is not None:
            apply_model_profile(self.settings, profile)
        if profile is not None or self.settings.llm_provider != previous_provider:
            if self._on_provider_change is not None:
                # The provider object is built from settings once; swap it so the change takes effect now.
                self._on_provider_change(build_model_provider(self.settings))
                self._restart_required = False
            else:
                self._restart_required = True
        return self.describe()

    def _persist(self, changes: dict[str, object]) -> None:
        path = overrides_path(self.settings)
        existing: dict[str, object] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing.update({name: value for name, value in changes.items()
                         if name in EDITABLE_FIELDS or name in (MODEL_LIST_KEY, ACTIVE_MODEL_KEY)})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            # The file holds API keys; keep it owner-only where the platform honours the mode.
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)
