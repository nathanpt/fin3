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

    def test_empty_range(self) -> None:
        strategy = ExchangeCalendarStrategy("XNYS")
        start = pd.Timestamp("2024-01-01", tz="UTC")
        end = pd.Timestamp("2024-01-01", tz="UTC")
        grid = strategy.generate_grid(start, end, Resolution.ONE_DAY)
        assert len(grid) == 0
