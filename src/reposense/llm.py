"""Centralized LLM access with automatic DeepSeek → Gemini → Poe fallback."""

from __future__ import annotations

import logging
import time
from typing import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage

from reposense.config import settings

logger = logging.getLogger(__name__)

MAX_LLM_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds


def _build_deepseek(temperature: float = 0, max_output_tokens: int = 8192) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        max_tokens=max_output_tokens,
    )


def _build_gemini(temperature: float = 0, max_output_tokens: int = 8192) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def _build_poe(temperature: float = 0, max_output_tokens: int = 8192) -> BaseChatModel:
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=settings.poe_model,
        api_key=settings.poe_api_key,
        base_url="https://api.poe.com/v1",
        temperature=temperature,
        max_tokens=max_output_tokens,
    )


# provider name → (model name getter, builder)
_PROVIDERS: dict[str, tuple[Callable[[], str], Callable[..., BaseChatModel]]] = {
    "DeepSeek": (lambda: settings.deepseek_model, _build_deepseek),
    "Gemini": (lambda: settings.gemini_model, _build_gemini),
    "Poe": (lambda: settings.poe_model, _build_poe),
}

_force_provider: str | None = None  # e.g. "Poe" to try that provider first
_rate_limited_until: dict[str, float] = {}  # provider → timestamp when cooldown expires
RATE_LIMIT_COOLDOWN = 60  # skip provider for 60s after rate-limit hit


def force_provider(provider: str | None) -> None:
    """Try a specific LLM provider first ('DeepSeek', 'Gemini', 'Poe', or None to reset)."""
    global _force_provider
    _force_provider = provider


def _is_rate_limited(provider: str) -> bool:
    until = _rate_limited_until.get(provider, 0)
    if time.time() < until:
        return True
    if until > 0:
        del _rate_limited_until[provider]
    return False


def mark_rate_limited(provider: str) -> None:
    _rate_limited_until[provider] = time.time() + RATE_LIMIT_COOLDOWN
    logger.warning("%s rate-limited, skipping for %ds", provider, RATE_LIMIT_COOLDOWN)


_mark_rate_limited = mark_rate_limited


def _ordered_providers() -> list[str]:
    """Configured providers in priority order, with a forced provider moved to the front."""
    names = settings.llm_keys
    if _force_provider:
        forced = next((n for n in names if n.lower() == _force_provider.lower()), None)
        if forced:
            names = [forced] + [n for n in names if n != forced]
    return names


def select_llm(temperature: float = 0, max_output_tokens: int = 8192) -> tuple[str, BaseChatModel]:
    """Return (provider name, LLM) for the first configured provider not in rate-limit cooldown."""
    names = _ordered_providers()
    if not names:
        raise RuntimeError("No LLM API key configured. Set DEEPSEEK_API_KEY, GOOGLE_API_KEY or POE_API_KEY in .env")
    available = [n for n in names if not _is_rate_limited(n)] or names
    name = available[0]
    if name != names[0]:
        logger.info("Using %s LLM (%s unavailable or rate-limited)", name, names[0])
    return name, _PROVIDERS[name][1](temperature, max_output_tokens)


def get_llm(temperature: float = 0, max_output_tokens: int = 8192) -> BaseChatModel:
    """Return the primary LLM. Use `invoke_with_fallback` for auto-degradation."""
    return select_llm(temperature, max_output_tokens)[1]


def _extract_token_usage(response) -> tuple[int, int]:
    """Extract input/output token counts from an LLM response."""
    usage = getattr(response, "usage_metadata", None)
    if usage:
        return (
            usage.get("input_tokens", 0) if isinstance(usage, dict) else getattr(usage, "input_tokens", 0),
            usage.get("output_tokens", 0) if isinstance(usage, dict) else getattr(usage, "output_tokens", 0),
        )
    resp_meta = getattr(response, "response_metadata", {})
    if "token_usage" in resp_meta:
        tu = resp_meta["token_usage"]
        return tu.get("prompt_tokens", 0), tu.get("completion_tokens", 0)
    return 0, 0


def invoke_with_fallback(
    messages: list[BaseMessage],
    *,
    temperature: float = 0,
    max_output_tokens: int = 8192,
) -> str:
    """Invoke LLM with retry + automatic fallback across configured providers.

    Returns the response content string.
    """
    from reposense.metrics import get_collector

    collector = get_collector()
    if collector.budget_exceeded(settings.max_tokens_per_analysis):
        raise RuntimeError(
            f"Token budget exceeded ({collector.total_tokens:,} >= {settings.max_tokens_per_analysis:,}). "
            "Analysis stopped to control costs."
        )

    names = _ordered_providers()
    if not names:
        raise RuntimeError("No LLM API key configured. Set DEEPSEEK_API_KEY, GOOGLE_API_KEY or POE_API_KEY in .env")

    last_exc: Exception | None = None

    for provider_name in names:
        if _is_rate_limited(provider_name):
            logger.info("Skipping %s (rate-limited, cooldown active)", provider_name)
            continue

        model_name, builder = _PROVIDERS[provider_name][0](), _PROVIDERS[provider_name][1]
        llm = builder(temperature, max_output_tokens)
        for attempt in range(MAX_LLM_RETRIES):
            t0 = time.time()
            try:
                response = llm.invoke(messages)
                elapsed = time.time() - t0
                input_tok, output_tok = _extract_token_usage(response)
                collector.record_llm_call(
                    provider=provider_name, model=model_name,
                    input_tokens=input_tok, output_tokens=output_tok,
                    elapsed_s=elapsed, success=True,
                )
                if attempt > 0 or provider_name != names[0]:
                    logger.info("LLM call succeeded via %s (attempt %d)", provider_name, attempt + 1)
                return response.content
            except Exception as exc:
                elapsed = time.time() - t0
                last_exc = exc
                is_rate_limit = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                collector.record_llm_call(
                    provider=provider_name, model=model_name,
                    input_tokens=0, output_tokens=0,
                    elapsed_s=elapsed, success=False, error=str(exc)[:200],
                )
                if is_rate_limit:
                    mark_rate_limited(provider_name)
                    break  # skip remaining retries, go to next provider
                wait = RETRY_BASE_DELAY ** (attempt + 1)
                logger.warning(
                    "%s call failed (attempt %d/%d): %s. Retrying in %ds...",
                    provider_name, attempt + 1, MAX_LLM_RETRIES, str(exc)[:150], wait,
                )
                time.sleep(wait)
        logger.warning("%s exhausted all retries. Trying next provider...", provider_name)

    raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}")
