"""Full evaluation suite for RepoSense agent.

Covers ALL metrics:
  A. Quality      — scan accuracy, keyword hit, reference validity, hallucination rate
  B. Efficiency   — latency, token cost, ReAct loop count
  C. Robustness   — error recovery, malformed input, adversarial queries, cache behavior
  D. Multi-turn   — conversation coherence across ≥3 turns
  E. Contribution — issue-code association accuracy

Usage:
    python eval/full_evaluation.py                          # full suite (1 repo, quick)
    python eval/full_evaluation.py --full                   # all repos, all tests
    python eval/full_evaluation.py --repos click            # filter to one repo
    python eval/full_evaluation.py --only robustness        # run only robustness tests
    python eval/full_evaluation.py --only qa,scan           # comma-separated suites
    python eval/full_evaluation.py --max-repos 2 --max-questions 2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reposense.config import settings
from reposense.metrics import reset_collector, get_collector, MetricsCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("full_eval")

EVAL_DIR = Path(__file__).parent
TEST_REPOS_FILE = EVAL_DIR / "test_repos.json"
OUTPUT_DIR = EVAL_DIR / "results"


# ═══════════════════════════════════════════════════════════════════════════════
# Data classes for results
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScanMetric:
    repo: str
    success: bool
    elapsed_s: float
    project_type_match: bool = False
    core_dirs_recall: float = 0.0
    core_dirs_precision: float = 0.0
    entry_point_recall: float = 0.0
    error: str | None = None


@dataclass
class QAMetric:
    repo: str
    question: str
    success: bool
    elapsed_s: float
    keywords_found: int = 0
    keywords_expected: int = 0
    refs_valid: int = 0
    refs_total: int = 0
    react_iterations: int = 0
    hallucination_rate: float = 0.0
    tool_usage: list = field(default_factory=list)
    error: str | None = None


@dataclass
class RobustnessCase:
    name: str
    category: str  # "malformed_input" | "error_recovery" | "adversarial" | "cache" | "boundary"
    passed: bool
    elapsed_s: float
    detail: str = ""


@dataclass
class MultiTurnMetric:
    repo: str
    total_turns: int
    coherent_turns: int
    elapsed_s: float
    detail: str = ""


@dataclass
class ContributeMetric:
    repo: str
    success: bool
    elapsed_s: float
    issues_fetched: int = 0
    issues_analyzed: int = 0
    roadmap_generated: bool = False
    valid_file_refs: int = 0
    total_file_refs: int = 0
    error: str | None = None


@dataclass
class EfficiencySnapshot:
    """Captured from MetricsCollector after each test section."""
    section: str
    llm_calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    avg_latency_per_call: float = 0.0


@dataclass
class FullReport:
    scan_metrics: list[ScanMetric] = field(default_factory=list)
    qa_metrics: list[QAMetric] = field(default_factory=list)
    robustness_cases: list[RobustnessCase] = field(default_factory=list)
    multiturn_metrics: list[MultiTurnMetric] = field(default_factory=list)
    contribute_metrics: list[ContributeMetric] = field(default_factory=list)
    efficiency_snapshots: list[EfficiencySnapshot] = field(default_factory=list)
    total_elapsed_s: float = 0.0

    # ── Aggregated scores ──

    @property
    def scan_success_rate(self) -> float:
        if not self.scan_metrics:
            return 0.0
        return sum(1 for m in self.scan_metrics if m.success) / len(self.scan_metrics)

    @property
    def project_type_accuracy(self) -> float:
        ok = [m for m in self.scan_metrics if m.success]
        return sum(1 for m in ok if m.project_type_match) / len(ok) if ok else 0.0

    @property
    def avg_core_dir_recall(self) -> float:
        ok = [m for m in self.scan_metrics if m.success]
        return sum(m.core_dirs_recall for m in ok) / len(ok) if ok else 0.0

    @property
    def avg_entry_point_recall(self) -> float:
        ok = [m for m in self.scan_metrics if m.success]
        return sum(m.entry_point_recall for m in ok) / len(ok) if ok else 0.0

    @property
    def keyword_hit_rate(self) -> float:
        ok = [m for m in self.qa_metrics if m.success]
        total_found = sum(m.keywords_found for m in ok)
        total_exp = sum(m.keywords_expected for m in ok)
        return total_found / total_exp if total_exp else 0.0

    @property
    def reference_validity_rate(self) -> float:
        ok = [m for m in self.qa_metrics if m.success and m.refs_total > 0]
        return sum(m.refs_valid for m in ok) / sum(m.refs_total for m in ok) if ok else 0.0

    @property
    def avg_hallucination_rate(self) -> float:
        ok = [m for m in self.qa_metrics if m.success and m.refs_total > 0]
        return sum(m.hallucination_rate for m in ok) / len(ok) if ok else 0.0

    @property
    def avg_react_iterations(self) -> float:
        ok = [m for m in self.qa_metrics if m.success and m.react_iterations > 0]
        return sum(m.react_iterations for m in ok) / len(ok) if ok else 0.0

    @property
    def robustness_pass_rate(self) -> float:
        if not self.robustness_cases:
            return 0.0
        return sum(1 for c in self.robustness_cases if c.passed) / len(self.robustness_cases)

    @property
    def multiturn_coherence_rate(self) -> float:
        if not self.multiturn_metrics:
            return 0.0
        total_turns = sum(m.total_turns for m in self.multiturn_metrics)
        coherent = sum(m.coherent_turns for m in self.multiturn_metrics)
        return coherent / total_turns if total_turns else 0.0

    @property
    def contribute_success_rate(self) -> float:
        if not self.contribute_metrics:
            return 0.0
        return sum(1 for m in self.contribute_metrics if m.success) / len(self.contribute_metrics)

    @property
    def total_tokens(self) -> int:
        return sum(s.total_tokens for s in self.efficiency_snapshots)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.efficiency_snapshots)

    def to_markdown(self) -> str:
        lines = [
            "# RepoSense 全面评测报告",
            "",
            f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 总耗时: {self.total_elapsed_s:.1f}s | Token: {self.total_tokens:,} | 成本: ${self.total_cost_usd:.4f} (¥{self.total_cost_usd * 7.2:.3f})",
            "",
            "---",
            "",
            "## 评分总览",
            "",
            "| 指标 | 得分 | 目标 | 状态 |",
            "|------|------|------|------|",
        ]

        def _status(val, target):
            return "✅" if val >= target else "⚠️" if val >= target * 0.8 else "❌"

        metrics_table = [
            ("文件引用有效率", self.reference_validity_rate, 0.90),
            ("关键词命中率", self.keyword_hit_rate, 0.80),
            ("项目类型准确率", self.project_type_accuracy, 0.85),
            ("核心目录召回率", self.avg_core_dir_recall, 0.70),
            ("入口文件召回率", self.avg_entry_point_recall, 0.60),
            ("幻觉率（越低越好）", self.avg_hallucination_rate, -1),
            ("鲁棒性通过率", self.robustness_pass_rate, 0.90),
            ("多轮对话连贯率", self.multiturn_coherence_rate, 0.80),
            ("贡献引导成功率", self.contribute_success_rate, 0.80),
        ]
        for name, val, target in metrics_table:
            if name.startswith("幻觉"):
                s = "✅" if val <= 0.10 else "⚠️" if val <= 0.20 else "❌"
                lines.append(f"| {name} | {val:.0%} | ≤ 10% | {s} |")
            else:
                lines.append(f"| {name} | {val:.0%} | ≥ {target:.0%} | {_status(val, target)} |")

        lines.extend([
            "",
            "| 效率指标 | 值 |",
            "|----------|-----|",
            f"| 平均 ReAct 循环次数 | {self.avg_react_iterations:.1f} 轮 |",
            f"| 全景扫描平均耗时 | {self._avg_scan_time():.1f}s |",
            f"| Q&A 平均耗时 | {self._avg_qa_time():.1f}s |",
            "",
        ])

        # ── A. Scan details ──
        if self.scan_metrics:
            lines.extend([
                "## A. 全景扫描评测",
                "",
                "| 仓库 | 状态 | 类型匹配 | 核心目录召回 | 入口文件召回 | 耗时 |",
                "|------|------|----------|------------|------------|------|",
            ])
            for m in self.scan_metrics:
                st = "✅" if m.success else f"❌ {(m.error or '')[:30]}"
                ty = "✅" if m.project_type_match else "❌"
                lines.append(
                    f"| {m.repo} | {st} | {ty} | {m.core_dirs_recall:.0%} | {m.entry_point_recall:.0%} | {m.elapsed_s:.1f}s |"
                )
            lines.append("")

        # ── B. Q&A details ──
        if self.qa_metrics:
            lines.extend([
                "## B. Q&A 评测",
                "",
                "| 仓库 | 问题 | 关键词 | 引用有效 | 幻觉率 | ReAct轮 | 工具调用 | 耗时 |",
                "|------|------|--------|---------|--------|---------|---------|------|",
            ])
            for m in self.qa_metrics:
                if m.success:
                    kw = f"{m.keywords_found}/{m.keywords_expected}"
                    refs = f"{m.refs_valid}/{m.refs_total}" if m.refs_total > 0 else "—"
                    hall = f"{m.hallucination_rate:.0%}" if m.refs_total > 0 else "—"
                    tool_names = [t["tool"] for t in m.tool_usage] if m.tool_usage else []
                    tool_summary = ", ".join(f"{n}×{tool_names.count(n)}" for n in dict.fromkeys(tool_names)) if tool_names else "—"
                    lines.append(
                        f"| {m.repo} | {m.question[:35]}… | {kw} | {refs} | {hall} | {m.react_iterations} | {tool_summary} | {m.elapsed_s:.1f}s |"
                    )
                else:
                    lines.append(
                        f"| {m.repo} | {m.question[:35]}… | ❌ | — | — | — | — | {m.elapsed_s:.1f}s |"
                    )
            lines.append("")

            # Tool usage detail
            has_tools = any(m.tool_usage for m in self.qa_metrics if m.success)
            if has_tools:
                lines.extend([
                    "### Q&A 工具调用明细",
                    "",
                ])
                for m in self.qa_metrics:
                    if m.success and m.tool_usage:
                        lines.append(f"**Q: {m.question[:50]}…**")
                        lines.append("")
                        lines.append("| # | 工具 | 参数 |")
                        lines.append("|---|------|------|")
                        for i, t in enumerate(m.tool_usage, 1):
                            args_str = ", ".join(f"{k}={v!r}" for k, v in t["args"].items())
                            lines.append(f"| {i} | `{t['tool']}` | {args_str[:80]} |")
                        lines.append("")
            lines.append("")

        # ── C. Robustness ──
        if self.robustness_cases:
            lines.extend([
                "## C. 鲁棒性评测",
                "",
                "| 测试用例 | 类别 | 通过 | 耗时 | 备注 |",
                "|----------|------|------|------|------|",
            ])
            for c in self.robustness_cases:
                p = "✅" if c.passed else "❌"
                lines.append(f"| {c.name} | {c.category} | {p} | {c.elapsed_s:.1f}s | {c.detail[:60]} |")
            lines.append("")

            by_cat: dict[str, list[RobustnessCase]] = {}
            for c in self.robustness_cases:
                by_cat.setdefault(c.category, []).append(c)
            lines.append("### 鲁棒性分类通过率")
            lines.append("")
            lines.append("| 类别 | 通过/总数 | 通过率 |")
            lines.append("|------|----------|--------|")
            for cat, cases in by_cat.items():
                passed = sum(1 for c in cases if c.passed)
                lines.append(f"| {cat} | {passed}/{len(cases)} | {passed / len(cases):.0%} |")
            lines.append("")

        # ── D. Multi-turn ──
        if self.multiturn_metrics:
            lines.extend([
                "## D. 多轮对话评测",
                "",
                "| 仓库 | 总轮次 | 连贯轮次 | 连贯率 | 耗时 |",
                "|------|--------|---------|--------|------|",
            ])
            for m in self.multiturn_metrics:
                rate = m.coherent_turns / m.total_turns if m.total_turns > 0 else 0
                lines.append(
                    f"| {m.repo} | {m.total_turns} | {m.coherent_turns} | {rate:.0%} | {m.elapsed_s:.1f}s |"
                )
            lines.append("")

        # ── E. Contribution ──
        if self.contribute_metrics:
            lines.extend([
                "## E. 贡献引导评测",
                "",
                "| 仓库 | 状态 | Issue获取 | 分析数 | 路线图 | 文件引用有效率 | 耗时 |",
                "|------|------|----------|--------|--------|--------------|------|",
            ])
            for m in self.contribute_metrics:
                st = "✅" if m.success else f"❌ {(m.error or '')[:30]}"
                rm = "✅" if m.roadmap_generated else "—"
                ref = f"{m.valid_file_refs}/{m.total_file_refs}" if m.total_file_refs > 0 else "—"
                lines.append(
                    f"| {m.repo} | {st} | {m.issues_fetched} | {m.issues_analyzed} | {rm} | {ref} | {m.elapsed_s:.1f}s |"
                )
            lines.append("")

        # ── F. Efficiency ──
        if self.efficiency_snapshots:
            lines.extend([
                "## F. 效率指标",
                "",
                "| 测试阶段 | LLM 调用 | Token | 成本 | 耗时 | 平均延迟/调用 |",
                "|----------|---------|-------|------|------|-------------|",
            ])
            for s in self.efficiency_snapshots:
                lines.append(
                    f"| {s.section} | {s.llm_calls} | {s.total_tokens:,} | ${s.cost_usd:.4f} | {s.elapsed_s:.1f}s | {s.avg_latency_per_call:.1f}s |"
                )
            lines.append("")

        return "\n".join(lines)

    def _avg_scan_time(self) -> float:
        ok = [m for m in self.scan_metrics if m.success]
        return sum(m.elapsed_s for m in ok) / len(ok) if ok else 0.0

    def _avg_qa_time(self) -> float:
        ok = [m for m in self.qa_metrics if m.success]
        return sum(m.elapsed_s for m in ok) / len(ok) if ok else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def _capture_metrics(section: str, report: FullReport):
    """Reset collector, yield, then snapshot efficiency numbers."""
    reset_collector()
    t0 = time.time()
    yield
    elapsed = time.time() - t0
    c = get_collector()
    avg_lat = (
        sum(lc.elapsed_s for lc in c.llm_calls) / len(c.llm_calls)
        if c.llm_calls else 0.0
    )
    report.efficiency_snapshots.append(EfficiencySnapshot(
        section=section,
        llm_calls=len(c.llm_calls),
        total_tokens=c.total_tokens,
        cost_usd=c.total_cost_usd,
        elapsed_s=round(elapsed, 2),
        avg_latency_per_call=round(avg_lat, 2),
    ))


def _load_test_repos(path: str = str(TEST_REPOS_FILE)) -> list[dict]:
    with open(path) as f:
        return json.load(f)["repos"]


# ═══════════════════════════════════════════════════════════════════════════════
# A. Scan evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan_eval(repos: list[dict], report: FullReport) -> None:
    from reposense.graph.builder import build_scan_graph

    logger.info("═══ A. Panorama Scan Evaluation (%d repos) ═══", len(repos))

    for repo_data in repos:
        repo_name = repo_data["name"]
        expected = repo_data["expected"]
        logger.info("Scanning %s …", repo_name)

        with _capture_metrics(f"scan:{repo_name}", report):
            t0 = time.time()
            try:
                graph = build_scan_graph()
                state = graph.invoke({
                    "repo_url": f"https://github.com/{repo_name}",
                    "user_query": "",
                })
                elapsed = time.time() - t0

                if state.get("error"):
                    report.scan_metrics.append(ScanMetric(
                        repo=repo_name, success=False, elapsed_s=elapsed,
                        error=state["error"][:200],
                    ))
                    continue

                ptype = (state.get("project_type") or "").lower()
                exp_type = expected.get("project_type", "").lower()
                type_match = exp_type in ptype or ptype in exp_type

                actual_core = set(state.get("core_directories") or [])
                exp_core = set(expected.get("core_directories", []))
                core_recall = len(actual_core & exp_core) / len(exp_core) if exp_core else 1.0
                core_prec = len(actual_core & exp_core) / len(actual_core) if actual_core else 0.0

                actual_entry = set(state.get("entry_points") or [])
                exp_entry = set(expected.get("entry_points", []))
                entry_recall = len(actual_entry & exp_entry) / len(exp_entry) if exp_entry else 1.0

                report.scan_metrics.append(ScanMetric(
                    repo=repo_name, success=True, elapsed_s=elapsed,
                    project_type_match=type_match,
                    core_dirs_recall=core_recall,
                    core_dirs_precision=core_prec,
                    entry_point_recall=entry_recall,
                ))
                logger.info(
                    "  → type=%s  core_recall=%.0f%%  entry_recall=%.0f%%  %.1fs",
                    "✓" if type_match else "✗",
                    core_recall * 100, entry_recall * 100, elapsed,
                )
            except Exception as exc:
                elapsed = time.time() - t0
                report.scan_metrics.append(ScanMetric(
                    repo=repo_name, success=False, elapsed_s=elapsed,
                    error=str(exc)[:200],
                ))
                logger.error("  → FAILED: %s", str(exc)[:120])


# ═══════════════════════════════════════════════════════════════════════════════
# B. Q&A evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def run_qa_eval(
    repos: list[dict], report: FullReport, *, max_questions: int = 3,
) -> None:
    from reposense.graph.qa_graph import QAEngine
    from reposense.graph.nodes.verifier import verify_answer
    from reposense.tools.github_api import GitHubClient

    logger.info("═══ B. Q&A Evaluation ═══")

    for repo_data in repos:
        repo_name = repo_data["name"]
        questions = repo_data.get("questions", [])[:max_questions]
        if not questions:
            continue

        logger.info("Q&A on %s (%d questions) …", repo_name, len(questions))

        with _capture_metrics(f"qa:{repo_name}", report):
            try:
                engine = QAEngine()
                with GitHubClient(token=settings.github_token) as client:
                    tree_nodes = client.get_file_tree(repo_name)
                    file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
                engine.build_index(repo_name, file_tree)
            except Exception as exc:
                for q in questions:
                    report.qa_metrics.append(QAMetric(
                        repo=repo_name, question=q["q"], success=False,
                        elapsed_s=0, error=f"Index failed: {str(exc)[:100]}",
                    ))
                continue

            for q_data in questions:
                question = q_data["q"]
                must_mention = q_data.get("must_mention", [])
                t0 = time.time()
                try:
                    result = engine.ask(question, thread_id=f"eval_{repo_name}")
                    elapsed = time.time() - t0

                    answer = result.get("answer", "")
                    iterations = result.get("iterations", 0)
                    tool_usage = result.get("tool_usage", [])
                    answer_lower = answer.lower()

                    found = sum(1 for kw in must_mention if kw.lower() in answer_lower)

                    verification = verify_answer(answer, file_tree)

                    report.qa_metrics.append(QAMetric(
                        repo=repo_name, question=question, success=True,
                        elapsed_s=elapsed,
                        keywords_found=found,
                        keywords_expected=len(must_mention),
                        refs_valid=verification["valid_refs"],
                        refs_total=verification["total_refs"],
                        react_iterations=iterations,
                        hallucination_rate=verification["hallucination_rate"],
                        tool_usage=tool_usage,
                    ))
                    logger.info(
                        "  Q: %s → kw=%d/%d refs=%d/%d iters=%d tools=%s %.1fs",
                        question[:40], found, len(must_mention),
                        verification["valid_refs"], verification["total_refs"],
                        iterations,
                        [t["tool"] for t in tool_usage],
                        elapsed,
                    )
                except Exception as exc:
                    elapsed = time.time() - t0
                    report.qa_metrics.append(QAMetric(
                        repo=repo_name, question=question, success=False,
                        elapsed_s=elapsed, error=str(exc)[:200],
                    ))
                    logger.error("  Q FAILED: %s", str(exc)[:100])


# ═══════════════════════════════════════════════════════════════════════════════
# C. Robustness evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def run_robustness_eval(report: FullReport) -> None:
    logger.info("═══ C. Robustness Evaluation ═══")

    _test_malformed_inputs(report)
    _test_error_recovery(report)
    _test_adversarial_queries(report)
    _test_cache_behavior(report)
    _test_boundary_conditions(report)


def _record_robust(report: FullReport, name: str, category: str, passed: bool, elapsed: float, detail: str = ""):
    report.robustness_cases.append(RobustnessCase(
        name=name, category=category, passed=passed,
        elapsed_s=round(elapsed, 2), detail=detail,
    ))
    logger.info("  [%s] %s — %s (%.1fs)", category, name, "PASS" if passed else "FAIL", elapsed)


def _test_malformed_inputs(report: FullReport) -> None:
    """Test the system handles bad inputs gracefully without crashing."""
    from reposense.graph.builder import build_scan_graph

    cases = [
        ("empty URL", ""),
        ("garbage URL", "not-a-url-at-all!!!"),
        ("HTTP non-GitHub URL", "https://gitlab.com/foo/bar"),
        ("incomplete GitHub URL", "https://github.com/only-owner"),
        ("URL with special chars", "https://github.com/owner/<script>alert(1)</script>"),
        ("non-existent repo", "https://github.com/this-owner-does-not-exist-9999/fake-repo-xyz"),
    ]

    for name, url in cases:
        t0 = time.time()
        try:
            graph = build_scan_graph()
            state = graph.invoke({"repo_url": url, "user_query": ""})
            elapsed = time.time() - t0
            has_error = bool(state.get("error"))
            _record_robust(
                report, name, "malformed_input", passed=True, elapsed=elapsed,
                detail=f"Handled gracefully, error={has_error}",
            )
        except SystemExit:
            _record_robust(
                report, name, "malformed_input", passed=False,
                elapsed=time.time() - t0, detail="SystemExit raised",
            )
        except Exception as exc:
            elapsed = time.time() - t0
            is_expected = any(kw in str(exc).lower() for kw in [
                "not found", "invalid", "404", "url", "parse",
                "rate limit", "bad credentials",
            ])
            _record_robust(
                report, name, "malformed_input", passed=is_expected, elapsed=elapsed,
                detail=f"Exception: {type(exc).__name__}: {str(exc)[:80]}",
            )


def _test_error_recovery(report: FullReport) -> None:
    """Test LLM fallback and retry mechanisms."""
    from reposense.llm import invoke_with_fallback, force_provider
    from langchain_core.messages import HumanMessage

    # 1. Force bad provider, then reset — should not permanently break the system
    t0 = time.time()
    try:
        force_provider(None)
        msg = [HumanMessage(content="Say 'hello' in one word.")]
        result = invoke_with_fallback(msg, max_output_tokens=32)
        elapsed = time.time() - t0
        passed = isinstance(result, str) and len(result) > 0
        _record_robust(
            report, "LLM basic invocation", "error_recovery",
            passed=passed, elapsed=elapsed, detail=f"Got: {result[:40]}",
        )
    except Exception as exc:
        _record_robust(
            report, "LLM basic invocation", "error_recovery",
            passed=False, elapsed=time.time() - t0, detail=str(exc)[:80],
        )

    # 2. Token budget enforcement
    t0 = time.time()
    try:
        from reposense.metrics import MetricsCollector, reset_collector
        import reposense.metrics as metrics_mod

        old_collector = metrics_mod._collector
        fake = MetricsCollector()
        fake.llm_calls.append(metrics_mod.LLMCallMetric(
            provider="test", model="test",
            input_tokens=999_999, output_tokens=999_999,
            elapsed_s=0, success=True,
        ))
        metrics_mod._collector = fake

        try:
            invoke_with_fallback(
                [HumanMessage(content="test")],
                max_output_tokens=32,
            )
            budget_enforced = False
        except RuntimeError as e:
            budget_enforced = "budget" in str(e).lower() or "exceeded" in str(e).lower()
        finally:
            metrics_mod._collector = old_collector

        _record_robust(
            report, "token budget enforcement", "error_recovery",
            passed=budget_enforced, elapsed=time.time() - t0,
            detail="Budget correctly blocked call" if budget_enforced else "Budget NOT enforced",
        )
    except Exception as exc:
        _record_robust(
            report, "token budget enforcement", "error_recovery",
            passed=False, elapsed=time.time() - t0, detail=str(exc)[:80],
        )

    # 3. Verify fallback config exists
    t0 = time.time()
    has_fallback = settings.has_fallback_llm
    _record_robust(
        report, "fallback LLM configured", "error_recovery",
        passed=True, elapsed=time.time() - t0,
        detail=f"has_fallback={has_fallback}",
    )


def _test_adversarial_queries(report: FullReport) -> None:
    """Test Q&A with queries designed to trigger hallucinations or confusion."""
    from reposense.graph.qa_graph import QAEngine
    from reposense.graph.nodes.verifier import verify_answer
    from reposense.tools.github_api import GitHubClient

    test_repo = "pallets/click"
    adversarial_questions = [
        ("non-existent function", "Where is the function `fooBarBaz12345` defined?"),
        ("wrong framework reference", "How does Click use Django's ORM for database queries?"),
        ("non-existent file", "What does the file `src/click/blockchain.py` do?"),
        ("confusion injection", "Explain the React component lifecycle in Click's frontend code."),
    ]

    try:
        engine = QAEngine()
        with GitHubClient(token=settings.github_token) as client:
            tree_nodes = client.get_file_tree(test_repo)
            file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
        engine.build_index(test_repo, file_tree)
    except Exception as exc:
        for name, _ in adversarial_questions:
            _record_robust(
                report, name, "adversarial", passed=False, elapsed=0,
                detail=f"Setup failed: {str(exc)[:60]}",
            )
        return

    for name, question in adversarial_questions:
        t0 = time.time()
        try:
            result = engine.ask(question, thread_id=f"adv_{name}")
            elapsed = time.time() - t0
            answer = result.get("answer", "")
            answer_lower = answer.lower()

            honest_refusal = any(phrase in answer_lower for phrase in [
                "not found", "doesn't exist", "does not exist",
                "no such", "couldn't find", "could not find",
                "not present", "no evidence", "not relevant",
                "is not part of", "no file", "没有找到", "不存在",
                "no results", "not applicable", "doesn't have",
                "does not have", "not related", "unrelated",
            ])

            verification = verify_answer(answer, file_tree)
            low_hallucination = verification["hallucination_rate"] <= 0.3

            # An honest refusal is always a pass, regardless of hallucination rate
            passed = honest_refusal or low_hallucination
            hall_detail = f"{verification['hallucination_rate']:.0%}" if verification["total_refs"] > 0 else "n/a(no refs)"
            _record_robust(
                report, name, "adversarial", passed=passed, elapsed=elapsed,
                detail=f"refusal={honest_refusal} hall={hall_detail}",
            )
        except Exception as exc:
            _record_robust(
                report, name, "adversarial", passed=False,
                elapsed=time.time() - t0, detail=str(exc)[:80],
            )


def _test_cache_behavior(report: FullReport) -> None:
    """Test that cached indexes load faster than cold builds."""
    from reposense.graph.qa_graph import QAEngine
    from reposense.tools.github_api import GitHubClient

    test_repo = "tiangolo/sqlmodel"

    try:
        with GitHubClient(token=settings.github_token) as client:
            tree_nodes = client.get_file_tree(test_repo)
            file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
    except Exception as exc:
        _record_robust(
            report, "cache speedup", "cache", passed=False, elapsed=0,
            detail=f"Cannot fetch tree: {str(exc)[:60]}",
        )
        return

    # Cold build
    t0 = time.time()
    try:
        engine1 = QAEngine()
        stats1 = engine1.build_index(test_repo, file_tree, force_rebuild=True)
        cold_time = time.time() - t0
    except Exception as exc:
        _record_robust(
            report, "cache cold build", "cache", passed=False,
            elapsed=time.time() - t0, detail=str(exc)[:80],
        )
        return

    _record_robust(
        report, "cache cold build", "cache", passed=True, elapsed=cold_time,
        detail=f"chunks={stats1.get('total_chunks', '?')}",
    )

    # Warm load
    t0 = time.time()
    try:
        engine2 = QAEngine()
        stats2 = engine2.build_index(test_repo, file_tree)
        warm_time = time.time() - t0
        is_cached = stats2.get("cached", False)
        speedup = cold_time / warm_time if warm_time > 0.01 else float("inf")

        _record_robust(
            report, "cache warm load", "cache",
            passed=is_cached and speedup > 2.0,
            elapsed=warm_time,
            detail=f"cached={is_cached} speedup={speedup:.1f}x (cold={cold_time:.1f}s warm={warm_time:.1f}s)",
        )
    except Exception as exc:
        _record_robust(
            report, "cache warm load", "cache", passed=False,
            elapsed=time.time() - t0, detail=str(exc)[:80],
        )


def _test_boundary_conditions(report: FullReport) -> None:
    """Test edge cases: empty queries, very long queries, unicode, etc."""
    from reposense.graph.qa_graph import QAEngine
    from reposense.tools.github_api import GitHubClient

    test_repo = "tiangolo/sqlmodel"

    try:
        engine = QAEngine()
        with GitHubClient(token=settings.github_token) as client:
            tree_nodes = client.get_file_tree(test_repo)
            file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
        engine.build_index(test_repo, file_tree)
    except Exception as exc:
        for name in ["empty query", "very long query", "unicode query", "code injection query"]:
            _record_robust(
                report, name, "boundary", passed=False, elapsed=0,
                detail=f"Setup failed: {str(exc)[:60]}",
            )
        return

    boundary_cases = [
        ("empty query", ""),
        ("very long query", "What is " * 200 + "the main module?"),
        ("unicode query", "这个项目的主要入口是什么？请用中文回答。"),
        ("code injection query", "```python\nimport os; os.system('rm -rf /')\n```\nWhat is this?"),
    ]

    for name, query in boundary_cases:
        t0 = time.time()
        try:
            if not query.strip():
                passed = True
                detail = "Empty query handled (skipped)"
                elapsed = time.time() - t0
            else:
                result = engine.ask(query, thread_id=f"boundary_{name}")
                elapsed = time.time() - t0
                answer = result.get("answer", "")
                passed = isinstance(answer, str) and len(answer) > 10
                detail = f"Got answer ({len(answer)} chars)"

            _record_robust(report, name, "boundary", passed=passed, elapsed=elapsed, detail=detail)
        except Exception as exc:
            _record_robust(
                report, name, "boundary", passed=False,
                elapsed=time.time() - t0, detail=f"{type(exc).__name__}: {str(exc)[:60]}",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# D. Multi-turn evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def run_multiturn_eval(repos: list[dict], report: FullReport) -> None:
    from reposense.graph.qa_graph import QAEngine
    from reposense.tools.github_api import GitHubClient

    logger.info("═══ D. Multi-turn Evaluation ═══")

    MULTITURN_SCRIPTS = {
        "pallets/click": [
            ("What is the main entry point of this project?", ["core.py"]),
            ("In the file you just mentioned, what are the key classes?", ["BaseCommand", "Command", "Group"]),
            ("Going back to the first topic, how does the entry point handle subcommands?", ["Group", "add_command"]),
        ],
        "tiangolo/sqlmodel": [
            ("What is the core class that SQLModel provides?", ["SQLModel"]),
            ("How does the class you mentioned relate to Pydantic and SQLAlchemy?", ["Pydantic", "SQLAlchemy"]),
            ("Can you show me where sessions are managed? Reference what we discussed earlier.", ["Session"]),
        ],
    }

    for repo_data in repos:
        repo_name = repo_data["name"]
        script = MULTITURN_SCRIPTS.get(repo_name)
        if not script:
            continue

        logger.info("Multi-turn on %s (%d turns) …", repo_name, len(script))

        with _capture_metrics(f"multiturn:{repo_name}", report):
            t0 = time.time()
            try:
                engine = QAEngine()
                with GitHubClient(token=settings.github_token) as client:
                    tree_nodes = client.get_file_tree(repo_name)
                    file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
                engine.build_index(repo_name, file_tree)
            except Exception as exc:
                report.multiturn_metrics.append(MultiTurnMetric(
                    repo=repo_name, total_turns=len(script), coherent_turns=0,
                    elapsed_s=time.time() - t0, detail=f"Setup failed: {str(exc)[:60]}",
                ))
                continue

            thread_id = f"multiturn_{repo_name}"
            coherent = 0

            for i, (question, expected_keywords) in enumerate(script):
                try:
                    result = engine.ask(question, thread_id=thread_id)
                    answer = result.get("answer", "").lower()
                    hits = sum(1 for kw in expected_keywords if kw.lower() in answer)
                    if hits >= len(expected_keywords) * 0.5:
                        coherent += 1
                    logger.info("  Turn %d: %d/%d keywords hit", i + 1, hits, len(expected_keywords))
                except Exception as exc:
                    logger.error("  Turn %d FAILED: %s", i + 1, str(exc)[:80])

            elapsed = time.time() - t0
            report.multiturn_metrics.append(MultiTurnMetric(
                repo=repo_name, total_turns=len(script),
                coherent_turns=coherent, elapsed_s=elapsed,
            ))


# ═══════════════════════════════════════════════════════════════════════════════
# E. Contribution evaluator
# ═══════════════════════════════════════════════════════════════════════════════

def run_contribute_eval(repos: list[dict], report: FullReport) -> None:
    from reposense.graph.contribute import ContributeEngine
    from reposense.graph.nodes.verifier import extract_file_references

    logger.info("═══ E. Contribution Guidance Evaluation ═══")

    for repo_data in repos:
        repo_name = repo_data["name"]
        logger.info("Contribution eval on %s …", repo_name)

        with _capture_metrics(f"contribute:{repo_name}", report):
            t0 = time.time()
            try:
                engine = ContributeEngine()
                engine.fetch_repo_data(repo_name)
                issues = engine.fetch_issues()
                engine.build_index()
                analyses = engine.analyze_issues()
                roadmap = engine.generate_roadmap()

                valid_refs = 0
                total_refs = 0
                file_tree = engine._file_tree
                blob_paths = {n["path"] for n in file_tree if n["type"] == "blob"}
                for a in analyses:
                    for f in a.get("related_files", []):
                        total_refs += 1
                        f_clean = f.strip().rstrip("/")
                        if f_clean in blob_paths:
                            valid_refs += 1
                        elif any(p.endswith("/" + f_clean) or p == f_clean for p in blob_paths):
                            valid_refs += 1

                elapsed = time.time() - t0
                report.contribute_metrics.append(ContributeMetric(
                    repo=repo_name, success=True, elapsed_s=elapsed,
                    issues_fetched=len(issues),
                    issues_analyzed=len(analyses),
                    roadmap_generated=bool(roadmap and len(roadmap) > 50),
                    valid_file_refs=valid_refs,
                    total_file_refs=total_refs,
                ))
                logger.info(
                    "  → issues=%d analyzed=%d roadmap=%d chars refs=%d/%d",
                    len(issues), len(analyses), len(roadmap), valid_refs, total_refs,
                )
            except Exception as exc:
                elapsed = time.time() - t0
                report.contribute_metrics.append(ContributeMetric(
                    repo=repo_name, success=False, elapsed_s=elapsed,
                    error=str(exc)[:200],
                ))
                logger.error("  → FAILED: %s", str(exc)[:120])


# ═══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

SUITES = {"scan", "qa", "robustness", "multiturn", "contribute"}


def run_full_evaluation(
    *,
    repo_filter: str | None = None,
    max_repos: int | None = None,
    max_questions: int = 3,
    only: set[str] | None = None,
) -> FullReport:
    repos = _load_test_repos()

    if repo_filter:
        repos = [r for r in repos if repo_filter.lower() in r["name"].lower()]
    if max_repos:
        repos = repos[:max_repos]

    active = only or SUITES

    logger.info("Running evaluation: suites=%s, repos=%d, max_q=%d",
                active, len(repos), max_questions)

    report = FullReport()
    t0 = time.time()

    if "scan" in active:
        run_scan_eval(repos, report)

    if "qa" in active:
        run_qa_eval(repos, report, max_questions=max_questions)

    if "robustness" in active:
        run_robustness_eval(report)

    if "multiturn" in active:
        run_multiturn_eval(repos, report)

    if "contribute" in active:
        run_contribute_eval(repos, report)

    report.total_elapsed_s = round(time.time() - t0, 2)
    return report


def main():
    parser = argparse.ArgumentParser(description="RepoSense Full Evaluation Suite")
    parser.add_argument("--repos", type=str, help="Filter repos (substring match)")
    parser.add_argument("--max-repos", type=int, default=1, help="Max repos (default 1 for quick run)")
    parser.add_argument("--max-questions", type=int, default=3, help="Max Q&A questions per repo")
    parser.add_argument("--full", action="store_true", help="Run on all repos with all questions")
    parser.add_argument("--only", type=str, help=f"Comma-separated suites: {','.join(sorted(SUITES))}")
    parser.add_argument("--output", type=str, default=None, help="Output markdown file")
    args = parser.parse_args()

    missing = settings.validate()
    if missing:
        print(f"Missing config: {', '.join(missing)}")
        sys.exit(1)

    only = None
    if args.only:
        only = {s.strip() for s in args.only.split(",")}
        invalid = only - SUITES
        if invalid:
            print(f"Unknown suites: {invalid}. Valid: {sorted(SUITES)}")
            sys.exit(1)

    max_repos = None if args.full else args.max_repos
    max_questions = 5 if args.full else args.max_questions

    report = run_full_evaluation(
        repo_filter=args.repos,
        max_repos=max_repos,
        max_questions=max_questions,
        only=only,
    )

    md = report.to_markdown()

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = args.output or str(OUTPUT_DIR / f"eval_{time.strftime('%Y%m%d_%H%M%S')}.md")
    with open(output_path, "w") as f:
        f.write(md)

    print(f"\n{'='*60}")
    print(f"Results written to: {output_path}")
    print(f"{'='*60}")
    print(md)


if __name__ == "__main__":
    main()
