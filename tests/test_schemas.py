"""Tests for schemas: library_name, AssetType, Resolution, empty_ohlcv."""

import pandas as pd

from fin3.schemas import OHLCV_COLUMNS, AssetType, Resolution, empty_ohlcv, library_name


class TestLibraryName:
    def test_equity_us(self) -> None:
        assert library_name(AssetType.EQUITY_US, Resolution.ONE_MINUTE, "databento") == "equities-1m-databento"

    def test_crypto(self) -> None:
        assert library_name(AssetType.CRYPTO, Resolution.ONE_HOUR, "binance") == "crypto-1h-binance"

    def test_futures(self) -> None:
        assert library_name(AssetType.FUTURES, Resolution.ONE_DAY, "databento") == "futures-1d-databento"

    def test_intraday_resolutions(self) -> None:
        assert library_name(AssetType.CRYPTO, Resolution.FIVE_MINUTE, "binance") == "crypto-5m-binance"
        assert library_name(AssetType.EQUITY_US, Resolution.FIFTEEN_MINUTE, "databento") == "equities-15m-databento"
        assert library_name(AssetType.FUTURES, Resolution.FOUR_HOUR, "databento") == "futures-4h-databento"

    def test_unmapped_asset_type_falls_back_to_enum_value(self) -> None:
        """``_ASSET_PREFIX.get(asset_type, asset_type.value)``: an asset type
        with no prefix mapping silently degrades to its ``.value`` as prefix.

        NOTE: this silent fallback is flagged for feat-020 (options)
        loud-fanout work — until then, pin the current behavior. All current
        AssetType members are mapped, so the fallback is exercised through a
        duck-typed stand-in, the same code path a future OPTIONS member would
        hit on its first call.
        """

        class _OptionsLike:
            value = "options"

        assert library_name(_OptionsLike(), Resolution.FIVE_MINUTE, "databento") == "options-5m-databento"


class TestAssetType:
    def test_mic_code_equity(self) -> None:
        assert AssetType.EQUITY_US.mic_code == "XNYS"

    def test_mic_code_crypto(self) -> None:
        assert AssetType.CRYPTO.mic_code is None

    def test_mic_code_futures(self) -> None:
        assert AssetType.FUTURES.mic_code == "CME"


class TestResolution:
    def test_timedelta_aliases(self) -> None:
        """Every Resolution member maps to a pandas-compatible alias.

        Pinned exhaustively: a new member without an entry here would make
        ``timedelta_alias`` raise KeyError on first use.
        """
        assert Resolution.ONE_MINUTE.timedelta_alias == "1min"
        assert Resolution.FIVE_MINUTE.timedelta_alias == "5min"
        assert Resolution.FIFTEEN_MINUTE.timedelta_alias == "15min"
        assert Resolution.ONE_HOUR.timedelta_alias == "1h"
        assert Resolution.FOUR_HOUR.timedelta_alias == "4h"
        assert Resolution.ONE_DAY.timedelta_alias == "1D"


class TestEmptyOhlcv:
    def test_schema(self) -> None:
        df = empty_ohlcv()
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert len(df) == 0
        assert str(df.index.tz) == "UTC"

    def test_columns_match_ohlcv_columns_constant(self) -> None:
        df = empty_ohlcv()
        assert list(df.columns) == list(OHLCV_COLUMNS)

    def test_index_name_is_none(self) -> None:
        assert empty_ohlcv().index.name is None

    def test_dtypes_are_object(self) -> None:
        """Pin current dtypes: the empty frame carries object columns. The
        float64 OHLC dtypes are imposed downstream (_reindex casts OHLC to
        float and volume to float), not by empty_ohlcv() itself."""
        df = empty_ohlcv()
        assert set(df.dtypes.astype(str)) == {"object"}
