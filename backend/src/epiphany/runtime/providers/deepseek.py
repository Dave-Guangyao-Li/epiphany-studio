from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from epiphany.runtime.providers.base import (
    ProviderAuthenticationError,
    ProviderContentFilteredError,
    ProviderError,
    ProviderInsufficientBalanceError,
    ProviderInvalidRequestError,
    ProviderNetworkError,
    ProviderOutputTruncatedError,
    ProviderOverloadedError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderResult,
    ProviderServerError,
    ProviderTimeoutError,
    TaskInvocation,
)
from epiphany.runtime.research_prompts import build_research_prompt

logger = logging.getLogger("epiphany.provider.deepseek")

DEFAULT_BASE_URL = "https://api.deepseek.com"
SUPPORTED_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})
SUPPORTED_BILLING_CURRENCIES = frozenset({"CNY", "USD"})
BillingCurrency = Literal["CNY", "USD"]

# Official prices per one million tokens, checked on 2026-07-28.
_PRICE_PER_MILLION_TOKENS = {
    "CNY": {
        "deepseek-v4-flash": {
            "input_cache_hit": Decimal("0.02"),
            "input_cache_miss": Decimal("1"),
            "output": Decimal("2"),
        },
        "deepseek-v4-pro": {
            "input_cache_hit": Decimal("0.025"),
            "input_cache_miss": Decimal("3"),
            "output": Decimal("6"),
        },
    },
    "USD": {
        "deepseek-v4-flash": {
            "input_cache_hit": Decimal("0.0028"),
            "input_cache_miss": Decimal("0.14"),
            "output": Decimal("0.28"),
        },
        "deepseek-v4-pro": {
            "input_cache_hit": Decimal("0.003625"),
            "input_cache_miss": Decimal("0.435"),
            "output": Decimal("0.87"),
        },
    },
}


