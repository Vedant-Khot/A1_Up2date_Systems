"""SQLite store for the SiteLog demo. Photos stay in a local folder."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
import zlib
import struct
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sitelog.db"
PHOTO_DIR = ROOT / "photos"
PASSWORD_SALT = "sitelog-demo"

STATUSES = ("new", "in_progress", "discarded", "completed")
STATUS_LABELS = {
    "new": "New",
    "in_progress": "In progress",
    "completed": "Completed",
    "discarded": "Discarded",
}
ROLE_LABELS = {
    "office": "Office",
    "engineer": "Engineer",
    "employee": "Engineer",
}
BRAND = "UP2DATE"
FERNET_ENV = "SITELOG_FERNET_KEY"


def hash_password(password: str) -> str:
    return hashlib.sha256(f"{PASSWORD_SALT}:{password}".encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _calc_hours_worked(arrival: Any, departure: Any) -> str:
    if not arrival or not departure:
        return ""
    try:
        arr_parts = [int(p) for p in str(arrival).split(":")[:2]]
        dep_parts = [int(p) for p in str(departure).split(":")[:2]]
        start_min = arr_parts[0] * 60 + arr_parts[1]
        end_min = dep_parts[0] * 60 + dep_parts[1]
        if end_min < start_min:
            end_min += 24 * 60
        diff = end_min - start_min
        h = diff // 60
        m = diff % 60
        if diff == 0:
            return "0 mins"
        if h == 0:
            return f"{m}m"
        if m == 0:
            return f"{h}h"
        return f"{h}h {m}m"
    except Exception:
        return ""


def _is_encrypted(value: str) -> bool:
    return value.startswith("gAAAA")


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get(FERNET_ENV, "").strip()
    if not raw:
        raise RuntimeError(
            "SITELOG_FERNET_KEY is missing. Mailbox passwords cannot be stored or read. "
            'Generate a key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError("SITELOG_FERNET_KEY is not a valid Fernet key.") from exc


def encrypt_secret(plain: str) -> str:
    if not plain or _is_encrypted(plain):
        return plain
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored or not _is_encrypted(stored):
        return stored
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt the mailbox password. Check SITELOG_FERNET_KEY.") from exc


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class Store:
    def __init__(self, database_path: Path = DB_PATH) -> None:
        self.database_path = database_path

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        with self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    serial_number INTEGER NOT NULL,
                    work_ref TEXT NOT NULL,
                    job_date TEXT,
                    client TEXT,
                    site_address TEXT,
                    arrival_time TEXT,
                    departure_time TEXT,
                    engineers TEXT,
                    issue TEXT,
                    findings TEXT,
                    materials TEXT,
                    part_number TEXT,
                    is_complete INTEGER,
                    recommended_works TEXT,
                    status TEXT NOT NULL,
                    engineer_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS photos (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_engineers (
                    job_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    PRIMARY KEY (job_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS job_messages (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mailbox (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen_emails (
                    message_id TEXT PRIMARY KEY,
                    job_id TEXT,
                    seen_at TEXT NOT NULL
                );
                """
            )
            self._ensure_job_columns(connection)
            self._ensure_user_columns(connection)
            self._ensure_mailbox_columns(connection)
            self._migrate_mailbox_password(connection)
            connection.execute("UPDATE jobs SET status = 'new' WHERE status = 'logged'")
            connection.execute("UPDATE jobs SET status = 'completed' WHERE status = 'reviewed'")
            connection.execute(
                """INSERT OR IGNORE INTO job_engineers (job_id, user_id)
                   SELECT id, engineer_id FROM jobs
                   WHERE engineer_id IN (SELECT id FROM users)"""
            )
        self._seed()

    def _ensure_job_columns(self, connection: sqlite3.Connection) -> None:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        additions = {
            "source": "TEXT NOT NULL DEFAULT 'form'",
            "discard_reason": "TEXT",
            "status_before_discard": "TEXT",
            "email_from": "TEXT",
            "email_subject": "TEXT",
            "email_body": "TEXT",
            "email_message_id": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")

    def _ensure_user_columns(self, connection: sqlite3.Connection) -> None:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "phone" not in existing:
            connection.execute("ALTER TABLE users ADD COLUMN phone TEXT")

    def _ensure_mailbox_columns(self, connection: sqlite3.Connection) -> None:
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(mailbox)")}
        additions = {
            "last_checked": "TEXT",
            "jobs_created": "INTEGER DEFAULT 0",
            "last_error": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE mailbox ADD COLUMN {name} {declaration}")

    def _seed(self) -> None:
        with self._db() as connection:
            if connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
                self._ensure_sam(connection)
                return
            office_id = str(uuid.uuid4())
            alex_id = str(uuid.uuid4())
            sam_id = str(uuid.uuid4())
            connection.executemany(
                "INSERT INTO users (id, email, name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                [
                    (office_id, "office@sitelog.demo", "Office Desk", hash_password("office123"), "office"),
                    (alex_id, "alex@sitelog.demo", "Alex Morgan", hash_password("engineer123"), "engineer"),
                    (sam_id, "sam@sitelog.demo", "Sam Reed", hash_password("engineer123"), "engineer"),
                ],
            )
            samples = [
                self._sample("NL-1101", "new", [alex_id], "01/10/2026", "Northline Housing", "14 Baker Street, London NW1 6XE", "Front door will not lock", "form"),
                self._sample("NL-1102", "in_progress", [alex_id, sam_id], "02/10/2026", "Harbour Homes", "8 Quay Road, London E14 9SH", "Leak under the kitchen sink", "form"),
                self._sample("NL-1103", "completed", [sam_id], "12/09/2026", "City Lets", "Flat 3, 22 King Street, London SE1 8PB", "Window handle snapped", "form"),
                self._sample("NL-1104", "discarded", [alex_id], "20/09/2026", "Harbour Homes", "2 Dock Lane, London E16 2RD", "Duplicate call for the same leak", "email"),
            ]
            for index, sample in enumerate(samples, start=1):
                job_id = str(uuid.uuid4())
                assignees = sample.pop("assignees")
                connection.execute(
                    """INSERT INTO jobs (
                        id, serial_number, work_ref, job_date, client, site_address,
                        arrival_time, departure_time, engineers, issue, findings,
                        materials, part_number, is_complete, recommended_works,
                        status, engineer_id, created_at, source, discard_reason,
                        status_before_discard, email_from, email_subject, email_body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id, index, sample["work_ref"], sample["job_date"], sample["client"],
                        sample["site_address"], "09:00", "10:30", "", sample["issue"],
                        "Checked the site and recorded the fault.", "", "", 0, "",
                        sample["status"], assignees[0], sample["created_at"], sample["source"],
                        "Opened in error. The same leak is already on NL-1102." if sample["status"] == "discarded" else "",
                        "new" if sample["status"] == "discarded" else "",
                        "dispatch@harbourhomes.example" if sample["source"] == "email" else "",
                        "New drainage call" if sample["source"] == "email" else "",
                        "Please attend 2 Dock Lane. The tenant reports a leak under the sink." if sample["source"] == "email" else "",
                    ),
                )
                for user_id in assignees:
                    connection.execute(
                        "INSERT INTO job_engineers (job_id, user_id) VALUES (?, ?)",
                        (job_id, user_id),
                    )
                filename = f"sample-{sample['work_ref'].lower()}.png"
                (PHOTO_DIR / filename).write_bytes(_solid_png(640, 360, sample["color"]))
                connection.execute(
                    "INSERT INTO photos (id, job_id, filename, created_at) VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), job_id, filename, sample["created_at"]),
                )
            first = connection.execute("SELECT id FROM jobs WHERE work_ref = 'NL-1102'").fetchone()
            if first:
                connection.execute(
                    "INSERT INTO job_messages (id, job_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), first["id"], alex_id, "On site. The trap is loose and I will need a return visit.", "2026-10-02T09:40:00+00:00"),
                )

    def _ensure_sam(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT id FROM users WHERE email = ?", ("sam@sitelog.demo",)).fetchone()
        if row:
            return
        connection.execute(
            "INSERT INTO users (id, email, name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "sam@sitelog.demo", "Sam Reed", hash_password("engineer123"), "engineer"),
        )

    def _sample(self, ref: str, status: str, assignees: list[str], date: str, client: str, address: str, issue: str, source: str) -> dict[str, Any]:
        colors = {"new": (99, 102, 241), "in_progress": (16, 185, 129), "completed": (244, 114, 182), "discarded": (148, 163, 184)}
        return {
            "work_ref": ref,
            "status": status,
            "assignees": assignees,
            "job_date": date,
            "client": client,
            "site_address": address,
            "issue": issue,
            "source": source,
            "created_at": "2026-10-02T09:00:00+00:00",
            "color": colors[status],
        }

    def authenticate(self, email: str, password: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if row is None or row["password_hash"] != hash_password(password):
            return None
        return self._user(row)

    def user_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user(row) if row else None

    def list_engineers(self) -> list[dict[str, Any]]:
        with self._db() as connection:
            rows = connection.execute("SELECT * FROM users WHERE role = 'engineer' ORDER BY name").fetchall()
        return [self._user(row) for row in rows]

    def create_engineer(self, name: str, email: str, phone: str, password: str) -> dict[str, Any]:
        name = name.strip()
        email = email.strip().lower()
        phone = phone.strip()
        if not name or not email or not phone or not password:
            raise ValueError("Name, email, phone, and password are required.")
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        if sum(character.isdigit() for character in phone) < 6:
            raise ValueError("Enter a phone number with at least 6 digits.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._db() as connection:
            taken = connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if taken is not None:
                raise ValueError("That email is already used.")
            user_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO users (id, email, name, password_hash, role, phone) VALUES (?, ?, ?, ?, 'engineer', ?)",
                (user_id, email, name, hash_password(password), phone),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._user(row)

    def _user(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "phone": row["phone"] if "phone" in keys and row["phone"] else "",
        }

    def can_access(self, user: dict[str, Any], job: dict[str, Any]) -> bool:
        if user["role"] == "office":
            return True
        return user["id"] in {person["id"] for person in job["assignees"]}

    def list_jobs(self, user: dict[str, Any], status: Optional[str] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM jobs"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self._db() as connection:
            rows = connection.execute(query, params).fetchall()
            jobs = [self._job(connection, row) for row in rows]
        if user["role"] != "office":
            jobs = [job for job in jobs if self.can_access(user, job)]
        if limit is not None:
            jobs = jobs[:limit]
        return jobs

    def counts(self, user: dict[str, Any]) -> dict[str, int]:
        jobs = self.list_jobs(user)
        return {status: sum(1 for job in jobs if job["status"] == status) for status in STATUSES}

    def job_by_work_ref(self, work_ref: str) -> Optional[dict[str, Any]]:
        key = work_ref.strip().lower()
        if not key:
            return None
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE lower(work_ref) = ? ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            return self._job(connection, row) if row else None

    def save_job(self, data: dict[str, Any], assignee_ids: Optional[list[str]], source: str = "form") -> dict[str, Any]:
        work_ref = str(data.get("work_ref") or "").strip()
        existing = self.job_by_work_ref(work_ref)
        if existing is None:
            return self.create_job(data, assignee_ids or [], source)
        payload = dict(data)
        payload["work_ref"] = existing["work_ref"]
        if source == "email":
            payload["source"] = "email"
            if (existing.get("site_address") or "").strip() not in {"", "From email"}:
                payload.pop("site_address", None)
            for field in (
                "job_date", "arrival_time", "departure_time", "findings", "materials",
                "part_number", "recommended_works", "is_complete",
            ):
                payload.pop(field, None)
            return self.update_job(existing["id"], payload, None) or existing
        return self.update_job(existing["id"], payload, assignee_ids) or existing

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return self._job(connection, row)

    def create_job(self, data: dict[str, Any], assignee_ids: list[str], source: str = "form") -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        created_at = _now()
        clean_ids = self._valid_engineer_ids(assignee_ids)
        # new = unassigned, in_progress/pending = assigned, completed = report submitted
        if _as_bool(data.get("is_complete")) or data.get("status") == "completed":
            status = "completed"
        elif clean_ids:
            status = "in_progress"
        else:
            status = "new"
        is_complete = 1 if status == "completed" else 0
        with self._db() as connection:
            serial = connection.execute("SELECT COALESCE(MAX(serial_number), 0) + 1 AS n FROM jobs").fetchone()["n"]
            connection.execute(
                """INSERT INTO jobs (
                    id, serial_number, work_ref, job_date, client, site_address,
                    arrival_time, departure_time, engineers, issue, findings,
                    materials, part_number, is_complete, recommended_works,
                    status, engineer_id, created_at, source, discard_reason,
                    status_before_discard, email_from, email_subject, email_body, email_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)""",
                (
                    job_id, serial,
                    str(data.get("work_ref") or "").strip(),
                    str(data.get("job_date") or "").strip(),
                    str(data.get("client") or "").strip(),
                    str(data.get("site_address") or "").strip(),
                    str(data.get("arrival_time") or "").strip(),
                    str(data.get("departure_time") or "").strip(),
                    "",
                    str(data.get("issue") or "").strip(),
                    str(data.get("findings") or "").strip(),
                    str(data.get("materials") or "").strip(),
                    str(data.get("part_number") or "").strip(),
                    is_complete,
                    str(data.get("recommended_works") or "").strip(),
                    status,
                    clean_ids[0] if clean_ids else "",
                    created_at,
                    source,
                    str(data.get("email_from") or ""),
                    str(data.get("email_subject") or ""),
                    str(data.get("email_body") or ""),
                    str(data.get("email_message_id") or ""),
                ),
            )
            for user_id in clean_ids:
                connection.execute("INSERT INTO job_engineers (job_id, user_id) VALUES (?, ?)", (job_id, user_id))
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._job(connection, row)

    def update_job(self, job_id: str, data: dict[str, Any], assignee_ids: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        fields = [
            "work_ref", "job_date", "client", "site_address", "arrival_time", "departure_time",
            "issue", "findings", "materials", "part_number", "recommended_works",
        ]
        assignments = []
        values: list[Any] = []
        for field in fields:
            if field in data:
                assignments.append(f"{field} = ?")
                values.append(str(data.get(field) or "").strip())
        if "is_complete" in data and "status" not in data:
            data = dict(data)
            data["status"] = "completed" if _as_bool(data.get("is_complete")) else "in_progress"
        if "status" in data and data["status"] == "completed":
            data = dict(data)
            data["is_complete"] = True
        if "is_complete" in data:
            assignments.append("is_complete = ?")
            values.append(1 if _as_bool(data.get("is_complete")) else 0)
        if "status" in data and data["status"] in {"new", "in_progress", "completed"}:
            assignments.append("status = ?")
            values.append(data["status"])
            if data["status"] != "completed" and "is_complete" not in data:
                assignments.append("is_complete = ?")
                values.append(0)
        for field in ("source", "email_from", "email_subject", "email_body", "email_message_id"):
            if field in data:
                assignments.append(f"{field} = ?")
                values.append(str(data.get(field) or "").strip())
        with self._db() as connection:
            if assignments:
                values.append(job_id)
                connection.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values)
            if assignee_ids is not None:
                clean_ids = self._valid_engineer_ids(assignee_ids)
                connection.execute("DELETE FROM job_engineers WHERE job_id = ?", (job_id,))
                for user_id in clean_ids:
                    connection.execute("INSERT INTO job_engineers (job_id, user_id) VALUES (?, ?)", (job_id, user_id))
                connection.execute(
                    "UPDATE jobs SET engineer_id = ? WHERE id = ?",
                    (clean_ids[0] if clean_ids else "", job_id),
                )
                current = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if current is not None and current["status"] != "discarded" and current["status"] != "completed":
                    next_status = "in_progress" if clean_ids else "new"
                    connection.execute(
                        "UPDATE jobs SET status = ?, is_complete = 0 WHERE id = ?",
                        (next_status, job_id),
                    )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._job(connection, row) if row else None

    def discard_job(self, job_id: str, reason: str) -> Optional[dict[str, Any]]:
        cleaned = reason.strip()
        if not cleaned:
            return self.get_job(job_id)
        with self._db() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["status"] == "discarded":
                return self.get_job(job_id)
            connection.execute(
                "UPDATE jobs SET status_before_discard = ?, status = 'discarded', discard_reason = ? WHERE id = ?",
                (row["status"], cleaned, job_id),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._job(connection, updated)

    def restore_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT status, status_before_discard FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["status"] != "discarded":
                return self.get_job(job_id) if row else None
            previous = row["status_before_discard"] if row["status_before_discard"] in {"new", "in_progress", "completed"} else "new"
            connection.execute(
                "UPDATE jobs SET status = ?, discard_reason = '', status_before_discard = '' WHERE id = ?",
                (previous, job_id),
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._job(connection, updated)

    def chat_threads(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        jobs = self.list_jobs(user)
        # Engineers only see pending (assigned) jobs — not new/unassigned or completed.
        if user.get("role") == "engineer":
            jobs = [job for job in jobs if job.get("status") == "in_progress"]
        previews = self._latest_messages([job["id"] for job in jobs])
        for job in jobs:
            preview = previews.get(job["id"]) or {}
            job["last_body"] = preview.get("body") or ""
            job["last_at"] = preview.get("created_at") or ""
        jobs.sort(key=lambda job: job["last_at"] or job.get("created_at") or "", reverse=True)
        return jobs

    def _latest_messages(self, job_ids: list[str]) -> dict[str, dict[str, str]]:
        if not job_ids:
            return {}
        marks = ",".join("?" for _ in job_ids)
        with self._db() as connection:
            rows = connection.execute(
                f"""SELECT job_id, body, created_at FROM job_messages
                    WHERE rowid IN (
                        SELECT MAX(rowid) FROM job_messages
                        WHERE job_id IN ({marks})
                        GROUP BY job_id
                    )""",
                job_ids,
            ).fetchall()
        return {row["job_id"]: {"body": row["body"], "created_at": row["created_at"]} for row in rows}

    def count_messages(self, job_id: str) -> int:
        with self._db() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM job_messages WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row["total"]) if row else 0

    def has_messages_before(self, job_id: str, created_at: str) -> bool:
        with self._db() as connection:
            row = connection.execute(
                "SELECT 1 FROM job_messages WHERE job_id = ? AND created_at < ? LIMIT 1",
                (job_id, created_at),
            ).fetchone()
        return bool(row)

    def list_messages(
        self,
        job_id: str,
        limit: Optional[int] = None,
        before_created_at: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with self._db() as connection:
            if limit is not None:
                if before_created_at:
                    query = """
                        SELECT * FROM (
                            SELECT m.id, m.author_id, m.body, m.created_at, u.name AS author_name, u.role AS author_role
                            FROM job_messages m JOIN users u ON u.id = m.author_id
                            WHERE m.job_id = ? AND m.created_at < ?
                            ORDER BY m.created_at DESC, m.rowid DESC
                            LIMIT ?
                        ) ORDER BY created_at ASC
                    """
                    rows = connection.execute(query, (job_id, before_created_at, int(limit))).fetchall()
                else:
                    query = """
                        SELECT * FROM (
                            SELECT m.id, m.author_id, m.body, m.created_at, u.name AS author_name, u.role AS author_role
                            FROM job_messages m JOIN users u ON u.id = m.author_id
                            WHERE m.job_id = ?
                            ORDER BY m.created_at DESC, m.rowid DESC
                            LIMIT ?
                        ) ORDER BY created_at ASC
                    """
                    rows = connection.execute(query, (job_id, int(limit))).fetchall()
            else:
                rows = connection.execute(
                    """SELECT m.id, m.author_id, m.body, m.created_at, u.name AS author_name, u.role AS author_role
                       FROM job_messages m JOIN users u ON u.id = m.author_id
                       WHERE m.job_id = ? ORDER BY m.created_at ASC, m.rowid ASC""",
                    (job_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, job_id: str, author_id: str, body: str) -> dict[str, Any]:
        message_id = str(uuid.uuid4())
        created_at = _now()
        cleaned = body.strip()
        with self._db() as connection:
            connection.execute(
                "INSERT INTO job_messages (id, job_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, job_id, author_id, cleaned, created_at),
            )
            row = connection.execute(
                """SELECT m.id, m.author_id, m.body, m.created_at, u.name AS author_name, u.role AS author_role
                   FROM job_messages m JOIN users u ON u.id = m.author_id
                   WHERE m.id = ?""",
                (message_id,),
            ).fetchone()
        if row is not None:
            return dict(row)
        return {
            "id": message_id,
            "author_id": author_id,
            "author_name": "",
            "author_role": "",
            "body": cleaned,
            "created_at": created_at,
        }

    def add_photo(self, job_id: str, filename: str) -> dict[str, str]:
        photo_id = str(uuid.uuid4())
        with self._db() as connection:
            connection.execute(
                "INSERT INTO photos (id, job_id, filename, created_at) VALUES (?, ?, ?, ?)",
                (photo_id, job_id, filename, _now()),
            )
        return {"id": photo_id, "filename": filename, "url": f"/photos/{filename}"}

    def delete_photo(self, job_id: str, photo_id: str) -> bool:
        with self._db() as connection:
            row = connection.execute(
                "SELECT filename FROM photos WHERE id = ? AND job_id = ?",
                (photo_id, job_id),
            ).fetchone()
            if row is None:
                return False
            filename = str(row["filename"] or "")
            connection.execute("DELETE FROM photos WHERE id = ? AND job_id = ?", (photo_id, job_id))
        if filename and "/" not in filename and "\\" not in filename and ".." not in filename:
            path = PHOTO_DIR / filename
            if path.is_file():
                path.unlink()
        return True

    def email_seen(self, message_id: str) -> bool:
        with self._db() as connection:
            row = connection.execute("SELECT 1 FROM seen_emails WHERE message_id = ?", (message_id,)).fetchone()
        return row is not None

    def remember_email(self, message_id: str, job_id: str) -> None:
        with self._db() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO seen_emails (message_id, job_id, seen_at) VALUES (?, ?, ?)",
                (message_id, job_id, _now()),
            )

    def get_mailbox(self) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT host, port, email, last_checked, jobs_created, last_error FROM mailbox WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "host": row["host"],
            "port": row["port"],
            "email": row["email"],
            "configured": True,
            "last_checked": row["last_checked"] or "",
            "jobs_created": int(row["jobs_created"] or 0),
            "last_error": row["last_error"] or "",
        }

    def record_mailbox_poll(self, created: int, error: str) -> None:
        with self._db() as connection:
            row = connection.execute("SELECT jobs_created FROM mailbox WHERE id = 1").fetchone()
            if row is None:
                return
            total = int(row["jobs_created"] or 0) + max(0, int(created))
            connection.execute(
                "UPDATE mailbox SET last_checked = ?, jobs_created = ?, last_error = ? WHERE id = 1",
                (_now(), total, (error or "")[:500]),
            )

    def delete_mailbox(self) -> None:
        with self._db() as connection:
            connection.execute("DELETE FROM mailbox WHERE id = 1")

    def job_for_photo(self, filename: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT job_id FROM photos WHERE filename = ?", (filename,)).fetchone()
            if row is None:
                return None
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)).fetchone()
            return self._job(connection, job) if job else None

    def _migrate_mailbox_password(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT password FROM mailbox WHERE id = 1").fetchone()
        if row is None:
            return
        stored = row["password"] or ""
        if not stored or _is_encrypted(stored):
            return
        connection.execute("UPDATE mailbox SET password = ? WHERE id = 1", (encrypt_secret(stored),))

    def mailbox_secret(self) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute("SELECT host, port, email, password FROM mailbox WHERE id = 1").fetchone()
        if row is None:
            return None
        secret = dict(row)
        secret["password"] = decrypt_secret(secret.get("password") or "")
        return secret

    def save_mailbox(self, host: str, port: int, email: str, password: str) -> dict[str, Any]:
        with self._db() as connection:
            current = connection.execute("SELECT password FROM mailbox WHERE id = 1").fetchone()
            stored_password = encrypt_secret(password or (current["password"] if current else ""))
            connection.execute(
                """INSERT INTO mailbox (id, host, port, email, password) VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET host = excluded.host, port = excluded.port,
                   email = excluded.email, password = excluded.password""",
                (host.strip(), port, email.strip(), stored_password),
            )
        return {"host": host.strip(), "port": port, "email": email.strip(), "configured": True}

    def _valid_engineer_ids(self, assignee_ids: list[str]) -> list[str]:
        wanted = []
        for raw in assignee_ids:
            value = str(raw or "").strip()
            if value and value not in wanted:
                wanted.append(value)
        if not wanted:
            return []
        with self._db() as connection:
            rows = connection.execute(
                f"SELECT id FROM users WHERE role = 'engineer' AND id IN ({','.join('?' for _ in wanted)})",
                wanted,
            ).fetchall()
        allowed = {row["id"] for row in rows}
        return [item for item in wanted if item in allowed]

    def _job(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        photos = connection.execute(
            "SELECT id, filename FROM photos WHERE job_id = ? ORDER BY created_at",
            (row["id"],),
        ).fetchall()
        assignees = connection.execute(
            """SELECT u.id, u.name, u.email, u.role FROM job_engineers je
               JOIN users u ON u.id = je.user_id WHERE je.job_id = ? ORDER BY u.name""",
            (row["id"],),
        ).fetchall()
        people = [self._user(person) for person in assignees]
        keys = row.keys()
        status = row["status"] if row["status"] in STATUSES else "new"
        return {
            "id": row["id"],
            "serial_number": row["serial_number"],
            "work_ref": row["work_ref"],
            "job_date": row["job_date"],
            "client": row["client"],
            "site_address": row["site_address"],
            "arrival_time": row["arrival_time"],
            "departure_time": row["departure_time"],
            "hours_worked": _calc_hours_worked(row["arrival_time"], row["departure_time"]),
            "engineers": ", ".join(person["name"] for person in people),
            "issue": row["issue"],
            "findings": row["findings"],
            "materials": row["materials"],
            "part_number": row["part_number"],
            "is_complete": bool(row["is_complete"]),
            "recommended_works": row["recommended_works"],
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "engineer_id": row["engineer_id"],
            "assignees": people,
            "created_at": row["created_at"],
            "source": row["source"] if "source" in keys and row["source"] else "form",
            "discard_reason": row["discard_reason"] if "discard_reason" in keys else "",
            "email_from": row["email_from"] if "email_from" in keys else "",
            "email_subject": row["email_subject"] if "email_subject" in keys else "",
            "email_body": row["email_body"] if "email_body" in keys else "",
            "photos": [
                {"id": photo["id"], "filename": photo["filename"], "url": f"/photos/{photo['filename']}"}
                for photo in photos
            ],
        }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


store = Store()
