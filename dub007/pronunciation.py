"""Project-wide spoken forms; official subtitle spelling stays untouched."""
import re


MI6 = re.compile(r"(?<!\w)(?:MI[ -]?6|em\s+i\s+sześć)(?!\w)", re.IGNORECASE)
AGENT_ID = re.compile(r"(?<!\w)00([1-9])(?!\w)")
AGENT_DIGITS = ("", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć")


def spoken_text(text):
    text = MI6.sub("Em Aj Six", text)
    return AGENT_ID.sub(lambda match: "zero zero " + AGENT_DIGITS[int(match[1])], text)


def apply(row):
    """Normalize both the paid text and the character-alignment search strings."""
    before = row["text_pl_tts"]
    row["text_pl_tts"] = spoken_text(before)
    displayed = row.get("display_segments") or [segment["text"] for segment in row.get("segments_pl", [])]
    parts = row.get("spoken_segments") or displayed
    normalized = [spoken_text(part) for part in parts]
    preserved = set(row.get("preserve_original_segment_indices", []))
    def matches(values):
        cursor = 0
        for index, value in enumerate(values):
            if index in preserved:
                continue
            position = row["text_pl_tts"].casefold().find(value.casefold(), cursor)
            if position < 0:
                return False
            cursor = position + len(value)
        return True
    if len(displayed) > 1:
        if len(normalized) != len(displayed) or not matches(normalized):
            current = [spoken_text(part) for part in displayed]
            if not matches(current):
                raise ValueError("Spoken segments do not match current TTS text; review before any paid request")
            normalized = current
            row["spoken_segments"] = normalized
            row["spoken_segment_review"] = "Rebuilt stale spoken alignment strings after a script adaptation; current paid text unchanged."
    if normalized != parts:
        row["spoken_segments"] = normalized
    if before != row["text_pl_tts"] or normalized != parts:
        row["pronunciation_review"] = "User preference: MI6 is spoken Em Aj Six and standalone00agent IDs preserve both spoken zeros; subtitle spelling unchanged."
    return row
