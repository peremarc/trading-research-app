from app.core.config import Settings
from app.db.migrations import upgrade_database_to_head


def test_upgrade_database_to_head_uses_configured_database_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_upgrade(config, revision: str) -> None:
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["script_location"] = config.get_main_option("script_location")
        captured["revision"] = revision

    monkeypatch.setattr("app.db.migrations.command.upgrade", fake_upgrade)

    upgrade_database_to_head(Settings(database_url="sqlite:///./test_runtime.db"))

    assert captured["url"] == "sqlite:///./test_runtime.db"
    assert str(captured["script_location"]).endswith("/backend/migrations")
    assert captured["revision"] == "head"
