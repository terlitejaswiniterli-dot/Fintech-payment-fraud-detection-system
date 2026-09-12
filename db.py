"""
db.py — single SQLite database shared by every layer of the architecture.

Tables:
  users            demo users with "known" devices/locations + spending history
  idempotency_keys exactly-once request cache
  ledger           the transaction ledger / state machine
  fraud_events     explainability trail (which rules/model fired, per transaction)
"""
import sqlite3
import os
import threading
import time
import uuid

DB_PATH = os.path.join(os.path.dirname(__file__), "fraudguard.db")
_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    known_devices TEXT,      -- comma separated device ids seen before
    known_locations TEXT,    -- comma separated city codes seen before
    avg_amount REAL DEFAULT 0,
    tx_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    status_code INTEGER,
    response_body TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS ledger (
    tx_id TEXT PRIMARY KEY,
    idempotency_key TEXT,
    user_id TEXT,
    amount REAL,
    device_id TEXT,
    location TEXT,
    merchant TEXT,
    risk_score REAL,
    decision TEXT,             -- APPROVE / STEP_UP / DECLINE
    otp_code TEXT,
    otp_verified INTEGER DEFAULT 0,
    state TEXT,                -- CREATED, STEP_UP_REQUIRED, DECLINED,
                                -- AUTHORIZED, PENDING_SETTLEMENT, SETTLED, FAILED
    settlement_attempts INTEGER DEFAULT 0,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS fraud_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id TEXT,
    factor TEXT,
    contribution REAL,
    detail TEXT
);
"""

DEMO_USERS = [
    # user_id, name, known_devices, known_locations, avg_amount, tx_count
    ("u_asha", "Asha Rao", "dev-iphone-14", "BLR,HYD", 4200.0, 38),
    ("u_karthik", "Karthik Iyer", "dev-pixel-8", "CHN", 1800.0, 21),
    ("u_new", "Priya", "", "", 0.0, 0),
    ("u_rahul", "Rahul Sharma", "dev-samsung-s24", "BLR", 3200.0, 17),
    ("u_sneha", "Sneha Reddy", "dev-oneplus-12", "HYD", 2600.0, 24),
    ("u_arjun", "Arjun Kumar", "dev-iphone-13", "CHN,BLR", 5100.0, 31),
    ("u_ananya", "Ananya Singh", "dev-pixel-7", "DEL", 2900.0, 19),
    ("u_vikram", "Vikram Rao", "dev-nothing-2", "BLR", 6400.0, 44),
    ("u_neha", "Neha Patel", "dev-samsung-a55", "MUM", 2200.0, 15),
    ("u_rohit", "Rohit Verma", "dev-realme-gt", "HYD,BLR", 3700.0, 27),
    ("u_meera", "Meera Iyer", "dev-iphone-15", "CHN", 4500.0, 35),
    ("u_aditya", "Aditya Nair", "dev-pixel-8a", "BLR", 3300.0, 22),
]


def init_db():
    with _lock:
        conn = get_conn()
        conn.executescript(SCHEMA)
        # Seed any missing demo users. This also upgrades an existing DB that
        # was created with the original 3-user prototype.
        conn.executemany(
            "INSERT OR IGNORE INTO users (user_id, name, known_devices, known_locations, avg_amount, tx_count) "
            "VALUES (?,?,?,?,?,?)",
            DEMO_USERS,
        )
        conn.commit()
        conn.close()


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now():
    return time.time()