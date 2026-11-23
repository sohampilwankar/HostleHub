"""SQLite data layer for HostelHub.

Everything that touches the database schema, policies or room occupancy
lives here so the rules are enforced in exactly one place.
"""
import json
import sqlite3
from datetime import datetime

from flask import current_app, g
from werkzeug.security import generate_password_hash

ROOM_TYPES = ["Single", "Double", "Triple"]
ACADEMIC_YEARS = [1, 2, 3, 4]
COMPLAINT_STATUSES = ["Submitted", "In Progress", "Resolved"]
ACTIVE_APPLICATION_STATUSES = ("Pending", "Approved")

DEFAULT_ADMIN = ("admin", "admin123")
DEMO_EMAIL_DOMAIN = "demo.hostelhub.local"
DEMO_PASSWORD = "student123"

# policy_name -> default value (all stored as TEXT in the policies table)
DEFAULT_POLICIES = {
    "max_room_capacity": "3",   # hard ceiling on beds per room
    "priority_year": "1",       # 0 = no priority year
    "applications_open": "Open",  # Open / Closed
    "min_year": "1",            # minimum academic year allowed to apply
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    email        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password     TEXT    NOT NULL,
    year         INTEGER NOT NULL CHECK (year BETWEEN 1 AND 4),
    student_code TEXT,             -- Student ID / enrollment number (unique index below)
    created_at   TEXT              -- registration timestamp
);

CREATE TABLE IF NOT EXISTS admins (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT    NOT NULL,
    block       TEXT    NOT NULL,
    room_type   TEXT    NOT NULL CHECK (room_type IN ('Single', 'Double', 'Triple')),
    capacity    INTEGER NOT NULL CHECK (capacity >= 1),
    occupied    INTEGER NOT NULL DEFAULT 0 CHECK (occupied >= 0 AND occupied <= capacity),
    UNIQUE (block, room_number)
);

CREATE TABLE IF NOT EXISTS applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    preferences      TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'Pending'
                     CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Allocated')),
    application_date TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id      INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    allocation_date TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Vacated'))
);

CREATE TABLE IF NOT EXISTS complaints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject     TEXT    NOT NULL,
    description TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'Submitted'
                CHECK (status IN ('Submitted', 'In Progress', 'Resolved')),
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_name  TEXT NOT NULL UNIQUE,
    policy_value TEXT NOT NULL
);

-- A student may hold at most one active allocation and one active application.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_allocation
    ON allocations(student_id) WHERE status = 'Active';
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_application
    ON applications(student_id) WHERE status IN ('Pending', 'Approved');
"""


class RuleError(Exception):
    """Raised when an operation would break a hostel rule."""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path):
    """Create tables, default policies and the default admin (idempotent)."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate_students(conn)
        for name, value in DEFAULT_POLICIES.items():
            conn.execute(
                "INSERT OR IGNORE INTO policies (policy_name, policy_value) VALUES (?, ?)",
                (name, value),
            )
        created_admin = False
        if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO admins (username, password) VALUES (?, ?)",
                (DEFAULT_ADMIN[0], generate_password_hash(DEFAULT_ADMIN[1])),
            )
            created_admin = True
        conn.commit()
        return created_admin
    finally:
        conn.close()


