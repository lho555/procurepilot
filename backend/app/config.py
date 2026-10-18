import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def database_url() -> URL:
    if value := os.getenv("DATABASE_URL"):
        url = make_url(value)
        if url.drivername not in {"mysql+pymysql", "sqlite"}:
            raise ValueError("Use mysql+pymysql or sqlite for DATABASE_URL")
        return url
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", "procurepilot"),
        password=os.getenv("MYSQL_PASSWORD", "procurepilot_dev"),
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        database=os.getenv("MYSQL_DATABASE", "procurepilot"),
        query={"charset": "utf8mb4"},
    )
