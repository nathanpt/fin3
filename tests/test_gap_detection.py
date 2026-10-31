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
        # Bars cover 00:00-11:00 (the 11:00 bar covers the [11:00,12:00]
        # chunk via the chunk-end-inclusive mask); everything from 12:00
        # through the 23:59 request end is exactly one gap.
        assert gaps == [
            (
                datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc),
            )
        ]

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
        # 2024-01-02 (Tue) and 2024-01-03 (Wed) are both XNYS sessions:
        # one chunk per session, midnight to just before the next midnight.
        assert len(chunks) == 2
        assert chunks[0] == (
            pd.Timestamp("2024-01-02 00:00", tz="UTC"),
            pd.Timestamp("2024-01-02 23:59:59.999999", tz="UTC"),
        )
        assert chunks[1] == (
            pd.Timestamp("2024-01-03 00:00", tz="UTC"),
            pd.Timestamp("2024-01-03 23:59:59.999999", tz="UTC"),
        )

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
        assert ts == pd.Timestamp("2024-01-01 00:00", tz="UTC")

    def test_ensure_utc_aware(self) -> None:
        # 2024-01-01 is EST (UTC-5): the instant must be preserved exactly.
        ts = ensure_utc(pd.Timestamp("2024-01-01", tz="US/Eastern"))
        assert ts.tz is not None
        assert str(ts.tz) == "UTC"
        assert ts == pd.Timestamp("2024-01-01 05:00", tz="UTC")
