"""Confidence thresholds — the single source of truth for every AI decision gate.

Every number here comes from plan section 6 ("Thresholds"). Nothing in the assistant
pipeline is allowed to hard-code a threshold outside this module: a write only ever
happens when the caller checked one of the helpers below first.
"""

from __future__ import annotations

# S0 router: below this, ask a clarifying question instead of dispatching.
INTENT_AUTO = 0.70

# S1 extraction: below this, ask the user to retake the photo.
READABILITY_MIN = 0.50

# Feature A material identification (A1, phase 03): below this, ask for a clearer photo.
IDENTIFY_MIN = 0.50

# S3 duplicate_of (feature B/C, phase 03/04).
DUP_ASK_LOW = 0.60
DUP_REJECT = 0.85

# S3 project assignment (feature B/C, phase 03/04).
PROJECT_CONFIRMED = 0.90
PROJECT_ASK_LOW = 0.60

# S3 amounts_consistent (feature B/C, phase 03/04).
AMOUNTS_OK = 0.50

# Feature C jev_verify faithfulness (genai scan mode, phase 03).
VERIFY_FAITHFUL = 0.90

# Feature C attach-to-existing-invoice (phase 03).
ATTACH_EXISTING = 0.85

# Feature A material pick (phase 04).
PICK_CONFIRMED = 0.85
PICK_ASK_LOW = 0.60

# S0 is_write: a request that mutates data (move_equipment, ...) needs this much
# Jev confidence to run unconfirmed; otherwise show a confirm/cancel choice.
IS_WRITE = 0.80


def intent_status(confidence: float) -> str:
    """ "confirmed" (dispatch straight to the intent) or "to_confirm" (ask)."""
    return "confirmed" if confidence >= INTENT_AUTO else "to_confirm"


def readability_ok(readability: float) -> bool:
    return readability >= READABILITY_MIN


def identify_ok(confidence: float) -> bool:
    return confidence >= IDENTIFY_MIN


def duplicate_status(confidence: float) -> str:
    """ "reject" (refuse, link the existing invoice), "ask" (show both) or "none"."""
    if confidence >= DUP_REJECT:
        return "reject"
    if confidence >= DUP_ASK_LOW:
        return "ask"
    return "none"


def project_status(confidence: float) -> str:
    """ "confirmed", "to_confirm" (two buttons) or "needs_review"."""
    if confidence >= PROJECT_CONFIRMED:
        return "confirmed"
    if confidence >= PROJECT_ASK_LOW:
        return "to_confirm"
    return "needs_review"


def amounts_ok(confidence: float) -> bool:
    return confidence >= AMOUNTS_OK


def verify_faithful(confidence: float) -> bool:
    return confidence >= VERIFY_FAITHFUL


def attach_existing_allowed(confidence: float) -> bool:
    return confidence >= ATTACH_EXISTING


def pick_status(confidence: float) -> str:
    """ "confirmed", "to_confirm" or "reject" (fall back to Lens / import photo-only)."""
    if confidence >= PICK_CONFIRMED:
        return "confirmed"
    if confidence >= PICK_ASK_LOW:
        return "to_confirm"
    return "reject"


def is_write_allowed(confidence: float) -> bool:
    return confidence >= IS_WRITE
