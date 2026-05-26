"""Tests for operational gap detection."""

from datetime import datetime, timezone

import pandas as pd

from fin3.schemas import AssetType, Resolution
from fin3.utils.date_utils import _chunk_boundaries, detect_gaps, ensure_utc
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


class TestChunkBoundaries:
    def test_crypto_hourly_chunks(self) -> None:
        start = pd.Timestamp("2024-01-01 00:00", tz="UTC")
        end = pd.Timestamp("2024-01-01 03:00", tz="UTC")
        chunks = _chunk_boundaries(start, end, AssetType.CRYPTO)
        assert len(chunks) == 3
        assert chunks[0] == (pd.Timestamp("2024-01-01 00:00", tz="UTC"), pd.Timestamp("2024-01-01 01:00", tz="UTC"))

    def test_exchange_daily_chunks(self) -> None:
        start = pd.Timestamp("2024-01-02", tz="UTC")
        end = pd.Timestamp("2024-01-03", tz="UTC")
        chunks = _chunk_boundaries(start, end, AssetType.EQUITY_US)
        assert len(chunks) >= 1  # 2024-01-02 is a trading day

    def test_exchange_holiday_no_chunks(self) -> None:
        start = pd.Timestamp("2024-01-01", tz="UTC")
        end = pd.Timestamp("2024-01-01", tz="UTC")
        chunks = _chunk_boundaries(start, end, AssetType.EQUITY_US)
        assert len(chunks) == 0  # New Year's Day


class TestDetectGapsEdgeCases:
    def test_adjacent_chunks_merged(self) -> None:
        """Two consecutive missing hourly chunks merge into one gap."""
        # No data at all in 3-hour window
        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 2, 59, tzinfo=timezone.utc)
        gaps = detect_gaps(None, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) == 1
        assert gaps[0] == (start, end)

    def test_tz_naive_existing_localized(self) -> None:
        """Existing data with tz-naive index gets localized to UTC for comparison."""
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [500]},
            index=pd.DatetimeIndex(["2024-01-01 00:00"]),  # tz-naive
        )
        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 59, tzinfo=timezone.utc)
        gaps = detect_gaps(df, start, end, AssetType.CRYPTO, Resolution.ONE_HOUR)
        assert len(gaps) == 0  # data present at 00:00

    def test_ensure_utc_naive(self) -> None:
        ts = ensure_utc(pd.Timestamp("2024-01-01"))
        assert ts.tz is not None
        assert str(ts.tz) == "UTC"

    def test_ensure_utc_aware(self) -> None:
        ts = ensure_utc(pd.Timestamp("2024-01-01", tz="US/Eastern"))
        assert str(ts.tz) == "UTC"
