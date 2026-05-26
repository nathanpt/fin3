"""Tests for schemas: library_name, AssetType, Resolution, empty_ohlcv."""

import pandas as pd

from fin3.schemas import AssetType, Resolution, empty_ohlcv, library_name


class TestLibraryName:
    def test_equity_us(self) -> None:
        assert library_name(AssetType.EQUITY_US, Resolution.ONE_MINUTE, "databento") == "equities-1m-databento"

    def test_crypto(self) -> None:
        assert library_name(AssetType.CRYPTO, Resolution.ONE_HOUR, "binance") == "crypto-tick-1h-binance"

    def test_futures(self) -> None:
        assert library_name(AssetType.FUTURES, Resolution.ONE_DAY, "databento") == "futures-1d-databento"


class TestAssetType:
    def test_mic_code_equity(self) -> None:
        assert AssetType.EQUITY_US.mic_code == "XNYS"

    def test_mic_code_crypto(self) -> None:
        assert AssetType.CRYPTO.mic_code is None

    def test_mic_code_futures(self) -> None:
        assert AssetType.FUTURES.mic_code == "CME"


class TestResolution:
    def test_timedelta_aliases(self) -> None:
        assert Resolution.ONE_MINUTE.timedelta_alias == "1min"
        assert Resolution.ONE_HOUR.timedelta_alias == "1h"
        assert Resolution.ONE_DAY.timedelta_alias == "1D"


class TestEmptyOhlcv:
    def test_schema(self) -> None:
        df = empty_ohlcv()
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert len(df) == 0
        assert str(df.index.tz) == "UTC"
