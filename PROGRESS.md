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
