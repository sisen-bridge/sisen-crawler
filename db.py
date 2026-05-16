"""
db.py
-----
Cloud SQL for PostgreSQL access, used by the Cloud Run Job.

Connections are made through the Cloud SQL Python Connector, which talks to
the Cloud SQL Admin API to discover the instance's IP and establish a
TLS-encrypted connection. No Cloud SQL Auth Proxy sidecar is required.

Environment variables (all required):
    INSTANCE_CONNECTION_NAME : '<project-id>:<region>:<instance-id>'
                               e.g. 'mercurial-cairn-496504-f9:us-central1:sisen-articles'
    DB_USER                  : PostgreSQL user (e.g. 'admin')
    DB_PASS                  : PostgreSQL password
                               (injected from Secret Manager via --set-secrets)
    DB_NAME                  : Database name (e.g. 'sisen_articles_db')
    DB_IP_TYPE               : 'PUBLIC' (default) or 'PRIVATE'
"""

import logging
import os

import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

_INSTANCE_CONNECTION_NAME = os.environ["INSTANCE_CONNECTION_NAME"]
_DB_USER = os.environ["DB_USER"]
_DB_PASS = os.environ["DB_PASS"]
_DB_NAME = os.environ["DB_NAME"]
_IP_TYPE = (
    IPTypes.PRIVATE
    if os.environ.get("DB_IP_TYPE", "PUBLIC").upper() == "PRIVATE"
    else IPTypes.PUBLIC
)

# `lazy` defers the first Admin-API refresh until the first connect() call,
# which keeps cold-start time low for Cloud Run Jobs.
_connector = Connector(refresh_strategy="lazy")


def _getconn():
    return _connector.connect(
        _INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=_DB_USER,
        password=_DB_PASS,
        db=_DB_NAME,
        ip_type=_IP_TYPE,
    )


engine: Engine = sqlalchemy.create_engine(
    "postgresql+pg8000://",
    creator=_getconn,
    pool_size=2,
    max_overflow=2,
    pool_recycle=1800,
    pool_pre_ping=True,
)


# Returns topic_id whether the row already existed or was just inserted.
# The no-op `DO UPDATE SET name = EXCLUDED.name` is the canonical trick for
# making RETURNING fire on conflict (DO NOTHING returns nothing on conflict).
_UPSERT_TOPIC_SQL = sqlalchemy.text(
    """
    INSERT INTO topic (name)
    VALUES (:name)
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING topic_id
    """
)


_INSERT_ARTICLE_SQL = sqlalchemy.text(
    """
    INSERT INTO article (topic_id, press_name, nation, url, ko_text, ja_text, summary)
    VALUES (:topic_id, :press_name, :nation, :url, :ko_text, :ja_text, :summary)
    ON CONFLICT (url) DO NOTHING
    RETURNING article_id
    """
)


def upsert_topic(conn, name: str) -> int:
    """Insert a topic if missing, return its topic_id either way."""
    return conn.execute(_UPSERT_TOPIC_SQL, {"name": name}).scalar_one()


def insert_article(
    conn,
    *,
    topic_id: int,
    press_name: str,
    nation: str,
    url: str,
    ko_text: str | None,
    ja_text: str | None,
    summary: str | None = None,
) -> int | None:
    """Insert one article. Returns the new article_id, or None if the URL
    already existed (the ON CONFLICT (url) DO NOTHING clause fired)."""
    return conn.execute(
        _INSERT_ARTICLE_SQL,
        {
            "topic_id": topic_id,
            "press_name": press_name,
            "nation": nation,
            "url": url,
            "ko_text": ko_text,
            "ja_text": ja_text,
            "summary": summary,
        },
    ).scalar()


def close() -> None:
    """Dispose the SQLAlchemy pool and close the Cloud SQL connector.
    Safe to call once at process exit."""
    engine.dispose()
    _connector.close()
