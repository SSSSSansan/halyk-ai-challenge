"""
Читает master_ledger_*.csv и строит связь scenario_id <-> account_id
по префиксу txn_id вида TXN-<scenario_id>-XXXX. scenario_id берём из
submission_template.json (список ключей answers), а не угадываем регуляркой -
так работает при ЛЮБОЙ схеме именования (P1, B4, KC, TXN-KC-CAP-29 и т.п.).
"""
import csv
from collections import defaultdict


def load_ledger(csv_path):
    """Возвращает список транзакций как список dict."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def build_scenario_account_map(transactions, known_scenario_ids):
    """
    known_scenario_ids: список scenario_id из submission_template.json.
    Сортируем по убыванию длины, чтобы длинные префиксы (S10) не терялись
    из-за более коротких совпадений (S1).
    """
    sorted_ids = sorted(known_scenario_ids, key=len, reverse=True)
    scenario_accounts = defaultdict(set)
    for txn in transactions:
        for sid in sorted_ids:
            if txn["txn_id"].startswith(f"TXN-{sid}-"):
                scenario_accounts[sid].add(txn["account_id"])
                break

    result = {}
    for scenario_id, accounts in scenario_accounts.items():
        if len(accounts) != 1:
            raise ValueError(
                f"scenario {scenario_id} привязан к {len(accounts)} счетам: {accounts} "
                f"— это подозрительно, надо разбираться руками, а не гадать."
            )
        result[scenario_id] = accounts.pop()
    return result


def transactions_for_scenario(transactions, scenario_id, account_id):
    """
    Транзакции заёмщика: либо помечены его scenario_id в txn_id,
    либо просто лежат на его счёте (на случай транзакций без префикса).
    """
    out = []
    for txn in transactions:
        if txn["account_id"] == account_id:
            out.append(txn)
    return out


if __name__ == "__main__":
    import json
    txns = load_ledger("/home/claude/halyk/data/master_ledger_2025.csv")
    template = json.load(open("/home/claude/halyk/data/submission_template.json"))
    mapping = build_scenario_account_map(txns, list(template["answers"].keys()))
    print("Найдено сценариев:", len(mapping))
    for scen, acc in sorted(mapping.items()):
        cnt = len(transactions_for_scenario(txns, scen, acc))
        print(f"  {scen} -> {acc}  ({cnt} транзакций)")