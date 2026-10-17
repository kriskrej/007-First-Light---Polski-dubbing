# Instalacja i zgodność

## Aktualna dostępność

Pierwsza publiczna paczka dla graczy jest w przygotowaniu. Automatyczne archiwum „Source code” z GitHuba zawiera narzędzia, a nie kompletny dubbing. Gotowe wydanie będzie osobnym załącznikiem w Releases.

Docelowy zakres pierwszego wydania: Windows i wersja Steam, audio gry EN oraz napisy PL. Numer obsługiwanego buildu zostanie podany przy konkretnej paczce po testach. Zgodność innych sklepów i kolejnych aktualizacji wymaga oddzielnej weryfikacji.

## Docelowy przebieg instalacji wydania

Instalator sprawdzi zamknięcie gry, jej wersję, wolne miejsce, sumy plików oraz istniejące mody. Przed zmianą zapisze dziennik i dane rollbacku. Nowy pakiet ma zachować bazowe archiwa i używać własnych patchy.

Komendy dla paczki konsumenckiej zostaną opublikowane wraz z jej faktycznie sprawdzonym instalatorem. Obecne skrypty w repozytorium są narzędziami developerskimi i wymagają lokalnie przygotowanych danych.

## Aktualizacja i usuwanie

Zatrzymaj grę przed zmianami. Korzystaj z aktualizacji i odinstalowania należących do danego wydania. Zachowaj dziennik oraz backup do czasu poprawnej weryfikacji. Nie usuwaj całego Runtime i nie nadpisuj ustawień innych modów.

Po aktualizacji gry sprawdź zgodność wydania moda. Niezgodność musi skutkować odmową instalacji lub przywracania do starych offsetów, a nie wymuszonym zapisem.

Testy na innej instalacji: [TESTING.md](TESTING.md). Bieżące ograniczenia: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