class DeepSeekProvider:
    """Small OpenAI-compatible DeepSeek adapter with no hidden retries."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        billing_currency: BillingCurrency | str = "USD",
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = 2_000,
        max_source_chars: int = 24_000,
        request_timeout_seconds: float = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key must not be blank")
        if model not in SUPPORTED_MODELS:
            raise ValueError(f"unsupported DeepSeek model: {model}")
        normalized_billing_currency = billing_currency.strip().upper()
        if normalized_billing_currency not in SUPPORTED_BILLING_CURRENCIES:
            raise ValueError(
                "DeepSeek billing currency must be one of: "
                f"{', '.join(sorted(SUPPORTED_BILLING_CURRENCIES))}"
            )
        if max_tokens < 1:
            raise ValueError("DeepSeek max_tokens must be positive")
        if max_source_chars < 1:
            raise ValueError("DeepSeek max_source_chars must be positive")
        if request_timeout_seconds <= 0:
            raise ValueError("DeepSeek request timeout must be positive")

        self.api_key = api_key
        self.model = model
        self.billing_currency = normalized_billing_currency
        self.base_url = _validated_base_url(base_url)
        self.max_tokens = max_tokens
        self.max_source_chars = max_source_chars
        self.request_timeout_seconds = request_timeout_seconds
        self._client = client

    async def generate(self, invocation: TaskInvocation) -> ProviderResult:
        prompt = build_research_prompt(
            task_kind=invocation.kind,
            task_input=invocation.input_json,
            max_source_chars=self.max_source_chars,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": prompt.messages,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": self.max_tokens,
            "stream": False,
            "temperature": 0.2,
            "user_id": invocation.run_id,
        }
        logger.info(
            "DeepSeek request started",
            extra={
                "event": "provider.deepseek.request.started",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "provider": self.name,
                "model": self.model,
                "source_segment_count": prompt.source_segment_count,
                "source_char_count": prompt.source_char_count,
            },
        )

        started_at = perf_counter()
        try:
            response = await self._post(payload)
            if response.status_code >= 400:
                raise _error_for_status(response.status_code)
            result = _parse_response(
                response,
                requested_model=self.model,
                billing_currency=self.billing_currency,
            )
        except httpx.TimeoutException as error:
            mapped_error: ProviderError = ProviderTimeoutError(
                "DeepSeek request exceeded its HTTP timeout"
            )
            self._log_failure(invocation, mapped_error, started_at=started_at)
            raise mapped_error from error
        except httpx.RequestError as error:
            mapped_error = ProviderNetworkError("DeepSeek request failed before a response")
            self._log_failure(invocation, mapped_error, started_at=started_at)
            raise mapped_error from error
        except ProviderError as error:
            self._log_failure(invocation, error, started_at=started_at)
            raise

        logger.info(
            "DeepSeek response accepted",
            extra={
                "event": "provider.deepseek.request.completed",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "provider": result.provider,
                "model": result.model,
                "status": "succeeded",
                "duration_ms": max(0, round((perf_counter() - started_at) * 1000)),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "estimated_cost_micros": result.estimated_cost_micros,
                "cost_currency": result.cost_currency,
            },
        )
        return result

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        if self._client is not None:
            return await self._client.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.request_timeout_seconds,
            )
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            return await client.post(url, headers=headers, json=payload)

    def _log_failure(
        self,
        invocation: TaskInvocation,
        error: ProviderError,
        *,
        started_at: float,
    ) -> None:
        logger.warning(
            "DeepSeek request failed",
            extra={
                "event": "provider.deepseek.request.failed",
                "run_id": invocation.run_id,
                "task_id": invocation.task_id,
                "task_kind": invocation.kind,
                "attempt": invocation.attempt,
                "provider": self.name,
                "model": self.model,
                "status": "failed",
                "duration_ms": max(0, round((perf_counter() - started_at) * 1000)),
                "error_code": error.code,
            },
        )


def _error_for_status(status_code: int) -> ProviderError:
    if status_code in {400, 422}:
        return ProviderInvalidRequestError(f"DeepSeek API returned HTTP {status_code}")
    if status_code in {401, 403}:
        return ProviderAuthenticationError(f"DeepSeek API returned HTTP {status_code}")
    if status_code == 402:
        return ProviderInsufficientBalanceError("DeepSeek API returned HTTP 402")
    if status_code == 408:
        return ProviderTimeoutError("DeepSeek API returned HTTP 408")
    if status_code == 429:
        return ProviderRateLimitedError("DeepSeek API returned HTTP 429")
    if status_code == 503:
        return ProviderOverloadedError("DeepSeek API returned HTTP 503")
    if status_code >= 500:
        return ProviderServerError(f"DeepSeek API returned HTTP {status_code}")
    return ProviderInvalidRequestError(f"DeepSeek API returned HTTP {status_code}")


def _parse_response(
    response: httpx.Response,
    *,
    requested_model: str,
    billing_currency: str,
) -> ProviderResult:
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("response root is not an object")
        usage = payload["usage"]
        if not isinstance(usage, dict):
            raise TypeError("usage is not an object")
        response_model = payload["model"]
        if response_model not in SUPPORTED_MODELS:
            raise ValueError("response model is unsupported")
        input_tokens = _non_negative_int(usage["prompt_tokens"], "prompt_tokens")
        output_tokens = _non_negative_int(usage["completion_tokens"], "completion_tokens")
        total_tokens = _non_negative_int(usage["total_tokens"], "total_tokens")
        if total_tokens != input_tokens + output_tokens:
            raise ProviderResponseError(
                "DeepSeek total_tokens did not match prompt_tokens plus completion_tokens"
            )
        cache_hit_tokens, cache_miss_tokens = _cache_usage(
            usage,
            input_tokens=input_tokens,
        )
    except ProviderError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ProviderResponseError("DeepSeek response did not match the expected shape") from error

    accounting_result = _accounting_result(
        model=response_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        billing_currency=billing_currency,
    )
    if response_model != requested_model:
        raise ProviderResponseError(
            "DeepSeek response model did not match the requested model",
            accounting_result=accounting_result,
        )

    try:
        choices = payload["choices"]
        choice = choices[0]
        finish_reason = choice["finish_reason"]
        content_text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ProviderResponseError(
            "DeepSeek response did not contain a completion choice",
            accounting_result=accounting_result,
        ) from error

    if finish_reason == "length":
        raise ProviderOutputTruncatedError(
            "DeepSeek response reached the output limit",
            accounting_result=accounting_result,
        )
    if finish_reason == "content_filter":
        raise ProviderContentFilteredError(
            "DeepSeek response was filtered",
            accounting_result=accounting_result,
        )
    if finish_reason == "insufficient_system_resource":
        raise ProviderOverloadedError(
            "DeepSeek stopped because inference resources were unavailable",
            accounting_result=accounting_result,
        )
    if finish_reason != "stop":
        raise ProviderResponseError(
            f"unexpected DeepSeek finish reason: {finish_reason}",
            accounting_result=accounting_result,
        )
    if not isinstance(content_text, str) or not content_text.strip():
        raise ProviderResponseError(
            "DeepSeek returned empty JSON content",
            accounting_result=accounting_result,
        )

    try:
        content = json.loads(content_text)
    except json.JSONDecodeError as error:
        raise ProviderResponseError(
            "DeepSeek returned invalid JSON content",
            accounting_result=accounting_result,
        ) from error
    if not isinstance(content, dict):
        raise ProviderResponseError(
            "DeepSeek JSON content must be an object",
            accounting_result=accounting_result,
        )

    return ProviderResult(
        content=content,
        provider=accounting_result.provider,
        model=accounting_result.model,
        input_tokens=accounting_result.input_tokens,
        output_tokens=accounting_result.output_tokens,
        estimated_cost_micros=accounting_result.estimated_cost_micros,
        cost_currency=accounting_result.cost_currency,
    )


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProviderResponseError(f"DeepSeek usage field {field} must be a non-negative integer")
    return value


def _cache_usage(usage: dict[str, Any], *, input_tokens: int) -> tuple[int, int]:
    hit_value = usage.get("prompt_cache_hit_tokens")
    miss_value = usage.get("prompt_cache_miss_tokens")
    if hit_value is None and miss_value is None:
        return 0, input_tokens
    hit_tokens = _non_negative_int(hit_value, "prompt_cache_hit_tokens")
    miss_tokens = _non_negative_int(miss_value, "prompt_cache_miss_tokens")
    if hit_tokens + miss_tokens != input_tokens:
        raise ProviderResponseError("DeepSeek cache token counts did not match prompt_tokens")
    return hit_tokens, miss_tokens


def _estimate_cost_micros(
    *,
    model: str,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
    billing_currency: str,
) -> int:
    prices = _PRICE_PER_MILLION_TOKENS[billing_currency][model]
    # tokens * currency-units-per-million-token equals millionths of that currency.
    micros = (
        Decimal(cache_hit_tokens) * prices["input_cache_hit"]
        + Decimal(cache_miss_tokens) * prices["input_cache_miss"]
        + Decimal(output_tokens) * prices["output"]
    )
    return int(micros.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _accounting_result(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    billing_currency: str,
) -> ProviderResult:
    return ProviderResult(
        content={},
        provider="deepseek",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_micros=_estimate_cost_micros(
            model=model,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            output_tokens=output_tokens,
            billing_currency=billing_currency,
        ),
        cost_currency=billing_currency,
    )


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.deepseek.com"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("DeepSeek base URL must be exactly https://api.deepseek.com")
    return DEFAULT_BASE_URL
