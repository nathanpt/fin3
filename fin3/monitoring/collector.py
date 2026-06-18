"""Metrics collection utilities for resource monitoring.

Provides:
- ``RSSSampler`` — background thread sampling process RSS via psutil.
- ``compute_symbol_sizes`` — total size for a subset of symbols (per-scan).
- ``compute_library_size`` — total size of an entire library (one scan).
- ``ByteCounter`` — accumulates payload bytes from provider responses.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pandas as pd
import psutil
import structlog

from fin3.storage.arctic import ArcticStorage

logger = structlog.get_logger(__name__)


@dataclass
class SampledMetrics:
    """Accumulated metrics sampled during a tracked operation."""

    peak_rss_bytes: int = 0
    baseline_rss_bytes: int = 0
    network_bytes: int = 0
    fetch_count: int = 0
    disk_before_bytes: int = 0
    disk_after_bytes: int = 0
    library_total_bytes: int = 0
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def rss_delta_bytes(self) -> int:
        return max(0, self.peak_rss_bytes - self.baseline_rss_bytes)

    @property
    def disk_delta_bytes(self) -> int:
        return self.disk_after_bytes - self.disk_before_bytes


class RSSSampler:
    """Background thread that samples process RSS at a fixed interval.

    Usage::

        sampler = RSSSampler(interval=0.5)
        sampler.start()
        # ... do work ...
        sampler.stop()
        peak = sampler.peak_rss
    """

    def __init__(self, interval: float = 0.5) -> None:
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._peak_rss: int = 0
        self._baseline_rss: int = 0

    @property
    def peak_rss(self) -> int:
        return self._peak_rss

    @property
    def baseline_rss(self) -> int:
        return self._baseline_rss

    def start(self) -> None:
        self._baseline_rss = psutil.Process().memory_info().rss
        self._peak_rss = self._baseline_rss
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                rss = psutil.Process().memory_info().rss
                if rss > self._peak_rss:
                    self._peak_rss = rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None
        # Final sample
        try:
            rss = psutil.Process().memory_info().rss
            if rss > self._peak_rss:
                self._peak_rss = rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


class ByteCounter:
    """Accumulates byte counts from DataFrames returned by provider fetch calls."""

    def __init__(self) -> None:
        self._total_bytes: int = 0
        self._fetch_count: int = 0
        self._lock = threading.Lock()

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def fetch_count(self) -> int:
        return self._fetch_count

    def add(self, df: pd.DataFrame) -> None:
        """Record bytes from a DataFrame returned by a provider fetch."""
        with self._lock:
            if df.empty:
                self._fetch_count += 1
                return
            self._total_bytes += int(df.memory_usage(deep=True).sum())
            self._fetch_count += 1

    def reset(self) -> None:
        with self._lock:
            self._total_bytes = 0
            self._fetch_count = 0


def compute_symbol_sizes(
    storage: ArcticStorage,
    library: str,
    symbols: list[str],
) -> int:
    """Return the total compressed size for a subset of *symbols* in *library*.

    Uses one ``get_sizes_for_symbol`` call per requested symbol. Cheap when
    *symbols* is small (the common case for a single ``get_data`` call).
    """
    return sum(storage.get_symbol_size(library, sym) for sym in symbols)


def compute_library_size(storage: ArcticStorage, library: str) -> int:
    """Return the total compressed size of the entire *library*.

    Uses a single ``get_sizes()`` scan — one backend round-trip instead of
    one per symbol — and avoids double-counting library-global key types.
    Use this for the library-total display; use ``compute_symbol_sizes`` for
    per-symbol deltas.
    """
    return storage.get_library_size(library)
