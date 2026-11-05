"""Live Cboe source and storage proof — gated behind FIN3_CBOE_LIVE=1.

**Rights gate:** running anything in this module requires documented rights
clearance covering (a) automated retrieval from Cboe, (b) private durable
storage of the retrieved data, and (c) personal, non-redistributed research
use. The environment opt-in (`FIN3_CBOE_LIVE=1`) is a test gate, not legal
permission; without clearance the module skips and the feature reports
"code-complete but NOT verified against the live API".

Requires real MinIO (`FIN3_MINIO__ENDPOINT`/`__ACCESS_KEY`/`__SECRET_KEY`,
optional `__SECURE`) for storage tests; direct-source tests need only source
connectivity. No Databento/Massive key. Uses a fresh UUID-named single bucket
(`cboe-test-<uuid>`) — never the shared ``TEST_BUCKET`` — whose absence is
confirmed before any creation; the fixture owns and removes only its own
bucket/libraries and surfaces teardown failures.
"""

from __future__ import annotations

import json
import os
import urllib.error
import warnings
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd
import pytest

from fin3.config.settings import CboeConfig, ClientConfig, LockConfig, MinioConfig
from fin3.core import MarketDataFetcher
from fin3.exceptions import StorageError
from fin3.providers.cboe import CboeProvider
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution
from fin3.storage.arctic import ArcticStorage
from fin3.utils.integrity import check_integrity
from fin3.utils.validation import validate_raw_provider_data, validate_storage_artifact

