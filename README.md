# cf-gui

Prosty panel do zarządzania wpisami `ingress` lokalnego tunelu Cloudflare. Przeznaczony dla jednego administratora na LXC z Debianem lub Ubuntu, na którym działa `cloudflared`. `cf-gui` i `cloudflared-manager` mogą współistnieć: panel ma własną usługę, komendę aktualizacji i automatycznie wybiera wolny port.

## Stos i architektura

Python 3, Flask i PyYAML. Flask renderuje HTML na serwerze, więc nie ma Node.js ani budowania frontendu. Zależności runtime są dwie, a kod jest rozdzielony według zadań:

- `cf_gui/config.py` — YAML, wpisy ingress, walidacja i backup;
- `cf_gui/cloudflared.py` — `route dns`, restart, status i dziennik;
- `cf_gui/auth.py` — pojedynczy login admin i sesja cookie;
- `cf_gui/web.py` — formularze i routing HTTP;
- `cf_gui/__main__.py` — serwer i wybór portu;
- `cf_gui/templates/` i `cf_gui/static/` — interfejs bez kroku budowania.

## Instalacja

Najpierw przetestuj na **testowym LXC** z działającym `cloudflared` i poprawnym `/etc/cloudflared/config.yml`. Po sprawdzeniu tworzenia backupu, wpisu DNS i restartu przenieś instalację na produkcyjny LXC.

Jako `root` na Debianie/Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/llit47/cf-gui/main/scripts/install.sh | bash
```

Installer instaluje pakiety systemowe, klonuje repo do `/opt/cf-gui`, tworzy środowisko Python i usługę `cf-gui.service`. Wypisuje wygenerowane hasło admina **jeden raz** oraz port, na którym panel wystartował. Zapisz hasło. Hash hasła i sekret sesji są w `/etc/cf-gui.env` z uprawnieniami `600`. W razie problemów ze startem sprawdź `journalctl -u cf-gui -n 50 --no-pager`.

Domyślny adres to `http://ADRES-LXC:8000`. Jeśli port 8000 jest zajęty, aplikacja próbuje 8001, 8002 itd. Rzeczywisty port jest zawsze zapisany w dzienniku `cf-gui.service`. Instalator go również wypisuje. Przed instalacją można ustawić `CF_GUI_CONFIG_PATH` (domyślnie `/etc/cloudflared/config.yml`) i `CF_GUI_PORT` (port początkowy, domyślnie 8000), np.:

```bash
curl -fsSL https://raw.githubusercontent.com/llit47/cf-gui/main/scripts/install.sh | CF_GUI_CONFIG_PATH=/inny/config.yml CF_GUI_PORT=8010 bash
```

Jeśli `curl` jest uruchamiany jako zwykły użytkownik, do uruchomienia instalatora jako root użyj `sudo bash` na końcu polecenia. Zmienne instalatora należy wtedy przekazać po `sudo`.

Usługa działa jako root, ponieważ zapisuje systemowy config cloudflared i wykonuje `systemctl restart cloudflared`. Panel nasłuchuje na wszystkich interfejsach LXC. Dostęp ogranicz zgodnie z konfiguracją własnej sieci i tunelu.

## Użycie

1. Zaloguj się hasłem wypisanym przez instalator.
2. Lista pokazuje wpisy `ingress` z `hostname` i ich `service`. Wpis fallback bez `hostname` pozostaje w configu i nie jest edytowany formularzem.
3. Dodaj, edytuj lub usuń wpis. Każdy zapis tworzy kopię poprzedniego pliku obok configu: `config.yml.bak.YYYYMMDDTHHMMSSffffffZ`. Następnie aplikacja waliduje wygenerowany YAML i atomowo podmienia plik.
4. Przy dodawaniu można zaznaczyć tworzenie DNS, co wywołuje `cloudflared tunnel route dns <tunnel> <hostname>`. Przycisk „Utwórz DNS” przy wpisie umożliwia ponowienie tej akcji. Pole `tunnel` musi być obecne w YAML. Wynik komendy jest pokazany w panelu.
5. W zakładce „Usługa” zrestartuj `cloudflared` po zmianach. Panel pokaże wynik restartu, status systemd i ostatnie 20 linii `journalctl`.

PyYAML może zmienić formatowanie i usunąć komentarze z pliku YAML podczas zapisu; pozostałe klucze oraz pola wpisów są zachowywane. Kopia sprzed zapisu pozwala wrócić do oryginału. Formularz sprawdza składnię YAML i podstawową strukturę `ingress`; wynik działania usługi widać po restarcie.

## Aktualizacja

```bash
cf-gui-update
```

Uruchom jako root. Aktualizator pobiera `main` przez fast-forward, aktualizuje pakiet Python i restartuje wyłącznie `cf-gui.service`. Nie modyfikuje plików, komend ani usługi `cloudflared-manager`. Nie nadpisuje lokalnych zmian w `/opt/cf-gui`; w takim przypadku kończy się z komunikatem. Config i dane logowania leżą poza repo i zostają zachowane.

## Rozwój lokalny

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

Do uruchomienia lokalnego ustaw `CF_GUI_CONFIG_PATH`, `CF_GUI_PASSWORD_HASH` (hash Werkzeug) i `CF_GUI_SECRET_KEY`, potem wykonaj `.venv/bin/python -m cf_gui`. Dla szybkiego wygenerowania hasha: `.venv/bin/python -c 'from werkzeug.security import generate_password_hash; print(generate_password_hash("hasło"))'`.
