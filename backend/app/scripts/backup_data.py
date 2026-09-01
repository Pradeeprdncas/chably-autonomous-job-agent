from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import settings


def database_path() -> Path:
    url = settings.resolved_database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError("BACKUP_ONLY_SUPPORTS_SQLITE")
    return Path(url.removeprefix("sqlite:///" if not url.startswith("sqlite:////") else "sqlite://"))


def main(destination: str | None = None):
    source = database_path().resolve()
    target_dir = Path(destination or (Path(settings.data_dir) / "backups")).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"chably-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(target)) as target_db:
        source_db.backup(target_db)
    with sqlite3.connect(str(target)) as backup_db:
        if backup_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            target.unlink(missing_ok=True)
            raise RuntimeError("SQLITE_BACKUP_INTEGRITY_CHECK_FAILED")
    chroma_source = Path(settings.resolved_chroma_path).resolve()
    chroma_target = target.with_suffix(".chroma")
    chroma_status = "not_present"
    if chroma_source.is_dir():
        shutil.copytree(chroma_source, chroma_target)
        chroma_status = "copied"
    print({"sqlite_backup": str(target), "sqlite_integrity": "ok", "chroma_backup": str(chroma_target) if chroma_status == "copied" else None, "chroma_status": chroma_status})
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--destination")
    main(parser.parse_args().destination)
