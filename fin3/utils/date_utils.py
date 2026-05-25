"""Operational gap detection (chunk-level, no master grid materialisation)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import structlog

from fin3.schemas import AssetType, Resolution

logger = structlog.get_logger(__name__)


def ensure_utc(dt: datetime | pd.Timestamp) -> pd.Timestamp:
    """Normalise a datetime/Timestamp to UTC.

    Localizes naive datetimes to UTC; converts aware ones to UTC.
    """
    ts = pd.Timestamp(dt)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _chunk_boundaries(
    start: pd.Timestamp,
    end: pd.Timestamp,
    asset_type: AssetType,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Return chunk boundaries for gap detection.

    Exchange-based assets: one chunk per trading day.
    Crypto (continuous): one chunk per hour.
    """
    if asset_type == AssetType.CRYPTO:
        chunks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
        current = start.floor("h")
        while current < end:
            chunk_end = min(current + pd.Timedelta(hours=1), end)
            chunks.append((current, chunk_end))
            current = chunk_end
        return chunks
    else:
        import exchange_calendars as ec

        mic = asset_type.mic_code
        if mic is None:
            return []
        cal = ec.get_calendar(mic)
        sessions = cal.sessions_in_range(start, end)
        chunks = []
        for session in sessions:
            s = pd.Timestamp(session)
            chunks.append((s, s + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)))
        return chunks


def detect_gaps(
    existing_df: pd.DataFrame | None,
    start: datetime,
    end: datetime,
    asset_type: AssetType,
    resolution: Resolution,
) -> list[tuple[datetime, datetime]]:
    """Return contiguous gap ranges as [(gap_start, gap_end), ...].

    Gap boundaries are snapped to the nearest valid trading timestamp
    according to the calendar strategy (not raw user-requested boundaries).

    If *existing_df* is None (symbol not found), the entire range is a gap.
    """
    start_ts = ensure_utc(start)
    end_ts = ensure_utc(end)

    if existing_df is None or existing_df.empty:
        return [(start_ts.to_pydatetime(), end_ts.to_pydatetime())]

    chunks = _chunk_boundaries(start_ts, end_ts, asset_type)
    if not chunks:
        return []

    missing_chunks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    existing_index = existing_df.index

    for chunk_start, chunk_end in chunks:
        mask = (existing_index >= chunk_start) & (existing_index <= chunk_end)
        if not mask.any():
            missing_chunks.append((chunk_start, chunk_end))

    if not missing_chunks:
        return []

    gaps: list[tuple[datetime, datetime]] = []
    gap_start, gap_end = missing_chunks[0]
    for cs, ce in missing_chunks[1:]:
        if cs <= gap_end + pd.Timedelta(microseconds=1):
            gap_end = max(gap_end, ce)
        else:
            gaps.append((gap_start.to_pydatetime(), gap_end.to_pydatetime()))
            gap_start, gap_end = cs, ce
    gaps.append((gap_start.to_pydatetime(), gap_end.to_pydatetime()))

    return gaps
