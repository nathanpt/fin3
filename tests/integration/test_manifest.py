"""Live integration: manifest sync through the real installed command.

Requires real MinIO and a Databento API key (env-gated by
``tests/integration/conftest.py``). Uses a fresh UUID-named single bucket —
never the shared ``TEST_BUCKET`` — so physical library names stay canonical
without touching existing session data. The fixture owns and removes only its
own bucket.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import shutil
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pytest

from fin3.config.settings import (
    ClientConfig,
    DatabentoConfig,
    LockConfig,
    MinioConfig,
)
from fin3.providers import ProviderRegistry
from fin3.providers.databento import DatabentoProvider
from fin3.storage.arctic import ArcticStorage

pytestmark = pytest.mark.integration

WINDOW_START = "2024-06-03T13:30:00Z"
WINDOW_END = "2024-06-03T13:35:59Z"
CANONICAL_LIB = "equities-1m-databento"
LOCK_TIMEOUT_S = 300.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _minio_config(bucket: str) -> MinioConfig:
    return MinioConfig(
        endpoint=_env("FIN3_MINIO__ENDPOINT"),
        access_key=_env("FIN3_MINIO__ACCESS_KEY"),
        secret_key=_env("FIN3_MINIO__SECRET_KEY"),
        secure=_env("FIN3_MINIO__SECURE", "false").lower() == "true",
        bucket=bucket,
    )


@pytest.fixture()
def manifest_bucket(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A fresh UUID bucket, fully removed after the test."""
    bucket = f"manifest-test-{uuid4().hex[:12]}"
    lock_dir = str(tmp_path_factory.mktemp("manifest_locks"))
    # Expose the shared lock dir to worker processes via an env var.
    os.environ["FIN3_TEST_LOCK_DIR"] = lock_dir
    yield bucket
    os.environ.pop("FIN3_TEST_LOCK_DIR", None)
    # Cleanup: delete our libraries, then the bucket itself (signed DELETE).
    storage = ArcticStorage(_minio_config(bucket))
    try:
        arctic = storage.arctic_for(bucket)
        for lib in list(arctic.list_libraries()):
            arctic.delete_library(lib)
        storage._bucket_request(bucket, "DELETE")
    except Exception as exc:  # a cleanup hiccup must not mask test results
        warnings.warn(
            f"integration cleanup: failed to remove bucket {bucket!r}: {exc!r}",
            RuntimeWarning,
            stacklevel=1,
        )


def _run_cli(args: list[str], bucket: str) -> tuple[int, dict | None, str]:
    """Run the real installed ``fin3`` command in a subprocess."""
    executable = shutil.which("fin3")
    if executable is None:
        executable = shutil.which("uv")
        assert executable is not None
        args = ["run", "fin3", *args]
    env = dict(os.environ)
    env["FIN3_MINIO__BUCKET"] = bucket
    proc_out = os.pipe()
    proc_err = os.pipe()
    import subprocess

    with subprocess.Popen(
        [executable, *args],
        env=env,
        stdout=proc_out[1],
        stderr=proc_err[1],
        cwd="/tmp",
    ) as proc:
        os.close(proc_out[1])
        os.close(proc_err[1])
        stdout = os.fdopen(proc_out[0]).read()
        stderr = os.fdopen(proc_err[0]).read()
        code = proc.wait(timeout=300)
    payload = json.loads(stdout) if stdout.strip() else None
    return code, payload, stderr


class TestManifestLiveSmoke:
    def test_status_dry_run_then_sync_aapl_window(
        self, manifest_bucket: str, tmp_path: Path
    ) -> None:
        # Absent-library status and dry-run must not create the bucket.
        probe = ArcticStorage(_minio_config(manifest_bucket))
        manifest_path = tmp_path / "aapl.yaml"
        manifest_path.write_text(
            f"""
version: 1
id: live-smoke
libraries:
  aapl-1m:
    asset_type: equity_us
    provider: databento
    resolution: 1m
    symbols: [AAPL]
    start: '{WINDOW_START}'
    end: '{WINDOW_END}'
    update_policy: incremental
""",
            encoding="utf-8",
        )

        assert probe.has_library(CANONICAL_LIB) is False

        code, payload, stderr = _run_cli(
            ["status", str(manifest_path), "--json"], manifest_bucket
        )
        assert code == 0, stderr
        assert payload is not None and payload["results"][0]["action"] == "create"
        code, payload, stderr = _run_cli(
            ["ensure", str(manifest_path), "--dry-run", "--json"], manifest_bucket
        )
        assert code == 0, stderr
        assert payload is not None and payload["dry_run"] is True
        # Read-only commands created neither library nor bucket.
        assert probe.has_library(CANONICAL_LIB) is False

        # Real download, gated by the declared cost ceiling.
        code, payload, stderr = _run_cli(
            [
                "sync",
                str(manifest_path),
                "--max-cost",
                "0.05",
                "--json",
            ],
            manifest_bucket,
        )

        if code == 1 and payload is not None and "error" in payload:
            # Cost ceiling hit: no data writes allowed, download proof is
            # skipped with an explicit reason and reported unverified.
            assert payload["error"]["kind"] == "operational"
            assert "max_cost" in payload["error"]["message"]
            assert probe.has_library(CANONICAL_LIB) is False
            pytest.skip(
                "live-download proof skipped: Databento estimate exceeds the "
                "USD 0.05 cap for the 2024-06-03 13:30-13:35Z AAPL window; "
                "coverage remains unverified"
            )
        assert code == 0, stderr
        assert payload is not None
        assert payload["totals"]["created"] == 1

        # Widened fetch stayed within the corresponding UTC day.
        fetch_ranges = payload["results"][0]["fetch_ranges"]
        assert len(fetch_ranges) == 1
        start_ts = pd.Timestamp(fetch_ranges[0][0])
        end_ts = pd.Timestamp(fetch_ranges[0][1])
        assert start_ts == pd.Timestamp("2024-06-03 00:00", tz="UTC")
        assert end_ts <= pd.Timestamp("2024-06-04 00:00", tz="UTC")

        # Inspect the actual stored data through the canonical library.
        storage = ArcticStorage(_minio_config(manifest_bucket))
        stored = storage.read(CANONICAL_LIB, "AAPL")
        assert stored is not None and len(stored) == 6
        expected_starts = pd.date_range(
            "2024-06-03 13:30", periods=6, freq="1min", tz="UTC"
        )
        assert stored.index.equals(expected_starts)
        positive = stored["volume"] > 0
        assert positive.all()
        ohlc_ok = (
            (stored["low"] <= stored["open"])
            & (stored["low"] <= stored["close"])
            & (stored["high"] >= stored["open"])
            & (stored["high"] >= stored["close"])
        )
        assert ohlc_ok.all()

        # Second incremental run: covered, nothing refetched, frame preserved.
        checksum_before = int(pd.util.hash_pandas_object(stored, index=True).sum())
        code, payload, stderr = _run_cli(
            ["sync", str(manifest_path), "--json"], manifest_bucket
        )
        assert code == 0, stderr
        assert payload is not None
        assert payload["results"][0]["reason"] == "covered"
        assert payload["results"][0]["rows_written"] == 0
        assert payload["definition_state"] == "unchanged"
        stored_after = storage.read(CANONICAL_LIB, "AAPL")
        assert (
            int(pd.util.hash_pandas_object(stored_after, index=True).sum())
            == checksum_before
        )


