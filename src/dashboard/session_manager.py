from __future__ import annotations

from typing import Any

from dashboard.repositories import (
    get_session,
    list_participants,
    list_submissions,
    update_session_status,
    normalize_voting_power
)

class SessionStateError(ValueError):
    pass

VALID_TRANSITIONS = {
    "draft": {"open", "cancelled"},
    "open": {"locked", "cancelled"},
    "locked": {"open", "preprocessing", "processing_ready", "cancelled"},
    "preprocessing": {"processing_ready", "cancelled", "locked"},
    "processing_ready": {"completed", "cancelled"},
    "completed": {"archived"},
    "archived": set(),
    "cancelled": set(),
}

def transition_session(session_id: str, new_status: str) -> None:
    session = get_session(session_id)
    if not session:
        raise SessionStateError(f"Unknown session ID: {session_id}")
    current = session["status"]
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        raise SessionStateError(f"Invalid status transition from {current} to {new_status}")
    if new_status == "locked":
        normalize_voting_power(session_id)
    update_session_status(session_id, new_status)

def completion_summary(session_id: str) -> dict[str, Any]:
    participants = list_participants(session_id)
    submissions = list_submissions(session_id)
    total = len(participants)
    submitted = len({s["participant_id"] for s in submissions})
    submitted_power =  sum(float(p["normalized_voting_power"] or 0) for p in participants if p["status"] == "submitted")
    return {
        "total_participants": total,
        "submitted_participants": submitted,
        "pending_participants": max(total - submitted, 0),
        "completion_ratio": submitted / total if total else 0.0,
        "submitted_normalized_power": submitted_power,
        "ready_basic": submitted > 0,
    }