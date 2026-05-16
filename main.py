"""
main.py
-------
Entrypoint for the GCP Cloud Run Job.

Scrapes Korean + Japanese news outlets for each (ko, ja) keyword pair
defined in webscrape.KEYWORDS, then writes the results into Cloud SQL
for PostgreSQL (database `sisen_articles_db`, tables `topic` + `article`).
"""

import logging

import db
from webscrape import extract_article_urls, scrape_articles

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)


# ── Outlets ────────────────────────────────────────────────────────────────────
KOREAN_OUTLETS = {
    "chosun": "https://www.chosun.com/nsearch/?query=",
    "yonhap": "https://www.yna.co.kr/search/index?query=",
}

JAPANESE_OUTLETS = {
    "tokyo_np": "https://www.tokyo-np.co.jp/search/?q=",
    "mainichi": "https://mainichi.jp/search/?q=",
}


def _topic_name(ko: str, ja: str) -> str:
    return f"{ko} / {ja}"


# article.{ko_title,ja_title,neutral_title} are VARCHAR(512); truncate to be safe.
_TITLE_MAX = 512


def _truncate(s: str | None, n: int = _TITLE_MAX) -> str | None:
    if not s:
        return None
    s = s.strip()
    return (s[: n - 1] + "…") if len(s) > n else (s or None)


def main() -> None:
    log.info("=== News Scraper starting ===")

    # 1. Collect article URLs (each record carries outlet/nation/keyword pair).
    records = extract_article_urls(KOREAN_OUTLETS, JAPANESE_OUTLETS)
    log.info("Collected %d article URLs", len(records))

    # 2. Fetch title + body for each article.
    articles = scrape_articles(records)
    log.info("Scraped %d articles", len(articles))

    if not articles:
        log.info("Nothing to write. Exiting.")
        db.close()
        return

    # 3. Upsert every (ko, ja) keyword pair as a topic, capture its topic_id.
    pairs = {(a["ko_keyword"], a["ja_keyword"]) for a in articles}
    topic_ids: dict[tuple[str, str], int] = {}
    with db.engine.begin() as conn:
        for ko, ja in pairs:
            topic_ids[(ko, ja)] = db.upsert_topic(conn, _topic_name(ko, ja))
    log.info("Resolved %d topics", len(topic_ids))

    # 4. Insert articles one transaction at a time so a single bad row
    #    doesn't roll back the whole batch.
    inserted = skipped = failed = 0
    for art in articles:
        body = (art["body"] or "").strip()
        title = _truncate(art["title"])
        if not body and not title:
            log.info("Empty article, skipping: %s", art["url"])
            failed += 1
            continue
        is_korean = art["nation"] == "Korea"
        try:
            with db.engine.begin() as conn:
                new_id = db.insert_article(
                    conn,
                    topic_id=topic_ids[(art["ko_keyword"], art["ja_keyword"])],
                    press_name=art["outlet"],
                    nation=art["nation"],
                    url=art["url"],
                    ko_text=body if is_korean else None,
                    ja_text=body if not is_korean else None,
                    ko_title=title if is_korean else None,
                    ja_title=title if not is_korean else None,
                    neutral_title=None,
                    summary=None,
                )
            if new_id is None:
                skipped += 1
            else:
                inserted += 1
        except Exception as exc:
            log.warning("Insert failed for %s — %s", art["url"], exc)
            failed += 1

    log.info(
        "Done. inserted=%d skipped(dup)=%d failed=%d",
        inserted, skipped, failed,
    )
    db.close()
    log.info("=== News Scraper done ===")


if __name__ == "__main__":
    main()
