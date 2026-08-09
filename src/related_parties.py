"""
В KYC-досье есть таблица "Организация | Доля голосующих прав" и явное правило
("35% и более = связанная сторона"). Порог мы вытаскиваем из текста регуляркой
(на случай если в приватном датасете он окажется другим), сам список организаций тоже.
Здесь LLM не нужен - структура текста стабильная и простая.
"""
import re

ROW_RE = re.compile(r"^(.+?)\s+(\d+(?:[.,]\d+)?)\s*%\s*$")
THRESHOLD_RE = re.compile(r"владеет?\s*(\d+(?:[.,]\d+)?)\s*%\s*и\s*более.*?связанными сторонами", re.IGNORECASE | re.DOTALL)


def parse_related_parties(kyc_text: str) -> dict:
    """Возвращает {"threshold_pct": float, "related_orgs": ["Org A", "Org B", ...]}"""
    threshold_match = THRESHOLD_RE.search(kyc_text)
    threshold = float(threshold_match.group(1).replace(",", ".")) if threshold_match else 35.0

    orgs = []
    for line in kyc_text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        name, pct = m.group(1).strip(), float(m.group(2).replace(",", "."))
        if name == "Организация" or "Доля" in name:  # заголовок таблицы, пропускаем
            continue
        if pct >= threshold:
            orgs.append(name)

    return {"threshold_pct": threshold, "related_orgs": orgs}


def _normalize(name: str) -> str:
    """Убираем точки/запятые/кавычки и лишние пробелы, чтобы 'L.L.P.' == 'LLP', 'Foo LLP.' == 'Foo LLP'."""
    cleaned = re.sub(r"[.,\"'«»]", "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def is_related_party(counterparty: str, related_orgs: list[str]) -> bool:
    """Сопоставление по вхождению названия, устойчивое к пунктуации (LLP vs L.L.P. и т.п.)."""
    cp = _normalize(counterparty)
    return any(_normalize(org) in cp or cp in _normalize(org) for org in related_orgs)


if __name__ == "__main__":
    import json
    with open("/home/claude/halyk/data/extracted_text.json") as f:
        extracted = json.load(f)
    kyc_map = json.load(open("/home/claude/halyk/data/kyc_dossier_map.json"))

    for scen, fn in kyc_map.items():
        result = parse_related_parties(extracted[fn])
        print(scen, "-> порог:", result["threshold_pct"], "| связанные:", result["related_orgs"])