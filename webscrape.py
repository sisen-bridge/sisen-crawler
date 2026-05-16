"""
webscrape.py
------------
Scrape Korean and Japanese news outlets using their own site-search pages.

Install:
    pip install requests beautifulsoup4 lxml

Quick start:
    from webscraper import extract_article_urls, scrape_articles

    korean_outlets = {
        "chosun": "https://www.chosun.com/nsearch/?query=",
        "yonhap": "https://www.yna.co.kr/search/index?query=",
    }
    japanese_outlets = {
        "tokyo_np": "https://www.tokyo-np.co.jp/search/?q=",
        "mainichi": "https://mainichi.jp/search/?q=",
    }
    # keywords default to the KEYWORDS constant defined in this file
    records  = extract_article_urls(korean_outlets, japanese_outlets)
    articles = scrape_articles(records)
"""

import re
import time
import logging
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

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

OUTLET_CONFIG: dict[str, dict] = {
    # ── Korean ────────────────────────────────────────────────────────────────
    "chosun": {
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
        "link_selector": ".gs-title a",
        "link_attr": "href",
        "base_url": "https://www.tokyo-np.co.jp",
        "url_must_contain": "/article/",
        "use_playwright": True,
    },
    "mainichi": {
        "link_selector": "ul.articlelist a",
        "link_attr": "href",
        "base_url": "https://mainichi.jp",
        "url_must_contain": "/articles/",
        "use_playwright": True,
    },

    "hankookilbo": {
        "link_selector": ".w-full > a, .gap-16 a",
        "link_attr": "href",
        "base_url": "https://www.hankookilbo.com",
        "url_must_contain": "/news/article/",
        "use_playwright": True,
    },
    "hani": {
        "link_selector": ".reverse-mo a",
        "link_attr": "href",
        "base_url": "https://www.hani.co.kr",
        "url_must_contain": "hani.co.kr/arti/",
        "use_playwright": True,
    },
    "khan": {
 
        "link_selector": ".news_list a, .search_list a, .article-list a, .c-list-title a, h3 a, h4 a",
        "link_attr": "href",
        "base_url": "https://www.khan.co.kr",
        "url_must_contain": "/article/",
        "use_playwright": True,
    },

    "akahata": {
        "link_selector": ".title.ellipsis.media-heading a, .media-heading a",
        "link_attr": "href",
        "base_url": "https://www.jcp.or.jp",
        "url_must_contain": "/akahata/aik",  # e.g. /akahata/aik07/2008-...
    },
    "sankei": {

        "link_selector": ".headline a",
        "link_attr": "href",
        "base_url": "https://www.sankei.com",
        "url_must_contain": "/article/",
        "url_must_match_pattern": r"/article/\d{8}-",
    },
    "yomiuri": {
        "search_url_template": "https://www.yomiuri.co.jp/web-search/?st=1&wo={keyword}&ac=srch&ar=1&fy=&fm=&fd=&ty=&tm=&td=",
        "link_selector": ".c-list-title a",
        "link_attr": "href",
        "base_url": "https://www.yomiuri.co.jp",
        "url_must_match_pattern": r"/\w+/\d{8}-",
        "url_must_contain": "yomiuri.co.jp/",
        "use_playwright": True,
    }
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


def _fetch_title(url: str) -> str:
    """Fetch a single article page and extract its title."""
    soup = _fetch(url)
    if soup is None:
        return ""
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


# ── Fixed keyword pairs ───────────────────────────────────────────────────────
# Korean keys are searched in Korean outlets (Chosun, Yonhap).
# Japanese values are searched in Japanese outlets (Tokyo NP, Mainichi).
# Add or remove pairs here as needed.
KEYWORDS: dict[str, str] = {
    "관광세,관광객,숙박세,오버투어리즘": "観光税、観光客、宿泊税、オーバーツーリズム",
    "동해 일본해 병기": "日本海呼称問題",
    "야스쿠니 참배": "靖国神社参拝",
    "일본산 수산물 수입규제": "日本福島産水産物輸出規制",
}

# ── Function 1 ────────────────────────────────────────────────────────────────

def extract_article_urls(
    korean_outlets: dict[str, str],
    japanese_outlets: dict[str, str],
    keywords: dict[str, str] | None = None,
    delay: float = 1.5,
) -> list[dict]:
    """
    For every (outlet, keyword) pair, fetch that outlet's search results page,
    pick the first article found, fetch its title, and return the result.

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
    List of dicts, each with keys: "outlet", "keyword", "url", "title".
    One entry per (outlet, keyword) pair.
    """
    if keywords is None:
        keywords = KEYWORDS
    seen: set[str] = set()
    article_entries: list[dict] = []

    # Build the work list. Each task carries the full (ko, ja) pair so that
    # whichever language we search with, the resulting URLs can still be
    # linked back to the same topic row in the DB.
    tasks: list[dict] = []
    for outlet_name, search_base_url in korean_outlets.items():
        for ko_kw, ja_kw in keywords.items():
            tasks.append({
                "outlet": outlet_name, "nation": "Korea",
                "search_base_url": search_base_url, "search_keyword": ko_kw,
                "ko_keyword": ko_kw, "ja_keyword": ja_kw,
            })
    for outlet_name, search_base_url in japanese_outlets.items():
        for ko_kw, ja_kw in keywords.items():
            tasks.append({
                "outlet": outlet_name, "nation": "Japan",
                "search_base_url": search_base_url, "search_keyword": ja_kw,
                "ko_keyword": ko_kw, "ja_keyword": ja_kw,
            })

    for task in tasks:
        outlet_name = task["outlet"]
        search_base_url = task["search_base_url"]
        keyword = task["search_keyword"]

        cfg = OUTLET_CONFIG.get(outlet_name, {})
        link_selector: str = cfg.get("link_selector", "")
        link_attr: str     = cfg.get("link_attr", "href")
        base_url: str      = cfg.get("base_url", "")

        # Auto-detect base_url from the search URL when not in config.
        if not base_url:
            parsed = urlparse(search_base_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

        # Support outlets with non-standard search URL formats
        template: str = cfg.get("search_url_template", "")
        if template:
            search_url = template.replace("{keyword}", quote(keyword, safe=""))
        else:
            search_url = f"{search_base_url}{quote(keyword, safe='')}"
        log.info("[%s] Fetching search results: %s", outlet_name, search_url)

        use_playwright: bool = cfg.get("use_playwright", False)
        soup = _fetch(search_url, use_playwright=use_playwright)
        if soup is None:
            time.sleep(delay)
            continue

        must_contain: str = cfg.get("url_must_contain", "")
        must_match: str   = cfg.get("url_must_match_pattern", "")
        if link_selector:
            candidates = []
            for a in soup.select(link_selector):
                href = a.get(link_attr, "")
                if href:
                    url = _to_absolute(href, base_url)
                    if not _is_article_url(url, must_contain):
                        continue
                    if must_match and not re.search(must_match, url):
                        continue
                    candidates.append(url)
        else:
            log.warning("[%s] Not in OUTLET_CONFIG — using generic extraction.", outlet_name)
            candidates = _generic_links(soup, base_url)

        # Take only the first unseen article URL, fetch its title immediately
        for url in candidates:
            if url not in seen:
                seen.add(url)
                title = _fetch_title(url)
                article_entries.append({
                    "outlet":  outlet_name,
                    "keyword": keyword,
                    "url":     url,
                    "title":   title,
                })
                log.info("[%s] keyword=%r → %s | title=%r", outlet_name, keyword, url, title[:60])
                break   # ← only 1 article per outlet/keyword
        else:
            log.info("[%s] keyword=%r → no articles found", outlet_name, keyword)

        time.sleep(delay)

    log.info("Collected %d article entries in total.", len(article_entries))
    return article_entries


# ── demo ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    korean_outlets = {
        "chosun":       "https://www.chosun.com/nsearch/?query=",
        "yonhap":       "https://www.yna.co.kr/search/index?query=",
        "hankookilbo":  "https://www.hankookilbo.com/search?searchText=",
        "hani":         "https://search.hani.co.kr/search?searchword=",
        "khan":         "https://search.khan.co.kr/?q=",
    }
    japanese_outlets = {
        "tokyo_np": "https://www.tokyo-np.co.jp/search/?q=",
        "mainichi":  "https://mainichi.jp/search/?q=",
        "akahata":  "https://www.jcp.or.jp/akahata/search/?q=",
        "sankei":   "https://www.sankei.com/search/?q=",
        "yomiuri":  "https://www.yomiuri.co.jp/web-search/",  # URL built from template in OUTLET_CONFIG
    }

    # Keywords defined in KEYWORDS constant; pass a custom dict to override.
    results = extract_article_urls(korean_outlets, japanese_outlets)
    print(f"\nFound {len(results)} results\n")
    for r in results:
        print("=" * 70)
        print("OUTLET :", r["outlet"])
        print("KEYWORD:", r["keyword"])
        print("URL    :", r["url"])
        print("TITLE  :", r["title"])
