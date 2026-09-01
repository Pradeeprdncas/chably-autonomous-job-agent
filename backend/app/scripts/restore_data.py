from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from app.config import settings
from app.scripts.backup_data import database_path


def _verified_sqlite(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(str(path)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLITE_BACKUP_INTEGRITY_CHECK_FAILED")


def main(source: str, chroma_source: str | None = None):
    """Restore an offline Chably data snapshot.

    Stop the API and all workers before running this command. The SQLite copy is
    verified and replaced atomically. Chroma is staged before its directory is
    swapped, so an incomplete copy never becomes the active index.
    """
    sqlite_source = Path(source).resolve()
    _verified_sqlite(sqlite_source)
    sqlite_target = database_path().resolve()
    if sqlite_source == sqlite_target:
        raise RuntimeError("RESTORE_SOURCE_EQUALS_TARGET")
    sqlite_target.parent.mkdir(parents=True, exist_ok=True)
    sqlite_stage = sqlite_target.with_name(f".{sqlite_target.name}.restore-{uuid.uuid4().hex}")
    shutil.copy2(sqlite_source, sqlite_stage)
    _verified_sqlite(sqlite_stage)
    os.replace(sqlite_stage, sqlite_target)

    default_chroma = sqlite_source.with_suffix(".chroma")
    chroma_backup = Path(chroma_source).resolve() if chroma_source else default_chroma
    chroma_target = Path(settings.resolved_chroma_path).resolve()
    chroma_status = "not_present"
    if chroma_backup.is_dir():
        chroma_target.parent.mkdir(parents=True, exist_ok=True)
        chroma_stage = chroma_target.with_name(f".{chroma_target.name}.restore-{uuid.uuid4().hex}")
        shutil.copytree(chroma_backup, chroma_stage)
        old_target = chroma_target.with_name(f".{chroma_target.name}.old-{uuid.uuid4().hex}")
        if chroma_target.exists():
            os.replace(chroma_target, old_target)
        os.replace(chroma_stage, chroma_target)
        if old_target.exists():
            shutil.rmtree(old_target)
        chroma_status = "restored"

    result = {"sqlite_restore": str(sqlite_target), "sqlite_integrity": "ok", "chroma_restore": str(chroma_target) if chroma_status == "restored" else None, "chroma_status": chroma_status}
    print(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restore a Chably snapshot while the API is stopped")
    parser.add_argument("source", help="SQLite backup created by backup_data")
    parser.add_argument("--chroma-source", help="Optional Chroma backup directory")
    arguments = parser.parse_args()
    main(arguments.source, arguments.chroma_source)
