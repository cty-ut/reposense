"""Automated benchmark for RepoSense.

Evaluates:
1. Panorama scan accuracy (project type, core dirs, entry points)
2. Q&A reference validity (file paths must exist)
3. Q&A content relevance (must mention expected keywords)
4. Performance (response time, token usage, cost)

Usage:
    python eval/benchmark.py                    # run on all repos
    python eval/benchmark.py --repos click      # run on specific repo
    python eval/benchmark.py --skip-scan        # Q&A only (skip panorama)
    python eval/benchmark.py --max-repos 3      # limit number of repos
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reposense.config import settings
from reposense.metrics import reset_collector, get_collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("benchmark")

EVAL_DIR = Path(__file__).parent
TEST_REPOS_FILE = EVAL_DIR / "test_repos.json"


@dataclass
class ScanResult:
    repo: str
    success: bool
    elapsed_s: float
    project_type_match: bool = False
    core_dirs_precision: float = 0.0
    core_dirs_recall: float = 0.0
    entry_points_found: int = 0
    entry_points_expected: int = 0
    error: str | None = None


@dataclass
class QAResult:
    repo: str
    question: str
    success: bool
    elapsed_s: float
    keywords_found: int = 0
    keywords_expected: int = 0
    refs_valid: int = 0
    refs_total: int = 0
    error: str | None = None


@dataclass
class BenchmarkReport:
    scan_results: list[ScanResult] = field(default_factory=list)
    qa_results: list[QAResult] = field(default_factory=list)
    total_elapsed_s: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    @property
    def scan_success_rate(self) -> float:
        if not self.scan_results:
            return 0.0
        return sum(1 for r in self.scan_results if r.success) / len(self.scan_results)

    @property
    def avg_project_type_accuracy(self) -> float:
        successes = [r for r in self.scan_results if r.success]
        if not successes:
            return 0.0
        return sum(1 for r in successes if r.project_type_match) / len(successes)

    @property
    def avg_core_dirs_recall(self) -> float:
        successes = [r for r in self.scan_results if r.success]
        if not successes:
            return 0.0
        return sum(r.core_dirs_recall for r in successes) / len(successes)

    @property
    def qa_keyword_hit_rate(self) -> float:
        successes = [r for r in self.qa_results if r.success]
        if not successes:
            return 0.0
        total_found = sum(r.keywords_found for r in successes)
        total_expected = sum(r.keywords_expected for r in successes)
        return total_found / total_expected if total_expected else 0.0

    @property
    def qa_ref_validity_rate(self) -> float:
        successes = [r for r in self.qa_results if r.success and r.refs_total > 0]
        if not successes:
            return 0.0
        return sum(r.refs_valid for r in successes) / sum(r.refs_total for r in successes)

    def to_markdown(self) -> str:
        lines = [
            "# RepoSense Benchmark Report",
            "",
            "## Summary",
            f"- **Total time:** {self.total_elapsed_s:.1f}s",
            f"- **Total tokens:** {self.total_tokens:,}",
            f"- **Total cost:** ${self.total_cost_usd:.4f} (¥{self.total_cost_usd * 7.2:.3f})",
            "",
            "## Panorama Scan",
            f"- **Success rate:** {self.scan_success_rate:.0%}",
            f"- **Project type accuracy:** {self.avg_project_type_accuracy:.0%}",
            f"- **Core directory recall:** {self.avg_core_dirs_recall:.0%}",
            "",
        ]

        if self.scan_results:
            lines.append("| Repo | Status | Type Match | Core Dir Recall | Entry Points | Time (s) |")
            lines.append("|------|--------|------------|-----------------|--------------|----------|")
            for r in self.scan_results:
                status = "✅" if r.success else f"❌ {r.error or ''}"
                type_match = "✅" if r.project_type_match else "❌"
                ep = f"{r.entry_points_found}/{r.entry_points_expected}"
                lines.append(
                    f"| {r.repo} | {status} | {type_match} | {r.core_dirs_recall:.0%} | {ep} | {r.elapsed_s:.1f} |"
                )
            lines.append("")

        lines.extend([
            "## Q&A Evaluation",
            f"- **Keyword hit rate:** {self.qa_keyword_hit_rate:.0%}",
            f"- **Reference validity:** {self.qa_ref_validity_rate:.0%}",
            "",
        ])

        if self.qa_results:
            lines.append("| Repo | Question | Keywords | Refs | Time (s) |")
            lines.append("|------|----------|----------|------|----------|")
            for r in self.qa_results:
                if r.success:
                    kw = f"{r.keywords_found}/{r.keywords_expected}"
                    refs = f"{r.refs_valid}/{r.refs_total}" if r.refs_total > 0 else "—"
                    lines.append(f"| {r.repo} | {r.question[:40]}... | {kw} | {refs} | {r.elapsed_s:.1f} |")
                else:
                    lines.append(f"| {r.repo} | {r.question[:40]}... | ❌ {r.error or ''} | — | {r.elapsed_s:.1f} |")
            lines.append("")

        return "\n".join(lines)


def _run_scan(repo_name: str, expected: dict) -> ScanResult:
    """Run panorama scan on a single repo and compare with expected."""
    from reposense.graph.builder import build_scan_graph

    t0 = time.time()
    try:
        graph = build_scan_graph()
        state = graph.invoke({
            "repo_url": f"https://github.com/{repo_name}",
            "user_query": "",
        })
        elapsed = time.time() - t0

        if state.get("error"):
            return ScanResult(repo=repo_name, success=False, elapsed_s=elapsed, error=state["error"])

        project_type = (state.get("project_type") or "").lower()
        expected_type = expected.get("project_type", "").lower()
        type_match = expected_type in project_type or project_type in expected_type

        actual_core = set(state.get("core_directories") or [])
        expected_core = set(expected.get("core_directories", []))
        if expected_core:
            recall = len(actual_core & expected_core) / len(expected_core)
            precision = len(actual_core & expected_core) / len(actual_core) if actual_core else 0
        else:
            recall = 1.0
            precision = 1.0

        actual_entry = set(state.get("entry_points") or [])
        expected_entry = set(expected.get("entry_points", []))
        entry_found = len(actual_entry & expected_entry)

        return ScanResult(
            repo=repo_name,
            success=True,
            elapsed_s=elapsed,
            project_type_match=type_match,
            core_dirs_precision=precision,
            core_dirs_recall=recall,
            entry_points_found=entry_found,
            entry_points_expected=len(expected_entry),
        )
    except Exception as exc:
        return ScanResult(repo=repo_name, success=False, elapsed_s=time.time() - t0, error=str(exc)[:200])


def _run_qa(repo_name: str, questions: list[dict], file_tree: list[dict]) -> list[QAResult]:
    """Run Q&A questions on a repo and evaluate answers."""
    from reposense.graph.qa_graph import QAEngine
    from reposense.graph.nodes.verifier import verify_answer
    from reposense.tools.github_api import GitHubClient

    results = []

    try:
        engine = QAEngine()

        if not file_tree:
            with GitHubClient(token=settings.github_token) as client:
                tree_nodes = client.get_file_tree(repo_name)
                file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

        engine.build_index(repo_name, file_tree)
    except Exception as exc:
        for q in questions:
            results.append(QAResult(
                repo=repo_name, question=q["q"], success=False,
                elapsed_s=0, error=f"Index build failed: {str(exc)[:100]}",
            ))
        return results

    for q_data in questions:
        question = q_data["q"]
        must_mention = q_data.get("must_mention", [])

        t0 = time.time()
        try:
            result = engine.ask(question, thread_id=f"bench_{repo_name}")
            elapsed = time.time() - t0

            answer = result.get("answer", "")
            answer_lower = answer.lower()

            found = sum(1 for kw in must_mention if kw.lower() in answer_lower)

            verification = verify_answer(answer, file_tree)

            results.append(QAResult(
                repo=repo_name,
                question=question,
                success=True,
                elapsed_s=elapsed,
                keywords_found=found,
                keywords_expected=len(must_mention),
                refs_valid=verification["valid_refs"],
                refs_total=verification["total_refs"],
            ))
        except Exception as exc:
            elapsed = time.time() - t0
            results.append(QAResult(
                repo=repo_name, question=question, success=False,
                elapsed_s=elapsed, error=str(exc)[:200],
            ))

    return results


def run_benchmark(
    repos_file: str = str(TEST_REPOS_FILE),
    *,
    repo_filter: str | None = None,
    max_repos: int | None = None,
    skip_scan: bool = False,
    skip_qa: bool = False,
    max_questions: int = 3,
) -> BenchmarkReport:
    """Run the full benchmark and return results."""
    with open(repos_file) as f:
        data = json.load(f)

    repos = data["repos"]

    if repo_filter:
        repos = [r for r in repos if repo_filter.lower() in r["name"].lower()]

    if max_repos:
        repos = repos[:max_repos]

    logger.info("Running benchmark on %d repos", len(repos))

    report = BenchmarkReport()
    t0 = time.time()

    for repo_data in repos:
        repo_name = repo_data["name"]
        logger.info("=== Evaluating %s ===", repo_name)

        reset_collector()

        file_tree: list[dict] = []

        if not skip_scan:
            logger.info("Running panorama scan on %s...", repo_name)
            scan_result = _run_scan(repo_name, repo_data["expected"])
            report.scan_results.append(scan_result)
            logger.info(
                "Scan %s: type=%s, core_recall=%.0f%%, time=%.1fs",
                "OK" if scan_result.success else "FAIL",
                "✓" if scan_result.project_type_match else "✗",
                scan_result.core_dirs_recall * 100,
                scan_result.elapsed_s,
            )

        if not skip_qa:
            questions = repo_data.get("questions", [])[:max_questions]
            if questions:
                logger.info("Running %d Q&A questions on %s...", len(questions), repo_name)
                qa_results = _run_qa(repo_name, questions, file_tree)
                report.qa_results.extend(qa_results)
                for qr in qa_results:
                    logger.info(
                        "  Q: %s → keywords=%d/%d, refs=%d/%d, time=%.1fs",
                        qr.question[:50], qr.keywords_found, qr.keywords_expected,
                        qr.refs_valid, qr.refs_total, qr.elapsed_s,
                    )

        collector = get_collector()
        report.total_tokens += collector.total_tokens
        report.total_cost_usd += collector.total_cost_usd

    report.total_elapsed_s = time.time() - t0
    return report


def main():
    parser = argparse.ArgumentParser(description="RepoSense Benchmark")
    parser.add_argument("--repos", type=str, help="Filter to repos containing this string")
    parser.add_argument("--max-repos", type=int, default=None, help="Max repos to evaluate")
    parser.add_argument("--max-questions", type=int, default=3, help="Max questions per repo")
    parser.add_argument("--skip-scan", action="store_true", help="Skip panorama scan")
    parser.add_argument("--skip-qa", action="store_true", help="Skip Q&A evaluation")
    parser.add_argument("--output", type=str, default=str(EVAL_DIR / "results.md"), help="Output file")
    args = parser.parse_args()

    missing = settings.validate()
    if missing:
        print(f"Missing config: {', '.join(missing)}")
        sys.exit(1)

    report = run_benchmark(
        repo_filter=args.repos,
        max_repos=args.max_repos,
        max_questions=args.max_questions,
        skip_scan=args.skip_scan,
        skip_qa=args.skip_qa,
    )

    md = report.to_markdown()

    with open(args.output, "w") as f:
        f.write(md)

    print(f"\nResults written to {args.output}")
    print(f"\n{md}")


if __name__ == "__main__":
    main()