def _migrate_students(conn):
    """Add student_code / created_at to databases created before signup existed.

    Existing rows are kept; they get a generated ID (HH0001 style, matching what
    the profile page showed before) and the migration time as created_at.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(students)")}
    if "student_code" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN student_code TEXT")
    if "created_at" not in cols:
        conn.execute("ALTER TABLE students ADD COLUMN created_at TEXT")
    conn.execute("UPDATE students SET student_code = printf('HH%04d', id) "
                 "WHERE student_code IS NULL OR student_code = ''")
    conn.execute("UPDATE students SET created_at = ? WHERE created_at IS NULL", (now(),))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_student_code "
                 "ON students(student_code COLLATE NOCASE)")


# ---------------------------------------------------------------- policies

def get_policies(conn):
    rows = conn.execute("SELECT policy_name, policy_value FROM policies").fetchall()
    values = dict(DEFAULT_POLICIES)
    values.update({r["policy_name"]: r["policy_value"] for r in rows})
    return {
        "max_room_capacity": int(values["max_room_capacity"]),
        "priority_year": int(values["priority_year"]),
        "applications_open": values["applications_open"] == "Open",
        "min_year": int(values["min_year"]),
    }


def save_policies(conn, values):
    for name, value in values.items():
        conn.execute(
            "INSERT INTO policies (policy_name, policy_value) VALUES (?, ?) "
            "ON CONFLICT(policy_name) DO UPDATE SET policy_value = excluded.policy_value",
            (name, str(value)),
        )
    conn.commit()


def effective_capacity(room, policies):
    """Beds that may be filled in a room under the current policy."""
    return min(room["capacity"], policies["max_room_capacity"])


def available_beds(room, policies):
    return max(0, effective_capacity(room, policies) - room["occupied"])


# ------------------------------------------------------------- allocations

def allocate_room(conn, application_id, room_id):
    """Assign a room to an approved application inside one transaction."""
    policies = get_policies(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        app = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if app is None:
            raise RuleError("Application not found.")
        if app["status"] != "Approved":
            raise RuleError("Only approved applications can be assigned a room.")

        has_room = conn.execute(
            "SELECT 1 FROM allocations WHERE student_id = ? AND status = 'Active'",
            (app["student_id"],),
        ).fetchone()
        if has_room:
            raise RuleError("This student already has an active room allocation.")

        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if room is None:
            raise RuleError("Room not found.")
        limit = effective_capacity(room, policies)
        if room["occupied"] >= limit:
            raise RuleError(
                f"Room {room['block']}-{room['room_number']} is full "
                f"({room['occupied']}/{limit} beds under current policy)."
            )

        # Guarded update: only succeeds if the room still has a free bed.
        cur = conn.execute(
            "UPDATE rooms SET occupied = occupied + 1 WHERE id = ? AND occupied < ?",
            (room_id, limit),
        )
        if cur.rowcount != 1:
            raise RuleError("Room became full. Please choose another room.")

        conn.execute(
            "INSERT INTO allocations (student_id, room_id, allocation_date, status) "
            "VALUES (?, ?, ?, 'Active')",
            (app["student_id"], room_id, now()),
        )
        conn.execute(
            "UPDATE applications SET status = 'Allocated' WHERE id = ?", (application_id,)
        )
        conn.commit()
        return room
    except Exception:
        conn.rollback()
        raise


def vacate_allocation(conn, allocation_id):
    """End an active allocation and free the bed inside one transaction."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        alloc = conn.execute(
            "SELECT * FROM allocations WHERE id = ? AND status = 'Active'", (allocation_id,)
        ).fetchone()
        if alloc is None:
            raise RuleError("Active allocation not found.")
        conn.execute("UPDATE allocations SET status = 'Vacated' WHERE id = ?", (allocation_id,))
        conn.execute(
            "UPDATE rooms SET occupied = occupied - 1 WHERE id = ? AND occupied > 0",
            (alloc["room_id"],),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --------------------------------------------------------------- demo data

def seed_demo(path):
    """Insert clearly-marked demo records. Returns False if already seeded."""
    init_db(path)
    conn = connect(path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM students WHERE email LIKE ?", (f"%@{DEMO_EMAIL_DOMAIN}",)
        ).fetchone()
        if exists:
            return False

        pw = generate_password_hash(DEMO_PASSWORD)
        students = [
            ("Aarav Sharma", "aarav", 1),
            ("Priya Patel", "priya", 2),
            ("Rohan Mehta", "rohan", 3),
            ("Sneha Kulkarni", "sneha", 1),
        ]
        ids = {}
        for n, (name, handle, year) in enumerate(students, start=1):
            cur = conn.execute(
                "INSERT INTO students (name, email, password, year, student_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, f"{handle}@{DEMO_EMAIL_DOMAIN}", pw, year, f"DEMO-{n:03d}", now()),
            )
            ids[handle] = cur.lastrowid

        rooms = [
            ("101", "A", "Double", 2), ("102", "A", "Triple", 3), ("103", "A", "Single", 1),
            ("201", "B", "Double", 2), ("202", "B", "Triple", 3),
        ]
        for number, block, rtype, cap in rooms:
            conn.execute(
                "INSERT OR IGNORE INTO rooms (room_number, block, room_type, capacity) "
                "VALUES (?, ?, ?, ?)",
                (number, block, rtype, cap),
            )

        def apply(handle, room_type, block, status):
            year = next(y for _, h, y in students if h == handle)
            prefs = json.dumps({"academic_year": year, "room_type": room_type, "block": block})
            cur = conn.execute(
                "INSERT INTO applications (student_id, preferences, status, application_date) "
                "VALUES (?, ?, ?, ?)",
                (ids[handle], prefs, status, now()),
            )
            return cur.lastrowid

        apply("aarav", "Double", "A", "Pending")
        apply("sneha", "Triple", "", "Pending")
        priya_app = apply("priya", "Double", "B", "Approved")
        conn.execute(
            "INSERT INTO complaints (student_id, subject, description, status, created_at) "
            "VALUES (?, ?, ?, 'Submitted', ?)",
            (ids["rohan"], "Water leakage in washroom",
             "There is a continuous water leak near the washbasin on the 2nd floor of Block A.",
             now()),
        )
        notices = [
            ("[Demo] Welcome to HostelHub",
             "Room applications for the current academic year are now open. "
             "Apply from the 'Apply for Room' page."),
            ("[Demo] Mess timings updated",
             "Breakfast 7:30-9:30, Lunch 12:30-2:00, Dinner 7:30-9:30 from Monday."),
        ]
        for title, desc in notices:
            conn.execute(
                "INSERT INTO notices (title, description, created_at) VALUES (?, ?, ?)",
                (title, desc, now()),
            )
        conn.commit()

        # Allocate Priya through the normal rule-checked path.
        room_b201 = conn.execute(
            "SELECT id FROM rooms WHERE block = 'B' AND room_number = '201'"
        ).fetchone()
        allocate_room(conn, priya_app, room_b201["id"])
        return True
    finally:
        conn.close()
