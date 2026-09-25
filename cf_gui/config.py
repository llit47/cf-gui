"""Read cloudflared YAML and prepare atomic, revision-checked file updates."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import tempfile

import yaml


class ConfigError(Exception):
    """Invalid or unavailable cloudflared configuration."""


class StaleConfigError(ConfigError):
    """The config changed after the form was displayed."""


@dataclass(frozen=True)
class Snapshot:
    document: dict
    content: bytes
    revision: str
    metadata: os.stat_result


def fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(f"{self.path.name}.cf-gui.lock")

    @staticmethod
    def _validate(document: object) -> dict:
        if not isinstance(document, dict):
            raise ConfigError("Konfiguracja musi być mapą YAML.")
        ingress = document.get("ingress")
        if not isinstance(ingress, list):
            raise ConfigError("Konfiguracja musi zawierać listę ingress.")
        for position, item in enumerate(ingress):
            if not isinstance(item, dict) or not isinstance(item.get("service"), str) or not item["service"].strip():
                raise ConfigError(f"Wpis ingress {position + 1} wymaga service.")
            if "hostname" in item and (not isinstance(item["hostname"], str) or not item["hostname"].strip()):
                raise ConfigError(f"Wpis ingress {position + 1} ma nieprawidłowy hostname.")
        return document

    def load_snapshot(self) -> Snapshot:
        try:
            with self.path.open("rb") as source:
                content = source.read()
                metadata = os.fstat(source.fileno())
            document = self._validate(yaml.safe_load(content.decode("utf-8")))
            return Snapshot(document, content, fingerprint(content), metadata)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError(f"Nie można odczytać konfiguracji: {exc}") from exc

    def load(self) -> dict:
        return self.load_snapshot().document

    def assert_revision(self, expected: str) -> os.stat_result:
        try:
            with self.path.open("rb") as source:
                content = source.read()
                metadata = os.fstat(source.fileno())
        except FileNotFoundError as exc:
            raise StaleConfigError("Konfiguracja zmieniła się od otwarcia formularza. Odśwież stronę i spróbuj ponownie.") from exc
        except OSError as exc:
            raise ConfigError(f"Nie można sprawdzić konfiguracji: {exc}") from exc
        if not expected or fingerprint(content) != expected:
            raise StaleConfigError("Konfiguracja zmieniła się od otwarcia formularza. Odśwież stronę i spróbuj ponownie.")
        return metadata

    @contextmanager
    def write_lock(self):
        """Serialize cf-gui writers across processes for the whole activation."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise ConfigError(f"Nie można otworzyć blokady konfiguracji: {exc}") from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except OSError as exc:
                raise ConfigError(f"Nie można zablokować konfiguracji: {exc}") from exc
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def apply_metadata(path: Path, metadata: os.stat_result | None) -> None:
        if metadata is None:
            os.chmod(path, 0o600)
            return
        current = path.stat()
        if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
            os.chown(path, metadata.st_uid, metadata.st_gid)
        os.chmod(path, stat.S_IMODE(metadata.st_mode))

    def _temporary_file(self, content: bytes, metadata: os.stat_result | None = None) -> Path:
        path = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".yml", dir=self.path.parent)
            path = Path(name)
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            self.apply_metadata(path, metadata)
            return path
        except OSError as exc:
            if path is not None:
                path.unlink(missing_ok=True)
            raise ConfigError(f"Nie można przygotować pliku konfiguracji: {exc}") from exc

    def candidate(self, document: dict) -> Path:
        self._validate(document)
        try:
            content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode("utf-8")
            self._validate(yaml.safe_load(content.decode("utf-8")))
        except yaml.YAMLError as exc:
            raise ConfigError(f"Niepoprawny YAML: {exc}") from exc
        return self._temporary_file(content)  # private mode until validation succeeds

    def backup(self, snapshot: Snapshot) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"{self.path.name}.bak.{stamp}")
        created = False
        try:
            with backup.open("xb") as target:
                created = True
                target.write(snapshot.content)
                target.flush()
                os.fsync(target.fileno())
            self.apply_metadata(backup, snapshot.metadata)
            return backup
        except OSError as exc:
            if created:
                backup.unlink(missing_ok=True)
            raise ConfigError(f"Nie można utworzyć backupu: {exc}") from exc

    def replace(self, candidate: Path, metadata: os.stat_result | None) -> None:
        try:
            self.apply_metadata(candidate, metadata)
            os.replace(candidate, self.path)
        except OSError as exc:
            raise ConfigError(f"Nie można aktywować konfiguracji: {exc}") from exc

    def restore(self, backup: Path | None, metadata: os.stat_result | None) -> None:
        if backup is None:
            try:
                self.path.unlink()
            except OSError as exc:
                raise ConfigError(f"Nie można usunąć nowego configu przy rollbacku: {exc}") from exc
            return
        try:
            content = backup.read_bytes()
        except OSError as exc:
            raise ConfigError(f"Nie można odczytać backupu: {exc}") from exc
        restored = self._temporary_file(content, metadata)
        try:
            self.replace(restored, metadata)
        finally:
            restored.unlink(missing_ok=True)

    @staticmethod
    def entries(document: dict) -> list[tuple[int, str, str]]:
        return [(index, item["hostname"], item["service"])
                for index, item in enumerate(document["ingress"]) if "hostname" in item]

    @staticmethod
    def add(document: dict, hostname: str, service: str) -> None:
        hostname, service = hostname.strip(), service.strip()
        if not hostname or not service:
            raise ConfigError("Hostname i service są wymagane.")
        if any(item.get("hostname") == hostname for item in document["ingress"]):
            raise ConfigError("Taki hostname już istnieje.")
        ingress = document["ingress"]
        fallback_index = next((i for i, item in enumerate(ingress) if "hostname" not in item), len(ingress))
        ingress.insert(fallback_index, {"hostname": hostname, "service": service})

    @staticmethod
    def edit(document: dict, index: int, hostname: str, service: str) -> None:
        item = ConfigStore._entry(document, index)
        hostname, service = hostname.strip(), service.strip()
        if not hostname or not service:
            raise ConfigError("Hostname i service są wymagane.")
        if any(i != index and entry.get("hostname") == hostname for i, entry in enumerate(document["ingress"])):
            raise ConfigError("Taki hostname już istnieje.")
        item["hostname"] = hostname
        item["service"] = service

    @staticmethod
    def delete(document: dict, index: int) -> None:
        ConfigStore._entry(document, index)
        del document["ingress"][index]

    @staticmethod
    def _entry(document: dict, index: int) -> dict:
        ingress = document["ingress"]
        if index < 0 or index >= len(ingress) or "hostname" not in ingress[index]:
            raise ConfigError("Nie ma takiego wpisu hostname.")
        return ingress[index]
