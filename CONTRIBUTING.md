# Współpraca

Zgłoszenia i poprawki są mile widziane. Przed większą zmianą opisz problem w Issues i uzgodnij zakres. Zachowuj oryginalne informacje o autorach wykorzystanego kodu.

## Poprawki kodu

1. Utwórz osobną gałąź i ogranicz zmianę do konkretnego problemu.
2. Uruchom testy według [DEVELOPMENT.md](docs/DEVELOPMENT.md).
3. W pull requeście opisz problem, zmianę, walidację i ograniczenia.
4. Dla operacji na plikach gry uwzględnij dry-run, odczyt po zapisie i rollback.

## Materiały i prywatność

Nie przesyłaj kluczy API, danych kont, oryginalnych archiwów/audio gry, pełnego scenariusza, zapisów gry ani prywatnych logów. Testy powinny korzystać z syntetycznych danych; testy prawdziwych zasobów muszą działać wyłącznie lokalnie.

Uruchamianie testów nie może wykonywać płatnych żądań TTS. Generowanie wymaga osobnej konfiguracji i zatwierdzonego limitu. Zachowuj listę dozwolonych plików w `.gitignore` i sprawdzaj indeks przed commitem.

Zmiany objęte MIT są wnoszone na tej samej licencji. Dla innych materiałów wyraźnie podaj źródło i uprawnienia; nie zakładaj, że licencja kodu obejmuje audio lub teksty gry.
