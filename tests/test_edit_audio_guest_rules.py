"""
`/edit-audio` posts multipart form data (no JSON body), so a guest user_id
sent there must be read from the FORM, not just query params / JSON. This
covers the gap: guests editing by voice never had their saved quality rules
applied because `_apply_text_edit` only checked query params and JSON.

Network-free: transcription, the edit runner, and rule loading are all
monkeypatched at their defining module (the source `_apply_text_edit` imports
`from`), which is what a `from .x import y` rebind inside a function picks
up at call time.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import edit_runner as edit_runner_mod
from app import quality_rules as quality_rules_mod
from app import server as server_mod
from app import transcription as transcription_mod


def _fake_transcribe(audio_bytes, mime_type):
    return "make it look nicer", 0.5


def test_edit_audio_reads_guest_user_id_from_multipart_form(monkeypatch):
    calls: dict = {}

    async def fake_run_text_edit(presentation_id, instruction, rules=None, **kwargs):
        calls["rules"] = rules
        return {"committed": True, "success_count": 1, "touched": {}}

    def fake_load_rule_set(user_id):
        calls["user_id"] = user_id
        return "SENTINEL_RULES"

    monkeypatch.setattr(transcription_mod, "transcribe", _fake_transcribe)
    monkeypatch.setattr(edit_runner_mod, "run_text_edit", fake_run_text_edit)
    monkeypatch.setattr(quality_rules_mod, "load_rule_set", fake_load_rule_set)

    client = TestClient(server_mod.app)
    resp = client.post(
        "/edit-audio",
        data={"presentation_id": "pid123", "user_id": "guest-abc"},
        files={"audio": ("clip.webm", b"0" * 2000, "audio/webm")},
    )

    assert resp.status_code == 200
    assert calls["user_id"] == "guest:guest-abc"
    assert calls["rules"] == "SENTINEL_RULES"


def test_edit_audio_with_no_user_id_anywhere_loads_default_rules(monkeypatch):
    calls: dict = {}

    async def fake_run_text_edit(presentation_id, instruction, rules=None, **kwargs):
        calls["rules"] = rules
        return {"committed": True, "success_count": 1, "touched": {}}

    def fake_load_rule_set(user_id):
        calls["user_id"] = user_id
        return "SENTINEL_RULES"

    monkeypatch.setattr(transcription_mod, "transcribe", _fake_transcribe)
    monkeypatch.setattr(edit_runner_mod, "run_text_edit", fake_run_text_edit)
    monkeypatch.setattr(quality_rules_mod, "load_rule_set", fake_load_rule_set)

    client = TestClient(server_mod.app)
    resp = client.post(
        "/edit-audio",
        data={"presentation_id": "pid123"},
        files={"audio": ("clip.webm", b"0" * 2000, "audio/webm")},
    )

    assert resp.status_code == 200
    assert calls["user_id"] is None
