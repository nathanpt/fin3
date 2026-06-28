"""Standalone display script for the tmux monitor pane.

Usage::

    python -m fin3.monitoring.display <metrics_file.json>

Tails the JSON metrics file written by ``ResourceTracker`` and renders a
live ``rich`` panel. Exits when the file reports ``done: true`` (after a
brief display of the final state).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live

from fin3.monitoring.collector import SampledMetrics
from fin3.monitoring.render import render_live_panel, render_summary

METRICS_INTERVAL = 0.5
FINAL_HOLD_SECONDS = 2.0
POLL_INTERVAL = 0.5


def _read_metrics(path: Path) -> tuple[dict[str, Any], bool]:
    """Read the metrics JSON file.

    Returns ``(data, done)`` where ``done`` indicates the tracker has
    written a final state. Returns empty defaults if the file doesn't
    exist yet.
    """
    try:
        text = path.read_text()
        data = json.loads(text)
        done = bool(data.get("done", False))
        return data, done
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, False


def _metrics_from_dict(data: dict[str, Any]) -> SampledMetrics:
    """Build a ``SampledMetrics`` from a JSON dict."""
    return SampledMetrics(
        peak_rss_bytes=int(data.get("peak_rss_bytes", 0)),
        baseline_rss_bytes=int(data.get("baseline_rss_bytes", 0)),
        network_bytes=int(data.get("network_bytes", 0)),
        fetch_count=int(data.get("fetch_count", 0)),
        disk_before_bytes=int(data.get("disk_before_bytes", 0)),
        disk_after_bytes=int(data.get("disk_after_bytes", 0)),
        library_total_bytes=int(data.get("library_total_bytes", 0)),
    )


def run(metrics_file: str) -> None:
    """Main display loop — tail the metrics file and render a live panel."""
    path = Path(metrics_file)
    console = Console()

    with Live(console=console, refresh_per_second=2) as live:
        # Wait for the file to appear
        deadline = time.monotonic() + 10.0
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL)

        done = False
        while not done:
            data, done = _read_metrics(path)
            metrics = _metrics_from_dict(data)
            elapsed = float(data.get("elapsed", 0.0))
            phase = str(data.get("phase", ""))
            symbols: list[str] | None = data.get("symbols")
            if isinstance(symbols, str):
                symbols = [s.strip() for s in symbols.split(",") if s.strip()]
            resolution = str(data.get("resolution", ""))

            live.update(
                render_live_panel(metrics, elapsed, phase, symbols, resolution)
            )
            if not done:
                time.sleep(METRICS_INTERVAL)

        # Show final state
        data, _ = _read_metrics(path)
        metrics = _metrics_from_dict(data)
        elapsed = float(data.get("elapsed", 0.0))
        symbols = data.get("symbols")
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        resolution = str(data.get("resolution", ""))
        rows = int(data.get("rows", 0))
        library = str(data.get("library", ""))

        live.update(
            render_summary(metrics, elapsed, symbols, resolution, rows, library)
        )
        time.sleep(FINAL_HOLD_SECONDS)


def main() -> None:
    """Entry point for the standalone monitoring display process."""
    if len(sys.argv) < 2:
        print("Usage: python -m fin3.monitoring.display <metrics_file.json>", file=sys.stderr)
        sys.exit(1)
    run(sys.argv[1])


if __name__ == "__main__":
    main()
