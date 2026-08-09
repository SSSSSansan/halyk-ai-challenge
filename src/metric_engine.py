"""
Считает actual для одного ковенанта по его структуре (из covenant_parser)
и классифицированным транзакциям заёмщика.

Три типа лимита (threshold_unit из covenant_parser): ratio / usd / share.
Формулу для ratio/share LLM тоже должен подсказать через data_sources_needed,
но саму арифметику (суммирование по категориям) делаем в коде — дешевле и надёжнее,
чем просить LLM считать числа.
"""


def sum_by_category(classified_txns: list[dict], category: str) -> float:
    """
    classified_txns: [{"txn_id", "amount", "category"}, ...].
    "EBITDA" - расчётная псевдо-категория = Revenue - OpEx (так определено в договорах).
    Возвращает модуль суммы.
    """
    if category == "EBITDA":
        revenue = sum_by_category(classified_txns, "Revenue")
        opex = sum_by_category(classified_txns, "OpEx")
        return abs(revenue - opex)

    total = sum(
        float(t["amount"]) for t in classified_txns
        if t["category"] == category and str(t.get("amount", "")).strip() != ""
    )
    return abs(total)


def compute_actual_absolute(classified_txns: list[dict], category: str) -> float:
    """Для ковенантов вида 'сумма по категории X не должна превышать/быть ниже лимита'."""
    return round(sum_by_category(classified_txns, category), 2)


def compute_actual_ratio(classified_txns: list[dict], numerator_cat: str, denominator_cat: str) -> float:
    """Для ковенантов-коэффициентов: numerator / denominator."""
    num = sum_by_category(classified_txns, numerator_cat)
    den = sum_by_category(classified_txns, denominator_cat)
    if den == 0:
        raise ValueError("Знаменатель = 0, коэффициент не считается — разбираться руками")
    return round(num / den, 2)


def compute_actual_share(classified_txns: list[dict], part_cat: str, whole_cat: str) -> float:
    """Для ковенантов-долей: часть / целое (например, доля платежей связанным сторонам в OpEx)."""
    part = sum_by_category(classified_txns, part_cat)
    whole = sum_by_category(classified_txns, whole_cat)
    if whole == 0:
        raise ValueError("Знаменатель = 0, доля не считается — разбираться руками")
    return round(part / whole, 2)


def compute_actual_max_of(classified_txns: list[dict], categories: list[str]) -> tuple[float, str]:
    """Для 'по отдельности, а не в совокупности' - берём максимум ИЗ сумм категорий, не их сумму.
    Возвращает (значение, категория-победитель) - категория нужна, чтобы найти evidence."""
    sums = {cat: sum_by_category(classified_txns, cat) for cat in categories}
    winner = max(sums, key=sums.get)
    return round(sums[winner], 2), winner


def find_evidence_transaction(classified_txns: list[dict], category: str) -> str | None:
    """
    Если категория содержит РОВНО одну транзакцию — она и есть доказательство.
    Если больше одной — evidence не определён однозначно, возвращаем None
    (по правилам конкурса для агрегатных тестов evidence всё равно не оценивается).
    """
    matching = [t["txn_id"] for t in classified_txns if t["category"] == category]
    if len(matching) == 1:
        return matching[0]
    return None


def warn_if_dominated_by_outlier(classified_txns: list[dict], category: str, scenario_id: str, clause_id: str):
    """
    Если ОДНА транзакция даёт больше 60% суммы категории - скорее всего это decoy-выброс
    (как $13M 'аренда' у одного заёмщика на публичном датасете). Не блокирует расчёт,
    просто печатает предупреждение, чтобы можно было проверить глазами.
    """
    matching = [(t["txn_id"], abs(float(t["amount"]))) for t in classified_txns
                if t["category"] == category and str(t.get("amount", "")).strip() != ""]
    if len(matching) < 2:
        return
    total = sum(a for _, a in matching)
    if total == 0:
        return
    biggest_id, biggest_amount = max(matching, key=lambda x: x[1])
    if biggest_amount / total > 0.6:
        print(f"  [подозрение] {scenario_id}/{clause_id}: {biggest_id} даёт "
              f"{round(100*biggest_amount/total)}% суммы категории '{category}' - проверь глазами, не decoy ли это")


if __name__ == "__main__":
    # Небольшой юнит-тест на выдуманных данных
    fake_txns = [
        {"txn_id": "TXN-X-0001", "amount": "-100000", "category": "RelatedPartyPayment"},
        {"txn_id": "TXN-X-0002", "amount": "-50000", "category": "OpEx"},
        {"txn_id": "TXN-X-0003", "amount": "-30000", "category": "OpEx"},
    ]
    print("Сумма RelatedPartyPayment:", compute_actual_absolute(fake_txns, "RelatedPartyPayment"))
    print("Доля RelatedPartyPayment в OpEx+RelatedParty:", end=" ")
    total_opex_like = [t for t in fake_txns]
    print(compute_actual_share(fake_txns, "RelatedPartyPayment", "OpEx"))
    print("Evidence для RelatedPartyPayment:", find_evidence_transaction(fake_txns, "RelatedPartyPayment"))
    print("Evidence для OpEx (двух транзакций):", find_evidence_transaction(fake_txns, "OpEx"))