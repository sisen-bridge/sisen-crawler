"""
main.py
-------
Entrypoint for GCP Cloud Run Jobs.
Runs the scraper and prints results to stdout (visible in Cloud Logging).

Adapt the output section at the bottom to write to Cloud Storage,
BigQuery, Firestore, or wherever you want to store the results.
"""

import json
import logging
import os

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
    "mainichi":  "https://mainichi.jp/search/?q=",
}


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("=== News Scraper starting ===")

    # 1. Collect article URLs from all outlets
    article_urls = extract_article_urls(KOREAN_OUTLETS, JAPANESE_OUTLETS)
    log.info("Collected %d article URLs", len(article_urls))

    # 2. Scrape title + body from each article
    articles = scrape_articles(article_urls)
    log.info("Scraped %d articles", len(articles))

    # 3. Output ────────────────────────────────────────────────────────────────
    # Currently writes newline-delimited JSON to stdout, which Cloud Logging
    # captures automatically.
    #
    # To write to Cloud Storage instead, replace this block with:
    #
    #   from google.cloud import storage
    #   client = storage.Client()
    #   bucket = client.bucket(os.environ["GCS_BUCKET"])
    #   blob = bucket.blob("results/articles.json")
    #   blob.upload_from_string(json.dumps(articles, ensure_ascii=False, indent=2),
    #                           content_type="application/json")
    #   log.info("Uploaded to gs://%s/results/articles.json", os.environ["GCS_BUCKET"])
    #
    for article in articles:
        print(json.dumps(article, ensure_ascii=False))

    log.info("=== News Scraper done ===")


if __name__ == "__main__":
    main()