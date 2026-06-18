"""Rich panel rendering for the live monitor and final summary."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fin3.monitoring.collector import SampledMetrics


def _fmt_bytes(n: float) -> str:
    """Format a byte count as a human-readable string."""
    sign = "+" if n >= 0 else "-"
    value = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{sign}{value:.1f} {unit}"
        value /= 1024
    return f"{sign}{value:.1f} PB"


def _fmt_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def render_live_panel(
    metrics: SampledMetrics,
    elapsed: float,
    phase: str = "",
    symbols: list[str] | None = None,
    resolution: str = "",
) -> Panel:
    """Render a live-updating resource panel for the tmux / inline display.

    Parameters
    ----------
    metrics : SampledMetrics
        Current sampled metrics.
    elapsed : float
        Seconds since operation start.
    phase : str
        Human-readable current phase label (e.g. ``"fetching AAPL..."``).
    symbols : list[str] or None
        Symbols being processed.
    resolution : str
        Bar resolution.
    """
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")

    sym_str = ", ".join(symbols) if symbols else "—"
    table.add_row("Symbols", sym_str)
    if resolution:
        table.add_row("Resolution", resolution)
    table.add_row("Duration", _fmt_duration(elapsed))
    table.add_row("Memory", _fmt_bytes(metrics.rss_delta_bytes) + " peak")
    table.add_row("Disk", _fmt_bytes(metrics.disk_delta_bytes))
    table.add_row("Net", f"{_fmt_bytes(metrics.network_bytes)} ({metrics.fetch_count} fetches)")

    if phase:
        table.add_row("Phase", Text(phase, style="yellow"))

    return Panel(
        table,
        title="[bold]fin3[/bold] monitor",
        border_style="blue",
        expand=True,
    )


def render_summary(
    metrics: SampledMetrics,
    elapsed: float,
    symbols: list[str] | None = None,
    resolution: str = "",
    rows: int = 0,
    library: str = "",
) -> Panel:
    """Render the final summary panel printed after operation completion.

    Parameters
    ----------
    metrics : SampledMetrics
        Final sampled metrics.
    elapsed : float
        Total operation duration in seconds.
    symbols : list[str] or None
        Symbols that were processed.
    resolution : str
        Bar resolution.
    rows : int
        Total rows in the returned DataFrame.
    library : str
        Library name for disk context.
    """
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="white")

    sym_str = ", ".join(symbols) if symbols else "—"
    table.add_row("Symbols", sym_str)
    if resolution:
        table.add_row("Resolution", resolution)
    table.add_row("Duration", _fmt_duration(elapsed))

    if rows > 0:
        table.add_row("Rows", f"{rows:,}")

    disk_detail = _fmt_bytes(metrics.disk_delta_bytes)
    if library:
        disk_detail += f"  ({_fmt_bytes(metrics.library_total_bytes)} total in {library})"
    table.add_row("Disk", disk_detail)
    table.add_row("Memory", _fmt_bytes(metrics.rss_delta_bytes) + " peak RSS")
    table.add_row("Net", f"{_fmt_bytes(metrics.network_bytes)} downloaded ({metrics.fetch_count} fetches)")

    return Panel(
        table,
        title="[bold]fin3 resource summary[/bold]",
        border_style="green",
        expand=True,
    )
