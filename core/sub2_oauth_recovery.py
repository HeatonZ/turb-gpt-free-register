"""Durable recovery ledger for Sub2API Codex OAuth callbacks.

Files intentionally live under the configured project output directory, not a temp
folder.  Callback secrets are present in this ledger because they are required for
replay; callers must never log the record or callback URL.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_dir(project_root: Path, output_dirname: str) -> Path:
    return project_root / output_dirname / "sub2_oauth_recovery"


def record_id(session_id: str, code: str = "", state: str = "", path: str = "") -> str:
    """Return the stable id for one authorization attempt.

    ``code`` is deliberately not part of the identity: the callback is a later
    snapshot of the authorization record and must update the same file.
    """
    value = "|".join((session_id or "", state or "", path or ""))
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def save_pending_authorization(*, directory: Path, email: str, auth_url: str,
                               session_id: str, state: str, redirect_uri: str,
                               endpoint: str, path: str) -> dict:
    rid = record_id(session_id, "", state, path)
    now = _now()
    row = {"version": 1, "type": "sub2_oauth_recovery", "record_id": rid,
           "email": email, "auth_url": auth_url, "session_id": session_id,
           "state": state, "redirect_uri": redirect_uri, "sub2_endpoint": endpoint,
           "sub2_path": path, "created_at": now, "updated_at": now,
           "attempts": 0, "status": "awaiting_callback"}
    _atomic_write(directory / f"{rid}.json", row)
    return row


def save_pending_callback(*, directory: Path, email: str, auth_url: str,
                          session_id: str, callback_url: str, state: str,
                          redirect_uri: str, endpoint: str, path: str,
                          payload: dict, authorization_record_id: str = "") -> dict:
    query = parse_qs(urlparse(callback_url).query)
    code = (query.get("code") or [""])[0]
    rid = authorization_record_id or record_id(session_id, state=state, path=path)
    now = _now()
    old_path = directory / f"{rid}.json"
    try:
        old = load_record(old_path)
    except (OSError, ValueError, TypeError):
        old = {}
    created_at = old.get("created_at", now)
    # Keep the exact create-from-oauth payload for deterministic replay.
    metadata = dict(payload or {})
    row = {"version": 1, "type": "sub2_oauth_recovery", "record_id": rid,
           "email": email, "auth_url": auth_url, "session_id": session_id,
           "code": code, "state": state, "redirect_uri": redirect_uri,
           "sub2_endpoint": endpoint, "sub2_path": path, "payload": metadata,
           "attempts": 0, "status": "pending", "created_at": created_at, "updated_at": now}
    _atomic_write(old_path, row)
    return row


def load_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def update_record(path: Path, **changes) -> dict:
    row = load_record(path)
    row.update(changes)
    row["updated_at"] = _now()
    _atomic_write(path, row)
    return row


def list_pending(directory: Path) -> list[dict]:
    return [load_record(p) for p in sorted(directory.glob("*.json"))
            if _safe_pending(p)]


def _safe_pending(path: Path) -> bool:
    try:
        row = load_record(path)
        return (row.get("status") in {"pending", "needs_reconcile"}
                and all(row.get(key) for key in ("session_id", "code", "state", "payload"))
                and isinstance(row.get("payload"), dict)
                and all(row["payload"].get(key) for key in ("session_id", "code", "state")))
    except (OSError, ValueError, TypeError):
        return False
