"""AI Provider capability registry; health discovery never sends model requests."""
from __future__ import annotations
from .user_agent import USER_AGENT

import shutil
import json
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable

import httpx

from .http import HttpRequest, HttpTransport, HttpxTransport


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"


class ProviderUnavailable(RuntimeError):
    pass


class OpenCodeGoClient:
    """OpenCode Go public model discovery; completion protocols remain separate."""

    def __init__(self, api_key: str | None = None, *, base_url: str = OPENCODE_GO_BASE_URL,
                 timeout: float = 10.0,
                 transport: HttpTransport | None = None,
                 cache_ttl: float = 300.0):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or HttpxTransport()
        self.cache_ttl = cache_ttl
        self._cache: tuple[float, list[dict]] | None = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def list_models(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            if self._cache and now - self._cache[0] < self.cache_ttl:
                return [dict(item) for item in self._cache[1]]
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        request = HttpRequest("GET", self.base_url + "/models", headers)
        try:
            response = self.transport(request, self.timeout, 1024 * 1024)
            if response.status != 200:
                raise ProviderUnavailable(
                    f"OpenCode Go returned HTTP {response.status}"
                )
            payload = json.loads(response.body.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, httpx.HTTPError) as exc:
            raise ProviderUnavailable("OpenCode Go model discovery failed") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ProviderUnavailable("OpenCode Go returned an invalid model list")
        models = []
        for item in data:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            models.append({
                "id": str(item["id"]),
                "object": str(item.get("object") or "model"),
                "owned_by": str(item.get("owned_by") or "opencode"),
            })
        with self._lock:
            self._cache = (now, models)
        return [dict(item) for item in models]


class ProviderKind(str, Enum):
    INFERENCE = "inference"
    AGENT = "agent"


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool = False
    structured_output: bool = False
    tool_use: bool = False
    sessions: bool = False
    cancellation: bool = False


@dataclass(frozen=True)
class ProviderStatus:
    id: str
    kind: ProviderKind
    provider: str
    auth_mode: str
    available: bool
    configured: bool | None
    experimental: bool
    capabilities: ProviderCapabilities
    note: str = ""

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "provider": self.provider,
            "auth_mode": self.auth_mode,
            "available": self.available,
            "configured": self.configured,
            "experimental": self.experimental,
            "capabilities": asdict(self.capabilities),
            "note": self.note,
        }


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderStatus] = {}

    def register(self, status: ProviderStatus) -> None:
        if status.id in self._providers:
            raise ValueError(f"duplicate provider id: {status.id}")
        self._providers[status.id] = status

    def health(self) -> dict:
        providers = [self._providers[key].public_dict() for key in sorted(self._providers)]
        return {"ok": True, "providers": providers}


def default_registry(
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ProviderStatus(
        id="opencode-go",
        kind=ProviderKind.INFERENCE,
        provider="opencode-go",
        auth_mode="api-key",
        available=True,
        configured=False,
        experimental=False,
        capabilities=ProviderCapabilities(streaming=True, structured_output=True),
        note="Direct HTTP adapter supported; API key is not configured in phase one.",
    ))
    registry.register(ProviderStatus(
        id="codex-local",
        kind=ProviderKind.AGENT,
        provider="codex",
        auth_mode="vendor-managed-oauth",
        available=executable_finder("codex") is not None,
        configured=None,
        experimental=False,
        capabilities=ProviderCapabilities(
            streaming=True, structured_output=True, tool_use=True,
            sessions=True, cancellation=True,
        ),
        note="Authentication remains owned by the Codex runtime.",
    ))
    registry.register(ProviderStatus(
        id="claude-code-local-personal",
        kind=ProviderKind.AGENT,
        provider="claude-code",
        auth_mode="vendor-managed-oauth",
        available=executable_finder("claude") is not None,
        configured=None,
        experimental=True,
        capabilities=ProviderCapabilities(
            streaming=True, structured_output=True, tool_use=True,
            sessions=True, cancellation=True,
        ),
        note="Personal-local experimental adapter; Peach never reads Claude credentials.",
    ))
    return registry
