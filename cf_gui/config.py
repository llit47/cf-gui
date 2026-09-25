"""Read cloudflared YAML and prepare atomic, revision-checked file updates."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

import yaml


class ConfigError(Exception):
    """Invalid or unavailable cloudflared configuration."""


class StaleConfigError(ConfigError):
    """The config changed after the form was displayed."""


_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_URL_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_IPV4_SHAPE = re.compile(r"[0-9]+(?:\.[0-9]+){3}\Z")


def _valid_dns_name(name: str, *, fqdn: bool) -> bool:
    labels = name.split(".")
    if len(name) > 253 or (fqdn and len(labels) < 2):
        return False
    if len(labels) > 1 and (len(labels[-1]) < 2 or not any(char.isalpha() for char in labels[-1])):
        return False
    return all(_DNS_LABEL.fullmatch(label) for label in labels)


def _form_hostname(value: str) -> str:
    hostname = value.strip()
    suffix = hostname[2:] if hostname.startswith("*.") else hostname
    if not _valid_dns_name(suffix, fqdn=True):
        raise ConfigError("Hostname musi być poprawną nazwą domenową (np. app.example.com).")
    return hostname


def _valid_origin_host(host: str) -> bool:
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
            return True
        except ValueError:
            return False
    if _IPV4_SHAPE.fullmatch(host):
        try:
            ipaddress.IPv4Address(host)
            return True
        except ValueError:
            return False
    return _valid_dns_name(host, fqdn=False)


def _form_service(value: str) -> str:
    service = value.strip()
    if service in ("hello_world", "bastion") or re.fullmatch(r"http_status:[1-9][0-9]{2}", service):
        return service
    if not service or any(char.isspace() for char in service):
        raise ConfigError("Service musi być poprawnym adresem originu lub usługą cloudflared.")
    if service.startswith(("unix:", "unix+tls:")):
        socket_path = service.split(":", 1)[1]
        if (not socket_path.startswith("/") or socket_path.startswith("//") or socket_path == "/"
                or "?" in socket_path or "#" in socket_path):
            raise ConfigError("Service unix wymaga bezwzględnej ścieżki do socketu.")
        return service
    explicit_scheme = bool(_URL_SCHEME.match(service))
    address = service if explicit_scheme else f"http://{service}"
    try:
        parsed = urlsplit(address)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ConfigError("Service ma nieprawidłowy host lub port.") from exc
    if parsed.path or "?" in address or "#" in address:
        raise ConfigError("Service originu nie może zawierać ścieżki, query ani fragmentu.")
    if (not host or not _valid_origin_host(host) or (port is not None and port < 1)
            or parsed.netloc.rsplit("@", 1)[-1].endswith(":")):
        raise ConfigError("Service ma nieprawidłowy host lub port.")
    if not explicit_scheme and port is None:
        raise ConfigError("Service bez schematu wymaga portu (np. origin:8096).")
    return service if explicit_scheme else address


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
        hostname, service = _form_hostname(hostname), _form_service(service)
        if any(item.get("hostname") == hostname for item in document["ingress"]):
            raise ConfigError("Taki hostname już istnieje.")
        ingress = document["ingress"]
        fallback_index = next((i for i, item in enumerate(ingress) if "hostname" not in item), len(ingress))
        ingress.insert(fallback_index, {"hostname": hostname, "service": service})

    @staticmethod
    def edit(document: dict, index: int, hostname: str, service: str) -> None:
        item = ConfigStore._entry(document, index)
        hostname, service = _form_hostname(hostname), _form_service(service)
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
