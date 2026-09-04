from __future__ import annotations

from dataclasses import asdict

from app.cookies import clear_login_state, describe_login_status, validate_saved_session
from app.logger import redact_sensitive


def session_status() -> dict:
    return asdict(describe_login_status())


def validate_session() -> dict:
    return asdict(validate_saved_session())


def clear_session() -> dict:
    result = clear_login_state()
    # Residue paths are diagnostics, never transport credential file contents.
    return {"ok": result.ok, "failures": [redact_sensitive(s) for s in result.failures],
            "remaining_count": len(result.remaining), "status": session_status()}
