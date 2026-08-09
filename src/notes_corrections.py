"""
"Примечания к финансовой отчётности" почти всегда содержат раздел
"ДОПОЛНЕНИЕ О СОБЛЮДЕНИИ КОВЕНАНТОВ" с точечными правками к леджеру:
  - сумма транзакции не указана в леджере, но раскрыта здесь
  - есть обязательство, которого вообще нет как строки в леджере (off-ledger)
  - транзакция должна быть исключена из ковенантного периода

У части заёмщиков этот раздел пуст (это нормально, не все заёмщики имеют исключения).
"""
import re
from llm_client import call_json

SYSTEM_PROMPT = """\
Ты читаешь раздел "ДОПОЛНЕНИЕ О СОБЛЮДЕНИИ КОВЕНАНТОВ" из примечаний к финансовой отчётности.
Найди все точечные корректировки к леджеру транзакций. Верни СТРОГО валидный JSON:

{
  "corrections": [
    {
      "type": "missing_amount" | "off_ledger_amount" | "exclude_txn" | "reclassify_txn",
      "txn_id": "TXN-... или null, если это off_ledger_amount без привязки к существующей транзакции",
      "amount": число (положительное) или null,
      "description": "краткое описание на русском, для чего эта корректировка",
      "reason": "почему аудитор её вносит"
    }
  ]
}

Если раздел пустой или корректировок нет - верни {"corrections": []}.
Не выдумывай корректировки, которых нет в тексте.
"""


def extract_corrections(notes_text: str) -> list[dict]:
    idx = notes_text.find("ДОПОЛНЕНИЕ О СОБЛЮДЕНИИ КОВЕНАНТОВ")
    if idx == -1:
        return []
    section = notes_text[idx:idx + 2000]  # раздел короткий, с запасом
    result = call_json(system_prompt=SYSTEM_PROMPT, user_prompt=section)
    return result.get("corrections", [])


def apply_corrections_to_transactions(transactions: list[dict], corrections: list[dict]) -> list[dict]:
    """
    Возвращает НОВЫЙ список транзакций с применёнными корректировками.
    off_ledger_amount добавляет псевдо-транзакцию (без даты, с описанием из корректировки).
    exclude_txn помечает amount=0 и добавляет флаг excluded=True (чтобы не участвовала в суммах).
    """
    by_id = {t["txn_id"]: dict(t) for t in transactions}
    extra = []

    for c in corrections:
        if c["type"] == "missing_amount" and c["txn_id"] in by_id:
            by_id[c["txn_id"]]["amount"] = str(-abs(c["amount"]))  # расходы отрицательные, как в леджере
        elif c["type"] == "exclude_txn" and c["txn_id"] in by_id:
            by_id[c["txn_id"]]["excluded"] = True
        elif c["type"] == "off_ledger_amount":
            extra.append({
                "txn_id": f"OFFLEDGER-{len(extra)+1}",
                "amount": str(-abs(c["amount"])),
                "description": c["description"],
                "counterparty": "",
                "currency": "USD",
                "off_ledger": True,
            })
        elif c["type"] == "reclassify_txn" and c["txn_id"] in by_id:
            by_id[c["txn_id"]]["forced_category"] = c.get("description")

    result = [t for t in by_id.values() if not t.get("excluded")]
    result.extend(extra)
    return result


if __name__ == "__main__":
    import json
    with open("/home/claude/halyk/data/extracted_text.json") as f:
        extracted = json.load(f)
    notes_map = json.load(open("/home/claude/halyk/data/financial_notes_map.json"))

    # Проверяем регэксп-поиск секции (без LLM) на известных нам примерах
    for scen in ["P1", "P8", "B4", "P4"]:
        text = extracted[notes_map[scen]]
        idx = text.find("ДОПОЛНЕНИЕ О СОБЛЮДЕНИИ КОВЕНАНТОВ")
        print(scen, "-> раздел найден:" if idx != -1 else "-> НЕ найден", "длина текста дальше:", len(text[idx:idx+2000]) if idx!=-1 else 0)
