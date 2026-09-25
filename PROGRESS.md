# Postęp

## Etap A — szkielet repo

Zrobione: struktura pakietu, katalogi szablonów, skryptów i testów; README z podziałem modułów oraz wyborem stosu.

Decyzja: Python 3, Flask i PyYAML. Brak kroku budowania frontendu; jeden administrator i jedna lokalna usługa.

Dalej: moduł konfiguracji YAML, backup, walidacja i testy.

## Etap B — konfiguracja YAML

Zrobione: odczyt i walidacja struktury, formularzowe operacje na wpisach hostname, backup z czasem UTC przed każdym zapisem oraz atomowa podmiana pliku. Testy obejmują backup, fallback, duplikaty i błędny YAML.

Decyzja: przy zapisie PyYAML może zmienić formatowanie i komentarze YAML; pozostałe klucze i pola wpisów są zachowane. Wpis bez hostname jest traktowany jako fallback i nie podlega edycji formularzem.

Dalej: komendy cloudflared, restart i logi.

## Etap C — kontrola cloudflared

Zrobione: osobny moduł dla `route dns`, restartu usługi, statusu i 20 ostatnich linii dziennika. Komendy uruchamiane bez powłoki, a po próbie restartu status i logi są pobierane również przy błędzie.

Decyzja: błędy CLI są zwracane jako wynik z tekstem, aby interfejs mógł je pokazać użytkownikowi.

Dalej: pojedynczy login admin i sesja cookie.

## Etap D — autentykacja

Zrobione: jedno konto `admin`, weryfikacja hasła z hasha Werkzeug, sesja cookie Flask i pojedynczy token dla formularzy POST.

Decyzja: hash hasła i losowy sekret sesji pochodzą z pliku środowiskowego tworzonego przez installer. Nie ma bazy użytkowników ani dodatkowych warstw autoryzacji.

Dalej: routing HTTP łączący moduły.

## Etap E — routing HTTP

Zrobione: trasy logowania, listy, dodawania, edycji, usuwania, DNS, statusu i restartu; uruchamianie WSGI na pierwszym wolnym porcie od 8000 i wypisanie wybranego portu do dziennika usługi.

Decyzja: po dodaniu wpisu można od razu utworzyć DNS, ale wynik DNS jest komunikatem osobnym od zapisu YAML. Zmiana YAML nie restartuje automatycznie cloudflared; operator używa przycisku restartu.

Dalej: szablony i prosty styl interfejsu.

## Etap F — interfejs

Zrobione: ekran logowania, tabela ingress, formularz dodawania/edycji, usuwanie, tworzenie DNS, status i dziennik usługi. CSS jest lokalny, responsywny, bez JavaScriptowego builda.

Decyzja: usunięcie i restart proszą o proste potwierdzenie w przeglądarce; wszelkie wyjście komend jest widoczne jako tekst.

Dalej: installer na Debian/Ubuntu.

## Etap G — installer

Zrobione: `scripts/install.sh` instaluje pakiety, klonuje repo do `/opt/cf-gui`, tworzy venv, losowe hasło admina i sekret sesji, plik `/etc/cf-gui.env`, usługę `cf-gui.service` i komendę `cf-gui-update`. Po starcie wypisuje hasło i port z dziennika.

Decyzja: usługa działa jako root, ponieważ w tym pojedynczym LXC musi zapisać `/etc/cloudflared/config.yml` i restartować systemową usługę cloudflared. Nazwy i ścieżki są odrębne od cloudflared-manager.

Dalej: skrypt aktualizujący.

## Etap H — updater

Zrobione: `scripts/update.sh`, uruchamiany unikalną komendą `cf-gui-update`, pobiera `main` przez fast-forward, aktualizuje zależności i restartuje wyłącznie `cf-gui.service`. Lokalnie zmodyfikowany kod blokuje aktualizację zamiast zostać nadpisany.

Decyzja: config YAML, hasło i sekret leżą poza repo `/opt/cf-gui`, więc aktualizacja ich nie dotyka. Skrypt nie używa nazw ani ścieżek cloudflared-manager.

Dalej: finalna dokumentacja oraz weryfikacja.

## Etap I — dokumentacja i przegląd

Zrobione: pełne README z instalacją, obsługą, aktualizacją, ograniczeniami YAML i testowym LXC. Dodano metadane pakowania, aby szablony i CSS trafiły do instalowanego pakietu. Installer wypisuje hasło przed startem usługi.

Weryfikacja: `compileall` i `bash -n` przeszły. Pełne testy uruchomiono w późniejszym etapie J.

Dalej: test instalacji i działania na testowym LXC, potem wdrożenie produkcyjne.


## Etap J — uruchomienie testów

Zrobione: utworzono osobne `.venv` projektu, zainstalowano zależności i dodano test HTTP obejmujący logowanie, formularz dodania, DNS oraz restart. `pytest -q`: 6 testów zaliczonych. Zbudowano wheel i sprawdzono obecność szablonów oraz CSS. Lokalny test z zajętym portem potwierdził przejście na kolejny port.

Decyzja: systemowy Python w tym kontenerze nie ma `ensurepip`, więc własne `.venv` utworzono opcją `--without-pip`, a pip doinstalowano przez istniejące narzędzie; środowisko testowe pozostaje odrębne od sąsiedniego projektu.

Dalej: wypchnąć repo do GitHub i przetestować installer oraz integrację z prawdziwym cloudflared na testowym LXC.


