"""``ResourceTracker`` — context manager for resource monitoring.

Wraps a fin3 operation (e.g. ``MarketDataFetcher.get_data()``) to track
memory, disk, and network usage, rendering a live panel in tmux (or inline)
and printing a summary panel on exit.

Usage::

    with ResourceTracker(storage, provider, library, symbols, resolution):
        df = fetcher.get_data(...)
    # Summary panel printed automatically on exit
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd
import structlog
from rich.console import Console
from rich.live import Live

from fin3.monitoring.collector import (
    ByteCounter,
    RSSSampler,
    SampledMetrics,
    compute_library_size,
    compute_symbol_sizes,
)
from fin3.monitoring.render import render_live_panel, render_summary
from fin3.monitoring.tmux import create_monitor_pane, is_in_tmux, kill_pane

if TYPE_CHECKING:
    from fin3.providers.base import DataProvider
    from fin3.schemas import Resolution
    from fin3.storage.arctic import ArcticStorage

logger = structlog.get_logger(__name__)

SAMPLE_INTERVAL = 0.5


class ResourceTracker:
    """Context manager that instruments resource usage during a fin3 operation.

    On enter:
    - Capture baseline disk sizes for the affected symbols.
    - Start an RSS sampler thread.
    - Wrap the provider's ``fetch()`` to count network bytes.
    - Create a tmux monitor pane (if in tmux) or inline live display.

    On exit:
    - Capture final disk sizes and compute deltas.
    - Write final metrics to the shared file (signals display to stop).
    - Render the summary panel to stderr.
    - Kill the tmux pane (if created).
    """

    def __init__(
        self,
        storage: ArcticStorage,
        provider: DataProvider,
        library: str,
        symbols: list[str],
        resolution: Resolution,
    ) -> None:
        self._storage = storage
        self._provider = provider
        self._library = library
        self._symbols = symbols
        self._resolution = resolution

        self._sampler = RSSSampler(interval=SAMPLE_INTERVAL)
        self._byte_counter = ByteCounter()
        self._original_fetch: Callable[..., pd.DataFrame] | None = None
        self._start_time: float = 0.0
        self._end_time: float = 0.0

        self._metrics_file: str = ""
        self._pane_id: str | None = None
        self._live: Live | None = None
        self._writer_thread: threading.Thread | None = None
        self._stop_writer = threading.Event()
        self._phase: str = ""

        self._rows: int = 0
        self._is_tty: bool = sys.stderr.isatty()
        self._console = Console(stderr=True)

        # Disk metrics: affected-symbol sizes are measured on enter and exit
        # (cheap, a few symbols). The whole-library total is measured once on
        # exit via a single get_sizes() scan (one round-trip, not N).
        self._disk_before: int = 0
        self._disk_after: int = 0
        self._library_total: int = 0

    def set_phase(self, phase: str) -> None:
        """Update the current operation phase for live display."""
        self._phase = phase

    def set_rows(self, rows: int) -> None:
        """Set the total row count for the summary."""
        self._rows = rows

    def __enter__(self) -> ResourceTracker:
        self._start_time = time.monotonic()

        # Baseline disk sizes for the affected symbols only (cheap).
        # The whole-library total is deferred to exit so startup isn't
        # blocked by scanning every symbol in the library.
        self._disk_before = compute_symbol_sizes(
            self._storage, self._library, self._symbols,
        )

        # Start memory sampling
        self._sampler.start()

        # Wrap provider fetch for network byte counting
        self._wrap_provider()

        # Set up display
        self._setup_display()

        logger.info("monitor.tracker_started",
                     library=self._library, symbols=self._symbols)

        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self._end_time = time.monotonic()

        # Stop memory sampling
        self._sampler.stop()

        # Restore original provider fetch
        self._restore_provider()

        # Final disk sizes: affected symbols (delta) + one whole-library
        # scan for the total (single get_sizes() call, not per-symbol).
        self._disk_after = compute_symbol_sizes(
            self._storage, self._library, self._symbols,
        )
        self._library_total = compute_library_size(self._storage, self._library)

        # Stop writer thread
        self._stop_writer.set()
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=2.0)

        # Build final metrics
        metrics = self._build_metrics()

        # Write final metrics (signals display to show summary and exit)
        self._write_metrics(metrics, done=True)

        # Kill tmux pane (after display has time to read final state)
        if self._pane_id is not None:
            time.sleep(2.5)  # let display render final summary
            kill_pane(self._pane_id)

        # Stop inline live display before rendering the final summary
        if self._live is not None:
            self._live.stop()
            self._live = None

        # Render summary to stderr
        if self._is_tty or True:  # always print summary
            elapsed = self._end_time - self._start_time
            panel = render_summary(
                metrics,
                elapsed,
                symbols=self._symbols,
                resolution=self._resolution.value,
                rows=self._rows,
                library=self._library,
            )
            self._console.print(panel)

        # Clean up temp file
        if self._metrics_file and os.path.exists(self._metrics_file):
            try:
                os.unlink(self._metrics_file)
            except OSError:
                pass

        logger.info("monitor.tracker_finished",
                     library=self._library,
                     duration=self._end_time - self._start_time,
                     peak_rss=metrics.peak_rss_bytes,
                     network_bytes=metrics.network_bytes,
                     disk_delta=metrics.disk_delta_bytes)

        return

    def _wrap_provider(self) -> None:
        """Monkey-patch the provider's fetch method to count bytes."""
        self._original_fetch = self._provider.fetch
        byte_counter = self._byte_counter

        def instrumented_fetch(*args: Any, **kwargs: Any) -> pd.DataFrame:
            assert self._original_fetch is not None
            result = self._original_fetch(*args, **kwargs)
            byte_counter.add(result)
            return result

        self._provider.fetch = instrumented_fetch  # type: ignore[method-assign]

    def _restore_provider(self) -> None:
        """Restore the original provider fetch method."""
        if self._original_fetch is not None:
            self._provider.fetch = self._original_fetch  # type: ignore[method-assign]
            self._original_fetch = None

    def _setup_display(self) -> None:
        """Set up the live display.

        - Inside tmux: open a dedicated monitor pane (separate process).
        - Native TTY (no tmux): an inline ``rich.live`` display on stderr.
        - Non-TTY (piped / CI / tests): no live display; summary only.

        A metrics file + writer thread are always created (used by the tmux
        pane, and cheap otherwise).
        """
        # Always create a metrics file for the tmux display
        fd, self._metrics_file = tempfile.mkstemp(
            suffix=".json", prefix="fin3-monitor-", dir="/tmp",
        )
        os.close(fd)
        self._write_initial_metrics()

        if is_in_tmux():
            self._pane_id = create_monitor_pane(self._metrics_file)
        elif self._is_tty:
            # Native terminal without tmux: render an inline live panel on
            # stderr. redirect_stderr routes structlog JSON lines above the
            # bar so they interleave cleanly instead of corrupting the redraw.
            initial = render_live_panel(
                SampledMetrics(baseline_rss_bytes=self._sampler.baseline_rss),
                0.0,
                "initialising...",
                self._symbols,
                self._resolution.value,
            )
            self._live = Live(
                initial,
                console=self._console,
                refresh_per_second=2,
                transient=False,
                redirect_stdout=False,
                redirect_stderr=True,
            )
            self._live.start()

        # Start writer thread regardless — logs structured metrics too
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True,
        )
        self._writer_thread.start()

    def _write_initial_metrics(self) -> None:
        """Write initial metrics so the display can start rendering immediately."""
        data = {
            "peak_rss_bytes": 0,
            "baseline_rss_bytes": self._sampler.baseline_rss,
            "network_bytes": 0,
            "fetch_count": 0,
            "disk_before_bytes": self._disk_before,
            "disk_after_bytes": self._disk_before,
            "library_total_bytes": self._library_total,
            "elapsed": 0.0,
            "phase": "initialising...",
            "symbols": self._symbols,
            "resolution": self._resolution.value,
            "rows": 0,
            "library": self._library,
            "done": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._metrics_file, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _writer_loop(self) -> None:
        """Background thread that writes metrics to the shared file."""
        while not self._stop_writer.wait(SAMPLE_INTERVAL):
            self._write_current_metrics(done=False)

    def _write_current_metrics(self, done: bool) -> None:
        """Write current metrics to the shared file."""
        elapsed = time.monotonic() - self._start_time
        data = {
            "peak_rss_bytes": self._sampler.peak_rss,
            "baseline_rss_bytes": self._sampler.baseline_rss,
            "network_bytes": self._byte_counter.total_bytes,
            "fetch_count": self._byte_counter.fetch_count,
            "disk_before_bytes": self._disk_before,
            "disk_after_bytes": self._disk_before,
            "library_total_bytes": self._library_total,
            "elapsed": elapsed,
            "phase": self._phase,
            "symbols": self._symbols,
            "resolution": self._resolution.value,
            "rows": self._rows,
            "library": self._library,
            "done": done,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._metrics_file, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

        # Inline live display (native TTY): refresh the panel.
        if self._live is not None:
            try:
                live_metrics = SampledMetrics(
                    peak_rss_bytes=self._sampler.peak_rss,
                    baseline_rss_bytes=self._sampler.baseline_rss,
                    network_bytes=self._byte_counter.total_bytes,
                    fetch_count=self._byte_counter.fetch_count,
                    disk_before_bytes=self._disk_before,
                    disk_after_bytes=self._disk_before,
                )
                self._live.update(
                    render_live_panel(
                        live_metrics, elapsed, self._phase,
                        self._symbols, self._resolution.value,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - display must not crash the run
                logger.warning("monitor.live_update_failed", error=str(exc))

    def _write_metrics(self, metrics: SampledMetrics, done: bool) -> None:
        """Write final metrics to the shared file."""
        if not self._metrics_file:
            return
        elapsed = self._end_time - self._start_time
        data = {
            "peak_rss_bytes": metrics.peak_rss_bytes,
            "baseline_rss_bytes": metrics.baseline_rss_bytes,
            "network_bytes": metrics.network_bytes,
            "fetch_count": metrics.fetch_count,
            "disk_before_bytes": metrics.disk_before_bytes,
            "disk_after_bytes": metrics.disk_after_bytes,
            "library_total_bytes": metrics.library_total_bytes,
            "elapsed": elapsed,
            "phase": "complete",
            "symbols": self._symbols,
            "resolution": self._resolution.value,
            "rows": self._rows,
            "library": self._library,
            "done": done,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._metrics_file, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _build_metrics(self) -> SampledMetrics:
        """Build final ``SampledMetrics`` from all collected data."""
        return SampledMetrics(
            peak_rss_bytes=self._sampler.peak_rss,
            baseline_rss_bytes=self._sampler.baseline_rss,
            network_bytes=self._byte_counter.total_bytes,
            fetch_count=self._byte_counter.fetch_count,
            disk_before_bytes=self._disk_before,
            disk_after_bytes=self._disk_after,
            library_total_bytes=self._library_total,
        )
