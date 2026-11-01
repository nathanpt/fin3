"""ManifestRunner scenarios against real LMDB storage.

The runner under test is the real ``ManifestRunner``; only the provider is a
transport-boundary stub registered through the existing registry map
(auto-restored via monkeypatch). Every scenario asserts consumer-visible
values: stored frames, report statuses/ranges, fetch counts, and state.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from fin3.config.settings import BinanceConfig, ClientConfig, LockConfig, MinioConfig
from fin3.exceptions import (
    ConfigurationError,
    CostLimitExceededError,
    ProviderError,
    StorageError,
)
from fin3.manifest import load_manifest
from fin3.providers import ProviderRegistry
from fin3.schemas import METADATA_LIBRARY
from fin3.storage.arctic import ArcticStorage
from fin3.sync import ManifestRunner
from tests.conftest import make_ohlcv

NOW = datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
LIB = "crypto-1h-manifest-test"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FIN3_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def lock_dir(tmp_path: Path) -> str:
    return str(tmp_path / "locks")


@pytest.fixture
def storage(tmp_path: Path, lock_dir: str) -> ArcticStorage:
    return ArcticStorage.from_lmdb(
        str(tmp_path / "lmdb"),
        lock=LockConfig(enabled=True, lock_dir=lock_dir),
    )


@pytest.fixture
def config() -> ClientConfig:
    return ClientConfig(
        minio=MinioConfig(endpoint="lmdb", access_key="", secret_key=""),
        providers={"manifest-test": BinanceConfig(api_key="secret-key-value")},
    )


@pytest.fixture
def runner(config: ClientConfig, storage: ArcticStorage) -> ManifestRunner:
    return ManifestRunner(config, storage=storage)


def write_manifest(
    tmp_path: Path,
    manifest_id: str,
    body: str,
) -> Path:
    path = tmp_path / f"{manifest_id}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def crypto_manifest(
    tmp_path: Path,
    manifest_id: str = "smoke",
    *,
    symbol: str = "BTC-USD",
    start: str = "2024-01-01",
    end: str = "2024-01-01T23:59:59Z",
    policy: str = "incremental",
    provider: str = "manifest-test",
    resolution: str = "1h",
) -> Path:
    return write_manifest(
        tmp_path,
        manifest_id,
        f"""
version: 1
id: {manifest_id}
libraries:
  ds1:
    asset_type: crypto
    provider: {provider}
    resolution: {resolution}
    symbols: [{symbol}]
    start: '{start}'
    end: {end}
    update_policy: {policy}
