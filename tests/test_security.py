from app.ai import redact_pii
from app.zalo import verify_secret
from app.config import settings


def test_pii_scrubbed():
    text = redact_pii("Gửi tới example@mail.com và số 0912345678")
    assert "example@mail.com" not in text
    assert "0912345678" not in text
    assert "[EMAIL]" in text


def test_default_secret_denied(monkeypatch):
    monkeypatch.setattr("app.zalo.settings", type("S", (), {"webhook_secret": "replace-with-placeholder"})())
    assert not verify_secret("replace-with-placeholder")
