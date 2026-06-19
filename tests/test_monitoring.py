"""Tests for fin3.monitoring — collector, tracker, render, tmux helpers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from rich.console import Console

from fin3.monitoring.collector import (
    ByteCounter,
    RSSSampler,
    SampledMetrics,
    compute_library_size,
    compute_symbol_sizes,
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


class TestComputeSymbolSizes:
    def test_returns_symbol_total(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)

        total = compute_symbol_sizes(storage, "test-lib", ["AAPL"])
        assert total > 0

    def test_only_counts_requested_symbols(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)

        one = compute_symbol_sizes(storage, "test-lib", ["AAPL"])
        both = compute_symbol_sizes(storage, "test-lib", ["AAPL", "MSFT"])
        assert both > one

    def test_empty_symbol_list(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=5, freq="1min")
        storage.write("test-lib", "AAPL", df)
        assert compute_symbol_sizes(storage, "test-lib", []) == 0

    def test_missing_symbol_counts_zero(self, storage) -> None:
        # get_symbol_size returns 0 for missing symbols
        total = compute_symbol_sizes(storage, "test-lib", ["NOPE"])
        assert total == 0


class TestComputeLibrarySize:
    def test_returns_library_total(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)

        total = compute_library_size(storage, "test-lib")
        assert total > 0

    def test_total_gte_single_symbol(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)

        library_total = compute_library_size(storage, "test-lib")
        symbol_total = compute_symbol_sizes(storage, "test-lib", ["AAPL"])
        assert library_total >= symbol_total

    def test_empty_library(self, storage) -> None:
        assert compute_library_size(storage, "empty-lib") == 0


class TestGetLibrarySize:
    def test_storage_get_library_size(self, storage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "MSFT", df)
        assert storage.get_library_size("test-lib") > 0

    def test_empty_library_returns_zero(self, storage) -> None:
        assert storage.get_library_size("empty-lib") == 0


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

    def test_tracker_writes_metrics_file(self, storage, monkeypatch) -> None:
        """Verify the metrics file is written during tracking (tmux mode).

        The shared metrics file is now tmux-exclusive (the inline TTY path
        reads tracker state directly via the live renderable), so simulate
        tmux mode without spawning a real pane.
        """
        import fin3.monitoring.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "is_in_tmux", lambda: True)
        monkeypatch.setattr(tracker_mod, "create_monitor_pane", lambda *a, **k: None)

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
            time.sleep(0.7)  # let the writer thread flush at least once
            # File should exist and be valid JSON during operation
            assert Path(tracker._metrics_file).exists()
            with open(tracker._metrics_file) as f:
                data = json.load(f)
            assert "elapsed" in data
            assert "symbols" in data

    def test_tracker_cleans_up_metrics_file(self, storage, monkeypatch) -> None:
        """Verify temp metrics file is removed after exit (tmux mode)."""
        import fin3.monitoring.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "is_in_tmux", lambda: True)
        monkeypatch.setattr(tracker_mod, "create_monitor_pane", lambda *a, **k: None)

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
            time.sleep(0.7)
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

    def test_inline_live_display_created_on_tty(self, storage) -> None:
        """On a TTY without tmux, an inline rich.live display is created."""
        from io import StringIO

        from rich.console import Console as RichConsole

        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )
        # Force the TTY path and redirect rendering to a buffer so the test
        # runs without a real terminal.
        buf = StringIO()
        tracker._is_tty = True
        tracker._console = RichConsole(file=buf, force_terminal=True, width=80)

        with tracker:
            # While inside the context, the inline live display must be active.
            assert tracker._live is not None
            time.sleep(0.7)  # let the writer thread refresh the live panel

        # After exit, the live display is stopped and cleared.
        assert tracker._live is None
        output = buf.getvalue()
        assert "fin3" in output  # panel title rendered at least once

    def test_no_live_display_when_not_a_tty(self, storage, monkeypatch) -> None:
        """Non-TTY (piped/CI) must not start a live display; summary only."""
        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )
        tracker._is_tty = False
        # Also ensure not detected as tmux
        monkeypatch.delenv("TMUX", raising=False)

        with tracker:
            assert tracker._live is None
            assert tracker._pane_id is None