""",
    )


def register_provider(
    monkeypatch,
    name: str = "manifest-test",
    *,
    fetch=None,
    estimate=None,
):
    """Register a stub provider on the real registry map (auto-restored)."""
    calls = {"fetch": [], "estimate_cost": []}

    class StubProvider:
        def __init__(self, config: object) -> None:
            pass

        def fetch(self, symbol, start, end, resolution, *, asset_type=None, **kw):
            calls["fetch"].append((symbol, pd.Timestamp(start), pd.Timestamp(end)))
            if fetch is not None:
                return fetch(symbol, start, end, resolution)
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"),
                pd.Timestamp(end),
                freq="1h",
                tz="UTC",
            )
            idx = idx[idx < pd.Timestamp(end)]
            if len(idx) == 0:
                return make_ohlcv("2024-01-01 00:00", periods=0, freq="1h")
            return make_ohlcv(str(idx[0]), periods=len(idx), freq="1h")

    if estimate is not None:

        def estimate_cost(self, symbol, start, end, resolution, *, asset_type=None):
            calls["estimate_cost"].append(
                (symbol, pd.Timestamp(start), pd.Timestamp(end))
            )
            return estimate(symbol, start, end, resolution)

        StubProvider.estimate_cost = estimate_cost  # type: ignore[attr-defined]

    monkeypatch.setitem(ProviderRegistry._PROVIDER_MAP, name, StubProvider)
    return calls


def inventory(storage: ArcticStorage) -> dict:
    """Full value inventory: deterministic checksum per symbol per library."""
    snapshot: dict = {}
    for lib in sorted(storage.arctic.list_libraries()):
        handle = storage.arctic[lib]
        symbols: dict = {}
        for sym in sorted(handle.list_symbols()):
            df = handle.read(sym).data
            symbols[sym] = {
                "rows": len(df),
                "checksum": int(pd.util.hash_pandas_object(df, index=True).sum()),
                "versions": len(handle.list_versions(sym)),
            }
        snapshot[lib] = symbols
    return snapshot


# ---------------------------------------------------------------------------
# Read-only guarantees
# ---------------------------------------------------------------------------


class TestReadOnlyBoundaries:
    def test_status_and_dry_runs_leave_storage_untouched(
        self, tmp_path, runner, storage, lock_dir, monkeypatch
    ) -> None:
        # Seed one populated symbol and one library with data.
        manifest_path = crypto_manifest(tmp_path, "ro", symbol="BTC-USD")
        register_provider(monkeypatch)
        manifest = load_manifest(manifest_path, now=NOW)
        runner.ensure(manifest)

        before = inventory(storage)
        locks_after_apply = set(Path(lock_dir).glob("*.lock"))

        for operation in (
            lambda: runner.status(manifest),
            lambda: runner.ensure(manifest, dry_run=True),
            lambda: runner.sync(manifest, dry_run=True),
        ):
            report = operation()
            assert report.results
            assert inventory(storage) == before
        # No drift state was created and previews added no lock files
        # (the earlier applying ensure legitimately created some).
        assert storage.read(METADATA_LIBRARY, "__manifest__ro") is None
        assert set(Path(lock_dir).glob("*.lock")) == locks_after_apply

    def test_dry_run_never_reaches_provider(
        self, tmp_path, runner, monkeypatch
    ) -> None:
        def exploding_fetch(*args, **kwargs):
            raise AssertionError("provider fetch reached during dry-run")

        def exploding_estimate(*args, **kwargs):
            raise AssertionError("provider cost API reached during dry-run")

        calls = register_provider(
            monkeypatch, fetch=exploding_fetch, estimate=exploding_estimate
        )
        manifest = load_manifest(crypto_manifest(tmp_path, "dry"), now=NOW)
        for report in (
            runner.status(manifest),
            runner.ensure(manifest, dry_run=True),
            runner.sync(manifest, dry_run=True),
        ):
            assert report.results[0].status == "planned"
            assert report.estimated_cost is None
        assert calls["fetch"] == [] and calls["estimate_cost"] == []

    def test_dry_run_does_not_create_library(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "d2"), now=NOW)
        report = runner.ensure(manifest, dry_run=True)
        assert report.planned_libraries == [LIB]
        assert report.created_libraries == []
        assert storage.arctic.list_libraries() == []


# ---------------------------------------------------------------------------
# Scenario 1: ensure creates exactly the declared grid
# ---------------------------------------------------------------------------


class TestEnsureCreatesGrid:
    def test_dry_run_then_apply_then_repeat(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "smoke"), now=NOW)

        preview = runner.ensure(manifest, dry_run=True)
        assert preview.results[0].action == "create"
        assert preview.results[0].status == "planned"
        assert preview.planned_libraries == [LIB]

        report = runner.ensure(manifest)
        result = report.results[0]
        assert result.status == "created"
        assert result.action == "create"
        assert result.rows_written == 24
        assert report.created_libraries == [LIB]

        stored = storage.read(LIB, "BTC-USD")
        expected = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        pd.testing.assert_frame_equal(
            stored, expected, check_exact=True, check_freq=False
        )
        assert stored.index[0] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
        assert stored.index[-1] == pd.Timestamp("2024-01-01 23:00", tz="UTC")
        assert len(calls["fetch"]) == 1

        # Repeated ensure: no fetch, no data change, no new versions.
        versions_before = len(storage.arctic[LIB].list_versions("BTC-USD"))
        checksum_before = inventory(storage)
        again = runner.ensure(manifest)
        assert again.results[0].status == "skipped"
        assert again.results[0].reason == "already_exists"
        assert calls["fetch"] == calls["fetch"][:1]
        assert inventory(storage) == checksum_before
        assert len(storage.arctic[LIB].list_versions("BTC-USD")) == versions_before


# ---------------------------------------------------------------------------
# Scenario 2: interior hole refill; off-grid preservation semantics
# ---------------------------------------------------------------------------


class TestIncrementalFill:
    def _seed_full_day(self, tmp_path, runner, storage, monkeypatch):
        manifest = load_manifest(crypto_manifest(tmp_path, "hole"), now=NOW)
        runner.ensure(manifest)
        return manifest

    def test_ensure_skips_holes_sync_restores_exact_value(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        manifest = self._seed_full_day(tmp_path, runner, storage, monkeypatch)

        full = storage.read(LIB, "BTC-USD")
        missing_value = full.loc[pd.Timestamp("2024-01-01 05:00", tz="UTC")]
        torn = full.drop(pd.Timestamp("2024-01-01 05:00", tz="UTC"))
        storage.write(LIB, "BTC-USD", torn)

        ensure_report = runner.ensure(manifest)
        assert ensure_report.results[0].reason == "already_exists"

        sync_report = runner.sync(manifest)
        result = sync_report.results[0]
        assert result.status == "updated"
        assert result.action == "fill"
        assert result.write_ranges == [
            (
                datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc),
            )
        ]
        restored = storage.read(LIB, "BTC-USD")
        assert len(restored) == 24
        assert (
            restored.loc[pd.Timestamp("2024-01-01 05:00", tz="UTC")]["close"]
            == missing_value["close"]
        )
        # Neighbours untouched.
        assert (
            restored.loc[pd.Timestamp("2024-01-01 04:00", tz="UTC")]["close"]
            == full.loc[pd.Timestamp("2024-01-01 04:00", tz="UTC")]["close"]
        )
        assert len(calls["fetch"]) == 2  # seed + one refill fetch

        # Second sync: zero fetches, zero writes, no state write.
        state_before = storage.read(METADATA_LIBRARY, "__manifest__hole")
        checksum = inventory(storage)
        second = runner.sync(manifest)
        assert second.results[0].status == "skipped"
        assert second.results[0].reason == "covered"
        assert len(calls["fetch"]) == 2
        assert inventory(storage) == checksum
        assert storage.read(METADATA_LIBRARY, "__manifest__hole").equals(state_before)

    def test_off_grid_row_inside_update_span_rejects_span(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest = self._seed_full_day(tmp_path, runner, storage, monkeypatch)

        full = storage.read(LIB, "BTC-USD")
        torn = full.drop(
            [
                pd.Timestamp("2024-01-01 05:00", tz="UTC"),
                pd.Timestamp("2024-01-01 06:00", tz="UTC"),
            ]
        )
        # Invalid off-grid row inside the proposed [05:00, 06:00] update span.
        invalid = make_ohlcv("2024-01-01 05:30", periods=1, freq="1min")
        storage.write(LIB, "BTC-USD", pd.concat([torn, invalid]).sort_index())

        before = storage.read(LIB, "BTC-USD")
        inventory_before = inventory(storage)

        report = runner.sync(manifest)
        result = report.results[0]
        assert result.status == "failed"
        assert result.reason == "fetch_or_validation_failed"

        # The rejected span left storage byte-identical: hole still open,
        # off-grid row kept, no new versions, no state writes.
        after = storage.read(LIB, "BTC-USD")
        assert pd.Timestamp("2024-01-01 05:00", tz="UTC") not in after.index
        assert pd.Timestamp("2024-01-01 06:00", tz="UTC") not in after.index
        assert pd.Timestamp("2024-01-01 05:30", tz="UTC") in after.index
        pd.testing.assert_frame_equal(after, before, check_freq=False)
        assert inventory(storage) == inventory_before


# ---------------------------------------------------------------------------
# Scenario 3: definition drift and end:now eligibility
# ---------------------------------------------------------------------------


class TestDriftAndNow:
    def test_extend_end_reports_drift_then_converges(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        day1 = crypto_manifest(tmp_path, "drift")
        manifest = load_manifest(day1, now=NOW)
        runner.sync(manifest)  # successful sync records the baseline hash

        extended = crypto_manifest(tmp_path, "drift", end="2024-01-02T23:59:59Z")
        extended_manifest = load_manifest(
            extended, now=datetime(2024, 1, 3, 1, 0, tzinfo=timezone.utc)
        )

        preview = runner.status(extended_manifest)
        assert preview.definition_state == "changed"
        assert preview.last_successful_hash == manifest.declaration_hash
        result = preview.results[0]
        assert result.action == "fill"
        assert result.write_ranges == [
            (
                datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 23, 0, tzinfo=timezone.utc),
            )
        ]

        synced = runner.sync(extended_manifest)
        assert synced.results[0].status == "updated"
        after = storage.read(LIB, "BTC-USD")
        assert len(after) == 48
        day1_part = after[after.index < pd.Timestamp("2024-01-02", tz="UTC")]
        expected_day1 = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        pd.testing.assert_frame_equal(
            day1_part, expected_day1, check_exact=True, check_freq=False
        )

        settled = runner.sync(extended_manifest)
        assert settled.results[0].reason == "covered"
        assert settled.definition_state == "unchanged"

    def test_now_bar_not_written_before_close(
        self, tmp_path, runner, storage, monkeypatch
    ):
        register_provider(monkeypatch)
        path = crypto_manifest(tmp_path, "now", end="now", symbol="ETH-USD")

        early = load_manifest(
            path, now=datetime(2024, 1, 2, 0, 30, tzinfo=timezone.utc)
        )
        report = runner.ensure(early)
        assert report.results[0].status == "created"
        stored = storage.read(LIB, "ETH-USD")
        assert stored.index[-1] == pd.Timestamp("2024-01-01 23:00", tz="UTC")

        # Ensure does not advance even at the later clock (already_exists).
        later_ensure = load_manifest(
            path, now=datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
        )
        assert runner.ensure(later_ensure).results[0].reason == "already_exists"

        eligible = load_manifest(
            path, now=datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
        )
        report = runner.sync(eligible)
        stored = storage.read(LIB, "ETH-USD")
        assert stored.index[-1] == pd.Timestamp("2024-01-02 00:00", tz="UTC")
        assert report.results[0].action == "fill"

    def test_exchange_daily_eligible_only_at_session_close(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        """XNYS daily bar becomes eligible exactly at session close (20:00Z)."""
        register_provider(
            monkeypatch,
            fetch=lambda symbol, start, end, resolution: make_ohlcv(
                str(pd.Timestamp(start).floor("D")), periods=1, freq="1D"
            ),
        )
        path = write_manifest(
            tmp_path,
            "eqd",
            """
