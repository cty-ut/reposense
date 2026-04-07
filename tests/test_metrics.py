"""Unit tests for the observability/metrics system."""

from __future__ import annotations

import time

import pytest

from reposense.metrics import (
    LLMCallMetric,
    MetricsCollector,
    NodeMetric,
    reset_collector,
    get_collector,
)


class TestLLMCallMetric:
    def test_total_tokens(self):
        m = LLMCallMetric(
            provider="Gemini", model="gemini-2.5-flash",
            input_tokens=1000, output_tokens=500,
            elapsed_s=2.5, success=True,
        )
        assert m.total_tokens == 1500

    def test_cost_calculation_gemini(self):
        m = LLMCallMetric(
            provider="Gemini", model="gemini-2.5-flash",
            input_tokens=1_000_000, output_tokens=100_000,
            elapsed_s=10.0, success=True,
        )
        assert m.cost_usd == pytest.approx(0.15 + 0.06, abs=0.01)

    def test_cost_calculation_poe(self):
        m = LLMCallMetric(
            provider="Poe", model="Claude-Sonnet-4",
            input_tokens=1_000_000, output_tokens=100_000,
            elapsed_s=10.0, success=True,
        )
        assert m.cost_usd == pytest.approx(3.0 + 1.5, abs=0.1)

    def test_unknown_model_uses_fallback_pricing(self):
        m = LLMCallMetric(
            provider="Other", model="unknown-model",
            input_tokens=1000, output_tokens=500,
            elapsed_s=1.0, success=True,
        )
        assert m.cost_usd > 0


class TestMetricsCollector:
    def test_track_node_success(self):
        c = MetricsCollector()
        with c.track_node("test_node"):
            time.sleep(0.01)
        assert len(c.node_timings) == 1
        assert c.node_timings[0].name == "test_node"
        assert c.node_timings[0].success is True
        assert c.node_timings[0].elapsed_s > 0

    def test_track_node_failure(self):
        c = MetricsCollector()
        with pytest.raises(ValueError):
            with c.track_node("failing_node"):
                raise ValueError("boom")
        assert len(c.node_timings) == 1
        assert c.node_timings[0].success is False
        assert "boom" in c.node_timings[0].error

    def test_record_llm_call(self):
        c = MetricsCollector()
        c.record_llm_call(
            provider="Gemini", model="gemini-2.5-flash",
            input_tokens=500, output_tokens=200,
            elapsed_s=1.5, success=True,
        )
        assert len(c.llm_calls) == 1
        assert c.total_input_tokens == 500
        assert c.total_output_tokens == 200
        assert c.total_tokens == 700

    def test_aggregated_metrics(self):
        c = MetricsCollector()
        c.record_llm_call("G", "gemini-2.5-flash", 100, 50, 1.0, True)
        c.record_llm_call("G", "gemini-2.5-flash", 200, 100, 2.0, True)
        c.record_llm_call("P", "Claude-Sonnet-4", 300, 150, 3.0, False, "timeout")
        assert c.total_tokens == 900
        assert c.successful_llm_calls == 2
        assert c.failed_llm_calls == 1
        assert c.total_cost_usd > 0

    def test_generate_report(self):
        c = MetricsCollector()
        c.record_llm_call("Gemini", "gemini-2.5-flash", 1000, 500, 2.5, True)
        with c.track_node("scanner"):
            pass
        report = c.generate_report()
        assert "执行报告" in report
        assert "scanner" in report
        assert "gemini-2.5-flash" in report
        assert "Token" in report

    def test_summary_line(self):
        c = MetricsCollector()
        c.record_llm_call("G", "gemini-2.5-flash", 100, 50, 1.0, True)
        summary = c.summary_line()
        assert "1 LLM calls" in summary
        assert "150 tokens" in summary
        assert "$" in summary

    def test_cost_rmb_conversion(self):
        c = MetricsCollector()
        c.record_llm_call("G", "gemini-2.5-flash", 100, 50, 1.0, True)
        assert c.total_cost_rmb == pytest.approx(c.total_cost_usd * 7.2, abs=0.001)


class TestGlobalCollector:
    def test_singleton(self):
        c1 = get_collector()
        c2 = get_collector()
        assert c1 is c2

    def test_reset(self):
        c1 = get_collector()
        c1.record_llm_call("G", "m", 100, 50, 1.0, True)
        c2 = reset_collector()
        assert c2 is not c1
        assert len(c2.llm_calls) == 0
        assert get_collector() is c2
