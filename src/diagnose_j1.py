"""
Быстрая диагностика без LLM-вызовов: почему extract_clauses() не находит 6.1 в договоре J1.
Кладём этот файл РЯДОМ с pipeline.py (в src/) и запускаем:

    python3 diagnose_j1.py /путь/к/папке/с/приватным/датасетом [SCENARIO_ID] [CLAUSE_ID]

По умолчанию SCENARIO_ID=J1, CLAUSE_ID=6.1 (можно переопределить для проверки других
подозрительных сценариев/пунктов тем же скриптом).
"""
import sys
import os
import json
import glob

from ledger_loader import load_ledger, build_scenario_account_map
from document_router import route_documents
from covenant_parser import CLAUSE_HEADER_RE


def main():
    if len(sys.argv) < 2:
        print("Использование: python3 diagnose_j1.py <папка_датасета> [scenario_id] [clause_id]")
        sys.exit(1)

    dataset_folder = sys.argv[1]
    scenario_id = sys.argv[2] if len(sys.argv) > 2 else "J1"
    clause_id = sys.argv[3] if len(sys.argv) > 3 else "6.1"

    with open(os.path.join(dataset_folder, "submission_template.json")) as f:
        template = json.load(f)
    known_scenario_ids = list(template["answers"].keys())

    csvs = glob.glob(os.path.join(dataset_folder, "*.csv"))
    if not csvs:
        print("!! Не найден .csv леджер в папке датасета")
        sys.exit(1)
    txns = load_ledger(csvs[0])
    scenario_to_account = build_scenario_account_map(txns, known_scenario_ids)

    if scenario_id not in scenario_to_account:
        print(f"!! scenario_id={scenario_id} не найден в леджере вообще (нет транзакций TXN-{scenario_id}-...)")
        sys.exit(1)
    account_id = scenario_to_account[scenario_id]
    print(f"account_id для {scenario_id}: {account_id}")

    docs_folder = os.path.join(dataset_folder, "documents")
    known_account_ids = list(scenario_to_account.values())
    routing, texts = route_documents(docs_folder, known_account_ids)

    agreement_fn = routing["agreements"].get(account_id)
    print(f"Найденный файл договора: {agreement_fn}")
    if not agreement_fn:
        print("!! Договор для этого account_id вообще не нашёлся в route_documents.")
        print("   Значит проблема в document_router.py (маркеры не сматчились на этот PDF),")
        print("   а не в covenant_parser.py. Нужно смотреть document_router.py отдельно.")
        sys.exit(0)

    text = texts[agreement_fn]
    print(f"Длина текста договора: {len(text)} символов\n")

    needed = list(template["answers"][scenario_id].keys())
    print(f"Пункты, которые нужны по шаблону для {scenario_id}: {needed}\n")

    matches = list(CLAUSE_HEADER_RE.finditer(text))
    found_ids = [m.group(1) for m in matches]
    print(f"Всего заголовков найдено регуляркой CLAUSE_HEADER_RE: {len(matches)}")
    print(f"Найденные номера: {found_ids}\n")

    for needed_id in needed:
        status = "НАЙДЕН" if needed_id in found_ids else "НЕ НАЙДЕН регуляркой"
        print(f"  {needed_id}: {status}")

    if clause_id not in found_ids:
        print(f"\n=== '{clause_id}' не матчится под (Пункт|Section|Clause|Article) + число ===")
        print(f"Ищем '{clause_id}' просто как подстроку текста, чтобы увидеть РЕАЛЬНЫЙ формат заголовка:\n")
        idx = text.find(clause_id)
        hits = 0
        while idx != -1 and hits < 15:
            snippet = text[max(0, idx - 50):idx + 100].replace("\n", " \\n ")
            print(f"  @ offset {idx}: ...{snippet}...")
            idx = text.find(clause_id, idx + 1)
            hits += 1
        if hits == 0:
            print(f"  '{clause_id}' вообще не встречается в тексте договора как подстрока.")
            print("  Значит либо это не та статья (номер расходится с другой нумерацией),")
            print("  либо контракт для этого account_id выбран неверно.")
    else:
        # для остальных подозрительных сценариев показываем контекст найденного совпадения
        m = next(m for m in matches if m.group(1) == clause_id)
        start = m.start()
        print(f"\nКонтекст найденного заголовка '{clause_id}':")
        print(text[max(0, start - 30):start + 300])


if __name__ == "__main__":
    main()
