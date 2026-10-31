"""Tests for fin3.monitoring — collector, tracker, render, tmux helpers."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from rich.console import Console

from fin3.monitoring import display
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
        # Known byte delta: captured from the pristine frame BEFORE the
        # pipeline touches it. memory_usage(deep=True) can materialise
        # string/other caches as a side effect, so measuring the frame after
        # ByteCounter.add() has run would risk pinning a mutated reading.
        expected_bytes = int(df.memory_usage(deep=True).sum())
        counter = ByteCounter()
        counter.add(df)
        assert counter.fetch_count == 1
        assert counter.total_bytes == expected_bytes

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
        # Allocate ~50 MiB AFTER start(): bytes() zero-fills its buffer, so
        # every page is touched and resident RSS must rise above the
        # baseline. stop() takes a synchronous final sample, so the peak is
        # observed without any real sleep.
        ballast = [bytes(1024 * 1024) for _ in range(50)]
        sampler.stop()
        assert sampler.baseline_rss > 0
        assert sampler.peak_rss > sampler.baseline_rss
        del ballast

    def test_stop_freezes_peak_rss(self) -> None:
        sampler = RSSSampler(interval=0.1)
        sampler.start()
        sampler.stop()
        peak = sampler.peak_rss
        # Sampling is over: later allocations must not move the recorded peak.
        _ = [b"x" * 100_000 for _ in range(200)]
        assert sampler.peak_rss == peak


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
        # Delegation contract: the sum of per-symbol storage sizes, exactly
        # what the backend reports (the >0 guard keeps 0==0 from passing
        # vacuously if the backend ever silently loses the write).
        assert total == storage.get_symbol_size("test-lib", "AAPL")
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
        # Delegation contract: one get_library_size() scan, exactly the
        # backend's whole-library total.
        assert total == storage.get_library_size("test-lib")
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

    def test_kill_pane_none_is_noop(self, monkeypatch) -> None:
        import fin3.monitoring.tmux as tmux_mod

        def _fail(*args, **kwargs):
            raise AssertionError("subprocess.run must not be called for None pane")

        monkeypatch.setattr(tmux_mod.subprocess, "run", _fail)
        kill_pane(None)  # neither invokes tmux nor raises


# ---------------------------------------------------------------------------
# Tracker tests
# ---------------------------------------------------------------------------


def _await_metrics_json(tracker: ResourceTracker, timeout: float = 2.0) -> dict[str, object]:
    """Poll the tracker's metrics file until it parses as JSON.

    The tmux writer thread flushes on its SAMPLE_INTERVAL, so arrival is
    asynchronous: poll at 0.05s (never sleep longer than that) until the
    deadline, then fail with context instead of racing a fixed sleep.
    """
    path = Path(tracker._metrics_file)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(path) as f:
                data: dict[str, object] = json.load(f)
            return data
        except (OSError, json.JSONDecodeError):
            time.sleep(0.05)
    raise AssertionError(
        f"metrics file {path} did not contain valid JSON within {timeout}s"
    )


class TestResourceTracker:
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

    def test_tracker_restores_provider_when_enter_fails(
        self, storage, monkeypatch
    ) -> None:
        """If ``__enter__`` raises (e.g. display setup fails), the provider
        must still be restored.

        The context-manager protocol does NOT call ``__exit__`` when
        ``__enter__`` fails, so without the abort path the monkey-patch and
        sampler thread would leak. (Contrast with
        ``test_tracker_restores_provider_on_exception``, which raises inside
        the ``with`` body and so exercises ``__exit__``, not ``__enter__``.)
        """
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
        # Force the last __enter__ step (display setup) to fail.
        monkeypatch.setattr(
            tracker,
            "_setup_display",
            MagicMock(side_effect=RuntimeError("display boom")),
        )

        with pytest.raises(RuntimeError, match="display boom"):
            tracker.__enter__()

        # __enter__ raised -> __exit__ never ran, but _abort_setup must have
        # restored the provider and stopped the sampler (no leaked patch/thread).
        assert provider.fetch is original_fetch
        assert tracker._sampler._thread is None

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
            # Writer thread flushes asynchronously: poll until the file
            # parses instead of sleeping a fixed 0.7s.
            data = _await_metrics_json(tracker)
            assert data["symbols"] == ["AAPL"]
            assert data["done"] is False
            assert isinstance(data["elapsed"], (int, float))
            assert data["elapsed"] >= 0

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
            # _setup_display writes the initial metrics synchronously, so the
            # file already exists here — no waiting required. __exit__ joins
            # the writer thread BEFORE unlinking, so no late write can race.
            path = tracker._metrics_file
            assert Path(path).exists()

        assert not Path(path).exists()

    def test_tracker_set_phase_and_rows(self, storage, monkeypatch) -> None:
        """Values passed to set_phase/set_rows surface in the metrics JSON."""
        import fin3.monitoring.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "is_in_tmux", lambda: True)
        monkeypatch.setattr(tracker_mod, "create_monitor_pane", lambda *a, **k: None)
        # Keep the writer thread's timer from firing so the test reads a
        # deterministic file flushed below.
        monkeypatch.setattr(tracker_mod, "SAMPLE_INTERVAL", 60.0)

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
            # Flush a snapshot synchronously (the writer thread calls the
            # same method on its timer) and parse what the pane would read.
            tracker._write_current_metrics(done=False)
            with open(tracker._metrics_file) as f:
                data = json.load(f)
            assert data["phase"] == "custom phase"
            assert data["rows"] == 999

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

        # After exit, the live display is stopped and cleared. Live.stop()
        # renders one final frame for non-transient displays, so the panel
        # reaches the buffer deterministically — no refresh-thread wait.
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

    def test_live_view_renders_current_state_without_writer_tick(
        self, storage
    ) -> None:
        """ADD-42 regression: the inline TTY display renders via a dynamic
        renderable whose ``__rich__`` re-reads tracker state on every tick,
        so the panel changes when sampler/counter state mutates — with no
        writer thread tick (the inline path has no writer thread at all).
        """
        from io import StringIO

        from rich.console import Console as RichConsole

        def _panel_text(renderable: object) -> str:
            console = RichConsole(record=True, width=80)
            console.print(renderable)
            return console.export_text()

        provider = MagicMock()
        provider.fetch = MagicMock(return_value=pd.DataFrame())

        tracker = ResourceTracker(
            storage=storage,
            provider=provider,
            library="test-lib",
            symbols=["AAPL"],
            resolution=Resolution.ONE_MINUTE,
        )
        # Force the TTY path, same pattern as the sibling TTY tests.
        tracker._is_tty = True
        tracker._console = RichConsole(file=StringIO(), force_terminal=True, width=80)

        with tracker:
            # Inline TTY mode: no tmux pane, no metrics file, no writer thread.
            assert tracker._live is not None
            assert tracker._pane_id is None
            assert tracker._metrics_file == ""
            assert tracker._writer_thread is None

            # Live drives itself: rendering the live renderable reads the
            # tracker's CURRENT state (rich's refresh thread calls __rich__
            # per tick; rendering it here plays the role of one tick).
            before = _panel_text(tracker._live.renderable)

            tracker._byte_counter.add(
                make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
            )
            tracker.set_phase("counting symbols")

            after = _panel_text(tracker._live.renderable)

        assert "0 fetches" in before
        assert "counting symbols" not in before
        assert "1 fetches" in after
        assert "counting symbols" in after

    def test_exit_kills_tmux_pane(self, storage, monkeypatch) -> None:
        """ADD-43: tracker teardown kills the tmux monitor pane it created
        (after its internal final-summary hold, which is patched here —
        no real sleep)."""
        import fin3.monitoring.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "is_in_tmux", lambda: True)
        monkeypatch.setattr(
            tracker_mod, "create_monitor_pane", lambda *a, **k: "%7"
        )
        killed: list[str] = []
        monkeypatch.setattr(
            tracker_mod, "kill_pane", lambda pane_id: killed.append(pane_id)
        )
        # Patch the tracker's view of `time`: record the teardown hold
        # instead of really sleeping 2.5s (the _FakeClock pattern below).
        clock = _FakeClock()
        monkeypatch.setattr(tracker_mod, "time", clock)

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
            assert tracker._pane_id == "%7"

        assert killed == ["%7"]
        # The exit path held for the final-summary window (patched, not real).
        assert clock.sleeps == [2.5]


# ---------------------------------------------------------------------------
# Display tests
# ---------------------------------------------------------------------------


class _FakeLive:
    """Minimal stand-in for ``rich.live.Live`` capturing updates in memory."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.updates: list[object] = []

    def __enter__(self) -> "_FakeLive":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def update(self, renderable: object) -> None:
        self.updates.append(renderable)


