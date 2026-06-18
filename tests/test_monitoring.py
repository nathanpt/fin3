"""Tests for fin3.monitoring — collector, tracker, render, tmux helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rich.console import Console

from fin3.monitoring.collector import (
    ByteCounter,
    RSSSampler,
    SampledMetrics,
    compute_disk_delta,
)
from fin3.monitoring.render import (
    _fmt_bytes,
    _fmt_duration,
    render_live_panel,
    render_summary,
)
from fin3.monitoring.tmux import is_in_tmux, kill_pane
from fin3.monitoring.tracker import ResourceTracker
from fin3.schemas import Resolution
from tests.conftest import make_ohlcv


# ---------------------------------------------------------------------------
# Collector tests
# ---------------------------------------------------------------------------


class TestByteCounter:
    def test_add_counts_bytes(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        counter = ByteCounter()
        counter.add(df)
        assert counter.fetch_count == 1
        assert counter.total_bytes == int(df.memory_usage(deep=True).sum())

    def test_add_empty_df_counts_fetch(self) -> None:
        counter = ByteCounter()
        counter.add(pd.DataFrame())
        assert counter.fetch_count == 1
        assert counter.total_bytes == 0

    def test_multiple_adds_accumulate(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        counter = ByteCounter()
        counter.add(df)
        counter.add(df)
        assert counter.fetch_count == 2
        assert counter.total_bytes == 2 * int(df.memory_usage(deep=True).sum())

    def test_reset(self) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=3, freq="1min")
        counter = ByteCounter()
        counter.add(df)
        counter.reset()
        assert counter.fetch_count == 0
        assert counter.total_bytes == 0


class TestRSSSampler:
    def test_baseline_and_peak(self) -> None:
        sampler = RSSSampler(interval=0.1)
        sampler.start()
        # Allocate some memory to push RSS up
        _ = [b"x" * 100_000 for _ in range(50)]
        time.sleep(0.3)
        sampler.stop()
        assert sampler.baseline_rss > 0
        assert sampler.peak_rss >= sampler.baseline_rss

    def test_stop_joins_thread(self) -> None:
        sampler = RSSSampler(interval=0.1)
        sampler.start()
        sampler.stop()
        assert sampler._thread is None


class TestSampledMetrics:
    def test_rss_delta(self) -> None:
        m = SampledMetrics(
            peak_rss_bytes=500,
            baseline_rss_bytes=300,
        )
        assert m.rss_delta_bytes == 200

    def test_rss_delta_never_negative(self) -> None:
        m = SampledMetrics(
            peak_rss_bytes=300,
            baseline_rss_bytes=500,
        )
        assert m.rss_delta_bytes == 0

    def test_disk_delta(self) -> None:
        m = SampledMetrics(
            disk_before_bytes=1000,
            disk_after_bytes=1500,
        )
        assert m.disk_delta_bytes == 500


class TestComputeDiskDelta:
    def test_returns_symbol_and_library_totals(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)

        symbol_total, library_total = compute_disk_delta(
            storage, "test-lib", ["AAPL"],
        )
        assert symbol_total > 0
        assert library_total >= symbol_total

    def test_empty_library(self, storage) -> None:
        symbol_total, library_total = compute_disk_delta(
            storage, "empty-lib", ["AAPL"],
        )
        assert symbol_total == 0
        assert library_total == 0


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


class TestFmtBytes:
    def test_bytes(self) -> None:
        assert _fmt_bytes(512) == "+512.0 B"

    def test_kb(self) -> None:
        assert _fmt_bytes(2048) == "+2.0 KB"

    def test_mb(self) -> None:
        assert _fmt_bytes(5 * 1024 * 1024) == "+5.0 MB"

    def test_negative(self) -> None:
        result = _fmt_bytes(-1024)
        assert "-1.0 KB" in result

    def test_gb(self) -> None:
        assert _fmt_bytes(2 * 1024 * 1024 * 1024) == "+2.0 GB"


class TestFmtDuration:
    def test_seconds(self) -> None:
        assert _fmt_duration(5.3) == "5.3s"

    def test_minutes(self) -> None:
        assert _fmt_duration(125.0) == "2m 5s"

    def test_zero(self) -> None:
        assert _fmt_duration(0.0) == "0.0s"


class TestRenderLivePanel:
    def test_panel_has_title(self) -> None:
        metrics = SampledMetrics(peak_rss_bytes=800, baseline_rss_bytes=200)
        panel = render_live_panel(metrics, elapsed=10.0, phase="testing")
        assert "fin3" in str(panel.title)

    def test_panel_contains_key_fields(self) -> None:
        metrics = SampledMetrics(
            peak_rss_bytes=800, baseline_rss_bytes=200,
            network_bytes=1024, fetch_count=3,
        )
        panel = render_live_panel(
            metrics, elapsed=42.0, phase="fetching...",
            symbols=["AAPL", "MSFT"], resolution="1m",
        )
        console = Console(record=True, width=80)
        console.print(panel)
        text = console.export_text()
        assert "AAPL" in text
        assert "1m" in text
        assert "fetching..." in text


class TestRenderSummary:
    def test_summary_contains_fields(self) -> None:
        metrics = SampledMetrics(
            peak_rss_bytes=842_000_000,
            baseline_rss_bytes=100_000_000,
            network_bytes=512_000_000,
            fetch_count=5,
            disk_after_bytes=128_000_000,
            disk_before_bytes=0,
            library_total_bytes=1_200_000_000,
        )
        panel = render_summary(
            metrics, elapsed=42.3,
            symbols=["AAPL", "MSFT"], resolution="1m",
            rows=1_204_800, library="equities-1m-databento",
        )
        console = Console(record=True, width=80)
        console.print(panel)
        text = console.export_text()
        assert "AAPL" in text
        assert "1m" in text
        assert "1,204,800" in text
        assert "equities-1m-databento" in text


# ---------------------------------------------------------------------------
# tmux tests
# ---------------------------------------------------------------------------


class TestTmuxHelpers:
    def test_is_in_tmux_false_no_env(self, monkeypatch) -> None:
        monkeypatch.delenv("TMUX", raising=False)
        assert is_in_tmux() is False

    def test_is_in_tmux_true_with_env(self, monkeypatch) -> None:
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
        assert is_in_tmux() is True

    def test_kill_pane_none_is_noop(self) -> None:
        kill_pane(None)  # should not raise


# ---------------------------------------------------------------------------
# Tracker tests
# ---------------------------------------------------------------------------


class TestResourceTracker:
    def test_tracker_lifecycle(self, storage) -> None:
        """Verify tracker enters/exits cleanly and writes metrics file."""
        provider = MagicMock()
        provider.fetch = MagicMock(return_value=make_ohlcv("2024-01-02 09:30", periods=5, freq="1min"))

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with tracker:
            tracker.set_phase("working")
            time.sleep(0.2)

        # Provider fetch should have been wrapped and restored
        assert provider.fetch is not None

    def test_tracker_wraps_provider_fetch(self, storage) -> None:
        """Verify the byte counter accumulates during fetch calls."""
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        expected_bytes = int(df.memory_usage(deep=True).sum())
        provider = MagicMock()
        original_fetch = MagicMock(return_value=df)
        provider.fetch = original_fetch

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with tracker:
            # Call the wrapped fetch — should still work and return df
            result = provider.fetch("AAPL")
            assert len(result) == 10
            assert tracker._byte_counter.total_bytes == expected_bytes
            assert tracker._byte_counter.fetch_count == 1

        # After exit, fetch is restored
        assert provider.fetch is original_fetch

    def test_tracker_restores_provider_on_exception(self, storage) -> None:
        """Verify provider is restored even if an exception occurs."""
        original_fetch = MagicMock(return_value=pd.DataFrame())
        provider = MagicMock()
        provider.fetch = original_fetch

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with pytest.raises(RuntimeError, match="boom"):
            with tracker:
                raise RuntimeError("boom")

        assert provider.fetch is original_fetch

    def test_tracker_writes_metrics_file(self, storage) -> None:
        """Verify the metrics file is written during tracking."""
        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with tracker:
            time.sleep(0.2)
            # File should exist and be valid JSON during operation
            assert Path(tracker._metrics_file).exists()
            with open(tracker._metrics_file) as f:
                data = json.load(f)
            assert "elapsed" in data
            assert "symbols" in data

    def test_tracker_cleans_up_metrics_file(self, storage) -> None:
        """Verify temp metrics file is removed after exit."""
        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with tracker:
            time.sleep(0.1)
            path = tracker._metrics_file

        assert not Path(path).exists()

    def test_tracker_set_phase_and_rows(self, storage) -> None:
        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )

        with tracker:
            tracker.set_phase("custom phase")
            tracker.set_rows(999)
            assert tracker._phase == "custom phase"
            assert tracker._rows == 999
