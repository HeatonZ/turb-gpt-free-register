import json
from pathlib import Path
from unittest.mock import patch

from core import codex_oauth
from core import sub2_oauth_recovery as ledger


def test_request_sub2_authorize_url_records_authorization(tmp_path):
    payload = {
        "data": {
            "url": "https://auth.test/authorize?state=STATE&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback",
            "session_id": "SESSION",
        }
    }
    saved = {
        "record_id": "AUTH-RECORD",
        "session_id": "SESSION",
        "state": "STATE",
        "sub2_path": "/api/v1/admin/openai/generate-auth-url",
    }

    with patch.object(codex_oauth, "_sub2_codex_request_json", return_value=payload) as request, \
            patch.object(codex_oauth.sub2_oauth_recovery, "ledger_dir", return_value=tmp_path), \
            patch.object(codex_oauth.sub2_oauth_recovery, "save_pending_authorization", return_value=saved) as save, \
            patch.object(codex_oauth, "_sub2_codex_base", return_value="https://sub2.test"):
        result = codex_oauth._request_sub2_authorize_url(email="user@example.test")

    assert result["authorization_record_id"] == "AUTH-RECORD"
    request.assert_called_once_with("POST", "/api/v1/admin/openai/generate-auth-url", {})
    kwargs = save.call_args.kwargs
    assert kwargs["email"] == "user@example.test"
    assert kwargs["session_id"] == "SESSION"
    assert kwargs["state"] == "STATE"
    assert kwargs["path"] == "/api/v1/admin/openai/generate-auth-url"


def test_atomic_pending_survives_failure(tmp_path):
    row = ledger.save_pending_callback(
        directory=tmp_path, email="user@example.test", auth_url="https://auth.test/?state=STATE",
        session_id="SESSION", callback_url="http://localhost:1455/auth/callback?code=CODE&state=STATE",
        state="STATE", redirect_uri="http://localhost:1455/auth/callback",
        endpoint="https://sub2.test", path="/api/v1/admin/openai/create-from-oauth",
        payload={"session_id": "SESSION", "code": "CODE", "state": "STATE", "priority": 50},
    )
    path = tmp_path / (row["record_id"] + ".json")
    updated = ledger.update_record(path, attempts=1, last_error="HTTP 502", status="pending")
    loaded = ledger.load_record(path)
    assert loaded["status"] == "pending"
    assert loaded["session_id"] == "SESSION"
    assert loaded["code"] == "CODE"
    assert loaded["state"] == "STATE"
    assert loaded["payload"]["priority"] == 50
    assert updated["attempts"] == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(path.read_text())["code"] == "CODE"


def test_awaiting_callback_is_not_pending(tmp_path):
    ledger.save_pending_authorization(
        directory=tmp_path, email="u@e.test", auth_url="https://auth.test/?state=T",
        session_id="S", state="T", redirect_uri="", endpoint="https://sub2.test", path="/callback")
    assert ledger.list_pending(tmp_path) == []


def test_callback_reuses_authorization_record(tmp_path):
    auth = ledger.save_pending_authorization(
        directory=tmp_path, email="u@e.test", auth_url="https://auth.test/?state=T",
        session_id="S", state="T", redirect_uri="", endpoint="https://sub2.test", path="/callback")
    callback = ledger.save_pending_callback(
        directory=tmp_path, email="u@e.test", auth_url="https://auth.test/?state=T",
        session_id="S", callback_url="http://localhost:1455/auth/callback?code=C&state=T",
        state="T", redirect_uri="", endpoint="https://sub2.test", path="/callback",
        payload={"session_id": "S", "code": "C", "state": "T"})
    assert callback["record_id"] == auth["record_id"]
    assert len(ledger.list_pending(tmp_path)) == 1
    assert ledger.list_pending(tmp_path)[0]["code"] == "C"


def test_success_status(tmp_path):
    row = ledger.save_pending_callback(
        directory=tmp_path, email="u@e.test", auth_url="https://auth.test",
        session_id="S", callback_url="http://localhost:1455/auth/callback?code=C&state=T",
        state="T", redirect_uri="", endpoint="https://sub2.test", path="/callback", payload={"code": "C"})
    path = tmp_path / (row["record_id"] + ".json")
    ledger.update_record(path, attempts=1, sub2_submit_response={"ok": True}, status="succeeded")
    assert ledger.load_record(path)["status"] == "succeeded"
