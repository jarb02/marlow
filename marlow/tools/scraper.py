"""
Marlow Web Scraper Tool

Extracts content from URLs using httpx + BeautifulSoup.
Supports text, links, tables, and raw HTML extraction.

Security: honest User-Agent, response size limits, no localhost access.

/ Extrae contenido de URLs usando httpx + BeautifulSoup.
"""

import re
import logging
from typing import Optional
from urllib.parse import urlparse

from marlow import __version__

logger = logging.getLogger("marlow.tools.scraper")

# Block internal/private network access
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|10\.\d+\.\d+\.\d+|"
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|\[::1\])$"
)

_MAX_TEXT = 5000
_MAX_HTML = 10000
_MAX_LINKS = 100
_MAX_REDIRECTS = 5
_TIMEOUT = 30


def _is_blocked_url(url: str) -> bool:
    """Check if URL targets a blocked (internal/private) host."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return bool(_BLOCKED_HOSTS.match(hostname))
    except Exception:
        return True


async def scrape_url(
    url: str,
    selector: Optional[str] = None,
    format: str = "text",
) -> dict:
    """
    Extract content from a URL.

    Args:
        url: The page to scrape. Prepends https:// if missing.
        selector: CSS selector to filter content (e.g., "article", ".main").
        format: Output format:
            - "text": Plain text, scripts/styles removed (default).
            - "links": All links with text and href.
            - "tables": Parsed HTML tables as row arrays.
            - "html": Raw HTML (truncated to 10KB).

    Returns:
        Dictionary with extracted content and metadata.

    / Extrae contenido de una URL.
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return {
            "error": "httpx and beautifulsoup4 not installed. Run: pip install httpx beautifulsoup4",
        }

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if _is_blocked_url(url):
        return {"error": "Access to internal/private network addresses is blocked for security."}

    valid_formats = ("text", "links", "tables", "html")
    if format not in valid_formats:
        return {"error": f"Unknown format: {format}. Use: {valid_formats}"}

    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            headers = {"User-Agent": f"Marlow/{__version__} (Desktop Automation Tool)"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Apply CSS selector if provided
        if selector:
            elements = soup.select(selector)
            if not elements:
                return {"error": f"No elements found for selector: {selector}", "url": url}
            content = "\n".join(str(el) for el in elements)
            soup = BeautifulSoup(content, "html.parser")

        if format == "text":
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [line for line in text.splitlines() if line.strip()]
            clean_text = "\n".join(lines)
            return {
                "success": True,
                "text": clean_text[:_MAX_TEXT],
                "url": str(response.url),
                "title": soup.title.string.strip() if soup.title and soup.title.string else "",
                "length": len(clean_text),
                "truncated": len(clean_text) > _MAX_TEXT,
                "status_code": response.status_code,
            }

        elif format == "links":
            links = []
            for a in soup.find_all("a", href=True):
                text = a.get_text(strip=True)
                href = a["href"]
                if text or href:
                    links.append({"text": text, "href": href})
            return {
                "success": True,
                "links": links[:_MAX_LINKS],
                "total": len(links),
                "truncated": len(links) > _MAX_LINKS,
                "url": str(response.url),
            }

        elif format == "tables":
            tables = []
            for table in soup.find_all("table"):
                rows = []
                for tr in table.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if cells:
                        rows.append(cells)
                if rows:
                    tables.append(rows)
            return {
                "success": True,
                "tables": tables,
                "total_tables": len(tables),
                "url": str(response.url),
            }

        elif format == "html":
            html_str = str(soup)
            return {
                "success": True,
                "html": html_str[:_MAX_HTML],
                "url": str(response.url),
                "length": len(html_str),
                "truncated": len(html_str) > _MAX_HTML,
            }

    except ImportError:
        return {"error": "httpx and beautifulsoup4 required. Run: pip install httpx beautifulsoup4"}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {url}"}
    except httpx.ConnectError:
        return {"error": f"Could not connect to {url}"}
    except httpx.TimeoutException:
        return {"error": f"Request timed out after {_TIMEOUT}s: {url}"}
    except Exception as e:
        logger.error(f"Scraper error: {e}")
        return {"error": str(e)}
