# sisen-crawler

Cloud Run Job that scrapes Korean (Chosun, Yonhap) and Japanese (Tokyo Shimbun,
Mainichi) news outlets for the keyword pairs in `webscrape.KEYWORDS` and writes
the results into Cloud SQL for PostgreSQL.

## Deployed environment

| | |
|---|---|
| Project / region | `mercurial-cairn-496504-f9` / `us-central1` |
| Cloud SQL instance | `sisen-articles` (PostgreSQL, public IP) |
| Database / user | `sisen_articles_db` / `admin` |
| DB password | Secret Manager secret `sisen-db-password` |
| Image | Artifact Registry repo `cloud-run-source-deploy` |
| Job | `web-scraper-job` |

## Build & run

```bash
# Build, push, and (re-)deploy the Job
gcloud builds submit --config=cloudbuild.yaml

# Execute the Job once
gcloud run jobs execute web-scraper-job --region=us-central1 --wait

# Tail logs from the most recent execution
gcloud beta run jobs executions logs read \
    --region=us-central1 \
    $(gcloud run jobs executions list --job=web-scraper-job \
        --region=us-central1 --limit=1 --format='value(name)')

# Open psql against the DB
gcloud sql connect sisen-articles --user=admin --database=sisen_articles_db
```

## Configuration

| Change | Edit |
|---|---|
| Keyword pairs to search | `KEYWORDS` in `webscrape.py` |
| Outlets to crawl | `KOREAN_OUTLETS` / `JAPANESE_OUTLETS` in `main.py` |
| Per-outlet CSS selectors | `OUTLET_CONFIG` in `webscrape.py` |
| Memory / CPU / task timeout | flags in `cloudbuild.yaml` |
| Cloud SQL / secret names | `substitutions` block in `cloudbuild.yaml` |

Apply with `gcloud builds submit --config=cloudbuild.yaml`.

## Files

| File | Role |
|---|---|
| `main.py` | Cloud Run Job entrypoint. |
| `webscrape.py` | HTML scraping. No DB dependency. |
| `db.py` | Cloud SQL connection + `upsert_topic` / `insert_article`. |
| `Dockerfile` | Container image. |
| `cloudbuild.yaml` | Build → push → deploy pipeline. |
| `gcp_setup.sh` | One-time GCP project setup (re-runnable). |

## Local development

```bash
pip install -r requirements.txt
playwright install chromium
python webscrape.py   # prints 3 scraped articles to stdout; no DB needed
```
