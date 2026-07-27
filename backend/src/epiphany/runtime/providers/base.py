from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    code = "provider_error"


class RetryableProviderError(ProviderError):
    code = "provider_temporarily_unavailable"


class ProviderTimeoutError(RetryableProviderError):
    code = "provider_timeout"


class ModelCallLimitExceeded(ProviderError):
    code = "model_call_limit_exceeded"


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
    estimated_cost_micros: int = 0
    cost_currency: str = "USD"

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("provider token usage cannot be negative")
        if self.estimated_cost_micros < 0:
            raise ValueError("provider estimated cost cannot be negative")
        if len(self.cost_currency) != 3 or not self.cost_currency.isalpha():
            raise ValueError("provider cost currency must be a three-letter code")


class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, invocation: TaskInvocation) -> ProviderResult: ...
