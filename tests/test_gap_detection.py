"""Tests for operational gap detection."""

from datetime import datetime, timezone

import pandas as pd

from fin3.schemas import AssetType, Resolution
from fin3.utils.date_utils import detect_gaps
from tests.conftest import make_ohlcv


class TestDetectGaps:
    def test_full_gap_when_no_existing_data(self) -> None:
        start = datetime(2024, 1, 2, tzinfo=timezone.utc)
        end = datetime(2024, 1, 5, tzinfo=timezone.utc)
        gaps = detect_gaps(None, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) == 1
        assert gaps[0] == (start, end)

    def test_no_gaps_when_full_coverage(self) -> None:
        df = make_ohlcv("2024-01-01", periods=24, freq="1h")
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
        gaps = detect_gaps(df, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) == 0

    def test_partial_gap_crypto(self) -> None:
        df = make_ohlcv("2024-01-01 00:00", periods=12, freq="1h")
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
        gaps = detect_gaps(df, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) >= 1
        assert gaps[0][0].hour >= 12

    def test_empty_df_treated_as_no_data(self) -> None:
        empty = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC"),
        )
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
        gaps = detect_gaps(empty, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) == 1
