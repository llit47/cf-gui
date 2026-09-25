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
