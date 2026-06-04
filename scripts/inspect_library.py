"""Inspect an ArcticDB library and generate an HTML data profile.

Usage:
    uv run python scripts/inspect_library.py <library> --resolution <res> --output <dir>

Examples:
    uv run python scripts/inspect_library.py equities-1d-databento --resolution 1d --output /home/nathan/dev/projects/fin3
    uv run python scripts/inspect_library.py equities-1m-databento --resolution 1m --output /tmp
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from fin3.config.settings import ClientConfig
from fin3.inspect import inspect_library
from fin3.schemas import Resolution
from fin3.storage.arctic import ArcticStorage


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _fmt_number(n: int) -> str:
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


def generate_html(result: "LibraryOverview", library: str, resolution: str) -> str:  # noqa: F821
    s = result.summary()
    df = result.to_dataframe()
    has_data = df[df["total_bars"] > 0].sort_values("total_bars", ascending=False)
    no_data = df[df["total_bars"] == 0]

    first_bar = result.date_range[0]
    last_bar = result.date_range[1]
    date_range_str = (
        f"{first_bar.strftime('%Y-%m-%d')} to {last_bar.strftime('%Y-%m-%d')}"
        if first_bar and last_bar
        else "N/A"
    )

    # Null bar stats
    total_null = int(has_data["null_bars"].sum()) if not has_data.empty else 0
    symbols_with_nulls = len(has_data[has_data["null_bars"] > 0]) if not has_data.empty else 0

    # Build symbol rows
    rows_html = ""
    for i, (_, row) in enumerate(has_data.iterrows()):
        health = _health_class(row["null_bars"], row["total_bars"])
        null_pct = (row["null_bars"] / row["total_bars"] * 100) if row["total_bars"] > 0 else 0
        rows_html += f"""
        <tr class="{health}">
            <td class="idx">{i + 1}</td>
            <td class="symbol">{row['symbol']}</td>
            <td>{row['first_bar'].strftime('%Y-%m-%d') if row['first_bar'] else ''}</td>
            <td>{row['last_bar'].strftime('%Y-%m-%d') if row['last_bar'] else ''}</td>
            <td class="num">{_fmt_number(row['total_bars'])}</td>
            <td class="num">{_fmt_number(row['null_bars'])}</td>
            <td class="num">{null_pct:.1f}%</td>
            <td class="num">{_fmt_size(row['size_bytes'])}</td>
            <td><span class="badge {health}">{health}</span></td>
        </tr>"""

    empty_rows_html = ""
    for i, (_, row) in enumerate(no_data.iterrows()):
        empty_rows_html += f"""
        <tr class="empty">
            <td class="idx">{len(has_data) + i + 1}</td>
            <td class="symbol">{row['symbol']}</td>
            <td colspan="7" class="empty-msg">no data</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{library} — Data Profile</title>
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
    max-width: 1200px;
    margin: 0 auto;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: var(--text-dim); font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
  }}
  .card .label {{ color: var(--text-dim); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 1.5rem; font-weight: 600; margin-top: 0.25rem; }}
  .card .value.green {{ color: var(--green); }}
  .card .value.accent {{ color: var(--accent); }}
  .card .value.yellow {{ color: var(--yellow); }}
  .card .value.purple {{ color: var(--purple); }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    font-size: 0.85rem;
  }}
  thead {{ background: #1c2129; }}
  th {{
    text-align: left;
    padding: 0.75rem 1rem;
    color: var(--text-dim);
    font-weight: 500;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
  }}
  td {{ padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: #1c2129; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .idx {{ color: var(--text-dim); width: 2rem; text-align: center; }}
  .symbol {{ font-weight: 600; font-family: 'SF Mono', 'Fira Code', monospace; }}
  .empty-msg {{ color: var(--text-dim); font-style: italic; text-align: center; }}
  tr.empty {{ opacity: 0.4; }}
  .badge {{
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 12px;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .badge.clean {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .badge.good {{ background: rgba(63,185,80,0.1); color: var(--green); }}
  .badge.warning {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .badge.critical {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  .badge.empty {{ background: rgba(139,148,158,0.1); color: var(--text-dim); }}
  .footer {{
    margin-top: 1.5rem;
    color: var(--text-dim);
    font-size: 0.75rem;
  }}
</style>
</head>
<body>

<h1>{library}</h1>
<p class="subtitle">Resolution: {resolution} &middot; Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="cards">
  <div class="card">
    <div class="label">Symbols</div>
    <div class="value accent">{len(has_data)}<span style="font-size:0.85rem;color:var(--text-dim)"> / {result.symbol_count}</span></div>
  </div>
  <div class="card">
    <div class="label">Total Rows</div>
    <div class="value">{_fmt_number(result.total_rows)}</div>
  </div>
  <div class="card">
    <div class="label">Storage Size</div>
    <div class="value purple">{_fmt_size(s['total_size_bytes'])}</div>
  </div>
  <div class="card">
    <div class="label">Date Range</div>
    <div class="value" style="font-size:1rem">{date_range_str}</div>
  </div>
  <div class="card">
    <div class="label">Null Bars</div>
    <div class="value {"yellow" if total_null > 0 else "green"}">{_fmt_number(total_null)}<span style="font-size:0.75rem;color:var(--text-dim)"> ({symbols_with_nulls} symbols)</span></div>
  </div>
</div>

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
      <th>Health</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}{empty_rows_html}
  </tbody>
</table>

<p class="footer">fin3 inspect &middot; {library}</p>

</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect an ArcticDB library and generate HTML report")
    parser.add_argument("library", help="ArcticDB library name")
    parser.add_argument(
        "--resolution", default="1d",
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Bar resolution (default: 1d)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Output directory for the HTML file",
    )
    args = parser.parse_args()

    resolution = Resolution(args.resolution)
    config = ClientConfig()
    storage = ArcticStorage(config.minio)

    print(f"Inspecting {args.library} ({resolution.value})...")
    result = inspect_library(storage, args.library, resolution)

    html = generate_html(result, args.library, resolution.value)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{args.library}.html"
    output_path = output_dir / filename

    output_path.write_text(html)
    print(f"Written: {output_path}")


if __name__ == "__main__":
    main()
