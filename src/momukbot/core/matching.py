from __future__ import annotations

import re

GENERIC_NAME_TOKENS = {
    "본점",
    "지점",
    "직영점",
    "역점",
    "점",
}


def normalize_match_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", text).lower()


def blog_text_matches_name(place_name: str, evidence_text: str) -> bool:
    name = normalize_match_text(place_name)
    evidence = normalize_match_text(evidence_text)
    if not name or not evidence:
        return False
    if len(name) <= 2:
        return _contains_standalone_name(place_name, evidence_text)
    if name in evidence:
        return True
    tokens = _place_name_tokens(place_name)
    return bool(tokens) and any(token in evidence for token in tokens)


def _place_name_tokens(name: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"[\s,/()]+", name):
        normalized = normalize_match_text(token)
        if normalized.endswith("점") and len(normalized) <= 4:
            continue
        if len(normalized) >= 3 and normalized not in GENERIC_NAME_TOKENS:
            tokens.append(normalized)
    return tokens


def _contains_standalone_name(name: str, text: str) -> bool:
    normalized = normalize_match_text(name)
    if not normalized:
        return False
    pattern = re.compile(rf"(?<![0-9A-Za-z가-힣]){re.escape(name.strip())}(?![0-9A-Za-z가-힣])")
    return bool(pattern.search(text))
