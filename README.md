# cf-gui

Prosty panel do zarządzania wpisami `ingress` lokalnego tunelu Cloudflare. Przeznaczony dla jednego administratora na LXC z Debianem lub Ubuntu, na którym działa `cloudflared`. `cf-gui` i `cloudflared-manager` mogą współistnieć: panel ma własną usługę, komendę aktualizacji i automatycznie wybiera wolny port.

## Stos i architektura

Python 3, Flask i PyYAML. Flask renderuje HTML na serwerze, więc nie ma Node.js ani budowania frontendu. Zależności runtime są dwie, a kod jest rozdzielony według zadań:

- `cf_gui/config.py` — YAML, revision, pliki candidate, backup, metadata i lock;
- `cf_gui/activation.py` — walidacja, aktywacja i rollback;
- `cf_gui/cloudflared.py` — walidacja przez CLI, `route dns`, restart, status i dziennik;
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
3. Dodaj, edytuj lub usuń wpis. Formularz wymaga domenowej nazwy `hostname` (dopuszcza `*.example.com`; limit 253 znaków obejmuje także `*.`) i poprawnego `service`. Adres originu bez schematu, np. `origin:8096` lub `192.0.2.79:8080`, dostaje prefiks `http://`; adresy originu ze ścieżką, query lub fragmentem są odrzucane. Jawne schematy, Unix socket (`unix:/...`, `unix+tls:/...`) oraz usługi specjalne (`bastion`, `socks-proxy`, `hello_world`, `hello-world`, `http_status:100–999`) pozostają bez zmian. Błędne dane są odrzucane przed przygotowaniem candidate. Aplikacja waliduje zmianę także przez `cloudflared`, tworzy backup, aktywuje config i automatycznie restartuje cloudflared. Panel pokazuje jeden z trzech wyników: sukces, nieudana aktywacja z udanym rollbackiem albo nieudana aktywacja z nieudanym rollbackiem. Po sukcesie używa krótkiego komunikatu; przy obu rodzajach błędu pełną diagnostykę pokazuje bezpośrednio na stronie, bez zapisywania jej w sesji cookie.
4. Przy dodawaniu można zaznaczyć tworzenie DNS, co wywołuje `cloudflared tunnel route dns <tunnel> <hostname>` tylko po udanej aktywacji. Przycisk „Utwórz DNS” przy wpisie umożliwia ponowienie tej akcji. Pole `tunnel` musi być obecne w YAML. Sukces daje krótki komunikat; przy błędzie pełny output komendy jest pokazany bezpośrednio na stronie.
5. W zakładce „Usługa” sprawdź status systemd i ostatnie 20 linii `journalctl`. Przycisk ręcznego restartu pozostaje dostępny.

### Bezpieczna aktywacja configu

Dla każdej mutacji `ingress` aplikacja trzyma advisory lock w pliku obok configu przez cały odcinek `read → revision check → candidate → validate → backup → atomic replace → restart/status → ewentualny rollback`. Lock serializuje zapisy dwóch procesów `cf-gui` oraz ręczny restart z panelu. Każdy formularz wysyła SHA-256 z **dokładnych bajtów pliku**, które były widoczne przy jego wyświetleniu. Po wejściu pod lock i ponownie tuż przed podmianą aplikacja sprawdza revision; konflikt odrzuca mutację i wymaga odświeżenia strony.

Candidate powstaje w prywatnym pliku tymczasowym w katalogu configu. Przed dotknięciem aktywnego pliku aplikacja sprawdza YAML i strukturę `ingress`, a następnie wykonuje `cloudflared tunnel --config <candidate> ingress validate`. Nieudana walidacja pozostawia aktywny config bez zmian, nie restartuje usługi i pokazuje pełną diagnostykę CLI bezpośrednio na stronie. Po udanej walidacji tworzy backup `config.yml.bak.YYYYMMDDTHHMMSSffffffZ`, atomowo podmienia config i restartuje cloudflared. Sukces wymaga udanego `systemctl restart`, poprawnego `systemctl status` i stanu `systemctl is-active`. Jeśli którykolwiek krok zawiedzie, aplikacja odtwarza poprzednie bajty z backupu, ponawia restart i ponownie sprawdza stan. Backup pozostaje na dysku. Przy podmianie zachowuje mode, uid i gid poprzedniego pliku; nowy plik bez poprzednika miałby mode `0600`.

Surowy output procesów `cloudflared`, `systemctl` i `journalctl` jest przekazywany w treści odpowiedzi HTTP, nigdy w sesji cookie Flask. Komunikaty `flash()` zawierają wyłącznie krótkie teksty kontrolowane przez aplikację.

Lock nie jest współdzielony z `cloudflared-manager`. Revision wykrywa zmianę wykonaną przez zewnętrzny writer **przed końcowym sprawdzeniem tuż przed podmianą**; nie eliminuje wyścigu między tym sprawdzeniem a samym `os.replace` ani zmian po aktywacji. Gdy config zmieni się po naszej podmianie, przed rollbackiem aplikacja nie nadpisuje tej nowszej wersji i zgłasza nieudany rollback. Walidacja ingress oraz stan `active` nie dowodzą poprawności ruchu przez tunel — to nadal należy sprawdzić na testowym LXC. PyYAML może zmienić formatowanie i usunąć komentarze z pliku YAML; pozostałe klucze oraz pola wpisów są zachowywane.

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
