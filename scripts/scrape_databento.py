#!/usr/bin/env python3
"""
Databento Docs Scraper

Scrapes the Databento documentation and converts HTML pages to Markdown.

The Databento docs are a client-side rendered SPA. This script uses a
Googlebot user-agent header to trigger server-side pre-rendering, which
returns full HTML content (~1.35MB per page) instead of the JS shell (~15KB).

Key insight: some sections (like the Historical API reference) render as a
single monolithic page containing all sub-sections. The scraper detects this
and extracts only the relevant section from the h2/h3 heading structure.

Usage:
    python scrape_databento.py              # curated subset (default)
    python scrape_databento.py --all        # all doc pages
"""

import argparse
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import html2text
import requests
from bs4 import BeautifulSoup, Tag

# Configuration
BASE_URL = "https://databento.com/docs/"
OUTPUT_FOLDER = "docs/databento"

# Googlebot user-agent triggers pre-rendering on the Databento SPA
# (1.35MB prerendered HTML vs 15KB JS shell with default user-agent).
CRAWLER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
}

# Regex matching the decorative link-icon images embedded in headings
_LINK_ICON_RE = re.compile(r"!\[.*?\]\(/docs/assets/images/link-icon\.\w+\.svg\)")

# Monolithic page prefixes — these URLs all return the same giant page.
# The scraper downloads them once and extracts individual sections.
_MONOLITHIC_PREFIXES = [
    "api-reference-historical",
]

# Mapping from URL slug (last segment) to the heading text used in the
# monolithic page's h2/h3 section headers.
_SLUG_TO_HEADING: dict[str, str] = {
    # Basics
    "overview": "Overview",
    "authentication": "Authentication",
    "schemas-and-conventions": "Schemas and conventions",
    "datasets": "Datasets",
    "symbology": "Symbology",
    "encodings": "Encodings",
    "compression": "Compression",
    "dates-and-times": "Dates and times",
    "errors": "Errors",
    "rate-limits": "Rate limits",
    "size-limits": "Size limits",
    "metered-pricing": "Metered pricing",
    "versioning": "Versioning",
    # Client
    "historical": "Historical",
    # Metadata
    "metadata-list-publishers": "Historical.metadata.list_publishers",
    "metadata-list-datasets": "Historical.metadata.list_datasets",
    "metadata-list-schemas": "Historical.metadata.list_schemas",
    "metadata-list-fields": "Historical.metadata.list_fields",
    "metadata-list-unit-prices": "Historical.metadata.list_unit_prices",
    "metadata-get-dataset-condition": "Historical.metadata.get_dataset_condition",
    "metadata-get-dataset-range": "Historical.metadata.get_dataset_range",
    "metadata-get-record-count": "Historical.metadata.get_record_count",
    "metadata-get-billable-size": "Historical.metadata.get_billable_size",
    "metadata-get-cost": "Historical.metadata.get_cost",
    # Timeseries
    "timeseries-get-range": "Historical.timeseries.get_range",
    "timeseries-get-range-async": "Historical.timeseries.get_range_async",
    # Symbology
    "symbology-resolve": "Historical.symbology.resolve",
    # Batch
    "batch-submit-job": "Historical.batch.submit_job",
    "batch-list-jobs": "Historical.batch.list_jobs",
    "batch-list-files": "Historical.batch.list_files",
    "batch-download": "Historical.batch.download",
    "batch-download-async": "Historical.batch.download_async",
    # Helpers
    "dbn-store": "DBNStore",
    "dbn-store-from-bytes": "DBNStore.from_bytes",
    "dbn-store-from-file": "DBNStore.from_file",
    "dbn-store-reader": "DBNStore.reader",
    "dbn-store-replay": "DBNStore.replay",
    "request-full-definitions": "DBNStore.request_full_definitions",
    "request-symbology": "DBNStore.request_symbology",
    "dbn-store-to-csv": "DBNStore.to_csv",
    "dbn-store-to-df": "DBNStore.to_df",
    "dbn-store-to-file": "DBNStore.to_file",
    "dbn-store-to-json": "DBNStore.to_json",
    "dbn-store-to-ndarray": "DBNStore.to_ndarray",
    "dbn-store-to-parquet": "DBNStore.to_parquet",
    "map-symbols-csv": "map_symbols_csv",
    "map-symbols-json": "map_symbols_json",
}

