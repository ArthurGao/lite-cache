"""Connection helpers vendored from lite-rag (design §4)."""

from lite_cache.db import redact_conninfo


def test_the_password_is_redacted() -> None:
    shown = redact_conninfo("postgresql://u:secret@h/db")
    assert "***" in shown
    assert "secret" not in shown
