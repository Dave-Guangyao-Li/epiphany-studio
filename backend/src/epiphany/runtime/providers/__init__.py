from typing import TYPE_CHECKING, Any

from epiphany.runtime.providers.base import (
    ModelCallLimitExceeded,
    ModelProvider,
    ProviderAuthenticationError,
    ProviderContentFilteredError,
    ProviderError,
    ProviderInputTooLargeError,
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
    RetryableProviderError,
    TaskInvocation,
)

if TYPE_CHECKING:
    from epiphany.runtime.providers.deepseek import DeepSeekProvider
    from epiphany.runtime.providers.fake import FakeProvider

__all__ = [
    "DeepSeekProvider",
    "FakeProvider",
    "ModelCallLimitExceeded",
    "ModelProvider",
    "ProviderAuthenticationError",
    "ProviderContentFilteredError",
    "ProviderError",
    "ProviderInputTooLargeError",
    "ProviderInsufficientBalanceError",
    "ProviderInvalidRequestError",
    "ProviderNetworkError",
    "ProviderOutputTruncatedError",
    "ProviderOverloadedError",
    "ProviderRateLimitedError",
    "ProviderResponseError",
    "ProviderResult",
    "ProviderServerError",
    "ProviderTimeoutError",
    "RetryableProviderError",
    "TaskInvocation",
]


def __getattr__(name: str) -> Any:
    """Load concrete Providers only when callers request them.

    Prompt builders depend on the neutral contracts in ``providers.base``.
    Importing concrete adapters here eagerly would make ``editor_prompts`` and
    ``deepseek`` initialize each other before either module is complete.
    """

    if name == "DeepSeekProvider":
        from epiphany.runtime.providers.deepseek import DeepSeekProvider

        globals()[name] = DeepSeekProvider
        return DeepSeekProvider
    if name == "FakeProvider":
        from epiphany.runtime.providers.fake import FakeProvider

        globals()[name] = FakeProvider
        return FakeProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
