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

Lokalna wersja produkcyjna ma kontrolę plików, instalacji i rollbacku oraz zestaw testów automatycznych. Użytkownik potwierdził pierwsze polskie słowa w grze. Pozostałe sceny i poprawka lipsync nie mają jeszcze pełnego potwierdzenia runtime.

Publiczny zestaw testów syntetycznych jest uruchamiany niezależnie od prywatnych danych. Kontrola z 17.10.2026 na Windows / Python 3.11: **96 testów, wynik OK, 5 testów wymagających plików gry pominiętych**. Ten sam zestaw w lokalnym workspace produkcyjnym przeszedł wszystkie 96 testów bez pominięć. Wynik testów kodu nie stanowi testu przejścia gry.
