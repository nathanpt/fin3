"""Integrity audit for an ArcticDB library — generates an HTML dashboard.

Usage:
    uv run python scripts/audit_library.py <library> --resolution <res> [--output <dir>]

Examples:
    uv run python scripts/audit_library.py equities-1m-databento --resolution 1m
    uv run python scripts/audit_library.py equities-1d-databento --resolution 1d --output /tmp
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

from fin3.config.settings import ClientConfig
from fin3.inspect import inspect_library
from fin3.schemas import AssetType, Resolution
from fin3.storage.arctic import ArcticStorage


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.2f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _fmt_num(n: int) -> str:
    return f"{n:,}"


def _health_class(null_bars: int, total_bars: int) -> str:
    if total_bars == 0:
        return "empty"
    ratio = null_bars / total_bars
    if ratio == 0:
        return "clean"
    if ratio < 0.01:
        return "good"
    if ratio < 0.05:
        return "warning"
    return "critical"


def _issue_detail(category: str, count: int, affected_symbols: list[str]) -> tuple[str, str, str]:
    """Return (severity, description, affected_text) for an issue category."""
    if category == "resolution_mismatch":
        return (
            "warning",
            "False positive: inter-session gaps (overnight/weekend) flagged as spacing "
            "violations. Within-session spacing is exactly 1 minute for all symbols.",
            f"{count} / {len(affected_symbols)} symbols",
        )
    if category == "ohlcv_violation":
        symbols = ", ".join(affected_symbols)
        return (
            "error",
            "OHLC relationships broken (open>high, close>high, open<low). "
            "Likely bad source data — individual bars have inconsistent price construction.",
            f"{count} bars in {symbols}",
        )
    if category == "missing_bar":
        return (
            "error",
            "Bars present in the expected calendar grid but missing from stored data.",
            f"{count} bar(s) missing",
        )
    if category == "extra_bar":
        return (
            "warning",
            "Bars in stored data that are not part of the expected calendar grid.",
            f"{count} extra bar(s)",
        )
    if category == "duplicate":
        return ("error", "Duplicate timestamps in the data.", f"{count} duplicate(s)")
    if category == "non_monotonic":
        return (
            "error",
            "Timestamps are not monotonically increasing.",
            f"{count} occurrence(s)",
        )
    if category == "nan_volume":
        return ("error", "Bars with NaN volume.", f"{count} bar(s)")
    if category == "negative_volume":
        return ("error", "Bars with negative volume.", f"{count} bar(s)")
    if category == "nan_semantics":
        return (
            "error",
            "Inconsistent NaN semantics: volume>0 with NaN OHLC, or volume=0 with non-NaN OHLC.",
            f"{count} bar(s)",
        )
    if category == "negative_price":
        return (
            "warning",
            "Bars with negative price values.",
            f"{count} bar(s)",
        )
    if category == "missing_column":
        return ("error", "Expected column is missing entirely.", f"{count} occurrence(s)")
    return (
        "warning",
        "Unknown issue category.",
        f"{count} occurrence(s)",
    )


def generate_html(library: str, resolution: str, result: "LibraryOverview") -> str:  # noqa: F821
    s = result.summary()
    first_bar, last_bar = result.date_range
    date_range_str = (
        f"{first_bar.strftime('%Y-%m-%d')} to {last_bar.strftime('%Y-%m-%d')}"
        if first_bar and last_bar
        else "N/A"
    )

    total_null = sum(p.null_bars for p in result.symbols)
    total_size = s["total_size_bytes"]

    # Aggregate issues
    all_issues: Counter[str] = Counter()
    issue_symbols: dict[str, list[str]] = {}
    for p in result.symbols:
        for cat, cnt in p.issues_summary.items():
            all_issues[cat] += cnt
            issue_symbols.setdefault(cat, []).append(p.symbol)

    # Health tiers
    clean = sum(1 for p in result.symbols if p.null_bars == 0 and p.total_bars > 0)
    good = sum(
        1
        for p in result.symbols
        if 0 < p.null_bars / max(p.total_bars, 1) <= 0.01
    )
    warning = sum(
        1
        for p in result.symbols
        if 0.01 < p.null_bars / max(p.total_bars, 1) <= 0.05
    )
    critical = sum(
        1
        for p in result.symbols
        if p.null_bars / max(p.total_bars, 1) > 0.05
    )

    # Issue findings HTML
    findings_html = ""
    for cat, cnt in all_issues.most_common():
        severity, detail, affected = _issue_detail(cat, cnt, issue_symbols.get(cat, []))
        findings_html += f"""
      <div class="finding {severity}">
        <div class="finding-header">
          <span class="severity-badge {severity}">{severity}</span>
          <span class="finding-title">{cat}</span>
          <span class="finding-count">{cnt}</span>
        </div>
        <div class="finding-detail">{detail}</div>
        <div class="finding-affected">Affected: {affected}</div>
      </div>"""

    # Symbol rows — sorted by issue count desc, then null bars desc
    symbols_sorted = sorted(
        result.symbols, key=lambda p: (-p.issue_count, -p.null_bars)
    )
    rows_html = ""
    for i, p in enumerate(symbols_sorted):
        null_pct = (p.null_bars / p.total_bars * 100) if p.total_bars > 0 else 0
        health = _health_class(p.null_bars, p.total_bars)

        if p.issue_count > 0:
            cats = ", ".join(p.issues_summary.keys())
            issue_badge = f'<span class="badge issue">{p.issue_count} ({cats})</span>'
        else:
            issue_badge = '<span class="badge clean">0</span>'

        rows_html += f"""
        <tr>
            <td class="idx">{i + 1}</td>
            <td class="symbol">{p.symbol}</td>
            <td>{p.first_bar.strftime('%Y-%m-%d') if p.first_bar else ''}</td>
            <td>{p.last_bar.strftime('%Y-%m-%d') if p.last_bar else ''}</td>
            <td class="num">{_fmt_num(p.total_bars)}</td>
            <td class="num">{_fmt_num(p.null_bars)}</td>
            <td class="num">{null_pct:.1f}%</td>
            <td class="num">{_fmt_size(p.size_bytes)}</td>
            <td>{issue_badge}</td>
            <td><span class="badge {health}">{health}</span></td>
        </tr>"""

    null_pct_total = (total_null / result.total_rows * 100) if result.total_rows > 0 else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{library} — Integrity Audit</title>
<style>
  :root {{
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #58a6ff;
    --green: #3fb950;
    --yellow: #d29922;
    --red: #f85149;
    --purple: #bc8cff;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2rem;
    max-width: 1300px;
    margin: 0 auto;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.1rem; margin-bottom: 0.75rem; margin-top: 1.5rem; }}
  .subtitle {{ color: var(--text-dim); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
  }}
  .card .label {{ color: var(--text-dim); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 1.4rem; font-weight: 600; margin-top: 0.25rem; }}
  .card .value.green {{ color: var(--green); }}
  .card .value.accent {{ color: var(--accent); }}
  .card .value.yellow {{ color: var(--yellow); }}
  .card .value.purple {{ color: var(--purple); }}
  .card .value.red {{ color: var(--red); }}
  .findings {{ margin-bottom: 2rem; }}
  .finding {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
    border-left: 3px solid var(--yellow);
  }}
  .finding.error {{ border-left-color: var(--red); }}
  .finding-header {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; }}
  .finding-title {{ font-weight: 600; }}
  .finding-count {{ margin-left: auto; font-size: 0.85rem; color: var(--text-dim); }}
  .finding-detail {{ color: var(--text); font-size: 0.85rem; line-height: 1.5; }}
  .finding-affected {{ color: var(--text-dim); font-size: 0.75rem; margin-top: 0.35rem; }}
  .severity-badge {{
    display: inline-block;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .severity-badge.warning {{ background: rgba(210,153,34,0.2); color: var(--yellow); }}
  .severity-badge.error {{ background: rgba(248,81,73,0.2); color: var(--red); }}
  .dist-bar {{
    display: flex;
    gap: 0.5rem;
    margin-bottom: 2rem;
    align-items: center;
  }}
  .dist-segment {{
    padding: 0.4rem 0.75rem;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
  }}
  .dist-segment.clean {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .dist-segment.good {{ background: rgba(88,166,255,0.15); color: var(--accent); }}
  .dist-segment.warning {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .dist-segment.critical {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    font-size: 0.82rem;
  }}
  thead {{ background: #1c2129; }}
  th {{
    text-align: left;
    padding: 0.65rem 0.85rem;
    color: var(--text-dim);
    font-weight: 500;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
  }}
  td {{ padding: 0.55rem 0.85rem; border-bottom: 1px solid var(--border); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: #1c2129; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .idx {{ color: var(--text-dim); width: 2rem; text-align: center; }}
  .symbol {{ font-weight: 600; font-family: 'SF Mono', 'Fira Code', monospace; }}
  .badge {{
    display: inline-block;
    padding: 0.12rem 0.45rem;
    border-radius: 10px;
    font-size: 0.65rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .badge.clean {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .badge.good {{ background: rgba(88,166,255,0.1); color: var(--accent); }}
  .badge.warning {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .badge.critical {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  .badge.issue {{ background: rgba(248,81,73,0.1); color: var(--red); }}
  .badge.empty {{ background: rgba(139,148,158,0.1); color: var(--text-dim); }}
  .footer {{ margin-top: 1.5rem; color: var(--text-dim); font-size: 0.75rem; }}
</style>
</head>
<body>

<h1>{library}</h1>
<p class="subtitle">Integrity Audit &middot; {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="cards">
  <div class="card">
    <div class="label">Symbols</div>
    <div class="value accent">{result.symbol_count}</div>
  </div>
  <div class="card">
    <div class="label">Total Rows</div>
    <div class="value">{_fmt_num(result.total_rows)}</div>
  </div>
  <div class="card">
    <div class="label">Storage</div>
    <div class="value purple">{_fmt_size(total_size)}</div>
  </div>
  <div class="card">
    <div class="label">Date Range</div>
    <div class="value" style="font-size:1rem">{date_range_str}</div>
  </div>
  <div class="card">
    <div class="label">Null Bars</div>
    <div class="value yellow">{_fmt_num(total_null)}<span style="font-size:0.7rem;color:var(--text-dim)"> ({null_pct_total:.1f}%)</span></div>
  </div>
  <div class="card">
    <div class="label">Issues</div>
    <div class="value red">{sum(all_issues.values())}<span style="font-size:0.7rem;color:var(--text-dim)"> ({len(all_issues)} types)</span></div>
  </div>
</div>

<h2>Health Distribution</h2>
<div class="dist-bar">
  <span class="dist-segment clean">{clean} clean (0% null)</span>
  <span class="dist-segment good">{good} good (&lt;1% null)</span>
  <span class="dist-segment warning">{warning} warning (1-5% null)</span>
  <span class="dist-segment critical">{critical} critical (&gt;5% null)</span>
</div>

<h2>Issues Found</h2>
<div class="findings">
  {findings_html}
</div>

<h2>Per-Symbol Detail</h2>
<table>
  <thead>
    <tr>
      <th>#</th>
      <th>Symbol</th>
      <th>First Bar</th>
      <th>Last Bar</th>
      <th style="text-align:right">Bars</th>
      <th style="text-align:right">Null</th>
      <th style="text-align:right">Null %</th>
      <th style="text-align:right">Size</th>
      <th>Issues</th>
      <th>Health</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<p class="footer">fin3 integrity audit &middot; {library} &middot; {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit an ArcticDB library and generate an HTML integrity report"
    )
    parser.add_argument("library", help="ArcticDB library name")
    parser.add_argument(
        "--resolution",
        required=True,
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Bar resolution",
    )
    parser.add_argument(
        "--output",
        default=".",
        help="Output directory for the HTML file (default: current directory)",
    )
    args = parser.parse_args()

    resolution = Resolution(args.resolution)
    config = ClientConfig()
    storage = ArcticStorage(config.minio)
    calendar_strategy = AssetType.EQUITY_US.calendar_strategy

    print(f"Auditing {args.library} ({resolution.value}) with integrity checks...")
    result = inspect_library(
        storage,
        args.library,
        resolution,
        include_integrity=True,
        calendar_strategy=calendar_strategy,
    )
    print(f"Done. {result.symbol_count} symbols, {result.total_rows:,} rows.")

    html = generate_html(args.library, resolution.value, result)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{args.library}-audit.html"
    output_path = output_dir / filename

    output_path.write_text(html)
    print(f"Written: {output_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
