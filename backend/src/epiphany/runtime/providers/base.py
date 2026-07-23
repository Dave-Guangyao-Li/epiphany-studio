from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    code = "provider_error"


class RetryableProviderError(ProviderError):
    code = "provider_temporarily_unavailable"


@dataclass(frozen=True, slots=True)
class TaskInvocation:
    task_id: str
    run_id: str
    kind: str
    attempt: int
    input_json: dict[str, Any]
    lease_token: str


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    async def generate(self, invocation: TaskInvocation) -> ProviderResult: ...
