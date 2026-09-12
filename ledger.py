"""
ledger.py — Layer 7: "Authorization -> Ledger" and the state machine that
underpins Layer 8 (settlement failure handling / recovery).

State machine:

  CREATED
     |
     v
  (fraud screening) -- DECLINE --------------------> DECLINED
     |
     +-- STEP_UP_REQUIRED --(otp fail/timeout)------> DECLINED
     |         |
     |     (otp ok)
     v         v
  AUTHORIZED
     |
     v
  PENDING_SETTLEMENT --(settlement network fails)--> PENDING_SETTLEMENT (retried)
     |                                                    |
     | (settlement succeeds, incl. after retry)           | (max retries exceeded)
     v                                                    v
  SETTLED                                              FAILED (needs manual reconciliation)
"""
from db import get_conn, new_id, now


def create_transaction(idempotency_key, user_id, amount, device_id, location, merchant,
                        risk_score, decision, otp_code=None):
    tx_id = new_id("tx")
    state = {
        "APPROVE": "AUTHORIZED",
        "STEP_UP": "STEP_UP_REQUIRED",
        "DECLINE": "DECLINED",
    }[decision]

    conn = get_conn()
    conn.execute(
        """INSERT INTO ledger
           (tx_id, idempotency_key, user_id, amount, device_id, location, merchant,
            risk_score, decision, otp_code, otp_verified, state, settlement_attempts,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,0,?,0,?,?)""",
        (tx_id, idempotency_key, user_id, amount, device_id, location, merchant,
         risk_score, decision, otp_code, state, now(), now()),
    )
    # Update user's rolling average / tx count so later demo transactions see fresh behavior.
    conn.execute(
        """UPDATE users SET
             avg_amount = (avg_amount * tx_count + ?) / (tx_count + 1),
             tx_count = tx_count + 1,
             known_devices = CASE WHEN instr(','||known_devices||',', ','||?||',') > 0
                              THEN known_devices ELSE trim(known_devices || ',' || ?, ',') END,
             known_locations = CASE WHEN instr(','||known_locations||',', ','||?||',') > 0
                                THEN known_locations ELSE trim(known_locations || ',' || ?, ',') END
           WHERE user_id = ?""",
        (amount, device_id, device_id, location, location, user_id),
    )
    conn.commit()
    conn.close()
    return tx_id, state


def get_transaction(tx_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM ledger WHERE tx_id = ?", (tx_id,)).fetchone()
    conn.close()
    return row


def update_state(tx_id, new_state, extra=None):
    conn = get_conn()
    if extra and "otp_verified" in extra:
        conn.execute(
            "UPDATE ledger SET state = ?, otp_verified = ?, updated_at = ? WHERE tx_id = ?",
            (new_state, extra["otp_verified"], now(), tx_id),
        )
    else:
        conn.execute(
            "UPDATE ledger SET state = ?, updated_at = ? WHERE tx_id = ?",
            (new_state, now(), tx_id),
        )
    conn.commit()
    conn.close()


def bump_settlement_attempt(tx_id):
    conn = get_conn()
    conn.execute(
        "UPDATE ledger SET settlement_attempts = settlement_attempts + 1, updated_at = ? WHERE tx_id = ?",
        (now(), tx_id),
    )
    conn.commit()
    conn.close()


def list_transactions(limit=100):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM ledger ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


def log_fraud_events(tx_id, factors):
    conn = get_conn()
    conn.executemany(
        "INSERT INTO fraud_events (tx_id, factor, contribution, detail) VALUES (?,?,?,?)",
        [(tx_id, f[0], f[1], f[2]) for f in factors],
    )
    conn.commit()
    conn.close()


def get_fraud_events(tx_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT factor, contribution, detail FROM fraud_events WHERE tx_id = ? ORDER BY id", (tx_id,)
    ).fetchall()
    conn.close()
    return rows