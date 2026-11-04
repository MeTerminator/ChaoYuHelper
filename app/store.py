"""Encrypted, atomic local persistence for platform sessions and application data."""

import json
import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "course_helper.db"
        key_path = directory / ".key"
        if not key_path.exists():
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value BLOB, PRIMARY KEY(kind,id))")
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def put(self, kind: str, key: str, value):
        encrypted = self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, key, encrypted))

    def get(self, kind: str, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(self.cipher.decrypt(row[0])) if row else default

    def all(self, kind: str):
        with self.connect() as db:
            rows = db.execute("SELECT id,value FROM records WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return {key: json.loads(self.cipher.decrypt(value)) for key, value in rows}

    def delete(self, kind: str, key: str):
        with self.connect() as db:
            db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))
