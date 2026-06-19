"""End-to-end tests for MarketDataFetcher."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.config.settings import ClientConfig, DatabentoConfig, LockConfig, MinioConfig
from fin3.core import MarketDataFetcher
from fin3.exceptions import CostLimitExceededError
from fin3.metadata.asset_profile import MetadataStore
from fin3.providers import ProviderRegistry
from fin3.schemas import AssetType, Resolution
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
    def test_get_data_fetches_and_stores(self, lmdb_storage: ArcticStorage) -> None:
        fetcher = _make_fetcher(lmdb_storage)

        mock_provider = MagicMock()
        mock_provider.fetch.return_value = make_ohlcv(
            "2024-01-01 00:00", periods=24, freq="1h"
        )
        mock_provider.get_instrument_bounds = MagicMock(
            return_value={"ipo_date": None, "delist_date": None}
        )
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
        assert len(result) > 0
        assert isinstance(result.columns, pd.MultiIndex)

    def test_get_data_second_call_skips_fetch(
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
        fetcher._providers._providers = {"databento": mock_provider}

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        call_count_after_first = mock_provider.fetch.call_count

        fetcher.get_data(
            asset_type=AssetType.CRYPTO,
            provider="databento",
            resolution=Resolution.ONE_HOUR,
            symbols=["BTC-USD"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc),
        )

        assert mock_provider.fetch.call_count == call_count_after_first

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
        """Realistic ticker formats pass the alphanumeric check (no ValueError)."""
        # Each of these contains at least one alphanumeric char and should not
        # trip the invalid-symbol guard. They'll fail later for other reasons
        # (no provider), but not with the validation error.
        for sym in ["AAPL", "BRK.B", "BRK B", "BTC-USD", "ES.n.0", "123"]:
            try:
                _make_fetcher(lmdb_storage).get_data(
                    asset_type=AssetType.EQUITY_US,
                    provider="databento",
                    resolution=Resolution.ONE_DAY,
                    symbols=[sym],
                    start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    end=datetime(2024, 1, 2, tzinfo=timezone.utc),
                )
            except ValueError as exc:
                pytest.fail(f"symbol {sym!r} wrongly rejected: {exc}")
            except Exception:
                # Other failures (provider/storage) are expected and fine.
                pass


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

        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0
        mock_provider.fetch.assert_called()

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
        assert not lmdb_storage.has_symbol("crypto-tick-1h-databento", "FAKESYMBOL")

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

        assert lmdb_storage.has_symbol("crypto-tick-1h-databento", "BTC-USD")

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
        assert lmdb_storage.has_symbol("crypto-tick-1h-databento", "BTC-USD")


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

        lib_name = "crypto-tick-1h-databento"
        # One acquisition per symbol, each keyed by its (library, symbol) pair.
        assert spy.call_count == 2
        spy.assert_any_call(lib_name, "BTC-USD")
        spy.assert_any_call(lib_name, "ETH-USD")
        # Sanity: the two recorded keys are distinct symbols.
        called_symbols = {call.args[1] for call in spy.call_args_list}
        assert called_symbols == {"BTC-USD", "ETH-USD"}
