"""
idempotency.py — Layer 3 in the architecture: "Idempotency Check".

Guarantees exactly-once processing per Idempotency-Key:
  - Same key + same payload  -> replay the cached response (duplicate).
  - Same key + different payload -> 409 conflict (client reused a key wrongly).
  - New key -> caller proceeds to fraud screening / authorization.

Uses a SQLite UNIQUE constraint on idempotency_key as the atomic
check-and-reserve primitive, so two concurrent requests with the same key
can't both "win" the race.
"""
import hashlib
import json
import sqlite3

from db import get_conn, now


def _hash_payload(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def reserve_key(idempotency_key: str, payload: dict):
    """
    Try to atomically reserve this idempotency key.
    Returns one of:
      ("new", None)                      -> proceed, caller must call store_result() later
      ("duplicate", cached_response)     -> replay this response, status code included
      ("conflict", None)                 -> same key, different payload
    """
    req_hash = _hash_payload(payload)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO idempotency_keys (idempotency_key, request_hash, status_code, response_body, created_at) "
            "VALUES (?, ?, NULL, NULL, ?)",
            (idempotency_key, req_hash, now()),
        )
        conn.commit()
        return "new", None
    except sqlite3.IntegrityError:
        # Key already exists -> either still in flight, a genuine duplicate, or a conflict.
        row = conn.execute(
            "SELECT request_hash, status_code, response_body FROM idempotency_keys WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            # Extremely rare race — treat as new.
            return "new", None
        if row["request_hash"] != req_hash:
            return "conflict", None
        if row["status_code"] is None:
            # Another request with this key is still being processed.
            return "in_flight", None
        return "duplicate", {
            "status_code": row["status_code"],
            "body": json.loads(row["response_body"]),
        }
    finally:
        conn.close()


def store_result(idempotency_key: str, status_code: int, body: dict):
    conn = get_conn()
    conn.execute(
        "UPDATE idempotency_keys SET status_code = ?, response_body = ? WHERE idempotency_key = ?",
        (status_code, json.dumps(body), idempotency_key),
    )
    conn.commit()
    conn.close()