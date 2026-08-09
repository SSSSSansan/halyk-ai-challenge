"""
Печатает ПОЛНЫЕ строки из master_ledger_2025.csv для указанных txn_id - чтобы
глазами оценить, похожа ли транзакция на decoy (странное описание/контрагент)
или это реальная крупная операция.

Запуск:
    python3 inspect_txns.py ../agentic-bank-hidden/master_ledger_2025.csv TXN-G1-0056 TXN-X2-0072 ...
"""
import sys
import csv


def main():
    csv_path = sys.argv[1]
    wanted = set(sys.argv[2:])

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = {row["txn_id"]: row for row in reader if row["txn_id"] in wanted}

    for txn_id in sys.argv[2:]:
        row = rows.get(txn_id)
        if not row:
            print(f"{txn_id}: НЕ НАЙДЕНО в csv")
            continue
        print(f"\n=== {txn_id} ===")
        for k, v in row.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
