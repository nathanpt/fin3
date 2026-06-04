"""Tests for defragmentation utilities."""

from __future__ import annotations

from datetime import datetime, timezone

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


class TestGetSegmentCount:
    def test_returns_zero_for_nonexistent_symbol(self, storage: ArcticStorage) -> None:
        assert storage.get_segment_count("test-lib", "NOEXIST") == 0

    def test_returns_count_after_write(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        count = storage.get_segment_count("test-lib", "AAPL")
        assert count >= 1

    def test_is_symbol_fragmented_returns_false_for_missing_symbol(
        self, storage: ArcticStorage
    ) -> None:
        assert storage.is_symbol_fragmented("test-lib", "NOEXIST") is False

    def test_defragment_symbol_returns_without_error_for_existing_symbol(
        self, storage: ArcticStorage
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)

        storage.defragment_symbol("test-lib", "AAPL", prune_previous_versions=True)


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
        assert result.status in {"ok", "would_defrag"}
        assert result.segments_before >= 1

    def test_specific_symbols(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        storage.write("test-lib", "TSLA", df)
        report = get_fragmentation_info(storage, "test-lib", symbols=["AAPL"])
        assert len(report.results) == 1
        assert report.results[0].symbol == "AAPL"


class TestDefragmentLibrary:
    def test_dry_run_does_not_mutate(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        seg_before = storage.get_segment_count("test-lib", "AAPL")

        report = defragment_library(storage, "test-lib", dry_run=True)
        seg_after = storage.get_segment_count("test-lib", "AAPL")

        assert seg_before == seg_after
        assert report.defragmented_count == 0

    def test_dry_run_reports_would_defrag_for_fragmented_symbol(
        self, storage: ArcticStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=10, freq="1min")
        storage.write("test-lib", "AAPL", df)
        monkeypatch.setattr(
            storage,
            "is_symbol_fragmented",
            lambda library, symbol: True,
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
            lambda library, symbol: True,
        )

        def boom(*args: object, **kwargs: object) -> None:
            raise StorageError("boom")

        monkeypatch.setattr(storage, "defragment_symbol", boom)

        report = defragment_library(storage, "test-lib")

        assert report.failed_count == 1
        assert report.results[0].status == "failed"
        assert "boom" in (report.results[0].error or "")

    def test_defragment_after_many_updates(self, storage: ArcticStorage) -> None:
        df = make_ohlcv("2024-01-02 09:30", periods=500, freq="1min")
        storage.write("test-lib", "AAPL", df)

        # Create many small updates to fragment the symbol
        for i in range(20):
            start_ts = datetime(2024, 1, 2, 9, 30 + i, tzinfo=timezone.utc)
            end_ts = datetime(2024, 1, 2, 9, 30 + i, tzinfo=timezone.utc)
            update_df = make_ohlcv(
                f"2024-01-02 09:{30 + i:02d}", periods=1, freq="1min", base_price=200.0
            )
            storage.update(
                "test-lib",
                "AAPL",
                update_df,
                date_range=(start_ts, end_ts),
            )

        seg_before = storage.get_segment_count("test-lib", "AAPL")
        assert seg_before >= 2  # at least write + updates

        report = defragment_library(storage, "test-lib", prune_previous_versions=True)
        assert len(report.results) == 1
        assert report.elapsed_seconds >= 0
        result = report.results[0]
        assert result.symbol == "AAPL"

        # If it was fragmented, segments should have decreased
        if result.was_fragmented:
            assert result.segments_after <= result.segments_before

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
