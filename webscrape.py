"""
news_scraper.py
---------------
Scrape Korean and Japanese news outlets using their own site-search pages.

Install:
    pip install requests beautifulsoup4 lxml

Quick start:
    from kj_scraper import extract_article_urls, scrape_articles

    korean_outlets = {
        "chosun": "https://www.chosun.com/nsearch/?query=",
        "yonhap": "https://www.yna.co.kr/search/index?query=",
    }
    japanese_outlets = {
        "nhk":   "https://www3.nhk.or.jp/news/search/?q=",
        "asahi": "https://www.asahi.com/search/?keywords=",
    }
    # keywords default to the KEYWORDS constant defined in this file
    article_urls = extract_article_urls(korean_outlets, japanese_outlets)
    articles     = scrape_articles(article_urls)
"""

import time
import logging
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

# Optional: playwright for JS-rendered pages.
# Install with: pip install playwright && playwright install chromium
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── shared HTTP session ────────────────────────────────────────────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,ja;q=0.8,en-US;q=0.7,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
})

# ── Per-outlet configuration ───────────────────────────────────────────────────
# Defines how to find article links on each outlet's search results page.
# Add or adjust selectors here if a site updates its HTML structure.
#
# Keys:
#   "link_selector" : CSS selector matching <a> tags that wrap article links.
#   "link_attr"     : attribute on that tag holding the URL (almost always "href").
#   "base_url"      : prepended to relative URLs (leave "" to auto-detect).
#
OUTLET_CONFIG: dict[str, dict] = {
    # ── Korean ────────────────────────────────────────────────────────────────
    "chosun": {
        # Results load inside div.search-feed > div.story-card-wrapper
        # Links point to biz.chosun.com or www.chosun.com article pages
        "link_selector": ".search-feed .story-card-wrapper a",
        "link_attr": "href",
        "base_url": "https://www.chosun.com",
        "url_must_contain": "chosun.com",
        "use_playwright": True,
    },
    "yonhap": {
        "link_selector": ".cts_atclst a, .list-type038 a, .news-con a, h2 a, h3 a",
        "link_attr": "href",
        "base_url": "https://www.yna.co.kr",
    },
    # ── Japanese ──────────────────────────────────────────────────────────────
    "tokyo_np": {
        # Search results: each article link sits in a parent with class "detail-ttl"
        "link_selector": ".detail-ttl a",
        "link_attr": "href",
        "base_url": "https://www.tokyo-np.co.jp",
        "url_must_contain": "/article/",
        "use_playwright": True,
    },
    "mainichi": {
        # Results load inside ul.articlelist.js-morelist > li > a
        # hrefs are protocol-relative: //mainichi.jp/articles/...
        "link_selector": "ul.articlelist a",
        "link_attr": "href",
        "base_url": "https://mainichi.jp",
        "url_must_contain": "/articles/",
        "use_playwright": True,
    },
    # Add more outlets here following the same pattern.
}

# ── helpers ────────────────────────────────────────────────────────────────────

def _fetch_requests(url: str, timeout: int = 12) -> str | None:
    """Fetch page HTML via requests. Returns raw HTML string or None."""
    try:
        resp = _SESSION.get(url, timeout=timeout)
        resp.raise_for_status()
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            resp.encoding = resp.apparent_encoding
        return resp.text
    except requests.RequestException as exc:
        log.warning("requests fetch failed: %s — %s", url, exc)
        return None


def _fetch_playwright(url: str, wait_ms: int = 3000) -> str | None:
    """Fetch page HTML via a headless Chromium browser (handles JS rendering)."""
    if not _PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page.goto(url, timeout=20000)
            page.wait_for_timeout(wait_ms)   # let JS render results
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        log.warning("playwright fetch failed: %s — %s", url, exc)
        return None


def _fetch(url: str, use_playwright: bool = False, timeout: int = 12) -> BeautifulSoup | None:
    """
    Fetch a URL and return BeautifulSoup, or None on failure.

    If use_playwright=True (set in OUTLET_CONFIG), uses a headless browser
    so JS-rendered content is visible. Falls back to requests if playwright
    is not installed.
    """
    if use_playwright and _PLAYWRIGHT_AVAILABLE:
        log.debug("Using playwright for: %s", url)
        html = _fetch_playwright(url)
    else:
        html = _fetch_requests(url, timeout=timeout)
        # If requests gets a near-empty body, try playwright as fallback
        if html and len(html) < 2000 and _PLAYWRIGHT_AVAILABLE:
            log.info("Tiny response (%d chars) — retrying with playwright: %s", len(html), url)
            html = _fetch_playwright(url) or html

    if html is None:
        return None
    return BeautifulSoup(html, "lxml")


