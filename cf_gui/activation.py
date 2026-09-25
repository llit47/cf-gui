"""Validate and activate a config, restoring the previous one if service startup fails."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from . import cloudflared
from .cloudflared import CommandResult
from .config import ConfigError, ConfigStore, Snapshot, StaleConfigError, fingerprint


@dataclass(frozen=True)
class ServiceCheck:
    restart: CommandResult
    status: CommandResult
    logs: CommandResult
    active: CommandResult

    @property
    def ok(self) -> bool:
        return self.restart.ok and self.status.ok and self.active.ok


@dataclass(frozen=True)
class ActivationResult:
    state: Literal["succeeded", "rolled_back", "rollback_failed"]
    backup: Path
    activation: ServiceCheck
    rollback: ServiceCheck | None = None
    rollback_error: str | None = None
    config_restored: bool = False

    def message(self) -> str:
        if self.state == "succeeded":
            return f"Aktywacja zakończona sukcesem. Backup: {self.backup}."
        lines = ["Aktywacja nie powiodła się; rollback zakończony sukcesem." if self.state == "rolled_back"
                 else "Aktywacja nie powiodła się; rollback nie powiódł się."]
        lines.append(f"Backup: {self.backup}.")
        lines.append(f"Pierwszy restart: {self.activation.restart.output}")
        lines.append(f"Status systemd po pierwszym restarcie: {self.activation.status.output}")
        lines.append(f"Stan po pierwszym restarcie: {self.activation.active.output}")
        lines.append(f"Dziennik po pierwszym restarcie: {self.activation.logs.output}")
        if self.rollback_error:
            lines.append(f"Przywracanie pliku: {self.rollback_error}")
        if self.config_restored:
            lines.append("Poprzedni config został przywrócony.")
        if self.rollback:
            lines.append(f"Restart po rollbacku: {self.rollback.restart.output}")
            lines.append(f"Status systemd po rollbacku: {self.rollback.status.output}")
            lines.append(f"Stan po rollbacku: {self.rollback.active.output}")
            lines.append(f"Dziennik po rollbacku: {self.rollback.logs.output}")
        return "\n".join(lines)


class Activator:
    def __init__(self, store: ConfigStore):
        self.store = store

    @staticmethod
    def _restart_and_check() -> ServiceCheck:
        try:
            restart, status, logs = cloudflared.restart_service()
            active = cloudflared.is_active()
            return ServiceCheck(restart, status, logs, active)
        except Exception as exc:
            failed = CommandResult(False, f"Nie można sprawdzić usługi: {exc}")
            return ServiceCheck(failed, failed, failed, failed)

    def apply(self, expected_revision: str, change: Callable[[dict], None]) -> ActivationResult:
        with self.store.write_lock():
            snapshot = self.store.load_snapshot()
            if not expected_revision or snapshot.revision != expected_revision:
                raise StaleConfigError("Konfiguracja zmieniła się od otwarcia formularza. Odśwież stronę i spróbuj ponownie.")
            document = snapshot.document
            change(document)
            candidate = self.store.candidate(document)
            try:
                validation = cloudflared.validate_config(candidate)
                if not validation.ok:
                    raise ConfigError(f"Walidacja cloudflared nie powiodła się:\n{validation.output}")
                # The external writer may not use our lock: check again at commit point.
                metadata = self.store.assert_revision(expected_revision)
                current = Snapshot(snapshot.document, snapshot.content, snapshot.revision, metadata)
                backup = self.store.backup(current)
                metadata = self.store.assert_revision(expected_revision)
                try:
                    candidate_revision = fingerprint(candidate.read_bytes())
                except OSError as exc:
                    raise ConfigError(f"Nie można odczytać candidate configu: {exc}") from exc
                # Preserve metadata from the file present immediately before replace.
                try:
                    self.store.apply_metadata(backup, metadata)
                except OSError as exc:
                    raise ConfigError(f"Nie można zachować metadanych backupu: {exc}") from exc
                self.store.replace(candidate, metadata)
                activation = self._restart_and_check()
                if activation.ok:
                    return ActivationResult("succeeded", backup, activation)

                try:
                    # Avoid overwriting a new external update made after our replace.
                    self.store.assert_revision(candidate_revision)
                    self.store.restore(backup, metadata)
                except ConfigError as exc:
                    return ActivationResult("rollback_failed", backup, activation, rollback_error=str(exc))
                rollback = self._restart_and_check()
                try:
                    self.store.assert_revision(snapshot.revision)
                    restored_matches = True
                    rollback_error = None
                except ConfigError as exc:
                    restored_matches = False
                    rollback_error = str(exc)
                state = "rolled_back" if rollback.ok and restored_matches else "rollback_failed"
                return ActivationResult(state, backup, activation, rollback=rollback,
                                        rollback_error=rollback_error, config_restored=True)
            finally:
                candidate.unlink(missing_ok=True)
