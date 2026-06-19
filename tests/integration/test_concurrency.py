"""Cross-process integration test: the per-symbol lock prevents a double-fetch.

Two processes call ``get_data()`` for the *same* symbol + range concurrently
against real MinIO. With file locking enabled they serialise: one process
detects the gap, fetches, and writes; the other acquires the lock afterwards,
sees no gap, and skips the fetch entirely. The total number of gap-fill
fetches is therefore exactly 1 (not 2).

Inherits the ``-m integration`` env-gated skip from
``tests/integration/conftest.py``: that conftest sets ``collect_ignore_glob``
to hide every ``.py`` in this directory when the MinIO env vars are absent, so
this file is only collected when the integration environment is present.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from fin3.config.settings import LockConfig, MinioConfig
from fin3.core import MarketDataFetcher
from fin3.metadata.asset_profile import MetadataStore
from fin3.providers import ProviderRegistry
from fin3.schemas import AssetType, Resolution, library_name
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv

pytestmark = pytest.mark.integration

# Single shared bucket used by the integration suite. Mirrors
# tests/integration/conftest.py's TEST_BUCKET; replicated rather than imported
# so this module does not depend on importing the conftest at module load.
TEST_BUCKET = "test-integration-e2e"


def _worker_get_data(
    out_queue: Any,
    fetch_counter: Any,
    barrier: Any,
    minio_endpoint: str,
    minio_access: str,
    minio_secret: str,
    minio_secure: bool,
    bucket: str,
    provider_name: str,
    lock_dir: str,
) -> None:
    """Worker process: build a fetcher with a stub provider and call get_data().

    Defined at module level so it is picklable under both the fork and spawn
    multiprocessing start methods. The stub provider is constructed *inside*
    the worker because ``MagicMock`` instances are not picklable and so cannot
    be passed across the process boundary.
    """
    try:
        minio_cfg = MinioConfig(
            endpoint=minio_endpoint,
            access_key=minio_access,
            secret_key=minio_secret,
            secure=minio_secure,
            bucket=bucket,
        )
        # Real storage, real MinIO, single-bucket mode, with locking ENABLED.
        # Both workers share the same lock_dir so they contend on the same
        # lock file for the same (library, symbol) pair.
        storage = ArcticStorage(
            minio_cfg,
            lock=LockConfig(
                enabled=True,
                lock_dir=lock_dir,
                timeout_s=120.0,
                poll_interval_s=0.2,
            ),
        )

        # Build the fetcher via the __new__ bypass (mirrors tests/test_core.py's
        # _make_fetcher): no real provider connection, no config needed for
        # get_data().
        fetcher = MarketDataFetcher.__new__(MarketDataFetcher)
        fetcher._storage = storage
        fetcher._metadata = MetadataStore(storage)
        registry = ProviderRegistry.__new__(ProviderRegistry)
        registry._providers = {}
        fetcher._providers = registry

        stub = MagicMock()

        def _fetch(
            symbol: str,
            start: datetime,
            end: datetime,
            resolution: Resolution,
            **kwargs: object,
        ) -> Any:
            # The contended call the lock is designed to serialise is the
            # gap-fill fetch in _fill_gap, which passes ``asset_type=...``. The
            # metadata-discovery fetch in bootstrap_metadata does NOT pass
            # asset_type. Count only the gap-fill fetch so the counter measures
            # exactly the race this feature targets.
            if "asset_type" in kwargs:
                with fetch_counter.get_lock():
                    fetch_counter.value += 1
            # Hourly bars aligned exactly to the requested hourly grid.
            return make_ohlcv("2024-01-01 00:00", periods=4, freq="1h")

        stub.fetch.side_effect = _fetch
        stub.get_instrument_bounds.return_value = {
            "ipo_date": None,
            "delist_date": None,
        }
        registry._providers = {provider_name: stub}

        # Synchronise so both workers enter get_data() at the same instant,
        # maximising the chance of a true race. The lock makes the outcome
        # deterministic regardless of ordering, so a barrier is best-effort.
        barrier.wait(timeout=30.0)

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider=provider_name,
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc),
        )
        out_queue.put(("ok", None))
    except BaseException as exc:
        # Surface any failure (including LockAcquisitionError) to the parent.
        out_queue.put(("error", repr(exc)))


class TestConcurrentAccess:
    """Prove the per-symbol file lock serialises concurrent get_data() calls."""

    def test_concurrent_get_data_fetches_exactly_once(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        endpoint = os.environ.get("FIN3_MINIO__ENDPOINT", "")
        access = os.environ.get("FIN3_MINIO__ACCESS_KEY", "")
        secret = os.environ.get("FIN3_MINIO__SECRET_KEY", "")
        secure = os.environ.get("FIN3_MINIO__SECURE", "false").lower() == "true"
        # Belt-and-suspenders: the integration conftest already hides this file
        # when the env is absent, but skip cleanly if it is collected anyway.
        if not (endpoint and access and secret):
            pytest.skip("FIN3_MINIO__* env vars not set")

        # A unique provider name per run -> a unique library name, so parallel
        # test runs never collide. Both workers share the SAME provider name so
        # they contend on the same lock file and the same library.
        provider_name = f"databento-{os.getpid()}-{uuid4().hex[:8]}"
        lock_dir = str(tmp_path_factory.mktemp("fin3_locks"))
        lib_name = library_name(AssetType.CRYPTO, Resolution.ONE_HOUR, provider_name)

        # Pre-create the library once, single-process, before any concurrency
        # begins. The per-symbol data lock guards _ensure_symbol, but the
        # library itself is created lazily by _get_or_create_library(), which is
        # ALSO reached outside that lock by ResourceTracker's baseline size scan
        # (compute_symbol_sizes -> get_symbol_size). Without pre-creation, both
        # workers would race a check-then-create on the library metadata itself
        # (an unrelated pre-existing TOCTOU) and one would raise "Library
        # already exists". Creating it up front mirrors realistic steady-state
        # usage (the library persists after first use) and isolates this test to
        # the per-symbol DATA lock it is meant to verify.
        setup = ArcticStorage(
            MinioConfig(
                endpoint=endpoint,
                access_key=access,
                secret_key=secret,
                secure=secure,
                bucket=TEST_BUCKET,
            )
        )
        try:
            setup._get_or_create_library(lib_name)
        except Exception:
            pass

        out_queue: Any = mp.Queue()
        fetch_counter: Any = mp.Value("i", 0)
        barrier: Any = mp.Barrier(2)

        worker_args = (
            out_queue,
            fetch_counter,
            barrier,
            endpoint,
            access,
            secret,
            secure,
            TEST_BUCKET,
            provider_name,
            lock_dir,
        )
        p1 = mp.Process(target=_worker_get_data, args=worker_args)
        p2 = mp.Process(target=_worker_get_data, args=worker_args)
        p1.start()
        p2.start()

        # Generous join deadline. The provider is a stub so get_data() is fast;
        # 60s is plenty even if one worker briefly waits on the lock. No
        # pytest-timeout dependency: a manual deadline loop is used instead.
        deadline = time.monotonic() + 60
        for proc in (p1, p2):
            remaining = max(1.0, deadline - time.monotonic())
            proc.join(timeout=remaining)
            assert not proc.is_alive(), "worker process timed out"

        # Drain whatever the workers reported (non-blocking).
        results: list[tuple[str, str | None]] = []
        while True:
            try:
                results.append(out_queue.get_nowait())
            except queue.Empty:
                break

        assert len(results) == 2, f"expected 2 worker results, got {results}"
        for status, detail in results:
            assert status == "ok", (
                f"worker reported error: {detail} "
                "(a LockAcquisitionError here indicates a bug or too-short timeout)"
            )

        # Core correctness claim: exactly one gap-fill fetch under the lock.
        assert fetch_counter.value == 1, (
            f"expected exactly one gap-fill fetch under the lock, "
            f"got {fetch_counter.value}"
        )

        # Cleanup: remove the run-specific library from the shared bucket so
        # repeated runs stay tidy. Wrapped so a teardown hiccup never masks the
        # real assertion above.
        try:
            arctic = setup.arctic_for(TEST_BUCKET)
            if lib_name in arctic.list_libraries():
                arctic.delete_library(lib_name)
        except Exception:
            pass