# Curated list of doc pages relevant to fin3's usage of the Databento Python SDK.
# Covers: db.Historical, timeseries.get_range(), OHLCV schemas, instrument
# definitions, symbology (stype_in/stype_out), XNAS.ITCH / GLBX.MDP3 datasets.
RELEVANT_SLUGS = [
    # Quickstart
    "quickstart/set-up",
    "quickstart/choose-service",
    # build-first-app is an empty placeholder in prerendered output
    # Historical API basics (monolithic page — sections extracted individually)
    "api-reference-historical/basics/overview",
    "api-reference-historical/basics/authentication",
    "api-reference-historical/basics/datasets",
    "api-reference-historical/basics/dates-and-times",
    "api-reference-historical/basics/encodings",
    "api-reference-historical/basics/errors",
    "api-reference-historical/basics/metered-pricing",
    "api-reference-historical/basics/rate-limits",
    "api-reference-historical/basics/schemas-and-conventions",
    "api-reference-historical/basics/size-limits",
    "api-reference-historical/basics/symbology",
    "api-reference-historical/basics/compression",
    "api-reference-historical/basics/versioning",
    # Timeseries API
    "api-reference-historical/timeseries/timeseries-get-range",
    # Client
    "api-reference-historical/client/historical",
    # Helpers (to_df, to_parquet, etc.)
    "api-reference-historical/helpers/dbn-store-to-df",
    "api-reference-historical/helpers/dbn-store-to-parquet",
    "api-reference-historical/helpers/dbn-store-to-file",
    "api-reference-historical/helpers/dbn-store",
    "api-reference-historical/helpers/request-symbology",
    "api-reference-historical/helpers/request-full-definitions",
    # Symbology API
    "api-reference-historical/symbology/symbology-resolve",
    # Batch download
    "api-reference-historical/batch/batch-download",
    "api-reference-historical/batch/batch-submit-job",
    # Metadata
    "api-reference-historical/metadata/metadata-get-dataset-range",
    "api-reference-historical/metadata/metadata-list-datasets",
    "api-reference-historical/metadata/metadata-list-schemas",
    # Schemas
    "schemas-and-data-formats/whats-a-schema",
    "schemas-and-data-formats/ohlcv",
    "schemas-and-data-formats/trades",
    "schemas-and-data-formats/instrument-definitions",
    "schemas-and-data-formats/statistics",
    "schemas-and-data-formats/status",
    "schemas-and-data-formats/mbp-1",
    "schemas-and-data-formats/bbo",
    "schemas-and-data-formats/mbo",
    "schemas-and-data-formats/mbp-10",
    # Standards & conventions
    "standards-and-conventions/symbology",
    # databento-binary-encoding is an empty placeholder in prerendered output
    # Examples
    "examples/basics-historical/requesting",
    "examples/basics-historical/encodings",
    "examples/basics-historical/ohlcv-resampling",
    "examples/basics-historical/eod",
    "examples/basics-historical/halts",
    "examples/symbology/all-dataset-symbols",
    "examples/symbology/continuous",
    "examples/symbology/parent-symbology",
    "examples/instrument-definitions",
    "examples/instrument-definitions/liquid-universe",
    "examples/instrument-definitions/tick-sizes",
    "examples/equities/equities-introduction/finding-an-equities-dataset",
    "examples/equities/consolidated-bbo",
    "examples/futures/futures-introduction/using-instrument-definitions-to-get-tick-size-expiration-and-matching-algorithm",
    "examples/security-master/enrich-instrument-definitions",
    # Venues - default datasets used by fin3
    "venues-and-datasets/xnas-itch",
    "venues-and-datasets/glbx-mdp3",
    # FAQs
    "faqs/usage-pricing-and-data-credits",
    "faqs/instruments-and-products",
    "faqs/streaming-vs-batch-download",
]


def _is_monolithic(slug: str) -> bool:
    """Check if a slug belongs to a monolithic page section."""
    return any(slug.startswith(p) for p in _MONOLITHIC_PREFIXES)


def _monolithic_base(slug: str) -> str:
    """Return the base URL for the monolithic page (first two path segments)."""
    parts = slug.split("/")
    return BASE_URL + parts[0]


def _section_heading(slug: str) -> str | None:
    """Map a slug's last segment to its heading text in the monolithic page."""
    last = slug.split("/")[-1]
    return _SLUG_TO_HEADING.get(last)