version: 1
id: eqd
libraries:
  eqd:
    asset_type: equity_us
    provider: manifest-test
    resolution: 1d
    symbols: [MSFT]
    start: '2024-06-03'
    end: now
    update_policy: incremental
""",
        )
        before = load_manifest(
            path, now=datetime(2024, 6, 3, 19, 59, tzinfo=timezone.utc)
        )
        report = runner.ensure(before)
        assert report.results[0].reason == "no_completed_bars"
        assert storage.read("equities-1d-manifest-test", "MSFT") is None

        at_close = load_manifest(
            path, now=datetime(2024, 6, 3, 20, 0, tzinfo=timezone.utc)
        )
        report = runner.ensure(at_close)
        assert report.results[0].status == "created"
        stored = storage.read("equities-1d-manifest-test", "MSFT")
        assert len(stored) == 1
        assert stored.index[0] == pd.Timestamp("2024-06-03 13:30", tz="UTC")

    def test_weekend_request_skips_but_library_is_created(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(
            monkeypatch,
            fetch=lambda symbol, start, end, resolution: make_ohlcv(
                "2024-06-10", periods=0, freq="1D"
            ),
        )
        path = write_manifest(
            tmp_path,
            "we",
            """
version: 1
id: we
libraries:
  eqwe:
    asset_type: equity_us
    provider: manifest-test
    resolution: 1d
    symbols: [TSLA]
    start: '2024-06-08'
    end: '2024-06-09'
    update_policy: incremental