# ---------------------------------------------------------------------------
# Concurrency: two workers, real symbol locks, shared isolated bucket
# ---------------------------------------------------------------------------


def _worker_sync(
    out_queue: Any,
    fetch_counter: Any,
    barrier: Any,
    bucket: str,
    manifest_path: str,
    manifest_id: str,
) -> None:
    """Worker: real MinIO + real Databento + real file locking."""
    try:
        from fin3.manifest import load_manifest
        from fin3.sync import ManifestRunner

        counter = fetch_counter

        class CountingDatabento(DatabentoProvider):
            def fetch(self, *args: Any, **kwargs: Any):
                with counter.get_lock():
                    counter.value += 1
                return super().fetch(*args, **kwargs)

        ProviderRegistry._PROVIDER_MAP["databento"] = CountingDatabento

        config = ClientConfig(
            minio=_minio_config(bucket),
            providers={
                "databento": DatabentoConfig(
                    api_key=_env("FIN3_PROVIDERS__DATABENTO__API_KEY"),
                    dataset=_env("FIN3_PROVIDERS__DATABENTO__DATASET", "XNAS.ITCH"),
                )
            },
            lock=LockConfig(
                enabled=True,
                lock_dir=os.environ["FIN3_TEST_LOCK_DIR"],
                timeout_s=LOCK_TIMEOUT_S,
                poll_interval_s=0.2,
            ),
        )
        storage = ArcticStorage(config.minio, lock=config.lock)
        runner = ManifestRunner(config, storage=storage)
        manifest = load_manifest(manifest_path, now=datetime.now(timezone.utc))

        barrier.wait(timeout=60.0)
        report = runner.sync(manifest)
        out_queue.put(("ok", report.results[0].status, report.results[0].reason))
    except BaseException as exc:  # surface every failure to the parent
        out_queue.put(("error", repr(exc), ""))


class TestConcurrentSync:
    def test_two_workers_converge_without_double_fetch(
        self, manifest_bucket: str, tmp_path: Path
    ) -> None:
        manifests = []
        for worker in ("w1", "w2"):
            path = tmp_path / f"{worker}.yaml"
            path.write_text(
                f"""
version: 1
id: conc-{worker}
libraries:
  aapl-1m:
    asset_type: equity_us
    provider: databento
    resolution: 1m
    symbols: [AAPL]
    start: '{WINDOW_START}'
    end: '{WINDOW_END}'
    update_policy: incremental
""",
                encoding="utf-8",
            )
            manifests.append(str(path))

        out_queue: Any = mp.Queue()
        fetch_counter: Any = mp.Value("i", 0)
        barrier: Any = mp.Barrier(2)

        workers = [
            mp.Process(
                target=_worker_sync,
                args=(out_queue, fetch_counter, barrier, manifest_bucket, path, wid),
            )
            for wid, path in zip(("w1", "w2"), manifests)
        ]
        for proc in workers:
            proc.start()
        deadline = time.monotonic() + LOCK_TIMEOUT_S * 2
        for proc in workers:
            remaining = max(1.0, deadline - time.monotonic())
            proc.join(timeout=remaining)
            assert not proc.is_alive(), "worker timed out (nested lock deadlock?)"

        results = []
        while True:
            try:
                results.append(out_queue.get_nowait())
            except queue.Empty:
                break
        assert len(results) == 2, results
        statuses = sorted(r[1] for r in results)
        # Exactly one worker fetched; the other found the data (or the run
        # failed for a non-lock reason, which must surface here).
        for status, detail, _reason in results:
            assert status != "error", detail
        assert statuses in (["created", "skipped"], ["updated", "skipped"]), results
        assert fetch_counter.value == 1, (
            "expected exactly one Databento fetch under the symbol lock, "
            f"got {fetch_counter.value}"
        )

        storage = ArcticStorage(_minio_config(manifest_bucket))
        stored = storage.read(CANONICAL_LIB, "AAPL")
        assert stored is not None and len(stored) == 6
