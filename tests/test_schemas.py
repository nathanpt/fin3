"""Tests for schemas: library_name, AssetType, Resolution, empty_ohlcv."""

import numpy as np
import pandas as pd
import pytest

from fin3.schemas import (
    OHLCV_COLUMNS,
    AssetType,
    Resolution,
    empty_ohlcv,
    library_name,
    observation_mask,
)


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

    def test_options_fixed_library_and_rejection_boundaries(self) -> None:
        """Options EOD is fixed: (1d, eodhd_options) → options-eod-eodhd;
        every other options combination raises instead of synthesizing a
        library name, and unknown asset types raise rather than falling
        back to the enum value."""
        assert (
            library_name(AssetType.OPTIONS, Resolution.ONE_DAY, "eodhd_options")
            == "options-eod-eodhd"
        )
        with pytest.raises(ValueError, match="options EOD uses provider"):
            library_name(AssetType.OPTIONS, Resolution.ONE_HOUR, "eodhd_options")
        with pytest.raises(ValueError, match="options EOD uses provider"):
            library_name(AssetType.OPTIONS, Resolution.ONE_DAY, "cboe")

    def test_unsupported_bar_asset_type_raises(self) -> None:
        """Bar library names are mapped, never silently derived from a raw
        enum value: an unmapped asset type is an explicit error."""

        class _Unmapped:
            value = "mystery"

        with pytest.raises(ValueError, match="unsupported asset type"):
            library_name(_Unmapped(), Resolution.FIVE_MINUTE, "databento")  # type: ignore[arg-type]


class TestAssetType:
    def test_mic_code_equity(self) -> None:
        assert AssetType.EQUITY_US.mic_code == "XNYS"

    def test_mic_code_crypto(self) -> None:
        assert AssetType.CRYPTO.mic_code is None

    def test_mic_code_futures(self) -> None:
        assert AssetType.FUTURES.mic_code == "CME"

    def test_mic_code_index_us(self) -> None:
        """XNYS is an explicit equity-session alignment proxy for index
        levels, not a claim that the index trades on NYSE."""
        assert AssetType.INDEX_US.mic_code == "XNYS"

    def test_index_us_library_name(self) -> None:
        assert (
            library_name(AssetType.INDEX_US, Resolution.ONE_DAY, "cboe")
            == "indices-1d-cboe"
        )

    def test_index_us_daily_grid_on_session_open(self) -> None:
        """Daily index grids reuse the XNYS session-open labels (14:30 UTC)
        and skip non-sessions (2024-01-06/07 is a weekend)."""
        grid = AssetType.INDEX_US.calendar_strategy.generate_grid(
            pd.Timestamp("2024-01-02", tz="UTC"),
            pd.Timestamp("2024-01-08", tz="UTC"),
            Resolution.ONE_DAY,
        )
        assert list(grid) == [
            pd.Timestamp("2024-01-02 14:30:00", tz="UTC"),
            pd.Timestamp("2024-01-03 14:30:00", tz="UTC"),
            pd.Timestamp("2024-01-04 14:30:00", tz="UTC"),
            pd.Timestamp("2024-01-05 14:30:00", tz="UTC"),
            pd.Timestamp("2024-01-08 14:30:00", tz="UTC"),
        ]


class TestObservationMask:
    """The observation/padding distinction: index rows are observed when the
    close is present (volume is legitimately 0); every other context keeps
    the traded-volume convention."""

    @staticmethod
    def _frame() -> pd.DataFrame:
        index = pd.DatetimeIndex(
            ["2024-01-02T14:30:00Z", "2024-01-03T14:30:00Z", "2024-01-04T14:30:00Z"]
        )
        return pd.DataFrame(
            {
                "open": [20.0, 21.0, np.nan],
                "high": [20.0, 21.0, np.nan],
                "low": [20.0, 21.0, np.nan],
                "close": [20.0, 21.0, np.nan],
                "volume": [0.0, 0.0, 0.0],
            },
            index=index,
        )

    def test_index_context_uses_close_presence(self) -> None:
        frame = self._frame()
        mask = observation_mask(frame, asset_type=AssetType.INDEX_US)
        assert mask.tolist() == [True, True, False]

    def test_default_context_uses_positive_volume(self) -> None:
        frame = self._frame()
        assert observation_mask(frame).tolist() == [False, False, False]

    def test_traded_rows_observed_by_volume(self) -> None:
        frame = self._frame()
        frame.iloc[2, frame.columns.get_loc("volume")] = 500.0
        assert observation_mask(frame).tolist() == [False, False, True]

    def test_empty_frame_yields_empty_mask(self) -> None:
        mask = observation_mask(empty_ohlcv(), asset_type=AssetType.INDEX_US)
        assert mask.tolist() == []


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
