<div align="center">

# 007 First Light — Polski dubbing

**Nieoficjalny polski dubbing do 007 First Light na PC / Steam**

Projekt i opracowanie: **Kris Krej**

[Stan projektu](docs/STATUS.md) · [Instalacja](docs/INSTALLATION.md) · [Znane problemy](docs/KNOWN_ISSUES.md) · [Rozwój narzędzi](docs/DEVELOPMENT.md)

</div>

Projekt ma umożliwić przejście całej gry z polskimi głosami i tekstem. Korzysta z oficjalnych polskich napisów dostępnych w grze; brakujące lub wymagające adaptacji kwestie są opracowywane z angielskiego oryginału. Głosy są syntetyzowane przy użyciu ElevenLabs.

## Próbka dubbingu w grze

https://github.com/user-attachments/assets/d099d0b6-7952-49cb-946b-a754983824b2

[Obejrzyj na YouTube](https://www.youtube.com/watch?v=F9PckKLI7JM)

Nagranie prezentuje dubbing w grze z lokalnej wersji rozwojowej projektu. Publiczna paczka dla graczy jest w przygotowaniu.

## Stan projektu

**Trwa przygotowanie pierwszego publicznego wydania testowego.** Repozytorium zawiera kod narzędzi i dokumentację. Gotowa paczka dla graczy zostanie udostępniona osobno w Releases po zakończeniu przygotowań i sprawdzeniu uprawnień do dystrybucji.

| Pomiar lokalnej wersji — 2026-10-21 | Wynik |
|---|---:|
| Gotowe polskie nagrania | 38 475 |
| Lokalnie zainstalowane polskie nagrania | 38 475 |
| Pokrycie znanego korpusu nagrań z oryginalnym tekstem | 99,421% |
| Dodatkowe gotowe nagrania bez pierwotnych napisów | 10 844 |
| Pokrycie zakresu dialogów z tekstem wymagających dubbingu | 99,982% |
| Pozostałe nierozstrzygnięte kwestie w tym zakresie | 5 |
| Procent ukończenia całej gry | W trakcie ustalania |
| Docelowa platforma | Windows / Steam |

W korpusie z tekstem gotowe jest 27 631 polskich nagrań; 156 oryginalnych odgłosów lub wypowiedzi obcojęzycznych zachowano celowo, a pięć krótkich kwestii pozostaje nierozstrzygniętych. Po wyłączeniu zachowanych oryginałów zakres wymagający dubbingu ma **99,982%** pokrycia. To pomiar zasobów, a nie czasu rozgrywki.

Dalsze reakcje bez napisów wyłączono z produkcji na życzenie właściciela projektu. Wcześniej przygotowane 10 844 dodatkowe polskie nagrania pozostają w paczce. Nie deklarujemy zastąpienia każdej reakcji słyszalnej w grze. Szczegóły: [stan](docs/STATUS.md) i [przygotowanie wydania](docs/RELEASE_PUBLICATION.md).

Lokalnie przygotowano **0.9.0-rc1** z samodzielnym instalatorem Windows i rollbackiem. Paczka audio nie została opublikowana. Przed uploadem pozostają potwierdzenia uprawnień opisane w [LEGAL.md](LEGAL.md).

## Dla graczy

Docelowa paczka będzie zawierać przygotowany dubbing oraz instalator i odinstalowanie. Do korzystania z gotowego wydania nie będzie potrzebny klucz API ani samodzielne generowanie głosów.

Wymagana jest własna kopia gry. Pierwsze wydanie będzie przygotowane dla konkretnej, przetestowanej wersji Steam. Zasady zgodności, instalacji i aktualizacji: [instrukcja](docs/INSTALLATION.md).

## Narzędzia

- Odczyt i mapowanie zasobów dialogowych oraz oficjalnego tekstu PL.
- Wznawialne generowanie z cache, kontrolą kosztów i historią żądań.
- Dopasowanie czasu, poziomu i markerów nagrań.
- Budowa osobnych patchy RPKG z kontrolą odczytu po zapisie.
- Dziennik instalacji, kontrola zgodności i odwracalne usuwanie własnych zmian.

Repozytorium narzędzi nie zastępuje gotowej paczki wydania. Uruchamianie produkcji dubbingu wymaga lokalnych danych, konfiguracji i uprawnień opisanych w [dokumentacji developerskiej](docs/DEVELOPMENT.md).

## Zgłaszanie problemów

W Issues podaj wersję moda i gry, rozdział, checkpoint oraz krótki opis oczekiwanego i rzeczywistego zachowania. Przy błędzie audio pomocny jest krótki klip. Sprawdź wcześniej [znane problemy](docs/KNOWN_ISSUES.md). Nie dołączaj kluczy, pełnych plików gry, prywatnych danych ani zapisów konta.

## Autorstwo i licencje

Autorem projektu jest **Kris Krej**. Wykorzystane projekty i ich autorów wymieniono w [Credits](CREDITS.md) oraz [Third-party notices](THIRD_PARTY_NOTICES.md).

Licencja MIT obejmuje własny kod narzędzi i dokumentację w zakresie posiadanych praw. Zasoby gry, teksty, modele głosów i paczki audio podlegają osobnym zasadom. Informacje o prawach i warunkach publikacji: [LEGAL.md](LEGAL.md).

Projekt fanowski, niezależny od IO Interactive i właścicieli praw do Jamesa Bonda. Nie jest oficjalną lokalizacją ani produktem przez nich zatwierdzonym.
