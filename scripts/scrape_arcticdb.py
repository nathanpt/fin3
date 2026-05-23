#!/usr/bin/env python3
"""
ArcticDB Wiki Scraper

Scrapes the ArcticDB documentation wiki and converts HTML pages to Markdown.

Usage:
    python scrape_arcticdb.py
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import html2text
import requests
from bs4 import BeautifulSoup
from readability import Document

# Configuration
BASE_URL = 'https://docs.arcticdb.io/latest/'
BASE_URL = "https://docs.arcticdb.io/latest/"
OUTPUT_FOLDER = "docs/arcticdb"

# Set of visited URLs to prevent infinite recursion
visited_urls = set()


def download_page(url: str) -> str | None:
    """
    Downloads the content of a web page from the given URL.

    Args:
        url: The URL of the web page to download.

    Returns:
        The content of the web page as a string, or None if there was an error.
    """
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        print(f"Error downloading {url}: {e}")
        return None


def extract_urls(
    html: str, base_url: str, ignored_extensions: list[str] | None = None
) -> set[str]:
    """
    Extracts all URLs from the given HTML content, resolving relative URLs
    and ignoring hash fragments.

    Args:
        html: The HTML content to extract URLs from.
        base_url: The base URL used to resolve relative URLs.
        ignored_extensions: File extensions to ignore (e.g., ['.txt', '.pdf']).

    Returns:
        A set of URLs extracted from the HTML content.
    """
    if ignored_extensions is None:
        ignored_extensions = [".txt", ".pdf", ".docx"]

    soup = BeautifulSoup(html, "html.parser")
    urls = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Resolve relative URLs and filter by hash fragment
        full_url = urljoin(base_url, href.split("#", 1)[0])
        # Ignore URLs ending with specific file extensions
        if any(full_url.endswith(ext) for ext in ignored_extensions):
            continue
        if urlparse(full_url).netloc == urlparse(base_url).netloc:
            urls.add(full_url)

    return urls


def html_to_markdown(html: str) -> str:
    """
    Converts HTML content to Markdown format.

    Args:
        html: The HTML content to be converted.

    Returns:
        The Markdown representation of the HTML content.
    """
    # Using readability to extract the main content
    document = Document(html)
    summary = document.summary()

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = False
    return converter.handle(summary)


def save_markdown(markdown: str, folder: str, filename: str) -> None:
    """
    Saves markdown content to a file.

    Args:
        markdown: The markdown content to save.
        folder: The folder path to save the file in.
        filename: The name of the file to save.
    """
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)

    filepath = folder_path / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)


def generate_filename(url: str, base_url: str) -> str:
    """
    Generate a filename based on the given URL and base URL.

    Args:
        url: The URL from which the filename will be generated.
        base_url: The base URL used to remove the common path from the URL.

    Returns:
        The generated filename.
    """
    # Parse the URLs
    parsed_url = urlparse(url)
    parsed_base_url = urlparse(base_url)

    # Remove the base URL path to get the unique part of the path
    base_path = parsed_base_url.path.strip("/")
    unique_path = parsed_url.path.strip("/")

    # If the base_path is not empty, remove it from the start of unique_path
    if base_path and unique_path.startswith(base_path):
        unique_path = unique_path[len(base_path) :].strip("/")

    # Split the path into segments and join them with hyphens
    if unique_path:
        filename = unique_path.replace("/", "-").lower() + ".md"
    else:
        filename = "index.md"
    return filename


def scrape_site(url: str, base_url: str, base_folder: str = "") -> None:
    """
    Scrapes a website recursively, saving the content as markdown files.

    Args:
        url: The URL of the website to scrape.
        base_url: The base URL of the website.
        base_folder: The base folder to save the markdown files.
    """
    # Ensure the URL starts with the base URL
    if not url.startswith(base_url):
        return

    if url in visited_urls or urlparse(url).netloc != urlparse(base_url).netloc:
        return

    visited_urls.add(url)

    print(f"Scraping {url}")
    html = download_page(url)

    if html:
        markdown = html_to_markdown(html)
        filename = generate_filename(url, base_url)
        folder = os.path.join(base_folder, urlparse(base_url).netloc)
        save_markdown(markdown, folder, filename)

        for link in extract_urls(html, url):
            scrape_site(link, base_url, base_folder)


def clean_directory(folder: str) -> None:
    """
    Deletes all files and folders in the specified directory.

    Args:
        folder: The path to the directory to be cleaned.
    """
    folder_path = Path(folder)
    if folder_path.exists():
        for item in folder_path.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    # Import shutil here to avoid issues if not needed
                    import shutil

                    shutil.rmtree(item)
            except Exception as e:
                print(f"Failed to delete {item}. Reason: {e}")


def main() -> int:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Scrape ArcticDB documentation wiki")
    parser.add_argument(
        "--output",
        default=OUTPUT_FOLDER,
        help=f"Output folder for scraped markdown files (default: {OUTPUT_FOLDER})",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not clean the output directory before scraping",
    )

    args = parser.parse_args()

    print(f"Target URL: {BASE_URL}")
    print(f"Output folder: {args.output}")

    # Clean output directory
    if not args.no_clean:
        print("Cleaning output directory...")
        clean_directory(args.output)

    # Scrape the site
    print("\nStarting scrape...")
    scrape_site(BASE_URL, BASE_URL, args.output)

    print(f"\nScraping complete!")
    print(f"Total URLs visited: {len(visited_urls)}")
    print(f"Files saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
