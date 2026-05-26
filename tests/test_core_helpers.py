"""Direct unit tests for core.py helper functions."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from fin3.core import (
    _align_symbols,
    _assert_boundary,
    _reindex,
    _write_metadata,
)
from fin3.exceptions import BoundaryMismatchError
from tests.conftest import make_ohlcv


class TestReindex:
    def test_empty_df_produces_volume_zero(self) -> None:
        grid = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
        result = _reindex(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), grid)
        assert len(result) == 3
        assert (result["volume"] == 0).all()
        assert result["open"].isna().all()

    def test_fills_missing_bars(self) -> None:
        df = make_ohlcv("2024-01-01 00:00", periods=1, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        result = _reindex(df, grid)
        assert len(result) == 3
        assert result.iloc[0]["volume"] > 0
        assert result.iloc[1]["volume"] == 0
        assert result.iloc[2]["volume"] == 0


class TestAlignSymbols:
    def test_single_symbol(self) -> None:
        df = make_ohlcv("2024-01-01", periods=3, freq="1h")
        result = _align_symbols({"BTC": df})
        assert isinstance(result.columns, pd.MultiIndex)

    def test_multi_symbol_union_index(self) -> None:
        df1 = make_ohlcv("2024-01-01 00:00", periods=2, freq="1h")
        df2 = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        result = _align_symbols({"BTC": df1, "ETH": df2})
        assert len(result) == 3  # union of [00, 01] and [01, 02]
        symbols = result.columns.get_level_values(0).unique().tolist()
        assert "BTC" in symbols
        assert "ETH" in symbols

    def test_empty_dict_returns_empty(self) -> None:
        result = _align_symbols({})
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestAssertBoundary:
    def test_mismatch_raises(self) -> None:
        df = make_ohlcv("2024-01-01 01:00", periods=2, freq="1h")
        grid = pd.date_range("2024-01-01 00:00", periods=3, freq="1h", tz="UTC")
        with pytest.raises(BoundaryMismatchError):
            _assert_boundary(df, grid)

    def test_empty_passes(self) -> None:
        _assert_boundary(pd.DataFrame(), pd.DatetimeIndex([], tz="UTC"))


class TestWriteMetadata:
    def test_metadata_keys(self) -> None:
        meta = _write_metadata("AAPL", MagicMock(), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"))
        assert "downloaded_at" in meta
        assert "provider" in meta
        assert "symbol" in meta
        assert meta["symbol"] == "AAPL"
