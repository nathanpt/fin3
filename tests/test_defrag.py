"""Tests for defragmentation utilities."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from fin3.exceptions import StorageError
from fin3.storage.arctic import ArcticStorage
from fin3.storage.defrag import (
    DefragReport,
    SymbolDefragResult,
    defragment_library,
    get_fragmentation_info,
)
from scripts.defragment_library import format_report
from tests.conftest import make_ohlcv


def fragment_symbol_with_updates(
    storage: ArcticStorage,
    library: str,
    symbol: str,
    *,
    first_minute_of_day: int,
    count: int = 100,
) -> None:
    """Push a symbol to arcticdb's fragmentation threshold with small updates.

    Performs *count* single-row 1-min updates, each on its own timestamp
    starting at minute-of-day *first_minute_of_day* (crossing midnight via the
    day offset). Every update lands past the existing data and creates its own
    segment; 100 updates on top of any single-write symbol crosses arcticdb's
    SymbolDataCompact.SegmentCount threshold, so is_symbol_fragmented reports
    True deterministically (Wave 1 proved >=101 segments are needed).
    """
    for i in range(count):
        minute_of_day = first_minute_of_day + i
        start = datetime(
            2024, 1, 2 + minute_of_day // 1440,
            (minute_of_day % 1440) // 60, minute_of_day % 60,
            tzinfo=timezone.utc,
        )
        update_df = make_ohlcv(
            start.strftime("%Y-%m-%d %H:%M"),
            periods=1,
            freq="1min",
            base_price=200.0,
        )
        storage.update(library, symbol, update_df, date_range=(start, start))


class TestGetSegmentCount:
    def test_returns_zero_for_nonexistent_symbol(self, storage: ArcticStorage) -> None:
        assert storage.get_segment_count("test-lib", "NOEXIST") == 0

    def test_returns_count_after_write(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        # A single write (prune_previous_versions=True) yields exactly one
        # TABLE_DATA segment: deterministic, not just a nonzero floor.
        assert storage.get_segment_count("test-lib", "AAPL") == 1

    def test_is_symbol_fragmented_returns_false_for_missing_symbol(
        self, storage: ArcticStorage
    ) -> None:
        assert storage.is_symbol_fragmented("test-lib", "NOEXIST") is False

    def test_defragment_symbol_compacts_fragmented_symbol(
        self, storage: ArcticStorage
    ) -> None:
        """A genuinely fragmented symbol (>= arcticdb's 100-segment compaction
        threshold) shrinks to a single segment after defragment_symbol."""
        storage.write(
            "test-lib", "AAPL", make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        )

        # 100 one-row updates on top of the initial write push the symbol to
        # 101 segments, reaching arcticdb's SymbolDataCompact.SegmentCount
        # threshold: is_symbol_fragmented only reports True when compaction
        # would reduce the segment count by >= 100.
        fragment_symbol_with_updates(
            storage, "test-lib", "AAPL", first_minute_of_day=9 * 60 + 40
        )

        segments_before = storage.get_segment_count("test-lib", "AAPL")
        assert segments_before >= 101
        assert storage.is_symbol_fragmented("test-lib", "AAPL") is True

        storage.defragment_symbol("test-lib", "AAPL", prune_previous_versions=True)

        segments_after = storage.get_segment_count("test-lib", "AAPL")
        assert segments_after < segments_before

        # Compaction must not lose or duplicate rows (10 + 100 single-row updates).
        result = storage.read("test-lib", "AAPL")
        assert result is not None
        assert len(result) == 10 + 100


class TestGetFragmentationInfo:
    def test_empty_library(self, storage: ArcticStorage) -> None:
        report = get_fragmentation_info(storage, "empty-lib")
        assert report.library == "empty-lib"
        assert report.results == []
        assert report.defragmented_count == 0

    def test_non_fragmented_symbol(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=100, freq="1min")
        storage.write("test-lib", "AAPL", df)
        report = get_fragmentation_info(storage, "test-lib", symbols=["AAPL"])
        assert len(report.results) == 1
        result = report.results[0]
        assert result.symbol == "AAPL"
        # get_fragmentation_info is always a dry run, so a healthy symbol is
        # exactly "ok" — never "would_defrag".
        assert result.status == "ok"
        assert result.was_fragmented is False
        assert result.segments_before == result.segments_after >= 1

    def test_specific_symbols(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "TSLA", df)
        report = get_fragmentation_info(storage, "test-lib", symbols=["AAPL"])
        assert len(report.results) == 1
        assert report.results[0].symbol == "AAPL"


class TestDefragmentLibrary:
    def test_dry_run_does_not_mutate(self, storage: ArcticStorage) -> None:
        """A dry run over a genuinely fragmented symbol leaves both the segment
        layout and the stored values untouched."""
        storage.write(
            "test-lib", "AAPL", make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        )
        fragment_symbol_with_updates(
            storage, "test-lib", "AAPL", first_minute_of_day=9 * 60 + 40
        )

        before = storage.read("test-lib", "AAPL")
        assert before is not None
        assert storage.is_symbol_fragmented("test-lib", "AAPL") is True
        seg_before = storage.get_segment_count("test-lib", "AAPL")

        report = defragment_library(storage, "test-lib", dry_run=True)

        assert storage.get_segment_count("test-lib", "AAPL") == seg_before
        after = storage.read("test-lib", "AAPL")
        assert after is not None
        assert len(after) == 110
        pd.testing.assert_frame_equal(after, before, check_exact=True, check_freq=False)

        assert report.defragmented_count == 0
        assert report.skipped_count == 0
        assert report.would_defrag_count == 1

    def test_dry_run_reports_would_defrag_for_fragmented_symbol(
        self, storage: ArcticStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        monkeypatch.setattr(
            storage,
            "is_symbol_fragmented",
            lambda *args, **kwargs: True,
        )

        called = False

        def fake_defrag(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(storage, "defragment_symbol", fake_defrag)

        report = defragment_library(storage, "test-lib", dry_run=True)

        assert called is False
        assert report.would_defrag_count == 1
        assert report.results[0].status == "would_defrag"

    def test_defrag_failure_is_reported(
        self, storage: ArcticStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        monkeypatch.setattr(
            storage,
            "is_symbol_fragmented",
            lambda *args, **kwargs: True,
        )

        def boom(*args: object, **kwargs: object) -> None:
            raise StorageError("boom")

        monkeypatch.setattr(storage, "defragment_symbol", boom)

        report = defragment_library(storage, "test-lib")

        assert report.failed_count == 1
        assert report.results[0].status == "failed"
        assert "boom" in (report.results[0].error or "")

    def test_failure_isolated_to_raising_symbol(
        self, storage: ArcticStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADD-44: one symbol's compaction raising must not stop the pass.

        defragment_library catches per symbol; the symbols after the failure
        still go through the mutating path and are reported as defragmented.
        """
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "TSLA", df)

        # Force every symbol down the mutating path, but AAPL's compaction
        # raises. TSLA's compaction is a recorded no-op: arcticdb refuses to
        # compact a symbol with nothing to compact, and the mechanism under
        # test here is defragment_library's per-symbol isolation, not arcticdb.
        monkeypatch.setattr(
            storage,
            "is_symbol_fragmented",
            lambda *args, **kwargs: True,
        )
        compacted: list[str] = []

        def flaky(library: str, symbol: str, **kwargs: object) -> None:
            if symbol == "AAPL":
                raise StorageError("boom-aapl")
            compacted.append(symbol)

        monkeypatch.setattr(storage, "defragment_symbol", flaky)

        report = defragment_library(storage, "test-lib", symbols=["AAPL", "TSLA"])

        # The loop continued past AAPL's failure and processed TSLA.
        assert compacted == ["TSLA"]
        assert report.failed_count == 1
        assert report.defragmented_count == 1
        aapl, tsla = report.results
        assert (aapl.symbol, aapl.status) == ("AAPL", "failed")
        assert "boom-aapl" in (aapl.error or "")
        assert (tsla.symbol, tsla.status) == ("TSLA", "defragmented")
        assert tsla.error is None

    def test_defragment_after_many_updates(self, storage: ArcticStorage) -> None:
        """A 500-row write plus 100 single-row appends is guaranteed fragmented
        (101 segments > arcticdb's threshold), so the assertions below are
        unconditional — no `if was_fragmented` escape hatch."""
        storage.write(
            "test-lib", "AAPL", make_ohlcv("2024-01-02 09:30", periods=500, freq="1min")
        )
        # Appends start at the first minute past the written frame
        # (09:30 + 500 minutes), so each update adds one segment.
        fragment_symbol_with_updates(
            storage, "test-lib", "AAPL", first_minute_of_day=9 * 60 + 30 + 500
        )

        segments_before = storage.get_segment_count("test-lib", "AAPL")
        assert segments_before >= 101
        assert storage.is_symbol_fragmented("test-lib", "AAPL") is True

        report = defragment_library(storage, "test-lib", prune_previous_versions=True)
        assert len(report.results) == 1
        assert report.elapsed_seconds >= 0
        assert report.defragmented_count == 1
        result = report.results[0]
        assert result.symbol == "AAPL"
        assert result.was_fragmented is True
        assert result.status == "defragmented"
        assert result.segments_after < result.segments_before

        # Compaction preserves every row (500 written + 100 appended).
        after = storage.read("test-lib", "AAPL")
        assert after is not None
        assert len(after) == 600

    def test_empty_library(self, storage: ArcticStorage) -> None:
        report = defragment_library(storage, "empty-lib")
        assert report.results == []
        assert report.defragmented_count == 0
        assert report.skipped_count == 0

    def test_specific_symbols_filter(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "TSLA", df)

        report = defragment_library(storage, "test-lib", symbols=["AAPL"])
        assert len(report.results) == 1
        assert report.results[0].symbol == "AAPL"

    def test_skipped_count(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=100, freq="1min")
        storage.write("test-lib", "AAPL", df)
        report = defragment_library(storage, "test-lib")
        # A single write is unlikely to be fragmented
        assert report.skipped_count == 1


class TestFormatReport:
    def test_format_report_includes_status_counts(self) -> None:
        report = DefragReport(
            library="test-lib",
            results=[
                SymbolDefragResult(
                    symbol="AAPL",
                    status="would_defrag",
                    was_fragmented=True,
                    segments_before=3,
                    segments_after=3,
                ),
                SymbolDefragResult(
                    symbol="TSLA",
                    status="failed",
                    was_fragmented=True,
                    segments_before=4,
                    segments_after=4,
                    error="boom",
                ),
            ],
            elapsed_seconds=0.1,
        )

        output = format_report(report)

        assert "Would defrag: 1" in output
        assert "Failed:       1" in output
        assert "status=would_defrag" in output
        assert "error=boom" in output
