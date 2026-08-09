"""
Считает баллы твоего submission.json против ground_truth.json ровно по формуле из CASE.ru.md:

  status              -> 0.50 если точное совпадение, иначе 0 (и вся ячейка = 0)
  actual              -> 0.30 * max(0, 1 - e/0.05),  e = |ваш - ключ| / |ключ|
  evidence_txn_id     -> 0.20 если совпадает точно с ключом
                         если в ключе null -> эти 0.20 "убывают" по той же шкале, что и actual

Запуск:
    python3 score_submission.py submission.json ground_truth.json
"""
import json
import sys


def score_cell(pred: dict, truth: dict) -> float:
    if pred is None:
        return 0.0

    # status - всё или ничего, и если неверен - вся ячейка 0
    if pred.get("status") != truth["status"]:
        return 0.0

    score = 0.50  # status верен

    # actual - по шкале
    actual_pred = pred.get("actual")
    actual_score = 0.0
    if isinstance(actual_pred, (int, float)) and truth["actual"] != 0:
        e = abs(actual_pred - truth["actual"]) / abs(truth["actual"])
        actual_score = 0.30 * max(0, 1 - e / 0.05)
    elif isinstance(actual_pred, (int, float)) and truth["actual"] == 0:
        actual_score = 0.30 if actual_pred == 0 else 0.0
    score += actual_score

    # evidence
    if truth["evidence_txn_id"] is None:
        # 0.20 убывает вместе с actual по той же шкале
        score += (actual_score / 0.30) * 0.20 if actual_score > 0 else 0.0
    else:
        if pred.get("evidence_txn_id") == truth["evidence_txn_id"]:
            score += 0.20

    return round(score, 4)


def score_submission(submission: dict, ground_truth: dict) -> dict:
    total = 0.0
    max_total = 0.0
    details = {}

    for scenario_id, covenants in ground_truth["scenarios"].items():
        details[scenario_id] = {}
        for clause_id, truth_cell in covenants["covenants"].items():
            pred_cell = submission.get("answers", {}).get(scenario_id, {}).get(clause_id)
            cell_score = score_cell(pred_cell, truth_cell)
            details[scenario_id][clause_id] = cell_score
            total += cell_score
            max_total += 1.0

    return {
        "total_score": round(total, 2),
        "max_score": max_total,
        "percentage": round(100 * total / max_total, 1) if max_total else 0,
        "details": details,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python3 score_submission.py submission.json ground_truth.json")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        submission = json.load(f)
    with open(sys.argv[2]) as f:
        ground_truth = json.load(f)

    result = score_submission(submission, ground_truth)
    print(f"ИТОГО: {result['total_score']} / {result['max_score']}  ({result['percentage']}%)\n")
    for scenario_id, clauses in result["details"].items():
        print(scenario_id, clauses)
