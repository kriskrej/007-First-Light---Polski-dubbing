<div align="center">

# 007 First Light — Polski dubbing

**Nieoficjalny polski dubbing do 007 First Light na PC / Steam**

Projekt i opracowanie: **Kris Krej**

[Stan projektu](docs/STATUS.md) · [Instalacja](docs/INSTALLATION.md) · [Znane problemy](docs/KNOWN_ISSUES.md) · [Rozwój narzędzi](docs/DEVELOPMENT.md)

</div>

Projekt ma umożliwić przejście całej gry z polskimi głosami i tekstem. Korzysta z oficjalnych polskich napisów dostępnych w grze; brakujące lub wymagające adaptacji kwestie są opracowywane z angielskiego oryginału. Głosy są syntetyzowane przy użyciu ElevenLabs.

## Stan projektu

**Trwa przygotowanie pierwszego publicznego wydania testowego.** Repozytorium zawiera kod narzędzi i dokumentację. Gotowa paczka dla graczy zostanie udostępniona osobno w Releases po zakończeniu przygotowań i sprawdzeniu uprawnień do dystrybucji.

| Pomiar lokalnej wersji — 17.10.2026 | Wynik |
|---|---:|
| Gotowe i lokalnie zainstalowane polskie nagrania | 14 071 |
| Pokrycie znanego korpusu nagrań z tekstem | 50,313% |
| Dodatkowe nagrania odzyskane bez pierwotnych napisów | 88 |
| Docelowa platforma | Windows / Steam |

Procent oznacza unikalne nagrania, a nie czas rozgrywki. Ukończenie zmapowanych scen fabularnych nie obejmuje automatycznie wspólnych reakcji postaci. Kontrola techniczna plików i potwierdzenie działania w grze są raportowane oddzielnie. Szczegóły: [stan i pokrycie](docs/STATUS.md).

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
