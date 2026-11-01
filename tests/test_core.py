"""End-to-end tests for MarketDataFetcher."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.config.settings import ClientConfig, DatabentoConfig, LockConfig, MinioConfig
from fin3.core import MarketDataFetcher
from fin3.exceptions import (
    ConfigurationError,
    CostLimitExceededError,
    DataValidationError,
)
from fin3.metadata.asset_profile import MetadataStore
from fin3.monitoring.tracker import ResourceTracker
from fin3.providers import ProviderRegistry
from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, library_name
from fin3.storage.arctic import ArcticStorage
from tests.conftest import make_ohlcv


def _make_fetcher(storage: ArcticStorage) -> MarketDataFetcher:
    """Build a MarketDataFetcher without connecting to real MinIO or providers."""
    fetcher = MarketDataFetcher.__new__(MarketDataFetcher)
    fetcher._config = ClientConfig(
        minio=MinioConfig(endpoint="unused", access_key="t", secret_key="t"),
        providers={"databento": DatabentoConfig(api_key="test_key")},
    )
    fetcher._storage = storage
    fetcher._providers = ProviderRegistry.__new__(ProviderRegistry)
    fetcher._providers._providers = {}
    fetcher._metadata = MetadataStore(storage)
    return fetcher


@pytest.fixture
def lmdb_storage(tmp_path: pytest.TempPathFactory) -> ArcticStorage:
    return ArcticStorage.from_lmdb(str(tmp_path / "e2e_lmdb"))


class TestMarketDataFetcherE2E:
    def test_get_data_empty_symbols_rejected(self, lmdb_storage: ArcticStorage) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        with pytest.raises(ValueError, match="non-empty"):
            fetcher.get_data(
                asset_type=AssetType.CRYPTO,
                provider="databento",
                resolution=Resolution.ONE_HOUR,
                symbols=[],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )

    def test_get_data_start_after_end_rejected(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        with pytest.raises(ValueError, match="must be before"):
            fetcher.get_data(
                asset_type=AssetType.CRYPTO,
                provider="databento",
                resolution=Resolution.ONE_HOUR,
                symbols=["BTC-USD"],
                start=datetime(2024, 1, 2, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

    @pytest.mark.parametrize(
        "bad",
        ["...", "???", "---", "   ", "@#$"],
    )
    def test_get_data_invalid_symbol_rejected(
        self, lmdb_storage: ArcticStorage, bad: str
    ) -> None:
        """Symbols with no alphanumeric chars are rejected up front."""
        fetcher = _make_fetcher(lmdb_storage)

        with pytest.raises(ValueError, match="invalid symbol"):
            fetcher.get_data(
                asset_type=AssetType.EQUITY_US,
                provider="databento",
                resolution=Resolution.ONE_DAY,
                symbols=[bad],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )

    def test_get_data_valid_symbol_formats_accepted(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Realistic ticker formats pass the alphanumeric check (no ValueError).

        The fetcher registry is empty, so every valid symbol must fail with
        ConfigurationError (unknown provider) — failing with anything else,
        especially ValueError, means the symbol was wrongly rejected.
        """
        for sym in ["AAPL", "BRK.B", "BRK B", "BTC-USD", "ES.n.0", "123"]:
            with pytest.raises(ConfigurationError):
                _make_fetcher(lmdb_storage).get_data(
                    asset_type=AssetType.EQUITY_US,
                    provider="databento",
                    resolution=Resolution.ONE_DAY,
                    symbols=[sym],
                    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, tzinfo=timezone.utc),
                )


