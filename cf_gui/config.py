"""Read and update the cloudflared YAML configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import tempfile

import yaml


class ConfigError(Exception):
    """Invalid or unavailable cloudflared configuration."""


class ConfigStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

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

    def load(self) -> dict:
        try:
            content = self.path.read_text(encoding="utf-8")
            return self._validate(yaml.safe_load(content))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"Nie można odczytać konfiguracji: {exc}") from exc

    def save(self, document: dict) -> Path | None:
        self._validate(document)
        try:
            content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
            self._validate(yaml.safe_load(content))  # validate generated YAML before touching disk
        except yaml.YAMLError as exc:
            raise ConfigError(f"Niepoprawny YAML: {exc}") from exc

        temp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            old_exists = self.path.exists()
            backup = None
            if old_exists:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = self.path.with_name(f"{self.path.name}.bak.{stamp}")
                shutil.copy2(self.path, backup)  # complete before replacement
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False) as temp:
                temp_name = temp.name
                temp.write(content)
                temp.flush()
                os.fsync(temp.fileno())
            if old_exists:
                os.chmod(temp_name, self.path.stat().st_mode)
            else:
                os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            return backup
        except OSError as exc:
            raise ConfigError(f"Nie można zapisać konfiguracji: {exc}") from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

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