class _FakeClock:
    """Replaces the ``time`` module inside ``display``: no real waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep()


DONE_PAYLOAD = {
    "done": True,
    "elapsed": 65.0,
    "phase": "done",
    "symbols": "AAPL,MSFT",
    "resolution": "1min",
    "rows": 240,
    "library": "equities-1m",
    "network_bytes": "2048",
}

LIVE_TITLE = "[bold]fin3[/bold] monitor"
SUMMARY_TITLE = "[bold]fin3 resource summary[/bold]"


def _install_fakes(monkeypatch) -> tuple[_FakeLive, _FakeClock]:
    live = _FakeLive()
    clock = _FakeClock()
    monkeypatch.setattr(display, "Live", lambda *a, **k: live)
    monkeypatch.setattr(display, "time", clock)
    return live, clock


class TestReadMetrics:
    def test_missing_file_returns_empty_defaults(self, tmp_path: Path) -> None:
        assert display._read_metrics(tmp_path / "absent.json") == ({}, False)

    def test_garbled_json_falls_back_to_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "metrics.json"
        path.write_text("{not valid json")
        assert display._read_metrics(path) == ({}, False)

    def test_done_flag_and_payload_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps({"done": True, "phase": "done"}))
        data, done = display._read_metrics(path)
        assert done is True
        assert data["phase"] == "done"

        path.write_text(json.dumps({"phase": "fetching..."}))
        _, done = display._read_metrics(path)
        assert done is False


class TestMetricsFromDict:
    def test_missing_keys_default_to_zero(self) -> None:
        m = display._metrics_from_dict({})
        assert m.peak_rss_bytes == 0
        assert m.baseline_rss_bytes == 0
        assert m.network_bytes == 0
        assert m.fetch_count == 0
        assert m.disk_before_bytes == 0
        assert m.disk_after_bytes == 0
        assert m.library_total_bytes == 0

    def test_coerces_numeric_strings_and_truncates_floats(self) -> None:
        m = display._metrics_from_dict(
            {
                "peak_rss_bytes": "4096",
                "network_bytes": 12.9,
                "fetch_count": "3",
            }
        )
        assert m.peak_rss_bytes == 4096
        assert m.network_bytes == 12  # int() truncates floats
        assert m.fetch_count == 3

    def test_non_numeric_string_raises_value_error(self) -> None:
        # Boundary of the coercion rule: digits coerce, words do not.
        with pytest.raises(ValueError):
            display._metrics_from_dict({"fetch_count": "abc"})


class TestRunLoop:
    def test_done_file_renders_summary_and_exits(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A done-flagged file: one panel update, summary, final hold, exit."""
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps(DONE_PAYLOAD))
        live, clock = _install_fakes(monkeypatch)

        display.run(str(path))

        assert len(live.updates) == 2
        assert live.updates[0].title == LIVE_TITLE
        assert live.updates[-1].title == SUMMARY_TITLE
        # Done on first read: no polling sleep, only the final hold.
        assert clock.sleeps == [display.FINAL_HOLD_SECONDS]

    def test_polls_until_done_then_exits(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Loop keeps polling at METRICS_INTERVAL until done flips true."""
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps({"phase": "fetching..."}))  # done: false
        live, clock = _install_fakes(monkeypatch)

        def flip_to_done() -> None:
            clock.on_sleep = None
            path.write_text(json.dumps(DONE_PAYLOAD))

        clock.on_sleep = flip_to_done

        display.run(str(path))

        assert clock.sleeps == [display.METRICS_INTERVAL, display.FINAL_HOLD_SECONDS]
        # panel (running), panel (done), then the final summary.
        assert len(live.updates) == 3
        assert live.updates[-1].title == SUMMARY_TITLE

    def test_waits_for_file_to_appear(self, tmp_path: Path, monkeypatch) -> None:
        """Missing metrics file: poll at POLL_INTERVAL until it shows up."""
        path = tmp_path / "metrics.json"
        live, clock = _install_fakes(monkeypatch)

        def create_file() -> None:
            clock.on_sleep = None
            path.write_text(json.dumps(DONE_PAYLOAD))

        clock.on_sleep = create_file

        display.run(str(path))

        assert display.POLL_INTERVAL in clock.sleeps
        assert clock.sleeps[-1] == display.FINAL_HOLD_SECONDS
        assert live.updates[-1].title == SUMMARY_TITLE


class TestMainEntryPoint:
    def test_missing_argument_prints_usage_and_exits(
        self, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["fin3.monitoring.display"])
        with pytest.raises(SystemExit) as exc:
            display.main()
        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err

    def test_dispatches_to_run_with_metrics_path(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        metrics = tmp_path / "metrics.json"
        calls: list[str] = []
        monkeypatch.setattr(sys, "argv", ["fin3.monitoring.display", str(metrics)])
        monkeypatch.setattr(display, "run", lambda metrics_file: calls.append(metrics_file))

        display.main()

        assert calls == [str(metrics)]
