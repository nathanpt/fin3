"""Tests for calendar grid generation."""

import pandas as pd

from fin3.calendar.exchange import ContinuousCalendarStrategy, ExchangeCalendarStrategy
from fin3.schemas import Resolution


class TestContinuousCalendarStrategy:
    def test_generates_continuous_grid(self) -> None:
        strategy = ContinuousCalendarStrategy()
        start = pd.Timestamp("2024-01-01", tz="UTC")
        end = pd.Timestamp("2024-01-01 23:59", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_HOUR)
        assert len(grid) == 24
        assert grid.tz is not None

    def test_one_minute_grid(self) -> None:
        strategy = ContinuousCalendarStrategy()
        start = pd.Timestamp("2024-01-01", tz="UTC")
        end = pd.Timestamp("2024-01-01 00:59", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 60


class TestExchangeCalendarStrategy:
    def test_generates_trading_day_grid(self) -> None:
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-02", tz="UTC")
        end = pd.Timestamp("2024-01-02", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 391  # 14:30-21:00 UTC inclusive = 391 bars

    def test_holiday_excluded(self) -> None:
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-01", tz="UTC")
        end = pd.Timestamp("2024-01-01", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 0

    def test_end_before_start_returns_empty_grid(self) -> None:
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-03", tz="UTC")
        end = pd.Timestamp("2024-01-02", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert isinstance(grid, pd.DatetimeIndex)
        assert grid.tz is not None
        assert len(grid) == 0

    def test_early_close_half_day(self) -> None:
        # 2024-07-03 is a July-3rd half day: NYSE closes 13:00 ET (17:00 UTC).
        # One-minute grid spans 13:30-17:00 UTC inclusive = 211 bars.
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-07-03", tz="UTC")
        end = pd.Timestamp("2024-07-03", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 211
        assert grid[0] == pd.Timestamp("2024-07-03 13:30", tz="UTC")
        assert grid[-1] == pd.Timestamp("2024-07-03 17:00", tz="UTC")

    def test_intraday_trim_to_requested_window(self) -> None:
        # start/end carrying intraday times trim the session grid to the
        # window: 15:00-17:00 UTC inside the 14:30-21:00 session.
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-02 15:00", tz="UTC")
        end = pd.Timestamp("2024-01-02 17:00", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 121
        assert grid[0] == start
        assert grid[-1] == end
        assert (grid >= start).all()
        assert (grid <= end).all()

    def test_hourly_grid_has_no_overhang_past_close(self) -> None:
        # A 6.5h session on the 1h grid yields 7 stamps (14:30..20:30 UTC);
        # nothing at or past the 21:00 close. NOTE: the test audit claimed
        # 8 bars "with the 21:30 bar dropped", but the real count is 7 --
        # pd.date_range never stamps past `close`, so no overhang bar is
        # ever generated. Verified against exchange_calendars 4.13.2.
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-02", tz="UTC")
        end = pd.Timestamp("2024-01-02", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_HOUR)
        assert len(grid) == 7
        assert grid[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert grid[-1] == pd.Timestamp("2024-01-02 20:30", tz="UTC")
        assert (grid <= pd.Timestamp("2024-01-02 21:00", tz="UTC")).all()

    def test_weekend_span_skips_non_sessions(self) -> None:
        # Friday -> Monday spans two full sessions (2 x 391 bars) with no
        # Saturday or Sunday stamps between them.
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-05", tz="UTC")
        end = pd.Timestamp("2024-01-08", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_MINUTE)
        assert len(grid) == 782
        assert set(grid.dayofweek) == {0, 4}  # Monday and Friday only
        assert grid[0] == pd.Timestamp("2024-01-05 14:30", tz="UTC")
        assert grid[-1] == pd.Timestamp("2024-01-08 21:00", tz="UTC")

    def test_dst_shifts_first_stamp_per_session(self) -> None:
        # NYSE opens 09:30 local time, so the UTC first stamp differs across
        # the DST boundary: 14:30Z in January (EST) vs 13:30Z in July (EDT).
        strategy = ExchangeCalendarStrategy("XNYS")
        jan = strategy.generate_grid(
            pd.Timestamp("2024-01-02", tz="UTC"),
            pd.Timestamp("2024-01-02", tz="UTC"),
            Resolution.ONE_MINUTE,
        )
        jul = strategy.generate_grid(
            pd.Timestamp("2024-07-03", tz="UTC"),
            pd.Timestamp("2024-07-03", tz="UTC"),
            Resolution.ONE_MINUTE,
        )
        assert jan[0] == pd.Timestamp("2024-01-02 14:30", tz="UTC")
        assert jul[0] == pd.Timestamp("2024-07-03 13:30", tz="UTC")
        assert jul[0] != jan[0]
