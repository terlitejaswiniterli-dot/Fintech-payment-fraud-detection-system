"""
settlement.py — simulates the external settlement network / rails.

In a real system this would call out to a card network, UPI switch, or
banking partner. Here it's simulated so the demo can deliberately trigger
Layer 8 ("Settlement Failure Handling") on command from the ops dashboard,
via `force_outage`.
"""
import random

# Toggled from the ops dashboard to force settlement failures on demand,
# so judges can *watch* PENDING_SETTLEMENT -> reconciliation -> SETTLED happen live.
_state = {"force_outage": False, "baseline_failure_rate": 0.15}


def set_outage(force: bool):
    _state["force_outage"] = force


def get_outage_state():
    return dict(_state)


def attempt_settlement(tx_id: str) -> bool:
    """Returns True if settlement succeeded, False if it failed (network/partner issue)."""
    if _state["force_outage"]:
        return False
    return random.random() > _state["baseline_failure_rate"]