""",
        )
        manifest = load_manifest(path, now=datetime(2024, 6, 10, tzinfo=timezone.utc))
        report = runner.ensure(manifest)
        assert report.results[0].reason == "no_completed_bars"
        assert "equities-1d-manifest-test" in report.created_libraries
        assert storage.has_library("equities-1d-manifest-test")


# ---------------------------------------------------------------------------
# Scenario 4: append-only
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_append_only_fills_only_after_global_tail(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        # Seed a 3-bar history (global tail at 02:00) before the manifest run.
        seed = make_ohlcv("2024-01-01 00:00", periods=3, freq="1h")
        storage.ensure_library(LIB)
        storage.write(LIB, "XRP-USD", seed)

        manifest = load_manifest(
            crypto_manifest(tmp_path, "app", symbol="XRP-USD", policy="append-only"),
            now=NOW,
        )
        report = runner.sync(manifest)
        result = report.results[0]
        assert result.status == "updated"
        assert result.action == "append"
        assert result.write_ranges[0][0] == datetime(
            2024, 1, 1, 3, 0, tzinfo=timezone.utc
        )

        stored = storage.read(LIB, "XRP-USD")
        assert len(stored) == 24

        # An interior hole behind the tail must NOT be refilled.
        torn = storage.read(LIB, "XRP-USD").drop(
            pd.Timestamp("2024-01-01 10:00", tz="UTC")
        )
        storage.write(LIB, "XRP-USD", torn)
        fetches_before = len(calls["fetch"])
        report = runner.sync(manifest)
        assert report.results[0].reason == "covered"
        after = storage.read(LIB, "XRP-USD")
        assert pd.Timestamp("2024-01-01 10:00", tz="UTC") not in after.index
        assert len(calls["fetch"]) == fetches_before

    def test_append_only_with_history_beyond_window_is_noop(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch)
        # History extends past the requested end (Jan 3 data while window ends Jan 1).
        history = pd.concat(
            [
                make_ohlcv("2024-01-01 00:00", periods=24, freq="1h"),
                make_ohlcv("2024-01-03 00:00", periods=24, freq="1h", base_price=500),
            ]
        )
        storage.ensure_library(LIB)
        storage.write(LIB, "ADA-USD", history)

        manifest = load_manifest(
            crypto_manifest(tmp_path, "app2", symbol="ADA-USD", policy="append-only"),
            now=NOW,
        )
        report = runner.sync(manifest)
        assert report.results[0].reason == "covered"
        assert calls["fetch"] == []
        assert len(storage.read(LIB, "ADA-USD")) == 48


# ---------------------------------------------------------------------------
# Scenario 5: full-refresh
# ---------------------------------------------------------------------------


class TestFullRefresh:
    def test_refresh_replaces_only_requested_window(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        outside = make_ohlcv("2024-01-01 00:00", periods=6, freq="1h")  # 00:00..05:00
        inside = make_ohlcv("2024-01-01 06:00", periods=4, freq="1h", base_price=999)
        storage.ensure_library(LIB)
        storage.write(LIB, "ADA-USD", pd.concat([outside, inside]))

        manifest = load_manifest(
            crypto_manifest(
                tmp_path,
                "rf",
                symbol="ADA-USD",
                start="2024-01-01T06:00:00Z",
                end="2024-01-01T09:59:59Z",
                policy="full-refresh",
            ),
            now=NOW,
        )
        report = runner.sync(manifest)
        result = report.results[0]
        assert result.status == "updated"
        assert result.action == "refresh"

        stored = storage.read(LIB, "ADA-USD")
        assert len(stored) == 10
        # Inside window: provider values (base 100), not the stale 999 seed.
        window = stored[stored.index >= pd.Timestamp("2024-01-01 06:00", tz="UTC")]
        assert (window["close"] < 900).all()
        # Outside window preserved verbatim.
        kept = stored[stored.index < pd.Timestamp("2024-01-01 06:00", tz="UTC")]
        pd.testing.assert_frame_equal(kept, outside, check_exact=True, check_freq=False)

    def test_refresh_chunk_failure_keeps_committed_and_old_chunks(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)

        def fetch(symbol, start, end, resolution):
            if pd.Timestamp(start).day == 2:
                raise ProviderError("provider exploded on day 2")
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), periods=24, freq="1h", tz="UTC"
            )
            return make_ohlcv(str(idx[0]), periods=24, freq="1h", base_price=42)

        # Old data across both days (distinct values: base 500).
        old = pd.concat(
            [
                make_ohlcv("2024-01-01 00:00", periods=24, freq="1h", base_price=500),
                make_ohlcv("2024-01-02 00:00", periods=24, freq="1h", base_price=500),
            ]
        )
        storage.ensure_library(LIB)
        storage.write(LIB, "SOL-USD", old)

        manifest = load_manifest(
            crypto_manifest(
                tmp_path,
                "rf2",
                symbol="SOL-USD",
                start="2024-01-01",
                end="2024-01-02T23:59:59Z",
                policy="full-refresh",
            ),
            now=datetime(2024, 1, 3, 1, 0, tzinfo=timezone.utc),
        )
        # Swap the registered provider for the scripted one.
        register_provider(monkeypatch, fetch=fetch)
        report = runner.sync(manifest)
        result = report.results[0]
        assert result.status == "failed"
        assert result.rows_written == 24  # day 1 committed before the failure
        assert "day 2" in result.error

        stored = storage.read(LIB, "SOL-USD")
        assert len(stored) == 48
        day1 = stored[stored.index < pd.Timestamp("2024-01-02", tz="UTC")]
        day2 = stored[stored.index >= pd.Timestamp("2024-01-02", tz="UTC")]
        assert (day1["close"] < 100).all()  # refreshed
        assert (day2["close"] > 400).all()  # old rows untouched

    def test_empty_response_fails_and_preserves_history(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(
            monkeypatch,
            fetch=lambda symbol, start, end, resolution: make_ohlcv(
                "2024-01-01 00:00", periods=0, freq="1h"
            ),
        )
        seed = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        storage.ensure_library(LIB)
        storage.write(LIB, "BNB-USD", seed)

        manifest = load_manifest(
            crypto_manifest(tmp_path, "empty", symbol="BNB-USD", policy="full-refresh"),
            now=NOW,
        )
        report = runner.sync(manifest)
        assert report.results[0].status == "failed"
        stored = storage.read(LIB, "BNB-USD")
        pd.testing.assert_frame_equal(stored, seed, check_exact=True, check_freq=False)

    def test_refresh_rejects_padding_over_positive_volume(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        # Provider returns data only for the first half of the window; the
        # seeded history has positive volume at a stamp the provider omits.
        def partial_fetch(symbol, start, end, resolution):
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), periods=12, freq="1h", tz="UTC"
            )
            return make_ohlcv(str(idx[0]), periods=12, freq="1h")

        register_provider(monkeypatch, fetch=partial_fetch)
        seed = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        storage.ensure_library(LIB)
        storage.write(LIB, "LINK-USD", seed)

        manifest = load_manifest(
            crypto_manifest(tmp_path, "pad", symbol="LINK-USD", policy="full-refresh"),
            now=NOW,
        )
        report = runner.sync(manifest)
        assert report.results[0].status == "failed"
        assert "padding" in (report.results[0].error or "")
        stored = storage.read(LIB, "LINK-USD")
        assert len(stored) == 24
        # Old values intact (no partial commit of the rejected chunk).
        pd.testing.assert_frame_equal(stored, seed, check_exact=True, check_freq=False)

    def test_independent_symbols_continue_after_failure(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        def fetch(symbol, start, end, resolution):
            if symbol == "BAD-USD":
                raise ProviderError("nope")
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), periods=24, freq="1h", tz="UTC"
            )
            return make_ohlcv(str(idx[0]), periods=24, freq="1h")

        register_provider(monkeypatch, fetch=fetch)
        path = write_manifest(
            tmp_path,
            "multi",
            """