def _is_article_url(url: str, must_contain: str = "") -> bool:
    """Filter out search pages, tag indexes, and non-HTTP links.

    If `must_contain` is set, the URL must also include that substring
    (e.g. "/article/" or "/articles/") to be accepted.
    """
    # Exact path-segment matches to skip — avoids false positives on URLs
    # that merely *contain* these strings (e.g. article slugs with "search" in them).
    skip_paths = ("/tag/", "/category/", "/topics/")
    # Scheme/protocol-level skips
    skip_prefixes = ("javascript:", "#")
    if not url.startswith("http"):
        return False
    if any(url.startswith(p) for p in skip_prefixes):
        return False
    if any(frag in url for frag in skip_paths):
        return False
    if must_contain and must_contain not in url:
        return False
    return True


def _to_absolute(href: str, base_url: str) -> str:
    """Convert a relative or protocol-relative href to an absolute URL."""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        # Protocol-relative URL e.g. //mainichi.jp/articles/...
        return "https:" + href
    return urljoin(base_url, href)


def _generic_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Fallback: collect all <a href> that look like article links."""
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        url = _to_absolute(a["href"], base_url)
        if _is_article_url(url) and url not in seen:
            seen.add(url)
            links.append(url)
    return links



def diagnose_outlet(search_url: str, outlet_name: str = "") -> None:
    """
    Diagnostic helper — call this when an outlet returns 0 URLs.

    Prints:
      - HTTP status and final URL (after redirects)
      - All <a href> tags found on the page, grouped by tag/class context
      - Top-level class names on <div> and <section> elements

    Use the output to identify the correct CSS selectors for OUTLET_CONFIG.

    Example
    -------
    diagnose_outlet("https://www.chosun.com/nsearch/?query=반도체", "chosun")
    """
    print(f"\n{'='*70}")
    print(f"DIAGNOSING: {outlet_name or search_url}")
    print(f"URL: {search_url}")

    try:
        resp = _SESSION.get(search_url, timeout=15)
        print(f"Status : {resp.status_code}")
        print(f"Final  : {resp.url}")
        print(f"Encoding: {resp.encoding} / apparent: {resp.apparent_encoding}")
    except Exception as exc:
        print(f"Request failed: {exc}")
        return

    soup = BeautifulSoup(resp.text, "lxml")

    # Show container classes (helps identify the results wrapper)
    print("\n── Top-level div/section classes (first 30) ──")
    containers = soup.find_all(["div", "section", "ul", "ol"], class_=True)
    seen_classes: set[str] = set()
    for el in containers:
        cls = " ".join(el.get("class", []))
        if cls and cls not in seen_classes:
            seen_classes.add(cls)
            print(f"  <{el.name} class='{cls}'>")
        if len(seen_classes) >= 30:
            break

    # Show all hrefs found
    print(f"\n── All <a href> on page ({len(soup.find_all('a', href=True))} total, showing first 40) ──")
    for i, a in enumerate(soup.find_all("a", href=True)):
        if i >= 40:
            break
        href = a.get("href", "")
        text = a.get_text(strip=True)[:60]
        parent_cls = " ".join(a.parent.get("class", [])) if a.parent else ""
        print(f"  [{i:02d}] href={href!r:60s} text={text!r} parent_class={parent_cls!r}")

    print(f"{'='*70}\n")


# ── Fixed keyword pairs ───────────────────────────────────────────────────────
# Korean keys are searched in Korean outlets (Chosun, Yonhap).
# Japanese values are searched in Japanese outlets (Tokyo NP, Mainichi).
# Add or remove pairs here as needed.
KEYWORDS: dict[str, str] = {
    "APEC정상회의":     "APEC首脳会議",
    "안동한일정상회담": "安東日韓首脳会談",
}

# ── Function 1 ────────────────────────────────────────────────────────────────

def extract_article_urls(
    korean_outlets: dict[str, str],
    japanese_outlets: dict[str, str],
    keywords: dict[str, str] | None = None,
    delay: float = 1.5,
) -> list[str]:
    """
    For every (outlet, keyword) pair, fetch that outlet's search results page
    and collect the article URLs found on it.

    Parameters
    ----------
    korean_outlets   : dict mapping outlet name → base search URL
                       e.g. {"chosun": "https://www.chosun.com/nsearch/?query="}
    japanese_outlets : same format for Japanese outlets
    keywords         : dict mapping Korean keyword → Japanese keyword.
                       Defaults to the module-level KEYWORDS constant if not given.
    delay            : polite pause between HTTP requests (seconds)

    Returns
    -------
    Deduplicated flat list of article URL strings across all outlets and keywords.
    """
    if keywords is None:
        keywords = KEYWORDS
    seen: set[str] = set()
    article_urls: list[str] = []

    # Build two separate (outlet_name, search_base_url, keyword) work lists.
    tasks: list[tuple[str, str, str]] = []
    for outlet_name, search_base_url in korean_outlets.items():
        for korean_kw in keywords.keys():
            tasks.append((outlet_name, search_base_url, korean_kw))
    for outlet_name, search_base_url in japanese_outlets.items():
        for japanese_kw in keywords.values():
            tasks.append((outlet_name, search_base_url, japanese_kw))

    for outlet_name, search_base_url, keyword in tasks:
        cfg = OUTLET_CONFIG.get(outlet_name, {})
        link_selector: str = cfg.get("link_selector", "")
        link_attr: str     = cfg.get("link_attr", "href")
        base_url: str      = cfg.get("base_url", "")

        # Auto-detect base_url from the search URL when not in config.
        if not base_url:
            parsed = urlparse(search_base_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        search_url = f"{search_base_url}{quote(keyword, safe='')}"
        log.info("[%s] Fetching search results: %s", outlet_name, search_url)

        use_playwright: bool = cfg.get("use_playwright", False)
        soup = _fetch(search_url, use_playwright=use_playwright)
        if soup is None:
            time.sleep(delay)
            continue

        must_contain: str = cfg.get("url_must_contain", "")
        if link_selector:
            candidates = []
            for a in soup.select(link_selector):
                href = a.get(link_attr, "")
                if href:
                    url = _to_absolute(href, base_url)
                    if _is_article_url(url, must_contain):
                        candidates.append(url)
        else:
            log.warning("[%s] Not in OUTLET_CONFIG — using generic extraction.", outlet_name)
            candidates = _generic_links(soup, base_url)

        new = 0
        for url in candidates:
            if url not in seen:
                seen.add(url)
                article_urls.append(url)
                new += 1

        log.info("[%s] keyword=%r → +%d URLs (running total: %d)",
                 outlet_name, keyword, new, len(article_urls))
        time.sleep(delay)

    log.info("Collected %d unique article URLs in total.", len(article_urls))
    return article_urls


# ── Function 2 ────────────────────────────────────────────────────────────────

def scrape_articles(
    article_urls: list[str],
    delay: float = 1.0,
) -> list[dict]:
    """
    Fetch each article URL and extract its title and main body text.

    Parameters
    ----------
    article_urls : list of URLs returned by extract_article_urls().
    delay        : polite pause between requests (seconds).

    Returns
    -------
    List of dicts, each with:
        "url"   : original article URL (str)
        "title" : article title        (str, empty string if not found)
        "body"  : main body text       (str, empty string if extraction failed)
    """
    results: list[dict] = []

    for url in article_urls:
        log.info("Scraping: %s", url)
        soup = _fetch(url)

        if soup is None:
            results.append({"url": url, "title": "", "body": ""})
            time.sleep(delay)
            continue

        # ── Title ──────────────────────────────────────────────────────────────
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else ""

        # ── Body ───────────────────────────────────────────────────────────────
        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "noscript", "iframe", "figure"]):
            tag.decompose()

        # Try semantic containers in priority order.
        content_el = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.find("div", class_=lambda c: c and any(
                kw in c.lower() for kw in ("article", "content", "story", "body", "text")
            ))
            or soup.body
        )

        body = ""
        if content_el:
            paragraphs = [
                p.get_text(separator=" ", strip=True)
                for p in content_el.find_all("p")
                if len(p.get_text(strip=True)) > 30   # skip captions / nav snippets
            ]
            body = "\n\n".join(paragraphs) if paragraphs else content_el.get_text("\n", strip=True)

        results.append({"url": url, "title": title, "body": body})
        log.info("  ✓ title=%r | body=%d chars", title[:60], len(body))
        time.sleep(delay)

    log.info("Scraped %d articles.", len(results))
    return results


# ── demo ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    korean_outlets = {
        "chosun": "https://www.chosun.com/nsearch/?query=",
        "yonhap": "https://www.yna.co.kr/search/index?query=",
    }
    japanese_outlets = {
        "tokyo_np": "https://www.tokyo-np.co.jp/search/?q=",
        "mainichi": "https://mainichi.jp/search/?q=",
    }

    # Keywords are defined in the KEYWORDS constant at the top of this file.
    # Pass a custom dict here only if you want to override them for this run.
    article_urls = extract_article_urls(korean_outlets, japanese_outlets)
    print(f"\nFound {len(article_urls)} article URLs\n")

    articles = scrape_articles(article_urls[:3])   # limit to 3 for the demo
    for art in articles:
        print("=" * 70)
        print("URL  :", art["url"])
        print("TITLE:", art["title"])
        print("BODY :", art["body"][:300] + ("…" if len(art["body"]) > 300 else ""))