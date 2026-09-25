from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from cf_gui import cloudflared
from cf_gui.activation import Activator
from cf_gui.cloudflared import CommandResult
from cf_gui.config import ConfigError, ConfigStore, StaleConfigError


SOURCE = """tunnel: test-tunnel
ingress:
  - hostname: old.example.com
    service: http://localhost:3000
  - service: http_status:404
"""


def command(ok=True, output="OK"):
    return CommandResult(ok, output)


def restart_tuple(ok=True):
    return command(ok, "restart"), command(ok, "status"), command(True, "journal")


def setup(tmp_path, monkeypatch, *, restarts=None, active=None, validate=None):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    store = ConfigStore(path)
    monkeypatch.setattr(cloudflared, "validate_config", validate or Mock(return_value=command()))
    restart = Mock(side_effect=restarts or [restart_tuple()])
    state = Mock(side_effect=active or [command(True, "active")])
    monkeypatch.setattr(cloudflared, "restart_service", restart)
    monkeypatch.setattr(cloudflared, "is_active", state)
    return store, restart, state


def add(document):
    ConfigStore.add(document, "new.example.com", "http://localhost:8080")


def test_valid_candidate_is_validated_then_backed_up_and_activated(tmp_path, monkeypatch):
    seen = []
    path = tmp_path / "config.yml"

    def validate(candidate):
        seen.append((candidate, candidate.read_text(), path.read_text()))
        return command()

    store, restart, state = setup(tmp_path, monkeypatch, validate=validate)
    original = store.load_snapshot()
    result = Activator(store).apply(original.revision, add)
    assert result.state == "succeeded"
    assert seen[0][0] != path and "new.example.com" in seen[0][1]
    assert seen[0][2] == SOURCE  # active file is untouched during validation
    assert result.backup.read_bytes() == original.content
    assert store.entries(store.load())[1][1] == "new.example.com"
    restart.assert_called_once()
    state.assert_called_once()
    assert not seen[0][0].exists()  # no candidate left behind


def test_invalid_cloudflared_candidate_keeps_active_config_and_diagnostics(tmp_path, monkeypatch):
    validator = Mock(return_value=command(False, "stdout detail\nstderr detail"))
    store, restart, _ = setup(tmp_path, monkeypatch, validate=validator)
    with pytest.raises(ConfigError, match="stderr detail") as error:
        Activator(store).apply(store.load_snapshot().revision, add)
    assert "stdout detail" in str(error.value)
    assert store.path.read_text() == SOURCE
    assert list(tmp_path.glob("config.yml.bak.*")) == []
    restart.assert_not_called()
    assert not validator.call_args.args[0].exists()


@pytest.mark.parametrize("first_restart,first_active", [
    (restart_tuple(False), command(True, "active")),
    (restart_tuple(True), command(False, "failed")),
    ((command(True, "restart"), command(False, "status failed"), command(True, "journal")),
     command(True, "active")),
])
def test_restart_or_inactive_service_rolls_back(tmp_path, monkeypatch, first_restart, first_active):
    store, restart, state = setup(tmp_path, monkeypatch,
                                  restarts=[first_restart, restart_tuple(True)],
                                  active=[first_active, command(True, "active")])
    result = Activator(store).apply(store.load_snapshot().revision, add)
    assert result.state == "rolled_back"
    assert result.config_restored
    assert store.path.read_text() == SOURCE
    assert result.backup.read_text() == SOURCE
    assert restart.call_count == 2 and state.call_count == 2


def test_rollback_restore_failure_is_reported(tmp_path, monkeypatch):
    store, restart, _ = setup(tmp_path, monkeypatch,
                              restarts=[restart_tuple(False)], active=[command(False, "failed")])
    monkeypatch.setattr(store, "restore", Mock(side_effect=ConfigError("disk failure")))
    result = Activator(store).apply(store.load_snapshot().revision, add)
    assert result.state == "rollback_failed"
    assert "disk failure" in result.message()
    assert "new.example.com" in store.path.read_text()
    assert result.backup.read_text() == SOURCE
    restart.assert_called_once()