def download_page(url: str) -> str | None:
    """Download a page using the crawler user-agent for prerendered content."""
    try:
        response = requests.get(url, headers=CRAWLER_HEADERS, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return None


def discover_urls(html: str) -> set[str]:
    """Extract all doc URLs from the prerendered navigation."""
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    for link in soup.find_all("a", href=True):
        full_url = normalize_url(urljoin(BASE_URL, link["href"]))
        if full_url.startswith(BASE_URL) and full_url != BASE_URL:
            urls.add(full_url)
    return urls


def _extract_section(container: Tag | BeautifulSoup, heading: str) -> str | None:
    """
    Extract the HTML content between a target heading and the next
    same-level heading within *container*.
    """
    target: Tag | None = None
    for h in container.find_all(["h2", "h3"], class_="scrolling-item"):
        if h.get_text(strip=True) == heading:
            target = h
            break

    if target is None:
        return None

    nodes: list[str] = []
    for sib in target.next_siblings:
        if (
            isinstance(sib, Tag)
            and sib.name in ("h2", "h3")
            and "scrolling-item" in sib.get("class", [])
        ):
            break
        nodes.append(str(sib))

    # Include the heading itself
    heading_html = str(target)
    return heading_html + "".join(nodes)


def html_to_markdown(
    html: str | None,
    section_heading: str | None = None,
    parsed_soup: BeautifulSoup | None = None,
) -> str:
    """
    Convert prerendered Databento docs HTML to Markdown.

    Accepts either raw *html* string or a pre-parsed *parsed_soup* (to avoid
    re-parsing the same monolithic page for every section extraction).

    If *section_heading* is provided, only that section is extracted from
    the monolithic page. Otherwise the full ``content-container`` div is used.
    """
    if parsed_soup is not None:
        soup = parsed_soup
    elif html:
        soup = BeautifulSoup(html, "html.parser")
    else:
        return ""

    if section_heading:
        content_div = soup.find("div", class_="content-container")
        if content_div:
            fragment = _extract_section(content_div, section_heading)
            if fragment is None:
                print(f"  Warning: section '{section_heading}' not found, falling back to full page")
                fragment = str(content_div)
        else:
            fragment = html
    else:
        content_div = soup.find("div", class_="content-container")
        fragment = str(content_div) if content_div else html

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = False
    md = converter.handle(fragment)

    # Remove decorative link-icon images from headings
    md = _LINK_ICON_RE.sub("", md)
    return md


def normalize_url(url: str) -> str:
    """Strip query parameters and hash fragments from a URL."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def generate_filename(url: str) -> str:
    """Derive a filename from the URL path (e.g. ``quickstart-set-up.md``)."""
    path = urlparse(url).path
    unique = path.replace("/docs/", "").strip("/")
    if unique:
        return unique.replace("/", "-").lower() + ".md"
    return "index.md"


def clean_directory(folder: str) -> None:
    """Delete all files and subdirectories in *folder*."""
    folder_path = Path(folder)
    if folder_path.exists():
        for item in folder_path.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                print(f"Failed to delete {item}. Reason: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Databento documentation")
    parser.add_argument(
        "--output",
        default=OUTPUT_FOLDER,
        help=f"Output folder (default: {OUTPUT_FOLDER})",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clean the output directory before scraping",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Discover and scrape ALL doc pages instead of the curated subset",
    )
    args = parser.parse_args()

    print(f"Target URL: {BASE_URL}")
    print(f"Output folder: {args.output}")

    if not args.no_clean:
        print("Cleaning output directory...")
        clean_directory(args.output)

    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    # Build URL list
    if args.all:
        print("\nDiscovering ALL URLs from index page...")
        index_html = download_page(BASE_URL)
        if not index_html:
            print("Failed to download index page.")
            return 1
        urls = sorted(discover_urls(index_html))
        (output_path / "index.md").write_text(
            html_to_markdown(index_html), encoding="utf-8"
        )
        print(f"Discovered {len(urls)} pages.")
    else:
        urls = [BASE_URL + slug for slug in RELEVANT_SLUGS]
        print(f"\nScraping {len(urls)} curated doc pages.")

    # Cache for monolithic pages: base_url -> (raw_html, parsed_soup)
    mono_cache: dict[str, tuple[str, BeautifulSoup]] = {}

    # Scrape each page
    errors = 0
    for i, url in enumerate(urls, start=1):
        filename = generate_filename(url)
        slug = url.replace(BASE_URL, "").strip("/")

        # Determine if this is a monolithic sub-page
        section_heading: str | None = None
        if _is_monolithic(slug):
            base = _monolithic_base(slug)
            section_heading = _section_heading(slug)
            if section_heading is None:
                print(f"[{i}/{len(urls)}] SKIP {url} (no heading mapping)")
                continue

            # Download the monolithic page once, cache parsed soup
            if base not in mono_cache:
                print(f"[{i}/{len(urls)}] Downloading monolithic: {base}")
                raw = download_page(base) or ""
                mono_cache[base] = (raw, BeautifulSoup(raw, "html.parser"))

            raw, cached_soup = mono_cache[base]
            if not raw:
                errors += 1
                continue

            md = html_to_markdown(None, section_heading=section_heading, parsed_soup=cached_soup)
            (output_path / filename).write_text(md, encoding="utf-8")
            print(f"[{i}/{len(urls)}] {slug} (section: {section_heading}) -> {filename}")
        else:
            print(f"[{i}/{len(urls)}] {url} -> {filename}")
            html = download_page(url)
            if html:
                md = html_to_markdown(html)
                (output_path / filename).write_text(md, encoding="utf-8")
            else:
                errors += 1

    print(f"\nDone! {len(urls) - errors} pages scraped, {errors} errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
