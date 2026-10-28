"""Observability metrics — timing, token counting, cost calculation."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

logger = logging.getLogger(__name__)

# Pricing per 1M tokens (USD) — approximate, as of 2025
PRICING = {
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "Claude-Sonnet-4": {"input": 3.00, "output": 15.00},
}


@dataclass
class LLMCallMetric:
    """Metrics for a single LLM invocation."""
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    success: bool
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        prices = PRICING.get(self.model, {"input": 0.5, "output": 1.5})
        return (
            self.input_tokens * prices["input"] / 1_000_000
            + self.output_tokens * prices["output"] / 1_000_000
        )


@dataclass
class NodeMetric:
    """Metrics for a single graph node execution."""
    name: str
    elapsed_s: float
    success: bool
    error: str | None = None


@dataclass
class MetricsCollector:
    """Collects metrics across an entire RepoSense session."""
    llm_calls: list[LLMCallMetric] = field(default_factory=list)
    node_timings: list[NodeMetric] = field(default_factory=list)
    github_api_calls: int = 0
    start_time: float = field(default_factory=time.time)

    @contextmanager
    def track_node(self, name: str) -> Generator[None, None, None]:
        t0 = time.time()
        error = None
        success = True
        try:
            yield
        except Exception as exc:
            error = str(exc)
            success = False
            raise
        finally:
            elapsed = time.time() - t0
            self.node_timings.append(NodeMetric(
                name=name, elapsed_s=round(elapsed, 3),
                success=success, error=error,
            ))
            logger.info("Node [%s] %s in %.2fs", name, "OK" if success else "FAILED", elapsed)

    def record_llm_call(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_s: float,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        metric = LLMCallMetric(
            provider=provider, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            elapsed_s=round(elapsed_s, 3), success=success, error=error,
        )
        self.llm_calls.append(metric)
        logger.info(
            "LLM [%s/%s] %d+%d tokens in %.2fs (%.6f USD)",
            provider, model, input_tokens, output_tokens, elapsed_s, metric.cost_usd,
        )

    @property
    def total_elapsed_s(self) -> float:
        return round(time.time() - self.start_time, 2)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.llm_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.llm_calls)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.llm_calls)

    @property
    def total_cost_rmb(self) -> float:
        return self.total_cost_usd * 7.2

    @property
    def successful_llm_calls(self) -> int:
        return sum(1 for c in self.llm_calls if c.success)

    @property
    def failed_llm_calls(self) -> int:
        return sum(1 for c in self.llm_calls if not c.success)

    def generate_report(self) -> str:
        """Generate a human-readable execution report."""
        lines = [
            "# RepoSense 执行报告",
            "",
            "## 总览",
            f"- **总耗时:** {self.total_elapsed_s}s",
            f"- **GitHub API 调用:** {self.github_api_calls} 次",
            f"- **LLM 调用:** {len(self.llm_calls)} 次 "
            f"({self.successful_llm_calls} 成功, {self.failed_llm_calls} 失败)",
            f"- **Token 总量:** {self.total_tokens:,} "
            f"(输入 {self.total_input_tokens:,} + 输出 {self.total_output_tokens:,})",
            f"- **预估成本:** ${self.total_cost_usd:.4f} (¥{self.total_cost_rmb:.3f})",
            "",
        ]

        if self.node_timings:
            lines.append("## 节点执行耗时")
            lines.append("")
            lines.append("| 节点 | 耗时 (s) | 状态 |")
            lines.append("|------|----------|------|")
            for n in self.node_timings:
                status = "✅" if n.success else f"❌ {n.error or ''}"
                lines.append(f"| {n.name} | {n.elapsed_s:.2f} | {status} |")
            lines.append("")

        if self.llm_calls:
            lines.append("## LLM 调用明细")
            lines.append("")
            lines.append("| # | 模型 | 输入 Token | 输出 Token | 耗时 (s) | 成本 ($) | 状态 |")
            lines.append("|---|------|-----------|-----------|----------|---------|------|")
            for i, c in enumerate(self.llm_calls, 1):
                status = "✅" if c.success else "❌"
                lines.append(
                    f"| {i} | {c.model} | {c.input_tokens:,} | {c.output_tokens:,} "
                    f"| {c.elapsed_s:.2f} | {c.cost_usd:.6f} | {status} |"
                )
            lines.append("")

        return "\n".join(lines)

    def check_budget(self, max_tokens: int) -> bool:
        """Return True if we're still within budget, False if exceeded."""
        return self.total_tokens < max_tokens

    def budget_exceeded(self, max_tokens: int) -> bool:
        """Return True if the token budget has been exceeded."""
        return self.total_tokens >= max_tokens

    def summary_line(self) -> str:
        """One-line summary for CLI output."""
        return (
            f"{self.total_elapsed_s}s | "
            f"{len(self.llm_calls)} LLM calls | "
            f"{self.total_tokens:,} tokens | "
            f"${self.total_cost_usd:.4f} (¥{self.total_cost_rmb:.3f})"
        )


# Global singleton for easy access
_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


def reset_collector() -> MetricsCollector:
    global _collector
    _collector = MetricsCollector()
    return _collector