## PR 1 — bezpieczna aktywacja configu (branch `pr1-safe-config-activation`)

Zrobione: candidate YAML jest walidowany przez `cloudflared tunnel --config <candidate> ingress validate` przed backupem i podmianą. Po atomowej aktywacji aplikacja restartuje usługę, sprawdza `is-active` i w razie niepowodzenia przywraca backup oraz ponawia restart. GUI rozróżnia sukces, udany rollback i nieudany rollback, pokazując diagnostykę. Mode, uid i gid istniejącego configu są zachowywane. SHA-256 dokładnej zawartości pliku jest wysyłany w formularzach i sprawdzany pod advisory lock przed zapisem. Lock obejmuje całą aktywację i serializuje mutacje procesów `cf-gui`.

Decyzja: `cloudflared-manager` nie używa naszego locka; przed końcowym sprawdzeniem revision jego zmiany są wykrywane, lecz pozostaje krótki wyścig z writerem zewnętrznym między sprawdzeniem a atomową podmianą. Po wykryciu późniejszej obcej zmiany rollback nie nadpisuje jej. Zachowano PyYAML, root service, bind i model jednego administratora.

Weryfikacja: rozszerzone testy obejmują walidację, backup, rollback obu wyników, stale revision, błędny indeks, metadata i blokadę równoległych mutacji. `pytest -q`: 28 testów zaliczonych. `compileall`, `bash -n` i `git diff --check` przeszły. Nadal potrzebny test z prawdziwym cloudflared/systemd na testowym LXC.


## PR 1 — poprawka po Codex Review: diagnostyka poza cookie

Zrobione: po sukcesie aktywacji pozostaje krótki flash i redirect. Wynik `rolled_back` jest renderowany bezpośrednio w HTTP 409, a `rollback_failed` w HTTP 500. Strona pokazuje oba restarty, statusy systemd, `is-active`, logi oraz wynik przywracania pliku, jeśli dany krok wystąpił. Żadna diagnostyka aktywacji/rollbacku nie jest zapisywana w sesji Flask.

Weryfikacja: test bardzo długiego dziennika potwierdza obecność treści w body i brak wywołania `flash()`, brak `_flashes` w sesji oraz brak dużego nagłówka cookie; testy sprawdzają komplet informacji dla obu stanów błędu. Pełne `pytest -q`: 30 testów zaliczonych. `compileall`, `bash -n` i `git diff --check` przeszły. Logika candidate, walidacji, revision, locka i rollbacku nie została zmieniona.


## PR 1 — poprawka po Codex Review: output CLI poza sesją

Zrobione: odrzucony candidate ma osobny typ błędu z pełną diagnostyką; GUI pokazuje ją w body wraz z informacją, że aktywny config nie został zmieniony i nie było restartu. Błąd `route dns` pokazuje pełny output w body, a sukces używa krótkiego stałego komunikatu. Pozostałe błędy formularza są prezentowane bez `flash(str(exc))`. Nie zmieniono wykonania komend ani logiki aktywacji.

Decyzja: ogólna zasada prezentacji to brak surowego outputu `cloudflared`, `systemctl` i `journalctl` w sesji cookie. Pełna diagnostyka trafia bezpośrednio do odpowiedzi HTTP. Testy obejmują długie wyniki walidacji i DNS oraz brak ich treści w sesji. Pełne `pytest -q`: 36 testów zaliczonych; `compileall`, `bash -n` i `git diff --check` przeszły. Nadal potrzebna jest weryfikacja na testowym LXC z prawdziwym `cloudflared`.


## PR 2 — walidacja pól ingress

Zrobione: formularz dodawania i edycji przyjmuje `hostname` tylko jako poprawną nazwę domenową z co najmniej dwiema etykietami, limitem 63 znaków na etykietę i 253 na całość. Origin bez schematu z poprawnym portem dostaje prefiks `http://`; jawne schematy i `http_status:404` zostają zachowane. Niepoprawne IPv4, port i nazwy hostów originu są odrzucane przed stworzeniem candidate. Walidacja CLI pozostaje kolejnym krokiem.

Decyzja: pojedyncza etykieta jest dozwolona dla lokalnego hosta originu, ale nie dla publicznego `hostname` ingressu. Nie dodano zależności; parser URL i walidacja IP pochodzą z biblioteki standardowej Pythona.

Weryfikacja: `pytest -q` — 68 testów zaliczonych; `compileall`, `bash -n` i `git diff --check` przeszły. Nadal należy sprawdzić całość z prawdziwym `cloudflared` na testowym LXC.

Poprawka po review: `hostname` dopuszcza `*.` wyłącznie na początku, z walidacją domeny dla sufiksu i limitem 253 znaków dla całego hostname. `service` zachowuje formy `unix:/...`, `unix+tls:/...`, `bastion`, `socks-proxy`, `hello_world` i `hello-world`; `http_status` wymaga kodu 100–999. Adresy originu ze ścieżką, query lub fragmentem są odrzucane przed candidate. Dodano regresje dla dodawania i edycji, także dla pełnej długości wildcardu 253/254/255 znaków; walidacja przez `cloudflared` pozostaje drugim krokiem. Pełne `pytest -q`: 103 testy zaliczone; `compileall`, `bash -n` i `git diff --check` przeszły.

Przykłady adresów infrastruktury w testach i dokumentacji zastąpiono domenami `example.com`, adresami z puli dokumentacyjnej `192.0.2.0/24` oraz neutralną nazwą lokalną `origin`.
