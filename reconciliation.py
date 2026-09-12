"""
reconciliation.py — Layer 8's recovery half: "Service Recovers -> Reconciliation -> SETTLED".

A background loop periodically retries every transaction stuck in
PENDING_SETTLEMENT. This is what makes the design fault-tolerant: an
authorized payment is never silently lost if the settlement network hiccups
— it is retried until it settles, or parked as FAILED for manual review
after too many attempts (so it never retries forever).
"""
import threading
import time

import ledger
import settlement

POLL_INTERVAL_SECONDS = 3
MAX_SETTLEMENT_ATTEMPTS = 5

_recent_events = []  # small in-memory log the dashboard can poll for a "live feed"
_events_lock = threading.Lock()


def _log(event: str):
    with _events_lock:
        _recent_events.append({"t": time.time(), "event": event})
        del _recent_events[:-50]


def get_recent_events():
    with _events_lock:
        return list(_recent_events)


def reconcile_once():
    """Run a single reconciliation pass. Returns list of (tx_id, outcome)."""
    results = []
    for row in ledger.list_transactions(limit=500):
        if row["state"] != "PENDING_SETTLEMENT":
            continue
        ledger.bump_settlement_attempt(row["tx_id"])
        attempts = row["settlement_attempts"] + 1
        ok = settlement.attempt_settlement(row["tx_id"])
        if ok:
            ledger.update_state(row["tx_id"], "SETTLED")
            _log(f"{row['tx_id']} SETTLED after reconciliation (attempt {attempts})")
            results.append((row["tx_id"], "SETTLED"))
        elif attempts >= MAX_SETTLEMENT_ATTEMPTS:
            ledger.update_state(row["tx_id"], "FAILED")
            _log(f"{row['tx_id']} marked FAILED after {attempts} settlement attempts — needs manual review")
            results.append((row["tx_id"], "FAILED"))
        else:
            _log(f"{row['tx_id']} settlement attempt {attempts} failed, will retry")
            results.append((row["tx_id"], "RETRY"))
    return results


def _loop(stop_event):
    while not stop_event.is_set():
        try:
            reconcile_once()
        except Exception as e:  # never let the background thread die silently
            _log(f"reconciliation error: {e}")
        stop_event.wait(POLL_INTERVAL_SECONDS)


_stop_event = threading.Event()
_thread = None


def start_background_reconciliation():
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, args=(_stop_event,), daemon=True)
        _thread.start()