def test_rollback_file_restored_but_service_still_failed(tmp_path, monkeypatch):
    store, restart, _ = setup(tmp_path, monkeypatch,
                              restarts=[restart_tuple(False), restart_tuple(False)],
                              active=[command(False, "failed"), command(False, "failed again")])
    result = Activator(store).apply(store.load_snapshot().revision, add)
    assert result.state == "rollback_failed"
    assert result.config_restored
    assert store.path.read_text() == SOURCE
    assert restart.call_count == 2


def test_external_change_before_request_is_rejected(tmp_path, monkeypatch):
    store, restart, _ = setup(tmp_path, monkeypatch)
    revision = store.load_snapshot().revision
    store.path.write_text(SOURCE + "# external update\n")
    with pytest.raises(StaleConfigError):
        Activator(store).apply(revision, add)
    assert store.path.read_text().endswith("# external update\n")
    restart.assert_not_called()


def test_external_change_during_validation_is_rejected_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "config.yml"

    def validate(candidate):
        path.write_text(SOURCE + "# changed while validating\n")
        return command()

    store, restart, _ = setup(tmp_path, monkeypatch, validate=validate)
    with pytest.raises(StaleConfigError):
        Activator(store).apply(store.load_snapshot().revision, add)
    assert path.read_text().endswith("# changed while validating\n")
    assert list(tmp_path.glob("config.yml.bak.*")) == []
    restart.assert_not_called()


def test_lock_serializes_mutations_and_second_becomes_stale(tmp_path, monkeypatch):
    entered_validation = Event()
    release_validation = Event()
    second_started = Event()
    second_done = Event()
    calls = []

    def validate(candidate):
        calls.append(candidate)
        entered_validation.set()
        assert release_validation.wait(3)
        return command()

    store, restart, _ = setup(tmp_path, monkeypatch, validate=validate)
    revision = store.load_snapshot().revision
    results = []

    def first():
        results.append(Activator(ConfigStore(store.path)).apply(revision, add).state)

    def second():
        second_started.set()
        try:
            Activator(ConfigStore(store.path)).apply(revision, lambda doc: ConfigStore.delete(doc, 0))
        except StaleConfigError:
            results.append("stale")
        finally:
            second_done.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    assert entered_validation.wait(3)
    second_thread.start()
    assert second_started.wait(3)
    assert not second_done.wait(0.1)
    assert len(calls) == 1
    release_validation.set()
    first_thread.join(3)
    second_thread.join(3)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert results == ["succeeded", "stale"]
    assert restart.call_count == 1
    assert [x[1] for x in store.entries(store.load())] == ["old.example.com", "new.example.com"]


def test_unexpected_restart_error_still_triggers_rollback(tmp_path, monkeypatch):
    store, restart, _ = setup(tmp_path, monkeypatch,
                              restarts=[RuntimeError("systemd client error"), restart_tuple(True)],
                              active=[command(True, "active")])
    result = Activator(store).apply(store.load_snapshot().revision, add)
    assert result.state == "rolled_back"
    assert store.path.read_text() == SOURCE
    assert restart.call_count == 2


def test_external_change_after_replace_is_not_overwritten_by_rollback(tmp_path, monkeypatch):
    store, _, _ = setup(tmp_path, monkeypatch, active=[command(False, "failed")])
    external = SOURCE + "# late external update\n"

    def failed_restart():
        store.path.write_text(external)
        return restart_tuple(False)

    monkeypatch.setattr(cloudflared, "restart_service", Mock(side_effect=failed_restart))
    result = Activator(store).apply(store.load_snapshot().revision, add)
    assert result.state == "rollback_failed"
    assert "Konfiguracja zmieniła się" in result.message()
    assert store.path.read_text() == external
    assert result.backup.read_text() == SOURCE


def test_external_change_after_backup_is_rejected_before_replace(tmp_path, monkeypatch):
    store, restart, _ = setup(tmp_path, monkeypatch)
    real_backup = store.backup

    def backup_then_external(snapshot):
        backup = real_backup(snapshot)
        store.path.write_text(SOURCE + "# external at commit point\n")
        return backup

    monkeypatch.setattr(store, "backup", backup_then_external)
    with pytest.raises(StaleConfigError):
        Activator(store).apply(store.load_snapshot().revision, add)
    assert store.path.read_text().endswith("# external at commit point\n")
    restart.assert_not_called()