class TestCostGate:
    """Tests for the max_cost pre-download cost check."""

    def test_get_data_raises_cost_limit_exceeded(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        mock_provider.estimate_cost.return_value = 5.00
        fetcher._providers._providers = {"databento": mock_provider}

        with pytest.raises(CostLimitExceededError) as exc_info:
            fetcher.get_data(
                asset_type=AssetType.CRYPTO,
                provider="databento",
                resolution=Resolution.ONE_HOUR,
                symbols=["BTC-USD"],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
                max_cost=1.00,
            )

        assert exc_info.value.estimated_cost == 5.00
        assert exc_info.value.max_cost == 1.00
        # estimate_cost should have been called during the cost check pass
        mock_provider.estimate_cost.assert_called()

    def test_get_data_proceeds_under_cost_limit(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        mock_provider.estimate_cost.return_value = 0.50
        fetcher._providers._providers = {"databento": mock_provider}

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
            max_cost=10.00,
        )

        # Proceeding means real provider data came back verbatim, not just a
        # non-empty shape.
        assert isinstance(result, pd.DataFrame)
        close = result[("BTC-USD", "close")]
        assert close.nunique() == 24
        assert close.iloc[0] == 100.2
        assert close.iloc[-1] == pytest.approx(102.5)
        stored = lmdb_storage.read("crypto-1h-databento", "BTC-USD")
        assert stored is not None
        pd.testing.assert_frame_equal(
            stored, mock_provider.fetch.return_value, check_freq=False
        )
        mock_provider.fetch.assert_called()
        mock_provider.estimate_cost.assert_called_once()

    def test_get_data_no_cost_check_when_max_cost_none(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        mock_provider.estimate_cost.return_value = 999.00
        fetcher._providers._providers = {"databento": mock_provider}

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert isinstance(result, pd.DataFrame)
        mock_provider.estimate_cost.assert_not_called()

    def test_get_data_skips_cost_check_when_provider_lacks_estimate_cost(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """The silent hasattr gate (core.py): ``max_cost`` set plus a provider
        WITHOUT ``estimate_cost`` means no cost check runs — get_data proceeds
        and fetches normally instead of raising."""

        class NoCostProvider:
            def fetch(self, **kwargs: object) -> pd.DataFrame:
                return make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")

            def get_instrument_bounds(
                self, symbol: str
            ) -> dict[str, datetime | None]:
                return {"ipo_date": None, "delist_date": None}

        fetcher = _make_fetcher(lmdb_storage)
        provider = NoCostProvider()
        assert not hasattr(provider, "estimate_cost")  # branch precondition
        fetcher._providers._providers = {"databento": provider}

        # max_cost low enough that any real estimate would exceed it — the
        # point is that the check never runs at all.
        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
            max_cost=0.01,
        )

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 24


class TestMonitoringWiring:
    """get_data wraps provider.fetch via ResourceTracker/ByteCounter; after a
    mocked fetch the tracker must have recorded real byte/fetch values."""

    def test_get_data_records_bytes_and_fetch_count(
        self, lmdb_storage: ArcticStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)
        bars = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        mock_provider = _mock_provider()
        mock_provider.fetch.return_value = bars
        fetcher._providers._providers = {"databento": mock_provider}

        # Capture the tracker instance get_data creates (it is a local of
        # get_data, so the only observable surface is the instance itself).
        created: list[ResourceTracker] = []
        real_init = ResourceTracker.__init__

        class CapturingTracker(ResourceTracker):
            def __init__(self, *args: object, **kwargs: object) -> None:
                real_init(self, *args, **kwargs)
                created.append(self)

        monkeypatch.setattr("fin3.core.ResourceTracker", CapturingTracker)
        # Hermetic regardless of the invoking environment: never open a
        # tmux monitor pane from a test.
        monkeypatch.delenv("TMUX", raising=False)

        # Deep-memory measurement is only stable on a freshly built frame
        # (pandas materialises caches as the pipeline touches it), so capture
        # the per-fetch expectation before get_data runs.
        per_fetch_bytes = int(bars.memory_usage(deep=True).sum())

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert isinstance(result, pd.DataFrame)
        assert len(created) == 1
        counter = created[0]._byte_counter
        # One counted fetch per provider call (discovery + gap fill), and the
        # exact deep-memory size of the returned frame per fetch.
        assert counter.fetch_count == mock_provider.fetch.call_count
        assert counter.fetch_count >= 1
        assert counter.total_bytes == per_fetch_bytes * counter.fetch_count
        assert counter.total_bytes > 0
        # The summary surface (SampledMetrics) reports the same counters.
        metrics = created[0]._build_metrics()
        assert metrics.fetch_count == counter.fetch_count
        assert metrics.network_bytes == counter.total_bytes


class TestEmptyDataGuard:
    """Tests for Bug 2: non-existent symbols should not persist null grids."""

    def test_empty_data_new_symbol_not_stored(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Provider returns empty data for a new symbol — nothing stored."""
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["FAKESYMBOL"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )

        assert isinstance(result, pd.DataFrame)
        # Should NOT have stored anything for this symbol
        assert not lmdb_storage.has_symbol("crypto-1h-databento", "FAKESYMBOL")

    def test_empty_data_existing_symbol_still_updated(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Provider returns empty data for an existing symbol — null grid stored."""
        fetcher = _make_fetcher(lmdb_storage)

        # Phase 1: write real data for day 1
        real_data = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        empty_df = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        mock_provider = MagicMock()
        # bootstrap discovery fetch + gap fill
        mock_provider.fetch.side_effect = [real_data, real_data]
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert lmdb_storage.has_symbol("crypto-1h-databento", "BTC-USD")

        # Phase 2: request day 2 — bootstrap returns cached metadata (no fetch),
        # gap fill returns empty data, but symbol already exists so null grid is stored
        mock_provider.fetch.side_effect = [empty_df]

        result = fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 2, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 23, 0, tzinfo=timezone.utc),
        )

        assert isinstance(result, pd.DataFrame)
        # Symbol should still exist in storage (not removed)
        assert lmdb_storage.has_symbol("crypto-1h-databento", "BTC-USD")

        # The null grid is actually persisted: day 2 holds 24 calendar-padded
        # rows (volume=0, OHLC=NaN) and day 1's real values are untouched.
        stored = lmdb_storage.read("crypto-1h-databento", "BTC-USD")
        assert stored is not None
        assert len(stored) == 48
        day1 = stored.loc[: pd.Timestamp("2024-01-01 23:00", tz="UTC")]
        assert len(day1) == 24
        assert day1["close"].iloc[0] == 100.2
        assert day1["close"].iloc[-1] == pytest.approx(102.5)
        assert (day1["volume"] == 1000.0).all()
        day2 = stored.loc[pd.Timestamp("2024-01-02", tz="UTC") :]
        assert len(day2) == 24
        assert (day2["volume"] == 0.0).all()
        assert day2[["open", "high", "low", "close"]].isna().all().all()


class TestSymbolLocking:
    """The check-then-act span in _ensure_symbol is guarded by a per-symbol lock."""

    def test_ensure_symbol_acquires_lock_per_symbol(self, tmp_path) -> None:
        """get_data acquires one lock per symbol; distinct symbols → distinct keys."""
        storage = ArcticStorage.from_lmdb(
            str(tmp_path / "lmdb"),
            lock=LockConfig(enabled=True, lock_dir=str(tmp_path / "locks")),
        )
        fetcher = _make_fetcher(storage)

        # Provider returns data covering the requested hourly range so a gap is
        # detected and _ensure_symbol runs fully (gap detection → fetch → write).
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        # Wrap the real lock_symbol so calls are recorded yet still performed.
        spy = MagicMock(wraps=storage.lock_symbol)
        storage.lock_symbol = spy  # type: ignore[method-assign]

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD", "ETH-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        lib_name = "crypto-1h-databento"
        # One acquisition per symbol, each keyed by its (library, symbol) pair.
        assert spy.call_count == 2
        spy.assert_any_call(lib_name, "BTC-USD")
        spy.assert_any_call(lib_name, "ETH-USD")
        # Sanity: the two recorded keys are distinct symbols.
        called_symbols = {call.args[1] for call in spy.call_args_list}
        assert called_symbols == {"BTC-USD", "ETH-USD"}


def _mock_provider() -> MagicMock:
    """MagicMock provider whose lifecycle bounds resolve to (None, None)."""
    mock_provider = MagicMock()
    mock_provider.get_instrument_bounds = MagicMock(
        return_value={"ipo_date": None, "delist_date": None}
    )
    return mock_provider


class TestGetDataSemantics:
    """Value-level semantics of get_data: read-back, idempotency, gap and
    cost behaviour, IPO clamping, calendar padding, and multi-symbol align."""

    @staticmethod
    def _get(
        fetcher: MarketDataFetcher,
        start: datetime,
        end: datetime,
        symbols: list[str] | None = None,
        max_cost: float | None = None,
    ) -> pd.DataFrame:
        return fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=symbols if symbols is not None else ["BTC-USD"],
            start=start,
            end=end,
            max_cost=max_cost,
        )

    @staticmethod
    def _lib() -> str:
        return library_name(AssetType.CRYPTO, Resolution.ONE_HOUR, "databento")

    def test_read_back_exact_values(self, lmdb_storage: ArcticStorage) -> None:
        """get_data returns the provider closes verbatim and stores the same."""
        fetcher = _make_fetcher(lmdb_storage)
        bars = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        mock_provider = _mock_provider()
        mock_provider.fetch.return_value = bars
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        close = result[("BTC-USD", "close")]
        # 24 distinct closes, byte-for-byte the provider's series.
        pd.testing.assert_series_equal(close, bars["close"], check_freq=False, check_names=False)
        assert close.nunique() == 24
        assert close.iloc[0] == 100.2
        assert close.iloc[-1] == pytest.approx(102.5)
        assert result.index.tz is not None
        assert str(result.index.tz) == "UTC"
        assert result.index.name is None

        stored = lmdb_storage.read(self._lib(), "BTC-USD")
        assert stored is not None
        pd.testing.assert_frame_equal(stored, bars, check_freq=False)

    def test_get_data_idempotent_by_value(self, lmdb_storage: ArcticStorage) -> None:
        """A second identical get_data neither fetches nor changes any value."""
        fetcher = _make_fetcher(lmdb_storage)
        bars = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        mock_provider = _mock_provider()
        mock_provider.fetch.return_value = bars
        fetcher._providers._providers = {"databento": mock_provider}

        args = (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )
        first = self._get(fetcher, *args)
        calls_after_first = mock_provider.fetch.call_count
        stored_first = lmdb_storage.read(self._lib(), "BTC-USD")

        second = self._get(fetcher, *args)

        pd.testing.assert_frame_equal(first, second)
        stored_second = lmdb_storage.read(self._lib(), "BTC-USD")
        assert stored_second is not None and stored_first is not None
        assert len(stored_second) == len(stored_first)
        pd.testing.assert_frame_equal(stored_second, stored_first, check_freq=False)
        assert mock_provider.fetch.call_count == calls_after_first

    def test_mid_gap_failure_keeps_first_gap_committed(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """When the second gap's fetch blows up, the first gap's bars are
        already committed and readable (per-gap durability)."""
        fetcher = _make_fetcher(lmdb_storage)
        lib = self._lib()
        # Interior chunk at 10:00-11:00 inside an 08:00-16:00 request:
        # hourly gap chunks merge to exactly two gaps [08:00,10:00) and [12:00,16:00).
        interior = make_ohlcv("2024-01-01 10:00", periods=2, freq="1h")
        lmdb_storage.write(lib, "BTC-USD", interior)

        mock_provider = _mock_provider()
        # Fetch #1 is the metadata discovery fetch; it must start at the
        # request start so the discovered ipo does not clamp eff_start
        # (otherwise the interior chunk would not yield two gaps).
        discovery = make_ohlcv("2024-01-01 08:00", periods=8, freq="1h")
        first_gap_bars = make_ohlcv("2024-01-01 08:00", periods=2, freq="1h")
        mock_provider.fetch.side_effect = [
            discovery,
            first_gap_bars,
            RuntimeError("boom"),
        ]
        fetcher._providers._providers = {"databento": mock_provider}

        with pytest.raises(RuntimeError, match="boom"):
            self._get(
                fetcher,
                datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
            )

        # 3 calls total: discovery + gap-1 fill + gap-2 fill (which raised).
        assert mock_provider.fetch.call_count == 3
        stored = lmdb_storage.read(lib, "BTC-USD")
        assert stored is not None
        assert stored.loc[pd.Timestamp("2024-01-01 08:00", tz="UTC"), "close"] == 100.2
        assert stored.loc[pd.Timestamp("2024-01-01 09:00", tz="UTC"), "close"] == 100.3
        assert stored.loc[pd.Timestamp("2024-01-01 08:00", tz="UTC"), "volume"] == 1000.0
        assert stored.loc[pd.Timestamp("2024-01-01 09:00", tz="UTC"), "volume"] == 1000.0

    def test_incremental_window_fetches_only_new_day(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Extending the window by one day issues exactly one fetch for day 2."""
        fetcher = _make_fetcher(lmdb_storage)
        day1 = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        day2 = make_ohlcv("2024-01-02 00:00", periods=24, freq="1h")
        mock_provider = _mock_provider()
        # discovery, day-1 gap fill, then day-2 gap fill.
        mock_provider.fetch.side_effect = [day1, day1, day2]
        fetcher._providers._providers = {"databento": mock_provider}

        self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )
        calls_after_day1 = mock_provider.fetch.call_count

        self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 23, 0, tzinfo=timezone.utc),
        )

        assert mock_provider.fetch.call_count == calls_after_day1 + 1
        last_call = mock_provider.fetch.call_args
        assert last_call.kwargs["start"] == datetime(2024, 1, 2, tzinfo=timezone.utc)
        assert last_call.kwargs["end"] == datetime(
            2024, 1, 2, 23, 0, tzinfo=timezone.utc
        )

    def test_cost_called_once_over_union_gap_span(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Interior gaps collapse to one estimate_cost call over the union
        span: start=min(gap starts), end=max(gap ends) — the documented
        _check_cost contract."""
        fetcher = _make_fetcher(lmdb_storage)
        lib = self._lib()
        interior = make_ohlcv("2024-01-01 10:00", periods=2, freq="1h")
        lmdb_storage.write(lib, "BTC-USD", interior)

        mock_provider = _mock_provider()
        mock_provider.fetch.side_effect = [
            make_ohlcv("2024-01-01 08:00", periods=8, freq="1h"),  # discovery
            make_ohlcv("2024-01-01 08:00", periods=2, freq="1h"),  # gap 1
            make_ohlcv("2024-01-01 12:00", periods=4, freq="1h"),  # gap 2
        ]
        mock_provider.estimate_cost.return_value = 0.5
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
            max_cost=100.0,
        )

        assert isinstance(result, pd.DataFrame)
        mock_provider.estimate_cost.assert_called_once()
        cost_call = mock_provider.estimate_cost.call_args
        assert cost_call.kwargs["start"] == datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
        assert cost_call.kwargs["end"] == datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

    def test_cost_exactly_at_limit_passes(self, lmdb_storage: ArcticStorage) -> None:
        """The cost gate is strict >: estimate == max_cost must not raise."""
        fetcher = _make_fetcher(lmdb_storage)
        mock_provider = _mock_provider()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.estimate_cost.return_value = 5.0
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
            max_cost=5.0,
        )

        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0
        mock_provider.fetch.assert_called()

    def test_ipo_inside_range_clamps_fetch_start(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """With an ipo inside the window, the provider is asked for nothing
        before the ipo and the result starts at the ipo."""
        fetcher = _make_fetcher(lmdb_storage)
        ipo = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
        mock_provider = _mock_provider()
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": ipo, "delist_date": None}
        )
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 05:00", periods=19, freq="1h"
        )
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        # Known bounds short-circuit the discovery fetch: exactly one call.
        mock_provider.fetch.assert_called_once()
        assert mock_provider.fetch.call_args.kwargs["start"] == ipo
        assert result.index[0] == pd.Timestamp("2024-01-01 05:00", tz="UTC")
        assert len(result) == 19
        assert (
            result.index < pd.Timestamp("2024-01-01 05:00", tz="UTC")
        ).sum() == 0

    def test_range_entirely_before_ipo_fetches_nothing(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """A window ending before the ipo fetches nothing and returns an
        empty frame that keeps the (symbol, field) column contract."""
        fetcher = _make_fetcher(lmdb_storage)
        ipo = datetime(2024, 2, 1, tzinfo=timezone.utc)
        mock_provider = _mock_provider()
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": ipo, "delist_date": None}
        )
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        mock_provider.fetch.assert_not_called()
        assert result.empty
        assert isinstance(result.columns, pd.MultiIndex)
        assert list(result.columns) == list(
            pd.MultiIndex.from_product([["BTC-USD"], list(OHLCV_COLUMNS)])
        )

    def test_partial_gap_fill_pads_calendar_and_skips_refetch(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """A provider returning only half the gap leaves calendar-padded
        volume=0/NaN rows for the rest, and a repeat request finds no
        residual gap to fetch."""
        fetcher = _make_fetcher(lmdb_storage)
        half = make_ohlcv("2024-01-01 00:00", periods=12, freq="1h")  # 00:00-11:00
        mock_provider = _mock_provider()
        mock_provider.fetch.return_value = half
        fetcher._providers._providers = {"databento": mock_provider}

        args = (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )
        first = self._get(fetcher, *args)
        calls_after_first = mock_provider.fetch.call_count

        stored = lmdb_storage.read(self._lib(), "BTC-USD")
        assert stored is not None
        assert len(stored) == 24
        tail = stored.loc[pd.Timestamp("2024-01-01 12:00", tz="UTC") :]
        assert len(tail) == 12
        assert (tail["volume"] == 0.0).all()
        assert tail[["open", "high", "low", "close"]].isna().all().all()

        second = self._get(fetcher, *args)

        assert mock_provider.fetch.call_count == calls_after_first
        pd.testing.assert_frame_equal(first, second)

    def test_multi_symbol_alignment_union_index_and_sorted_columns(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        """Two symbols covering different sub-ranges align onto the union
        hourly index with NaN padding outside each symbol's range and
        lexicographically sorted (symbol, field) columns."""
        fetcher = _make_fetcher(lmdb_storage)
        btc = make_ohlcv("2024-01-01 00:00", periods=12, freq="1h")  # 00:00-11:00
        eth = make_ohlcv("2024-01-01 08:00", periods=16, freq="1h")  # 08:00-23:00

        def fake_fetch(**kwargs: object) -> pd.DataFrame:
            return btc if kwargs["symbol"] == "BTC-USD" else eth

        mock_provider = _mock_provider()
        mock_provider.fetch.side_effect = fake_fetch
        fetcher._providers._providers = {"databento": mock_provider}

        result = self._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
            symbols=["BTC-USD", "ETH-USD"],
        )

        expected_index = pd.date_range(
            "2024-01-01 00:00", "2024-01-01 23:00", freq="h", tz="UTC"
        )
        assert result.index.equals(expected_index)

        # _align_symbols contract: pd.concat(axis=1).sort_index(axis=1),
        # i.e. symbols sorted, fields alphabetical within each symbol.
        assert list(result.columns) == list(
            pd.MultiIndex.from_product(
                [["BTC-USD", "ETH-USD"], ["close", "high", "low", "open", "volume"]]
            )
        )

        eth_cutoff = pd.Timestamp("2024-01-01 07:00", tz="UTC")
        eth_head = result.loc[:eth_cutoff, ("ETH-USD", "close")]
        assert len(eth_head) == 8
        assert eth_head.isna().all()
        assert result.loc[:eth_cutoff, ("ETH-USD", "volume")].isna().all()

        btc_tail = result.loc[pd.Timestamp("2024-01-01 12:00", tz="UTC") :]
        assert btc_tail[("BTC-USD", "close")].isna().all()

        # Real values survive inside each symbol's own range.
        assert result.loc[
            pd.Timestamp("2024-01-01 00:00", tz="UTC"), ("BTC-USD", "close")
        ] == 100.2
        assert result.loc[
            pd.Timestamp("2024-01-01 23:00", tz="UTC"), ("ETH-USD", "close")
        ] == pytest.approx(101.7)


class TestValueLevelRejection:
    """A rule-invalid update must fail loudly and leave stored data intact."""

    def test_corrupt_update_rejected_and_storage_untouched(
        self, lmdb_storage: ArcticStorage
    ) -> None:
        fetcher = _make_fetcher(lmdb_storage)
        lib = library_name(AssetType.CRYPTO, Resolution.ONE_HOUR, "databento")
        good = make_ohlcv("2024-01-01 00:00", periods=24, freq="1h")
        mock_provider = _mock_provider()
        # discovery fetch + day-1 gap fill
        mock_provider.fetch.side_effect = [good, good]
        fetcher._providers._providers = {"databento": mock_provider}

        TestGetDataSemantics._get(
            fetcher,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )
        stored_before = lmdb_storage.read(lib, "BTC-USD")
        versions_before = lmdb_storage.arctic[lib].list_versions("BTC-USD")
        assert stored_before is not None and len(stored_before) == 24

        corrupt_update = make_ohlcv("2024-01-02 00:00", periods=24, freq="1h")
        corrupt_update.iloc[0, corrupt_update.columns.get_loc("low")] = -1.0
        # Only the day-2 gap fill is fetched on the extended window.
        mock_provider.fetch.side_effect = [corrupt_update]
        with pytest.raises(DataValidationError) as excinfo:
            TestGetDataSemantics._get(
                fetcher,
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, 23, 0, tzinfo=timezone.utc),
            )
        assert type(excinfo.value) is DataValidationError
        assert excinfo.value.rule == "negative_price"

        stored_after = lmdb_storage.read(lib, "BTC-USD")
        assert stored_after is not None
        pd.testing.assert_frame_equal(stored_after, stored_before)
        assert lmdb_storage.arctic[lib].list_versions("BTC-USD") == versions_before
