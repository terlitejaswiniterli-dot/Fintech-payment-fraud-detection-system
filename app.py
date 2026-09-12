"""
app.py — Layer 2 "API Gateway" + orchestration of every other layer.

POST /api/payments
    Validates the request, enforces idempotency, runs inline fraud
    screening, applies the risk policy, and either authorizes (-> ledger
    -> settlement) or requires step-up or declines.

POST /api/payments/<tx_id>/verify-otp
    Completes the STEP_UP path for medium-risk transactions.

GET  /api/transactions
    Ops view of the ledger (dashboard).

POST /api/admin/outage, POST /api/admin/reconcile-now, GET /api/admin/status
    Demo controls so judges can watch Layer 8 (settlement failure +
    recovery/reconciliation) happen live.
"""
import os
import random
import string
import sys
import time

from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(__file__))
import db
import idempotency
import fraud_engine
import risk_policy
import ledger
import settlement
import reconciliation

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


# ---------------------------------------------------------------- frontend --
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "Index.html")


# ------------------------------------------------------------------ users --
@app.route("/api/users")
def list_users():
    """Return users for the recipient picker, with optional name search."""
    search = (request.args.get("search") or "").strip()
    conn = db.get_conn()
    if search:
        like = f"%{search}%"
        rows = conn.execute(
            "SELECT user_id, name, avg_amount, tx_count FROM users "
            "WHERE name LIKE ? OR user_id LIKE ? ORDER BY name LIMIT 20",
            (like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT user_id, name, avg_amount, tx_count FROM users ORDER BY name LIMIT 50"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# --------------------------------------------------------------- payments --
@app.route("/api/payments", methods=["POST"])
def create_payment():
    idem_key = request.headers.get("Idempotency-Key")
    if not idem_key:
        return jsonify({"error": "Idempotency-Key header is required"}), 400

    payload = request.get_json(force=True, silent=True) or {}
    required = ["user_id", "amount", "device_id", "location", "merchant"]
    missing = [f for f in required if f not in payload]
    if missing:
        return jsonify({"error": f"missing fields: {missing}"}), 400

    # ---- Layer 3: Idempotency check -----------------------------------
    status, cached = idempotency.reserve_key(idem_key, payload)
    if status == "duplicate":
        body = dict(cached["body"])
        body["_replayed"] = True
        return jsonify(body), cached["status_code"]
    if status == "conflict":
        return jsonify({"error": "Idempotency-Key reused with a different request body"}), 409
    if status == "in_flight":
        return jsonify({"error": "This request is already being processed"}), 409

    # ---- fetch user context ---------------------------------------------
    conn = db.get_conn()
    user_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (payload["user_id"],)).fetchone()
    conn.close()
    if user_row is None:
        resp = {"error": "unknown user_id"}
        idempotency.store_result(idem_key, 404, resp)
        return jsonify(resp), 404

    amount = float(payload["amount"])

    # ---- Layers 4 & 5: inline fraud screening + hybrid fraud engine ------
    risk_score, factors, features = fraud_engine.score_transaction(
        user_row, amount, payload["device_id"], payload["location"], payload["merchant"]
    )

    # ---- Layer 6: risk + policy engine ------------------------------------
    decision = risk_policy.decide(risk_score)

    otp_code = None
    if decision == "STEP_UP":
        otp_code = "".join(random.choices(string.digits, k=6))

    tx_id, state = ledger.create_transaction(
        idem_key, payload["user_id"], amount, payload["device_id"], payload["location"],
        payload["merchant"], risk_score, decision, otp_code,
    )
    ledger.log_fraud_events(tx_id, factors)

    # ---- Layer 7: authorization -> ledger, then attempt settlement -------
    if state == "AUTHORIZED":
        _authorize_and_settle(tx_id)
        tx = ledger.get_transaction(tx_id)
        state = tx["state"]

    response_body = {
        "tx_id": tx_id,
        "decision": decision,
        "risk_score": risk_score,
        "state": state,
        "explanation": [{"factor": f[0], "contribution": f[1], "detail": f[2]} for f in factors],
    }
    if decision == "STEP_UP":
        # DEMO ONLY: a real system sends this via SMS/push, never in the API response.
        response_body["demo_otp"] = otp_code

    status_code = 200 if decision != "DECLINE" else 402
    idempotency.store_result(idem_key, status_code, response_body)
    return jsonify(response_body), status_code


def _authorize_and_settle(tx_id):
    """Move AUTHORIZED -> PENDING_SETTLEMENT, attempt settlement once immediately.
    If it fails here, it's left in PENDING_SETTLEMENT for the background
    reconciliation loop (Layer 8) to pick up and retry."""
    ledger.update_state(tx_id, "PENDING_SETTLEMENT")
    ledger.bump_settlement_attempt(tx_id)
    ok = settlement.attempt_settlement(tx_id)
    if ok:
        ledger.update_state(tx_id, "SETTLED")
    # else: stays PENDING_SETTLEMENT; reconciliation loop will retry it.


@app.route("/api/payments/<tx_id>/verify-otp", methods=["POST"])
def verify_otp(tx_id):
    payload = request.get_json(force=True, silent=True) or {}
    code = payload.get("otp")

    tx = ledger.get_transaction(tx_id)
    if tx is None:
        return jsonify({"error": "unknown transaction"}), 404
    if tx["state"] != "STEP_UP_REQUIRED":
        return jsonify({"error": f"transaction is in state {tx['state']}, not awaiting step-up"}), 409

    if code == tx["otp_code"]:
        ledger.update_state(tx_id, "AUTHORIZED", extra={"otp_verified": 1})
        _authorize_and_settle(tx_id)
        tx = ledger.get_transaction(tx_id)
        return jsonify({"tx_id": tx_id, "result": "VERIFIED", "state": tx["state"]})
    else:
        ledger.update_state(tx_id, "DECLINED")
        return jsonify({"tx_id": tx_id, "result": "OTP_MISMATCH", "state": "DECLINED"}), 401


# ----------------------------------------------------------------- ledger --
@app.route("/api/transactions")
def list_transactions():
    rows = ledger.list_transactions(limit=200)
    return jsonify([dict(r) for r in rows])


@app.route("/api/transactions/<tx_id>")
def get_transaction(tx_id):
    tx = ledger.get_transaction(tx_id)
    if tx is None:
        return jsonify({"error": "not found"}), 404
    events = ledger.get_fraud_events(tx_id)
    body = dict(tx)
    body["explanation"] = [dict(e) for e in events]
    return jsonify(body)


# ------------------------------------------------------------------ admin --
@app.route("/api/admin/status")
def admin_status():
    return jsonify({
        "outage": settlement.get_outage_state(),
        "recent_events": reconciliation.get_recent_events(),
        "server_time": time.time(),
    })


@app.route("/api/admin/outage", methods=["POST"])
def admin_outage():
    payload = request.get_json(force=True, silent=True) or {}
    settlement.set_outage(bool(payload.get("force", False)))
    return jsonify(settlement.get_outage_state())


@app.route("/api/admin/reconcile-now", methods=["POST"])
def admin_reconcile_now():
    results = reconciliation.reconcile_once()
    return jsonify({"results": [{"tx_id": t, "outcome": o} for t, o in results]})


if __name__ == "__main__":
    db.init_db()
    reconciliation.start_background_reconciliation()
    app.run(host="0.0.0.0", port=5050, debug=False)