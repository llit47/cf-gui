# cf-gui

Prosty panel do zarządzania lokalnym plikiem `cloudflared` na pojedynczym LXC z Debianem lub Ubuntu. Jeden administrator; aplikacja działa jako osobna usługa `cf-gui.service` i nie ingeruje w instalację `cloudflared-manager`.

## Architektura

- Python 3 + Flask: trasy HTTP, sesja cookie i szablony HTML bez budowania frontendu.
- PyYAML: odczyt i zapis `/etc/cloudflared/config.yml` (ścieżka przez `CF_GUI_CONFIG_PATH`).
- Standardowe narzędzia systemowe: `cloudflared`, `systemctl`, `journalctl`.
- `cf_gui/config.py`: YAML, wpisy ingress, backup i zapis.
- `cf_gui/cloudflared.py`: operacje CLI.
- `cf_gui/auth.py`: pojedynczy login admin.
- `cf_gui/web.py`: formularze i routing.
- `cf_gui/__main__.py`: serwer i dobór portu.

Ten stos ma mało zależności i działa na Debianie/Ubuntu bez Node.js i bez kompilowania zasobów. Panel należy najpierw wdrożyć na testowym LXC.

Szczegóły instalacji i użycia zostaną uzupełnione wraz z implementacją.
