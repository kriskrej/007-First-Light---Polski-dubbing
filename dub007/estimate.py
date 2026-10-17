"""Scenario estimate, intentionally distinct from verified audio deduplication."""
import json

from .common import ARTIFACTS, ROOT, save_json, active_account


def run():
    account_id = active_account()
    rate, usd_pln, tax, retakes = 0.08, 3.9042, 0.23, 0.30
    rows = []
    for characters in (500_000, 1_000_000, 2_000_000):
        submitted = round(characters * (1 + retakes))
        dollars = submitted / 1000 * rate
        rows.append({"text_pl_characters_assumed": characters, "retake_and_tags_reserve": retakes,
                     "characters_submitted": submitted, "tts_usd_before_tax": round(dollars, 2),
                     "tts_pln_before_tax": round(dollars * usd_pln, 2),
                     "tts_pln_with_assumed_tax": round(dollars * usd_pln * (1 + tax), 2)})
    report = {"status": "preliminary_scenarios_not_a_corpus_quote", "pricing_checked_date": "2026-10-14",
              "pricing_url": "https://elevenlabs.io/pricing/api", "models": ["eleven_v4", "eleven_v3", "eleven_multilingual_v2"],
              "usd_per_1000_characters": rate, "usd_pln_assumed": usd_pln, "tax_assumed": tax,
              "paid_translation_api": False, "account_plan_checked": False,
              "limitations": ["Unmapped dialogue and speech without subtitles still require classification",
                              "Actual invoice conversion into PLN and possible voice premiums are not final invoice evidence",
                              "NBP USD/PLN 3.9042 from 2026-10-14 is a mid rate; bank conversion and taxes may differ",
                              "No Wwise purchases or plan upgrades included"], "scenarios": rows,
              "poc_max_characters": 10_000, "poc_max_usd_before_tax": 0.8,
              "poc_max_pln_with_assumed_tax": round(0.8 * usd_pln * (1 + tax), 2),
              "exchange_rate_source": "https://api.nbp.pl/api/exchangerates/rates/a/usd/2026-10-14/?format=json"}
    from .tts import usage
    report["tts_characters_sent"] = usage()["submitted_characters"]
    campaign_path = ROOT / "docs/CAMPAIGN_PROGRESS.json"
    if campaign_path.exists():
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        report["campaign_usage_observed"] = campaign["usage"]
        report["campaign_usage_checked_utc"] = campaign["updated_utc"]
        report["tts_characters_sent"] = campaign["usage"]["submitted_characters"]
    corpus_path = ROOT / "docs" / "CORPUS.json"
    coverage_path = ROOT / "docs" / "COVERAGE.json"
    if corpus_path.exists() and coverage_path.exists():
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        base_chars = corpus["characters_pl_after_audio_dedup"]
        fallback_chars = round(coverage["missing_pl_english_characters"] * 1.10)
        submitted = round((base_chars + fallback_chars) * (1 + retakes))
        dollars = submitted / 1000 * rate
        report["known_corpus_estimate"] = {
            "official_pl_characters": base_chars, "fallback_pl_characters_assumed": fallback_chars,
            "fallback_length_multiplier_assumed": 1.10, "characters_submitted_with_reserve": submitted,
            "tts_usd_before_tax": round(dollars, 2), "tts_pln_before_tax": round(dollars * usd_pln, 2),
            "tts_pln_with_assumed_tax": round(dollars * usd_pln * (1 + tax), 2),
            "quoted_audio_resources": coverage["unique_audio_with_en_or_pl_text"],
            "unresolved_dlge_resources": corpus["unresolved_dlge_resources"],
            "audio_without_en_or_pl_text": coverage["audio_without_en_or_pl_text"],
            "scope": "known scripted speech, not yet a final quote for every spoken asset"}
    account_path = ARTIFACTS / "elevenlabs_preflight.json"
    preflight = json.loads(account_path.read_text(encoding="utf-8")) if account_path.exists() else {}
    if preflight and preflight.get("account_id", "1") == account_id:
        account = preflight["account"]
        report["account_plan_checked"] = True
        report["account_observed"] = {key: account.get(key) for key in
                                       ("tier", "status", "character_count", "character_limit", "can_extend_character_limit")}
        report["account_observed"]["api_dollar_balance_conversion_verified"] = False
        actual_path = ARTIFACTS / "poc" / "account-after-sample.json"
        if account_id == "1" and actual_path.exists():
            actual = json.loads(actual_path.read_text(encoding="utf-8"))
            report["account_observed"].update({key: actual[key] for key in ("character_count", "character_limit")})
    snapshot_path = ARTIFACTS / "campaign" / f"account-{account_id}-credit-snapshot.json"
    if account_id == "1" and not snapshot_path.exists():
        snapshot_path = ARTIFACTS / "campaign/account-credit-snapshot.json"
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        report["account_plan_checked"] = True
        report["account_observed"] = {**snapshot["account"],
                                      "checked_unix": snapshot["checked_unix"],
                                      "api_dollar_balance_conversion_verified": False}
    report["account_id"] = account_id
    save_json(ROOT / "docs" / "ESTIMATE.json", report)
    measured = report.get("known_corpus_estimate")
    notes = ["# Wstępna wycena pełnego polskiego dubbingu", "", "Sprawdzenie cennika: 14.10.2026. Wyliczenia opisują generację głosów przez TTS. Tłumaczenie i adaptację wykonuje agent; nie planujemy osobnego płatnego API tłumaczeniowego.",
             "", "## Stawka i założenia", "", "Oficjalny [cennik ElevenAPI](https://elevenlabs.io/pricing/api) podaje 0,08 USD / 1000 znaków dla v4, v3 i Multilingual v2. Ceny API są w USD i nie zawierają podatków; nie przeliczać automatycznie kredytów ElevenCreative na rozliczenie API.",
             "", "Kurs średni NBP z 14.10.2026: 3,9042 PLN/USD, tabela 200/A/NBP/2026. Kurs banku i podatki mogą się różnić. Podatek: założone 23%. Zapas na tagi, poprawki tekstu i retake'i: 30% znaków. Podstawa nie obejmuje taryf własnych głosów; Greenway ma potwierdzone wyższe pobranie kredytów. Nie obejmuje zakupów licencji ani zwiększania planu. Rezerwa jest szacunkiem, nie ograniczeniem późniejszych partii.",
             "", "## Odczyt lokalnej buildy", ""]
    if measured:
        notes += [f"Build Steam: 25753571. Po deduplikacji według angielskiego zasobu audio odczytano **{base_chars:,} znaków oficjalnego PL**. Jest {measured['quoted_audio_resources']:,} unikalnych zasobów z tekstem EN lub PL. Dla 614 zasobów bez PL znanych jest 15 234 znaków EN; założono 10% rozszerzenia przy tłumaczeniu.",
                  "", f"Znany korpus z rezerwą: **{submitted:,} znaków TTS**, około **{measured['tts_pln_with_assumed_tax']:.2f} PLN brutto** ({measured['tts_usd_before_tax']:.2f} USD przed podatkiem). Roboczy budżet dla całego projektu dubbingu: **600–1000 PLN**; kwota wymaga aktualizacji po rozwiązaniu brakujących mapowań i QA.",
                  "", f"Pozostaje {measured['unresolved_dlge_resources']} nierozpoznanych wariantów DLGE oraz {measured['audio_without_en_or_pl_text']:,} aktywnych referencji audio bez tekstu EN/PL. Nie wolno uznać ich wszystkich za mowę do płatnej generacji ani wszystkich za odgłosy niewymagające tłumaczenia. Trzeba je sklasyfikować; słyszalna mowa bez tekstu powiększy korpus. Powyższa kwota nie jest gwarantowanym maksimum całej gry."]
    notes += ["", "## Warianty objętości", "", "| Znaki PL przed rezerwą | Znaki wysłane z rezerwą 30% | USD przed podatkiem | PLN z założonym podatkiem |", "|---:|---:|---:|---:|"]
    for row in rows:
        notes.append(f"| {row['text_pl_characters_assumed']:,} | {row['characters_submitted']:,} | {row['tts_usd_before_tax']:.2f} | {row['tts_pln_with_assumed_tax']:.2f} |")
    notes += ["", "## Konto i zużycie", "", "Odczyt API potwierdził konto Creator, limit 300000 kredytów i dostęp do v4. Używamy istniejących kredytów; nie zmieniono planu ani dopłat. Dane odczytu konta i jego czas są w ESTIMATE.json. Istniejący abonament jest osobnym kosztem.",
              "", f"Łącznie wysłano **{report['tts_characters_sent']} znaków do TTS**, z tagami i retake'ami. Kredyty mogą być wyższe z powodu taryf głosów. Bieżące uzgodnione kredyty i orientacyjny ekwiwalent PLN: `CAMPAIGN_PROGRESS.json`; zasady: `COST_RECONCILIATION.md`. Ekwiwalent nie jest fakturą ani nową płatnością kartą.",
              "", "Użytkownik zatwierdził pełny dubbing po działającej próbce. Nocna zgoda obejmuje dotychczasowy projekt i ma limit 200 PLN do 07:00 Europe/Warsaw 15.10.2026. Ledger sprawdza limit, czas i dostępne kredyty przed płatnym POST. Nie kupować planu ani włączać automatycznych dopłat.",
              "", "Korpus zawiera również odgłosy i obce języki zachowywane po weryfikacji bez TTS. Napisy, WEM-y i płatne generacje nie są równoważnymi licznikami. Robocze 600–1000 PLN pozostaje przedziałem planistycznym; taryfy głosów i nierozpoznana mowa nie pozwalają zagwarantować maksymalnego kosztu."]
    (ROOT / "docs" / "ESTIMATE.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return report
