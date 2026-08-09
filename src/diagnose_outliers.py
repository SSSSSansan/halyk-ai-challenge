"""
Не требует LLM и не требует пайплайна - читает уже готовый файл
final_submission_debug_classified.json (он у тебя уже есть в репозитории) и для
подозрительных сценариев показывает разбивку по категориям + есть ли одна
транзакция, которая "тащит" всю сумму категории (decoy-паттерн, как фиктивная
аренда на $13M из публичного датасета).

Запуск (из папки, где лежит final_submission_debug_classified.json):

    python3 diagnose_outliers.py final_submission_debug_classified.json X2 G2 G1 B2 F1
"""
import sys
import json
from collections import defaultdict


def main():
    if len(sys.argv) < 3:
        print("Использование: python3 diagnose_outliers.py <debug_classified.json> <scenario_id> [scenario_id...]")
        sys.exit(1)

    debug_path = sys.argv[1]
    scenario_ids = sys.argv[2:]

    with open(debug_path, encoding="utf-8") as f:
        debug = json.load(f)

    for scenario_id in scenario_ids:
        if scenario_id not in debug:
            print(f"\n=== {scenario_id}: нет в debug-файле (пропущен пайплайном) ===")
            continue

        txns = debug[scenario_id]
        by_category = defaultdict(list)
        for t in txns:
            by_category[t["category"]].append(t)

        print(f"\n=== {scenario_id}: {len(txns)} транзакций ===")
        for category, cat_txns in sorted(by_category.items()):
            amounts = [abs(float(t["amount"] or 0)) for t in cat_txns]
            total = sum(amounts)
            if total == 0:
                continue
            max_amt = max(amounts)
            max_txn = next(t for t in cat_txns if abs(float(t["amount"] or 0)) == max_amt)
            share = max_amt / total * 100
            flag = "  <<< ПОДОЗРЕНИЕ, одна транзакция даёт >50%" if share > 50 else ""
            print(f"  {category:22s} sum={total:>15,.2f}  n={len(cat_txns):4d}  "
                  f"max_txn={max_txn['txn_id']} ({max_amt:,.2f}, {share:.0f}% суммы){flag}")


if __name__ == "__main__":
    main()