if os.environ.get("FIN3_CBOE_LIVE") != "1":
    pytest.skip(
        "Cboe live proof gated: set FIN3_CBOE_LIVE=1 only after documented "
        "rights clearance (automated retrieval, private durable storage, "
        "personal non-redistributed research)",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

CANONICAL_LIB = "indices-1d-cboe"

NOV_START = datetime(2023, 11, 1, tzinfo=timezone.utc)
NOV_END = datetime(2023, 11, 7, 23, 59, 59, 999999, tzinfo=timezone.utc)
NOV_DATES = [
    "2023-11-01T13:30:00Z",
    "2023-11-02T13:30:00Z",
    "2023-11-03T13:30:00Z",
    "2023-11-06T14:30:00Z",
    "2023-11-07T14:30:00Z",
]
NOV_CLOSES = {
    "VIX": [16.87, 15.66, 14.91, 14.89, 14.81],
    "VIX3M": [18.55, 17.80, 17.22, 17.03, 16.92],
}

JUL_START = datetime(2023, 7, 3, tzinfo=timezone.utc)
JUL_END = datetime(2023, 7, 5, 23, 59, 59, 999999, tzinfo=timezone.utc)
JUL_CLOSES = {
    "VIX": [13.57, 14.18],
    "VIX3M": [16.07, 16.51],
}

DECADE_START = datetime(2016, 1, 4, tzinfo=timezone.utc)
DECADE_END = datetime(2026, 11, 4, 23, 59, 59, 999999, tzinfo=timezone.utc)

FIRST_SOURCE_DATE = {"VIX": "1990-01-02", "VIX3M": "2009-09-18"}


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


def _require_minio_env() -> None:
    missing = [
        setting
        for setting in (
            "FIN3_MINIO__ENDPOINT",
            "FIN3_MINIO__ACCESS_KEY",
            "FIN3_MINIO__SECRET_KEY",
        )
        if not _env(setting)
    ]
    if missing:
        pytest.skip(f"MinIO integration settings absent: {', '.join(missing)}")


@pytest.fixture()
def cboe_bucket(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A fresh UUID-named single bucket, confirmed absent before creation.

    Only the owned bucket is removed afterwards; a teardown hiccup is
    surfaced as a warning and never masks the test result.
    """
    _require_minio_env()
    lock_dir = str(tmp_path_factory.mktemp("cboe_locks"))
    os.environ["FIN3_TEST_LOCK_DIR"] = lock_dir

    bucket = ""
    for _ in range(5):
        candidate = f"cboe-test-{uuid4().hex[:12]}"
        probe = ArcticStorage(_minio_config(candidate))
        try:
            if not probe._bucket_exists(candidate):
                bucket = candidate
                break
        except StorageError as exc:
            os.environ.pop("FIN3_TEST_LOCK_DIR", None)
            pytest.skip(f"bucket probe for {candidate!r} failed: {exc}")
    if not bucket:
        os.environ.pop("FIN3_TEST_LOCK_DIR", None)
        pytest.skip("could not generate an unused bucket name after 5 tries")

    yield bucket

    os.environ.pop("FIN3_TEST_LOCK_DIR", None)
    storage = ArcticStorage(_minio_config(bucket))
    try:
        arctic = storage.arctic_for(bucket)
        for lib in list(arctic.list_libraries()):
            arctic.delete_library(lib)
        storage._bucket_request(bucket, "DELETE")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:  # DELETE 404 = bucket already absent: fine
            warnings.warn(
                f"cboe integration cleanup: failed to remove bucket {bucket!r}: "
                f"{exc!r}",
                RuntimeWarning,
                stacklevel=1,
            )
    except Exception as exc:  # a cleanup hiccup must not mask test results
        warnings.warn(
            f"cboe integration cleanup: failed to remove bucket {bucket!r}: "
            f"{exc!r}",
            RuntimeWarning,
            stacklevel=1,
        )


@pytest.fixture()
def cboe_client_config(
    monkeypatch: pytest.MonkeyPatch, cboe_bucket: str
) -> ClientConfig:
    """Isolated Cboe-only config: unrelated provider credentials cleared.

    The shared conftest loaded the repository dotenv before this fixture
    runs; unrelated ``FIN3_PROVIDERS__*`` variables and ``MASSIVE_API_KEY``
    are removed here so dotenv/environment merging cannot construct other
    providers. ``_env_file=None`` prevents re-reading dotenv. Shell ``env -u``
    alone cannot do this — the dotenv load happens in-process.
    """
    for key in list(os.environ):
        if key.startswith("FIN3_PROVIDERS__") and "__CBOE__" not in key:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    return ClientConfig(
        _env_file=None,
        minio=_minio_config(cboe_bucket),
        providers={"cboe": CboeConfig()},
        lock=LockConfig(enabled=True, lock_dir=os.environ["FIN3_TEST_LOCK_DIR"]),
    )


def _record(
    label: str,
    provider: CboeProvider,
    closes_by_symbol: dict[str, pd.Series],
    retrieval: datetime,
) -> dict[str, Any]:
    first = next(iter(closes_by_symbol.values()))
    record = {
        "range": label,
        "source_urls": [
            f"{provider._base_url}/{symbol}_History.csv" for symbol in closes_by_symbol
        ],
        "retrieved_at_utc": retrieval.isoformat(),
        "shape": [len(first), len(closes_by_symbol)],
        "closes": {
            symbol: [None if pd.isna(v) else float(v) for v in series.tolist()]
            for symbol, series in closes_by_symbol.items()
        },
        "first_date": first.index[0].isoformat() if len(first) > 0 else None,
        "last_date": first.index[-1].isoformat() if len(first) > 0 else None,
    }
    print(json.dumps(record, default=str))
    return record


class TestDirectSourceRanges:
    """Two distinct live range checks (the CSV source has no pages; these are
    not pagination)."""

    def test_november_and_july_ranges_recorded(self) -> None:
        provider = CboeProvider(CboeConfig())
        retrieval = datetime.now(timezone.utc)

        nov = {
            symbol: provider.fetch(
                symbol, NOV_START, NOV_END, Resolution.ONE_DAY,
                asset_type=AssetType.INDEX_US,
            )
            for symbol in ("VIX", "VIX3M")
        }
        jul = {
            symbol: provider.fetch(
                symbol, JUL_START, JUL_END, Resolution.ONE_DAY,
                asset_type=AssetType.INDEX_US,
            )
            for symbol in ("VIX", "VIX3M")
        }

        for symbol, df in nov.items():
            assert [t.date().isoformat() for t in df.index] == [
                "2023-11-01", "2023-11-02", "2023-11-03", "2023-11-06", "2023-11-07",
            ]
            assert df["close"].tolist() == NOV_CLOSES[symbol]
            assert str(df["close"].dtype) == "float64"
        # Live source evidence (recorded 2026-11-05): VIX publishes a row for
        # the non-session 2023-07-04 (close 13.7); VIX3M publishes none.
        expected_july = {
            "VIX": {
                "dates": ["2023-07-03", "2023-07-04", "2023-07-05"],
                "closes": [13.57, 13.7, 14.18],
            },
            "VIX3M": {
                "dates": ["2023-07-03", "2023-07-05"],
                "closes": [16.07, 16.51],
            },
        }
        for symbol, df in jul.items():
            expected = expected_july[symbol]
            assert [t.date().isoformat() for t in df.index] == expected["dates"], symbol
            assert df["close"].tolist() == expected["closes"], symbol

        _record(
            "2023-11-01..07",
            provider,
            {symbol: df["close"] for symbol, df in nov.items()},
            retrieval,
        )
        _record(
            "2023-07-03..05",
            provider,
            {symbol: df["close"] for symbol, df in jul.items()},
            retrieval,
        )


class TestStandardPathWindow:
    def test_get_data_window_shape_values_and_stored_readback(
        self, cboe_client_config: ClientConfig
    ) -> None:
        fetcher = MarketDataFetcher(cboe_client_config)
        result = fetcher.get_data(
            AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, ["VIX", "VIX3M"],
            NOV_START, NOV_END,
        )
        assert result.shape == (5, 10)
        assert list(result.index) == list(pd.to_datetime(NOV_DATES, utc=True))
        for symbol, closes in NOV_CLOSES.items():
            close = result[(symbol, "close")]
            assert close.tolist() == closes
            for field in ("open", "high", "low"):
                assert result[(symbol, field)].tolist() == closes
            assert result[(symbol, "volume")].eq(0.0).all()
        # No padded observation among these five sessions.
        for symbol in ("VIX", "VIX3M"):
            assert result[(symbol, "close")].notna().all()

        storage = ArcticStorage(_minio_config(cboe_client_config.minio.bucket))
        for symbol in ("VIX", "VIX3M"):
            stored = storage.read(CANONICAL_LIB, symbol)
            assert stored is not None
            pd.testing.assert_frame_equal(
                stored,
                result.xs(symbol, axis=1, level=0).loc[:, list(OHLCV_COLUMNS)],
                check_freq=False,
            )

    def test_july_four_never_migrates_onto_a_session(
        self, cboe_client_config: ClientConfig
    ) -> None:
        fetcher = MarketDataFetcher(cboe_client_config)
        result = fetcher.get_data(
            AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, ["VIX", "VIX3M"],
            JUL_START, JUL_END,
        )
        assert [t.date().isoformat() for t in result.index] == [
            "2023-07-03", "2023-07-05",
        ]
        for symbol, closes in JUL_CLOSES.items():
            assert result[(symbol, "close")].tolist() == closes
            assert result[(symbol, "volume")].eq(0.0).all()

        # The direct source row for the non-session date exists but must
        # never have been shifted onto July 3 or July 5.
        direct = CboeProvider(CboeConfig()).fetch(
            "VIX", JUL_START, JUL_END, Resolution.ONE_DAY,
            asset_type=AssetType.INDEX_US,
        )
        assert pd.Timestamp("2023-07-04", tz="UTC") in direct.index
        assert pd.Timestamp("2023-07-04", tz="UTC") not in result.index

    def test_bounds_match_pinned_source_first_dates(self) -> None:
        provider = CboeProvider(CboeConfig())
        for symbol, first_date in FIRST_SOURCE_DATE.items():
            bounds = provider.get_instrument_bounds(symbol)
            assert bounds["delist_date"] is None
            ipo = bounds["ipo_date"]
            assert ipo is not None
            observed = ipo.date().isoformat()
            assert observed == first_date, (
                f"{symbol} first source date changed: pinned {first_date}, "
                f"observed {observed}. Inspect and record the new source "
                "evidence rather than silently re-pinning."
            )
            print(f"{symbol} ipo_date={ipo.isoformat()} delist_date=None")


class TestDecadeBackfill:
    def test_decade_backfill_on_owned_bucket(
        self, cboe_client_config: ClientConfig
    ) -> None:
        fetcher = MarketDataFetcher(cboe_client_config)
        result = fetcher.get_data(
            AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, ["VIX", "VIX3M"],
            DECADE_START, DECADE_END,
        )
        assert result.shape[1] == 10

        storage = ArcticStorage(_minio_config(cboe_client_config.minio.bucket))
        strategy = AssetType.INDEX_US.calendar_strategy
        summary: dict[str, Any] = {}
        for symbol in ("VIX", "VIX3M"):
            stored = storage.read(CANONICAL_LIB, symbol)
            assert stored is not None
            source = CboeProvider(CboeConfig())._history(symbol)
            source_close = {ts.date(): float(v) for ts, v in source["close"].items()}

            validate_storage_artifact(
                stored, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
            observed = stored["close"].notna()
            # Both ends observed; span exceeds ten calendar years.
            assert observed.iloc[0] and observed.iloc[-1]
            span_days = (stored.index[-1] - stored.index[0]).days
            assert span_days > 365 * 10

            # Every observed stored close equals the source close by date.
            for ts, close in stored["close"].items():
                if pd.isna(close):
                    continue
                assert ts.date() in source_close, f"{symbol} {ts.date()} not in source"
                assert close == source_close[ts.date()], (
                    f"{symbol} {ts.date()}: stored {close} != source "
                    f"{source_close[ts.date()]}"
                )
            # An observed row's date must be a source date, and padding rows
            # must be dates the source does not serve on XNYS sessions.
            for ts, close in stored["close"].items():
                if pd.isna(close):
                    assert ts.date() not in source_close

            grid = strategy.generate_grid(
                stored.index[0], stored.index[-1], Resolution.ONE_DAY
            )
            report = check_integrity(
                stored, grid, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
            )
            error_findings = [i for i in report.issues if i.severity == "error"]
            assert error_findings == [], (
                f"{symbol} audit errors: {report.summary}"
            )
            summary[symbol] = {
                "total_rows": len(stored),
                "observed_rows": int(observed.sum()),
                "null_rows": int((~observed).sum()),
                "first": stored.index[0].isoformat(),
                "last": stored.index[-1].isoformat(),
                "warnings": report.summary,
            }
            print(json.dumps({symbol: summary[symbol]}, default=str))
        # Raw provider output for the window validates too (Stage 1).
        raw = CboeProvider(CboeConfig()).fetch(
            "VIX", DECADE_START, DECADE_END, Resolution.ONE_DAY,
            asset_type=AssetType.INDEX_US,
        )
        validate_raw_provider_data(
            raw, Resolution.ONE_DAY, asset_type=AssetType.INDEX_US
        )


class TestPersistentReuse:
    def test_fresh_fetcher_reads_without_fetch_or_new_version(
        self, cboe_client_config: ClientConfig
    ) -> None:
        args = (AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, ["VIX", "VIX3M"])
        first_fetcher = MarketDataFetcher(cboe_client_config)
        first = first_fetcher.get_data(*args, NOV_START, NOV_END)
        arctic = first_fetcher._storage.arctic_for(cboe_client_config.minio.bucket)
        library = arctic[CANONICAL_LIB]
        versions = {
            symbol: len(library.list_versions(symbol))
            for symbol in ("VIX", "VIX3M")
        }

        # Any provider fetch in the second process must fail loudly.
        def explode(*a: object, **k: object) -> pd.DataFrame:
            raise RuntimeError("covered range must not fetch")

        original = CboeProvider.fetch
        CboeProvider.fetch = explode  # type: ignore[method-assign]
        try:
            second = MarketDataFetcher(cboe_client_config).get_data(
                *args, NOV_START, NOV_END
            )
        finally:
            CboeProvider.fetch = original  # type: ignore[method-assign]

        pd.testing.assert_frame_equal(first, second)
        assert {
            symbol: len(library.list_versions(symbol))
            for symbol in ("VIX", "VIX3M")
        } == versions


class TestIsolatedConfig:
    def test_constructor_and_access_with_unrelated_keys_absent(
        self, cboe_client_config: ClientConfig, cboe_bucket: str
    ) -> None:
        """The Cboe access path works with no other provider key present."""
        assert set(cboe_client_config.providers) == {"cboe"}
        fetcher = MarketDataFetcher(cboe_client_config)
        result = fetcher.get_data(
            AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, ["VIX", "VIX3M"],
            NOV_START, NOV_END,
        )
        assert result.shape == (5, 10)
        overview = fetcher.inspect(
            AssetType.INDEX_US, "cboe", Resolution.ONE_DAY, include_integrity=True
        )
        assert overview.library == CANONICAL_LIB
        assert overview.symbol_count == 2
        for profile in overview.symbols:
            assert profile.total_bars == 5
            assert profile.null_bars == 0
