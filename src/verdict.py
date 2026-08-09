"""
Финальный шаг: сравниваем actual с threshold по comparator и выносим status.
"""


def determine_status(actual: float, threshold: float, comparator: str) -> str:
    """
    comparator: "max" -> actual не должен превышать threshold
                "min" -> actual не должен быть ниже threshold
    """
    if comparator == "max":
        return "COMPLIANT" if actual <= threshold else "BREACH"
    elif comparator == "min":
        return "COMPLIANT" if actual >= threshold else "BREACH"
    raise ValueError(f"Неизвестный comparator: {comparator}")


def build_answer_cell(actual: float, threshold: float, comparator: str, evidence_txn_id: str | None) -> dict:
    status = determine_status(actual, threshold, comparator)
    return {
        "status": status,
        "actual": round(abs(actual), 2),
        # evidence указываем только при BREACH и только если он реально найден -
        # для COMPLIANT и агрегатных тестов почти всегда null
        "evidence_txn_id": evidence_txn_id if status == "BREACH" else None,
    }


if __name__ == "__main__":
    print(build_answer_cell(actual=1.68, threshold=9.0, comparator="max", evidence_txn_id=None))
    print(build_answer_cell(actual=12.0, threshold=9.0, comparator="max", evidence_txn_id="TXN-X-0020"))
