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


@pytest.mark.parametrize("hostname", [
    "dupadupa", "bad host.example.com", "https://app.example.com", "app.example.com/path",
    "app.example.com:443", "-app.example.com", "app-.example.com", "app..example.com",
    "192.0.2.79", "a" * 64 + ".example.com", ".".join(["a" * 63] * 3 + ["b" * 62]),
    "foo.*.example.com", "*.*.example.com", "*", "*.dupadupa", "*." + "a" * 64 + ".example.com",
    "*." + ".".join(["a" * 63] * 3 + ["b" * 62]),
])
def test_form_rejects_invalid_hostname(tmp_path: Path, hostname: str):
    document = {"ingress": [{"service": "http_status:404"}]}
    with pytest.raises(ConfigError, match="Hostname"):
        ConfigStore.add(document, hostname, "http://origin:8096")
    assert len(document["ingress"]) == 1


@pytest.mark.parametrize(("service", "expected"), [
    ("192.0.2.79:8080", "http://192.0.2.79:8080"),
    ("origin:8096", "http://origin:8096"),
    ("origin:1", "http://origin:1"),
    ("origin:65535", "http://origin:65535"),
    ("http://origin:8096", "http://origin:8096"),
    ("https://origin:443", "https://origin:443"),
    ("ssh://origin:22", "ssh://origin:22"),
    ("tcp://192.0.2.79:25565", "tcp://192.0.2.79:25565"),
    ("unix:/path/to/socket", "unix:/path/to/socket"),
    ("unix+tls:/path/to/socket", "unix+tls:/path/to/socket"),
    ("bastion", "bastion"),
    ("hello_world", "hello_world"),
    ("http_status:404", "http_status:404"),
    ("http_status:100", "http_status:100"),
    ("http_status:999", "http_status:999"),
])
def test_form_normalizes_service_and_preserves_explicit_schemes(tmp_path: Path, service: str, expected: str):
    document = {"ingress": [{"service": "http_status:404"}]}
    ConfigStore.add(document, "  game.example.com  ", f"  {service}  ")
    assert document["ingress"][0] == {"hostname": "game.example.com", "service": expected}


@pytest.mark.parametrize("service", [
    "192.0.2.333:8000", "http://192.0.2.333:8000", "origin:0", "origin:65536",
    "http://origin:65536", "origin:abc", "http://origin:", "21.dsa.2321.d:8000",
    "origin:8096/path", "http://origin:8096/path", "http://origin:8096?x=1",
    "http://origin:8096#fragment", "unix:relative/path", "unix+tls:", "unix://host/path",
    "http_status:000", "http_status:099", "http_status:99", "http_status:1000",
])
def test_form_rejects_invalid_service(tmp_path: Path, service: str):
    document = {"ingress": [{"service": "http_status:404"}]}
    with pytest.raises(ConfigError, match="Service"):
        ConfigStore.add(document, "app.example.com", service)
    assert document == {"ingress": [{"service": "http_status:404"}]}


def test_form_edit_uses_same_validation_and_normalization(tmp_path: Path):
    document = {"ingress": [{"hostname": "old.example.com", "service": "http://origin:8096"},
                            {"service": "http_status:404"}]}
    with pytest.raises(ConfigError):
        ConfigStore.edit(document, 0, "dupadupa", "origin:8080")
    assert document["ingress"][0]["hostname"] == "old.example.com"
    ConfigStore.edit(document, 0, "  new.example.com  ", "192.0.2.79:8080")
    assert document["ingress"][0] == {"hostname": "new.example.com", "service": "http://192.0.2.79:8080"}
    with pytest.raises(ConfigError):
        ConfigStore.edit(document, 0, "*.example.com", "origin:8096/path")
    assert document["ingress"][0]["service"] == "http://192.0.2.79:8080"
    ConfigStore.edit(document, 0, "  *.example.com  ", "unix+tls:/path/to/socket")
    assert document["ingress"][0] == {"hostname": "*.example.com", "service": "unix+tls:/path/to/socket"}


def test_form_accepts_253_character_domain_name():
    hostname = ".".join(["a" * 63] * 3 + ["b" * 61])
    assert len(hostname) == 253
    document = {"ingress": [{"service": "http_status:404"}]}
    ConfigStore.add(document, hostname, "origin:8096")
    assert document["ingress"][0]["hostname"] == hostname


@pytest.mark.parametrize("suffix", ["example.com", ".".join(["a" * 63] * 3 + ["b" * 61])])
def test_form_accepts_wildcard_with_valid_domain_suffix(suffix):
    document = {"ingress": [{"service": "http_status:404"}]}
    ConfigStore.add(document, f"*.{suffix}", "origin:8096")
    assert document["ingress"][0]["hostname"] == f"*.{suffix}"


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