version: 1
id: multi
libraries:
  ds1:
    asset_type: crypto
    provider: manifest-test
    resolution: 1h
    symbols: [BAD-USD, GOOD-USD]
    start: '2024-01-01'
    end: '2024-01-01T23:59:59Z'
    update_policy: incremental
""",
        )
        manifest = load_manifest(path, now=NOW)
        report = runner.sync(manifest)
        by_symbol = {r.symbol: r for r in report.results}
        assert by_symbol["BAD-USD"].status == "failed"
        assert by_symbol["GOOD-USD"].status == "created"
        assert report.failed_count == 1
        assert storage.read(LIB, "GOOD-USD") is not None
        assert storage.read(LIB, "BAD-USD") is None


# ---------------------------------------------------------------------------
# Scenario 6: cost authorization
# ---------------------------------------------------------------------------


class TestCostAuthorization:
    def test_single_estimator_call_per_target_with_overlap_multiplier(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        calls = register_provider(monkeypatch, estimate=lambda *a, **k: 2.0)
        # Multi-session equity target: 2 sessions -> widened fetch intervals
        # that overlap must not produce per-chunk estimator calls.
        path = write_manifest(
            tmp_path,
            "cost",
            """
version: 1
id: cost
libraries:
  eqc:
    asset_type: equity_us
    provider: manifest-test
    resolution: 1m
    symbols: [AAPL, MSFT]
    start: '2024-06-03T13:30:00Z'
    end: '2024-06-04T20:00:00Z'
    update_policy: incremental
