# Stan projektu

Migawka lokalnej produkcji: **2026-10-21**. Autor: **Kris Krej**.

Gotowe **38 475** polskich nagrań; lokalnie zainstalowane **38 475**. Gotowy dubbing obejmuje **27 631 / 27 792** znanych nagrań z oryginalnym tekstem (**99,421%**) oraz **10 844** dodatkowych nagrań bez pierwotnych napisów. Lokalny kandydat 0.9.0-rc1 jest przygotowany; audio nie zostało publicznie udostępnione.

**Procent ukończenia całej gry pozostaje nieustalony.** Osobny audyt wykrył **55 041 kandydatów bez jakiegokolwiek tekstu EN/PL/source**. Wymagają klasyfikacji: część to mowa, część odgłosy i powtórzenia. Ta liczba nie jest liczbą brakujących kwestii do płatnej generacji. Wcześniejszy procent oparty wyłącznie na 27 792 nagraniach z tekstem nie obejmował tego dodatkowego korpusu.

Dodatkowy, rozłączny przegląd obejmuje **9 906 odgłosów VOX**. Zidentyfikowano i zachowano **3 570** oryginalnych odgłosów bez słów, m.in. wysiłku, bólu i kaszlu. Nie doliczamy ich do polskich nagrań. Oba spisy kandydatów służą klasyfikacji, a nie obliczaniu procentu całej gry lub liczby potrzebnych płatnych nagrań.

Poniższa tabela obejmuje rozpoznane zakresy fabularne, gotowe PL i celowo zachowane oryginały. 100% w tabeli nie oznacza wszystkich reakcji słyszalnych w danym rozdziale ani potwierdzenia całej sceny w grze.

| Nr | Rozdział | PL gotowe | Zachowane oryginały | Zmapowany zakres | Opracowane |
|---:|---|---:|---:|---:|---:|
| 1 | Against the Odds | 566 | 22 | 588 | 100.000% |
| 2 | In His Majesty’s Secret Service | 141 | 0 | 141 | 100.000% |
| 3 | The Needle’s Eye | 317 | 7 | 324 | 100.000% |
| 4 | The Heart of the Matter | 331 | 0 | 331 | 100.000% |
| 5 | A New Home | 564 | 6 | 570 | 100.000% |
| 6 | A Night Out | 547 | 0 | 547 | 100.000% |
| 7 | A Matter of Considerable Delicacy | 551 | 4 | 555 | 100.000% |
| 8 | All the Time in the World | 951 | 12 | 963 | 100.000% |
| 9 | Out of the Ashes | 400 | 0 | 400 | 100.000% |
| 10 | The Past Never Dies | 1876 | 10 | 1886 | 100.000% |
| 11 | Uninvited | 1165 | 5 | 1170 | 100.000% |
| 12 | Knightfall | 849 | 4 | 853 | 100.000% |
| 13 | Going Old School | 116 | 1 | 117 | 100.000% |
| 14 | Time to Die | 1623 | 8 | 1631 | 100.000% |
| 15 | Man of the Hour | 293 | 1 | 294 | 100.000% |
| 16 | Wave of the Future | 876 | 1 | 877 | 100.000% |
| 17 | For England | 375 | 5 | 380 | 100.000% |

Wspólna biblioteka NPC z oryginalnym tekstem: **15 693 / 15 745 PL — 99,67%**. Dodatkowe kwestie bez napisów są liczone osobno.

W grze potwierdzono pierwszą polską wypowiedź moda. Audyt ostatniej instalacji (2026-10-21) obejmuje **38 475 WEM i 7 829 DLGE**: zero markerów za data, zero zmian EN/FaceFX, zero brakujących zależności twarzy. Po poprawce markerów użytkownik potwierdził 19.10.2026 działanie ust przez całą rozmowę z Q w laboratorium. To potwierdzenie dotyczy zgłoszonego scenariusza, nie wszystkich cutscenek i nagrań.

Pełne wyniki lokalnej produkcji i rozliczenia pozostają prywatne. Publiczne wyniki testów kodu są widoczne w Actions; wymagane sprawdzenia wydania opisuje [TESTING.md](TESTING.md).

## Zakres po korekcie właściciela — 21.10.2026

Dalsze tłumaczenie i generowanie reakcji bez napisów wyłączono. W zakresie dialogów z tekstem wymagających dubbingu gotowe jest **27 631/27 636 (99,982%)**; **5** krótkich kwestii pozostaje w oryginale do rozstrzygnięcia. Zachowane oryginalne odgłosy i obce języki są liczone osobno. Pełnego procentu czasu gry nie wyliczamy z tych liczb.

Samodzielny EXE przeszedł instalację nad istniejącymi patchami, ponowną instalację tej samej paczki bez duplikacji, rollback zachowujący poprzednie pliki i manifest, a także czystą instalację i rollback. Testy wykonywano na kopii manifestu i patchy z dostępem tylko do odczytu do rzeczywistych bazowych archiwów. Nie jest to dodatkowy test działania w silniku gry.

Zestaw obejmuje 133 testy produkcyjne bez pominięć oraz 133 uruchomione z publicznego katalogu, z pięcioma pominiętymi wymagającymi prywatnych zasobów gry. Kandydat dla graczy nie wymaga Pythona, Wwise ani klucza API. [Materiały wydania](RELEASE_PUBLICATION.md) wyjaśniają zakres, instalację i otwarte uprawnienia publikacyjne.
