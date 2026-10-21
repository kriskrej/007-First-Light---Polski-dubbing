# Przygotowanie wydania 0.9.0-rc1

Wydanie zawiera instalator Windows, gotowy polski dźwięk i różnice tekstowe odtwarzane z lokalnej gry. Odbiorca rozpakowuje ZIP i uruchamia `ZAINSTALUJ.cmd`; cofnięcie zapewnia `USUN.cmd`. Nie potrzebuje Pythona, Wwise ani konta ElevenLabs. W grze wybiera audio **English** i napisy **Polski**.

Instalator wymaga sprawdzonej wersji Steam. Sprawdza indeksy archiwów i oryginalne zasoby, wybiera wolne numery patchy, zachowuje lokalny manifest i dziennik, a następnie kontroluje zapis. Ponowne uruchomienie tej samej wersji nie dodaje kolejnej instalacji. Usunięcie obejmuje własne pliki i zmiany manifestu, z kontrolą późniejszych modyfikacji.

Pełne oryginalne DLGE i cudzy `packagedefinition.txt` nie są dołączone do paczki. Zmiany tekstu odtwarza się z własnej instalacji odbiorcy. Paczka audio, pliki gry, dane produkcyjne i backupy pozostają poza historią Git.

## Rzeczywisty zakres

Wersja kandydująca ma 38 475 polskich nagrań, w tym 27 631 w znanym korpusie z tekstem i 10 844 wcześniej przygotowane bez pierwotnych napisów. Zmapowane zakresy fabularne rozdziałów 1–17 są opracowane. W korpusie z tekstem zachowano 156 oryginalnych odgłosów lub wypowiedzi obcojęzycznych; pięć krótkich kwestii pozostaje nierozstrzygniętych. Daje to 99,982% zakresu wymagającego dubbingu z tekstem. Dalsze reakcje bez napisów wyłączono z produkcji na życzenie właściciela projektu. To nie jest procent czasu rozgrywki ani obietnica zastąpienia każdej reakcji w grze.

Nowe pliki przechodzą kontrolę offline. Potwierdzenia użytkownika obejmują wcześniejszą pierwszą wypowiedź, usta przez całą rozmowę z Q i około dwugodzinny grany fragment. Wydanie nie ma potwierdzenia pełnego przejścia gry.

## Publikacja

Przygotowanym miejscem publikacji jest [dział 007 First Light na Nexus Mods](https://www.nexusmods.com/games/007firstlight/mods). Plik główny powinien być oznaczony jako wersja kandydująca, z instalacją ręczną przez dołączony instalator. Nie deklarujemy obsługi Vortex. Kod może być udostępniany przez obecne repozytorium GitHub.

Opis musi informować o syntezie głosów ElevenLabs. Zgodnie z [zasadami Nexus Mods](https://help.nexusmods.com/article/28-file-submission-guidelines) zastosuj `AI-Generated Content`; dla wygenerowanego opisu lub materiałów promocyjnych także `AI Media`. Nie oznaczaj całego wydania wyłącznie jako `AI Assisted`.

Przed publicznym udostępnieniem audio pozostają konkretne potwierdzenia praw opisane w [LEGAL.md](../LEGAL.md): wymóg pisemnej zgody IOI z punktu 3.1(j) EULA oraz podstawa dystrybucji dwóch użytych własnych modeli głosu oznaczonych w koncie jako klonowane. Same posiadanie konta i płatnego planu nie stanowi dowodu tych zgód. Przegląd materiałów zawierających oryginalne odgłosy pozostaje częścią przeglądu praw. Kod i dokumentacja publikowane w repozytorium nie oznaczają publikacji paczki audio.
