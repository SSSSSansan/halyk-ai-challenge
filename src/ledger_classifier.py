"""
В csv нет колонки категории — определяем её по description/counterparty через LLM.
Работаем батчами (по одному заёмщику ~55 транзакций — влезает в один вызов),
чтобы не тратить деньги на 1473 отдельных запроса.
"""
import json
from llm_client import call_json

CATEGORIES = [
    "Revenue", "CapEx", "OpEx", "Payroll", "Utilities",
    "Rent", "Interest", "Insurance", "RelatedPartyPayment", "Other",
]

SYSTEM_PROMPT = f"""\
Ты — бухгалтер, классифицируешь банковские транзакции заёмщика по описанию и контрагенту.
Категории: {", ".join(CATEGORIES)}.

ВАЖНО про знак суммы: положительная сумма = поступление денег (может быть только "Revenue"
или "Other", если это явно не операционная выручка). Отрицательная сумма = расход
(любая категория, КРОМЕ "Revenue").

"RelatedPartyPayment" ставь, если контрагент похож на связанную/дочернюю/аффилированную
структуру ПО НАЗВАНИЮ (например содержит "Capital", "Holding", "Partners", "Group" в связке
с названием, похожим на инвестиционную/управляющую компанию), ОСОБЕННО если описание операции
похоже на "management fee", "advisory retainer", "consulting fee", "management advisory" —
такие описания почти всегда означают платёж связанной/аффилированной стороне.

На входе — список транзакций (json). Верни СТРОГО валидный JSON вида:
{{"classifications": [{{"txn_id": "...", "category": "..."}}, ...]}}
По одной записи на каждую входную транзакцию, в том же порядке, ничего не пропускай.
"""


def _sign_guardrail(txn_amount: str, category: str) -> str:
    """
    Детерминированная страховка поверх LLM: знак суммы жёстко ограничивает возможные категории.
    Это не требует LLM и ловит систематические ошибки классификации.
    """
    try:
        amount = float(txn_amount)
    except (TypeError, ValueError):
        return category  # пустая/битая сумма - не наша забота на этом этапе
    if amount > 0 and category not in ("Revenue", "Other"):
        return "Revenue"  # поступление не может быть расходной категорией
    if amount < 0 and category == "Revenue":
        return "Other"  # расход не может быть выручкой
    return category


def classify_transactions(transactions: list[dict]) -> dict:
    """
    transactions: список dict с полями txn_id, description, counterparty, amount, currency
    Возвращает {txn_id: category}
    """
    payload = [
        {
            "txn_id": t["txn_id"],
            "description": t["description"],
            "counterparty": t["counterparty"],
            "amount": t["amount"],
            "currency": t["currency"],
        }
        for t in transactions
    ]
    result = call_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(payload, ensure_ascii=False),
    )
    amounts_by_id = {t["txn_id"]: t["amount"] for t in transactions}
    return {
        c["txn_id"]: _sign_guardrail(amounts_by_id.get(c["txn_id"]), c["category"])
        for c in result["classifications"]
    }


def apply_notes_corrections(classifications: dict, corrections: list[dict]) -> dict:
    """
    corrections: [{"txn_id": "...", "action": "reclassify"|"exclude", "new_category": "..."}]
    Корректировки из "Примечаний к отчётности" ВСЕГДА имеют приоритет над базовой классификацией.
    """
    result = dict(classifications)
    for c in corrections:
        if c["action"] == "exclude":
            result[c["txn_id"]] = "ExcludedFromPeriod"
        elif c["action"] == "reclassify":
            result[c["txn_id"]] = c["new_category"]
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/claude/halyk/src")
    from ledger_loader import load_ledger, transactions_for_scenario

    txns = load_ledger("/home/claude/halyk/data/master_ledger_2025.csv")
    p5_txns = transactions_for_scenario(txns, "P5", "ACC-7805")
    print(f"Транзакций P5: {len(p5_txns)}, готовы к классификации через LLM (нужен ключ)")
    print("Пример транзакции:", p5_txns[0])