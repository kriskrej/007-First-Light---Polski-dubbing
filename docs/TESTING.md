# Plan testów wydania

## Kontrola techniczna

Sprawdź instalację na czystej kopii obsługiwanego buildu Steam, dry-run, odczyt po zapisie, rollback oraz ponowną instalację. Osobno sprawdź późniejszy mod, przerwanie instalacji i zmianę wersji gry. Testy destrukcyjnych scenariuszy wykonuj na kopiach danych syntetycznych.

## Kontrola w grze

- Początek Against the Odds: właściwe postacie, początek i koniec wypowiedzi, zgodność PL napisów.
- Out of the Ashes: odtwórz całą scenę w biurze M, przejście do Q-Lab i pierwszą rozmowę z Q. Sprawdź usta Bonda i Q przez całą rozmowę.
- Rozmowa przez radio, rozmowa zwykła i głos przez głośnik: poziom, pogłos i zrozumiałość.
- Dłuższa scena z kilkoma rozmówcami: pauzy, nakładanie głosów, wymowa nazw oraz liczb.
- Przywrócenie oryginalnego stanu: dźwięk, napisy i pozostałe mody.

Zapisuj build gry, wersję paczki, checkpoint, kroki i wynik. Krótki klip może pomóc w diagnozie. Nie rozpowszechniaj oryginalnych plików gry ani prywatnych zapisów.

## Zakres dotychczasowej walidacji

Zgłoszony około dwugodzinny test potwierdził dobrą jakość tłumaczenia w granym fragmencie; pozostałe usłyszane angielskie kwestie dotyczyły wspólnych okrzyków najemników, m.in. wykrycia gracza i granatów. Dokładna wersja paczki i rozdziały nie zostały określone. To potwierdzenie tego fragmentu, bez pełnej walidacji całej gry.

Lokalna wersja produkcyjna ma kontrolę plików, instalacji i rollbacku oraz zestaw testów automatycznych. Użytkownik potwierdził pierwsze polskie słowa w grze. 19.10.2026 użytkownik potwierdził po poprawce markerów działanie ust przez całą rozmowę z Q w laboratorium w Out of the Ashes. Pozostałe sceny wymagają dalszych testów w grze.

Publiczny zestaw testów syntetycznych jest uruchamiany niezależnie od prywatnych danych. Kontrola z 21.10.2026 na Windows / Python 3.11: **133 uruchomione testy, wynik OK, 5 testów wymagających plików gry pominiętych**. W lokalnym workspace produkcyjnym wszystkie 133 testy przeszły bez pominięć. Obejmują odczyt kampanii przy równoległym zapisie SQLite, import zakresu bez blokowania zapisu podczas odczytów konfiguracji, uzupełnianie starszych mapowań bez zmiany współdzielonych wpisów oraz odzyskiwanie historii pojedynczych wypowiedzi z kontrolą identyfikatorów. Wynik testów kodu nie zastępuje pełnego przejścia gry.


Dodatkowo sprawdzono rzeczywisty samodzielny EXE na danych aktualnego buildu: pełne odtworzenie patchy, instalację nad istniejącymi plikami, idempotencję, rollback z identycznym wcześniejszym manifestem i zachowanymi patchami, czystą instalację oraz czysty rollback. Zestaw 133 testów zawiera też regresje błędu flush/replace WAV, zawężania płatnego zakresu i pojedynczej przejrzanej zmiany modelu. Żaden z tych testów nie stanowi pełnego przejścia gry.
