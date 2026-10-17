# Narzędzia developerskie

Wymagany jest Python 3.11 lub nowszy oraz Git. W Windows polecenie `python` musi wskazywać właściwy interpreter. Na innych systemach może być dostępne jako `python3`.

## Testy bez instalacji gry

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools/fetch_test_dependencies.py
.\.venv\Scripts\python.exe tools/run_tests.py
.\.venv\Scripts\python.exe tools/check_repository.py
```

Skrypt pobiera do ignorowanego `research/` wyłącznie przypięte źródła toolboxa i toolkitu, potrzebne do testów. Nie pobiera gry, nie generuje głosów i nie wykonuje płatnych żądań. Testy wymagające rzeczywistych plików są pomijane bez lokalnej instalacji.

Zależności testowe wymagają pierwszego połączenia z GitHubem i PyPI. Kolejne uruchomienia sprawdzają przypięty commit i korzystają z lokalnych źródeł. `requirements-dev.txt` zawiera przypięty `imageio-ffmpeg`, który dostarcza FFmpeg do testów audio. Runner używa istniejącego FFmpeg z PATH albo umieszcza lokalną kopię testową w ignorowanym `artifacts/test-tools/`. Przed uruchomieniem testów usuwa klucze API ze środowiska procesu potomnego. Testy syntetyczne nie potrzebują Wwise ani klucza ElevenLabs.

## Praca na własnej kopii gry

Obecny kod wiąże `ROOT` z katalogiem projektu i oczekuje lokalnego `Runtime/`. Pełny workspace produkcyjny musi być przygotowany w katalogu instalacji gry. Sam klon tego repozytorium poza grą służy do rozwoju kodu i testów syntetycznych.

Przykłady w `config/*.example.json` wymagają dostosowania i zapisania pod nazwą bez `.example`. Lokalny `config/campaign.json` ma domyślnie blokować generowanie i zerowy budżet. Nie kopiuj limitów, historii ani identyfikatorów cudzych kont. Klucze przechowuj w ignorowanym `.env` lub zmiennych środowiskowych.

Odczyt pełnych zasobów wymaga osobno pozyskanych źródeł i narzędzi: zobacz [sources.json](sources.json). Kodowanie wymaga własnej instalacji Wwise, przygotowanie audio FFmpeg, a kontrola WEM narzędzia vgmstream. Ich binaria nie są częścią repozytorium.

```powershell
python -m dub007 --help
python -m dub007 campaign --help
python -m dub007 install --help
python -m dub007 rollback --help
```

Polecenia dotyczące gry wymagają przygotowanego workspace i danych. `api-check` wykonuje odczyty API; `campaign` może wykonywać płatne żądania dopiero po osobnym skonfigurowaniu i zatwierdzeniu limitów. Szczegóły komend sprawdzaj w `--help`.

## Publikacja

Kod i dokumentacja trafiają do Gita. Gotowe paczki są oddzielnymi załącznikami Releases i wymagają uprzedniego audytu praw oraz zawartości. Historia produkcji, wyodrębnione dialogi, audio, konta, cache i backupy pozostają poza repozytorium.

Przed commitem uruchom `python tools/check_repository.py --staged`. Ustaw autorstwo i adres GitHub noreply lokalnie dla repozytorium; sprawdź także metadane commita przed wysłaniem.
