from pathlib import Path
import os
import stat

import pytest

from cf_gui.config import ConfigError, ConfigStore, StaleConfigError


SOURCE = """tunnel: example-tunnel
credentials-file: /etc/cloudflared/example.json
ingress:
  - hostname: old.example.com
    service: http://localhost:3000
  - service: http_status:404
"""


def test_entry_edits_preserve_fallback_and_other_keys(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    document = store.load()
    store.add(document, "new.example.com", "http://localhost:8080")
    assert [item.get("hostname") for item in document["ingress"]] == [
        "old.example.com", "new.example.com", None
    ]
    store.edit(document, 1, "changed.example.com", "http://localhost:9090")
    store.delete(document, 0)
    assert store.entries(document) == [(0, "changed.example.com", "http://localhost:9090")]
    assert document["tunnel"] == "example-tunnel"


def test_revision_tracks_exact_file_bytes(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    revision = store.load_snapshot().revision
    path.write_text(SOURCE + "# external comment\n")
    with pytest.raises(StaleConfigError):
        store.assert_revision(revision)


def test_bad_yaml_and_invalid_candidate_do_not_replace_file(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    with pytest.raises(ConfigError):
        store.candidate({"ingress": [{"hostname": "bad.example.com"}]})
    assert path.read_text() == SOURCE
    path.write_text("ingress: [\n")
    with pytest.raises(ConfigError):
        store.load()


def test_duplicate_and_fallback_are_protected(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    document = ConfigStore(path).load()
    with pytest.raises(ConfigError):
        ConfigStore.add(document, "old.example.com", "http://localhost")
    with pytest.raises(ConfigError):
        ConfigStore.delete(document, 1)


def test_replace_preserves_mode_owner_and_group(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    os.chmod(path, 0o640)
    store = ConfigStore(path)
    before = path.stat()
    candidate = store.candidate(store.load())
    store.replace(candidate, before)
    after = path.stat()
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


def test_new_file_uses_private_mode(tmp_path: Path):
    store = ConfigStore(tmp_path / "new.yml")
    candidate = store.candidate({"ingress": [{"service": "http_status:404"}]})
    store.replace(candidate, None)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_existing_backup_is_never_deleted_on_name_collision(tmp_path: Path, monkeypatch):
    from datetime import datetime, timezone
    import cf_gui.config as config_module

    class FixedDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    existing = tmp_path / "config.yml.bak.20260101T000000000000Z"
    existing.write_text("older backup")
    monkeypatch.setattr(config_module, "datetime", FixedDatetime)
    store = ConfigStore(path)
    with pytest.raises(ConfigError):
        store.backup(store.load_snapshot())
    assert existing.read_text() == "older backup"