""",
        )
        manifest = load_manifest(path, now=datetime(2024, 6, 5, tzinfo=timezone.utc))
        report = runner.sync(manifest, max_cost=100.0)
        assert report.estimated_cost == pytest.approx(4.0)  # 2 targets x 2.0
        assert report.results[0].status == "created"
        assert report.results[1].status == "created"
        # Exactly one estimator call per (library, symbol).
        assert [s for s, _, _ in calls["estimate_cost"]] == ["AAPL", "MSFT"]
        assert len(calls["estimate_cost"]) == 2

    def test_estimated_overlapping_intervals_multiply_once_per_target(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        # Crypto 2-day target: two daily chunks, fetch intervals do not
        # overlap -> multiplier 1.
        calls = register_provider(monkeypatch, estimate=lambda *a, **k: 3.0)
        manifest = load_manifest(
            crypto_manifest(
                tmp_path,
                "cost2",
                symbol="DOT-USD",
                start="2024-01-01",
                end="2024-01-02T23:59:59Z",
            ),
            now=datetime(2024, 1, 3, 1, 0, tzinfo=timezone.utc),
        )
        report = runner.sync(manifest, max_cost=100.0)
        assert report.estimated_cost == pytest.approx(3.0)
        assert len(calls["estimate_cost"]) == 1

    def test_equality_passes_and_exceedance_writes_nothing(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch, estimate=lambda *a, **k: 5.0)
        manifest = load_manifest(
            crypto_manifest(tmp_path, "ce", symbol="BTC-USD"), now=NOW
        )
        ok = runner.sync(manifest, max_cost=5.0)
        assert ok.results[0].status == "created"

        other = load_manifest(
            crypto_manifest(tmp_path, "ce2", symbol="XRP-USD"), now=NOW
        )
        with pytest.raises(CostLimitExceededError) as excinfo:
            runner.sync(other, max_cost=4.99)
        assert excinfo.value.estimated_cost == pytest.approx(5.0)
        assert storage.read(LIB, "XRP-USD") is None

    def test_missing_estimator_fails_closed(
        self, tmp_path, runner, monkeypatch
    ) -> None:
        register_provider(monkeypatch)  # no estimate_cost method
        manifest = load_manifest(crypto_manifest(tmp_path, "ne"), now=NOW)
        with pytest.raises(ConfigurationError, match="cost estimation"):
            runner.sync(manifest, max_cost=10.0)

    def test_invalid_estimate_fails_closed(self, tmp_path, runner, monkeypatch) -> None:
        register_provider(monkeypatch, estimate=lambda *a, **k: float("nan"))
        manifest = load_manifest(crypto_manifest(tmp_path, "ie"), now=NOW)
        with pytest.raises(ConfigurationError, match="invalid cost estimate"):
            runner.sync(manifest, max_cost=10.0)

    def test_negative_max_cost_rejected(self, tmp_path, runner, monkeypatch) -> None:
        register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "nm"), now=NOW)
        with pytest.raises(ConfigurationError, match="max_cost"):
            runner.sync(manifest, max_cost=-1.0)

    def test_concurrent_change_outside_plan_fails_target(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch, estimate=lambda *a, **k: 1.0)
        manifest = load_manifest(
            crypto_manifest(tmp_path, "cc", symbol="BTC-USD"), now=NOW
        )
        runner.sync(manifest)  # full day stored; symbol covered

        # The plan snapshot sees a covered target (no approved fetch
        # intervals). A concurrent writer deletes a stored bar before the
        # target lock re-plan, producing fetch work outside the plan.
        original_plan_all = runner._plan_all

        def plan_then_delete(m, *, mode):
            plans = original_plan_all(m, mode=mode)
            stored = storage.read(LIB, "BTC-USD")
            storage.write(LIB, "BTC-USD", stored.drop(stored.index[0]))
            return plans

        monkeypatch.setattr(runner, "_plan_all", plan_then_delete)
        report = runner.sync(manifest, max_cost=10.0)
        result = report.results[0]
        assert result.status == "failed"
        assert result.reason == "plan_changed"
        assert "rerun" in (result.error or "")

    def test_preflight_unknown_provider_rejected(
        self, tmp_path, runner, monkeypatch
    ) -> None:
        register_provider(monkeypatch, name="manifest-test")
        manifest = load_manifest(
            crypto_manifest(tmp_path, "pf", provider="ghost"), now=NOW
        )
        with pytest.raises(ConfigurationError, match="not configured"):
            runner.status(manifest)


# ---------------------------------------------------------------------------
# Scenario 7: manifest state
# ---------------------------------------------------------------------------


class TestManifestState:
    @pytest.fixture
    def state_setup(self, tmp_path, runner, storage, monkeypatch):
        calls = register_provider(monkeypatch)
        good = load_manifest(
            crypto_manifest(tmp_path, "st", symbol="GOOD-USD"), now=NOW
        )
        return calls, good

    def test_successful_sync_updates_hash_failures_do_not(
        self, tmp_path, runner, storage, monkeypatch, state_setup
    ) -> None:
        calls, good = state_setup

        def fetch(symbol, start, end, resolution):
            if symbol == "BAD-USD":
                raise ProviderError("boom")
            idx = pd.date_range(
                pd.Timestamp(start).ceil("h"), periods=24, freq="1h", tz="UTC"
            )
            return make_ohlcv(str(idx[0]), periods=24, freq="1h")

        register_provider(monkeypatch, fetch=fetch)
        failing = write_manifest(
            tmp_path,
            "st",
            """
