"""Small, validated mood vocabulary for paragraph narration."""

from __future__ import annotations

import json
import re
from typing import Literal

Mood = Literal["neutral", "warm", "suspense"]
MOODS = frozenset(("neutral", "warm", "suspense"))
EFFECT_PATTERNS = {
    "door_creak": re.compile(
        r"\bdoor\b.{0,50}\bcreak(?:s|ed|ing)?\b|\bcreak(?:s|ed|ing)?\b.{0,50}\bdoor\b",
        re.IGNORECASE,
    ),
    "footsteps": re.compile(r"\bfootsteps?\b|\bfootfalls?\b", re.IGNORECASE),
    "thunder": re.compile(r"\bthunder(?:clap|ed)?\b", re.IGNORECASE),
    "knock": re.compile(
        r"\bknock(?:s|ed|ing)?\b.{0,30}\b(?:door|window|wall)\b|"
        r"\b(?:door|window|wall)\b.{0,30}\bknock(?:s|ed|ing)?\b",
        re.IGNORECASE,
    ),
}
MAX_CUES_PER_PARAGRAPH = 2
CLAUSE_BOUNDARIES = re.compile(r"[;,]|\b(?:but|however|yet)\b", re.IGNORECASE)
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|never|without|neither|nor)\b|\b\w+n['’]t\b", re.IGNORECASE,
)

SUSPENSE_WORDS = frozenset(
    ("afraid", "blood", "creak", "creepy", "dark", "fear", "footsteps", "frightened",
     "ghost", "haunted", "horror", "monster", "scary", "shadow", "scream",
     "terrified", "thunder", "whisper")
)
WARM_WORDS = frozenset(
    ("bright", "comfort", "friend", "gentle", "happy", "hope", "hug", "kind",
     "laugh", "peaceful", "smile", "sunlight", "warm", "welcome")
)


def fallback_moods(paragraphs: list[str]) -> list[Mood]:
    """Keep narration usable when mood analysis is unavailable."""
    result: list[Mood] = []
    for paragraph in paragraphs:
        words = set(re.findall(r"[a-z]+", paragraph.lower()))
        if words & SUSPENSE_WORDS or EFFECT_PATTERNS["door_creak"].search(paragraph):
            result.append("suspense")
        elif len(words & WARM_WORDS) >= 1:
            result.append("warm")
        else:
            result.append("neutral")
    return result


def parse_moods(raw: str, count: int) -> list[Mood] | None:
    """Reject incomplete or out-of-vocabulary model output."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("moods"), list):
        return None
    moods = data["moods"]
    if len(moods) != count or any(not isinstance(mood, str) or mood not in MOODS for mood in moods):
        return None
    return moods


def split_sentences(paragraph: str) -> list[str]:
    """Keep the exact spoken text while giving cues stable sentence indexes."""
    separated = re.sub(r"([.!?][”\"’]?)\s+(?=[A-Z“\"‘])", r"\1\n", paragraph.strip())
    return [sentence.strip() for sentence in separated.split("\n") if sentence.strip()]


def fallback_cues(sentences: list[list[str]]) -> list[dict[str, int | str]]:
    """Use keyword cues offline, conservatively skipping clauses with obvious negation."""
    cues: list[dict[str, int | str]] = []
    for paragraph_index, paragraph_sentences in enumerate(sentences):
        count = 0
        for sentence_index, sentence in enumerate(paragraph_sentences):
            affirmative_clauses = [
                clause for clause in CLAUSE_BOUNDARIES.split(sentence)
                if not NEGATION_PATTERN.search(clause)
            ]
            for effect, pattern in EFFECT_PATTERNS.items():
                if any(pattern.search(clause) for clause in affirmative_clauses):
                    cues.append(
                        {
                            "paragraph_index": paragraph_index,
                            "sentence_index": sentence_index,
                            "effect": effect,
                        }
                    )
                    count += 1
                    break
            if count >= MAX_CUES_PER_PARAGRAPH:
                break
    return cues


def parse_cues(raw: str, sentences: list[list[str]]) -> list[dict[str, int | str]] | None:
    """Accept only approved effects tied to a literal event in a known sentence."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("cues"), list):
        return None

    cues: list[dict[str, int | str]] = []
    paragraph_counts: dict[int, int] = {}
    used_sentences: set[tuple[int, int]] = set()
    for item in data["cues"]:
        if not isinstance(item, dict):
            return None
        paragraph_index = item.get("paragraph_index")
        sentence_index = item.get("sentence_index")
        effect = item.get("effect")
        if (
            type(paragraph_index) is not int
            or type(sentence_index) is not int
            or not isinstance(effect, str)
            or effect not in EFFECT_PATTERNS
            or not 0 <= paragraph_index < len(sentences)
            or not 0 <= sentence_index < len(sentences[paragraph_index])
        ):
            return None
        position = (paragraph_index, sentence_index)
        if (
            position in used_sentences
            or paragraph_counts.get(paragraph_index, 0) >= MAX_CUES_PER_PARAGRAPH
        ):
            continue
        if not EFFECT_PATTERNS[effect].search(sentences[paragraph_index][sentence_index]):
            continue
        cues.append(
            {
                "paragraph_index": paragraph_index,
                "sentence_index": sentence_index,
                "effect": effect,
            }
        )
        used_sentences.add(position)
        paragraph_counts[paragraph_index] = paragraph_counts.get(paragraph_index, 0) + 1
    return cues


def combine_cues(
    fallback: list[dict[str, int | str]],
    suggested: list[dict[str, int | str]] | None,
) -> list[dict[str, int | str]]:
    """Honor validated AI cues, including silence; fall back only when unavailable."""
    result = fallback if suggested is None else suggested
    return sorted(result, key=lambda cue: (cue["paragraph_index"], cue["sentence_index"]))
