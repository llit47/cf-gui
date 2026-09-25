from pathlib import Path

import pytest

from cf_gui.config import ConfigError, ConfigStore


SOURCE = """tunnel: example-tunnel
credentials-file: /etc/cloudflared/example.json
ingress:
  - hostname: old.example.com
    service: http://localhost:3000
  - service: http_status:404
"""


def test_add_edit_delete_preserves_fallback_and_creates_backup(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    document = store.load()
    store.add(document, "new.example.com", "http://localhost:8080")
    backup = store.save(document)
    assert backup is not None and backup.read_text() == SOURCE
    assert [item.get("hostname") for item in store.load()["ingress"]] == [
        "old.example.com", "new.example.com", None
    ]
    document = store.load()
    store.edit(document, 1, "changed.example.com", "http://localhost:9090")
    store.delete(document, 0)
    store.save(document)
    assert store.entries(store.load()) == [(0, "changed.example.com", "http://localhost:9090")]
    assert store.load()["tunnel"] == "example-tunnel"


def test_bad_yaml_and_invalid_entry_do_not_replace_file(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    with pytest.raises(ConfigError):
        store.save({"ingress": [{"hostname": "bad.example.com"}]})
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