version: 1
id: st
libraries:
  ds1:
    asset_type: crypto
    provider: manifest-test
    resolution: 1h
    symbols: [BAD-USD]
    start: '2024-01-01'
    end: '2024-01-01T23:59:59Z'
    update_policy: incremental
""",
        )
        manifest = load_manifest(failing, now=NOW)
        report = runner.sync(manifest)
        assert report.failed_count == 1
        assert report.last_successful_hash is None  # untracked, unchanged
        assert storage.read(METADATA_LIBRARY, "__manifest__st") is None

        # A successful retry records the hash.
        register_provider(monkeypatch)
        retry = runner.sync(good)
        assert retry.failed_count == 0
        state = storage.read(METADATA_LIBRARY, "__manifest__st")
        assert state is not None
        row = state.iloc[0]
        assert row["declaration_hash"] == good.declaration_hash
        assert row["operation"] == "sync"
        assert retry.last_successful_hash == good.declaration_hash
        assert retry.definition_state == "unchanged"

    def test_ensure_and_previews_do_not_advance_state(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "ns"), now=NOW)
        runner.sync(manifest)
        stored_hash = storage.read(METADATA_LIBRARY, "__manifest__ns").iloc[0][
            "declaration_hash"
        ]

        # Tamper: simulate a changed declaration with a new hash but same id.
        changed = load_manifest(
            crypto_manifest(tmp_path, "ns", symbol="ETH-USD"), now=NOW
        )
        runner.ensure(changed)
        runner.status(changed)
        runner.ensure(changed, dry_run=True)
        runner.sync(changed, dry_run=True)
        assert (
            storage.read(METADATA_LIBRARY, "__manifest__ns").iloc[0]["declaration_hash"]
            == stored_hash
        )

    def test_state_write_failure_reports_unknown_and_keeps_bars(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        first = load_manifest(crypto_manifest(tmp_path, "sw"), now=NOW)
        runner.sync(first)  # establishes state with the first hash

        real_write = storage.write

        def flaky_write(library, symbol, data, **kwargs):
            if library == METADATA_LIBRARY:
                raise StorageError("acknowledgement lost")
            return real_write(library, symbol, data, **kwargs)

        monkeypatch.setattr(storage, "write", flaky_write)
        changed = load_manifest(
            crypto_manifest(tmp_path, "sw", symbol="ETH-USD"), now=NOW
        )
        report = runner.sync(changed)
        # The state write failure fails the overall run (run-level error +
        # unknown outcome) without marking the symbol result failed: the bars
        # themselves were committed.
        assert report.state_write_unknown is True
        assert report.results[0].status == "created"
        assert any("state write failed" in e for e in report.errors)
        # Valid bars kept.
        assert storage.read(LIB, "ETH-USD") is not None
        # Observed/last-known state reported rather than claimed rollback.
        assert report.last_successful_hash == first.declaration_hash

    def test_corrupt_state_is_configuration_error(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "corrupt"), now=NOW)
        storage.ensure_library(METADATA_LIBRARY)
        storage.write(
            METADATA_LIBRARY,
            "__manifest__corrupt",
            pd.DataFrame(
                {
                    "declaration_hash": ["nothex"],
                    "last_sync_at": ["2024-01-01T00:00:00+00:00"],
                    "operation": ["sync"],
                }
            ),
        )
        with pytest.raises(ConfigurationError, match="declaration_hash"):
            runner.status(manifest)

    def test_multi_record_state_is_configuration_error(
        self, tmp_path, runner, storage, monkeypatch
    ) -> None:
        register_provider(monkeypatch)
        manifest = load_manifest(crypto_manifest(tmp_path, "corrupt2"), now=NOW)
        storage.ensure_library(METADATA_LIBRARY)
        storage.write(
            METADATA_LIBRARY,
            "__manifest__corrupt2",
            pd.DataFrame(
                {
                    "declaration_hash": ["a" * 64, "b" * 64],
                    "last_sync_at": ["2024-01-01T00:00:00+00:00"] * 2,
                    "operation": ["sync", "sync"],
                }
            ),
        )
        with pytest.raises(ConfigurationError, match="exactly one record"):
            runner.status(manifest)

    def test_untracked_then_changed_states(self, tmp_path, runner, monkeypatch) -> None:
        register_provider(monkeypatch)
        first = load_manifest(crypto_manifest(tmp_path, "st2"), now=NOW)
        assert runner.status(first).definition_state == "untracked"
        runner.sync(first)
        second = load_manifest(
            crypto_manifest(tmp_path, "st2", symbol="ETH-USD"), now=NOW
        )
        assert runner.status(second).definition_state == "changed"
