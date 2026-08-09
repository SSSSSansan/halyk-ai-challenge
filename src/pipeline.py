"""
Главный скрипт. Запуск:

    export OPENAI_API_KEY="твой ключ"
    python3 pipeline.py /путь/к/папке/с/датасетом /путь/к/submission.json

Папка с датасетом должна содержать:
  master_ledger_2025.csv (или любой один .csv)
  documents/ (папка с PDF)
  submission_template.json
"""
import sys
import os
import json
import glob

from ledger_loader import load_ledger, build_scenario_account_map, transactions_for_scenario
from document_router import route_documents
from covenant_parser import extract_clauses, parse_covenant
from ledger_classifier import classify_transactions
from metric_engine import compute_actual_absolute, compute_actual_ratio, compute_actual_share, compute_actual_max_of, find_evidence_transaction, warn_if_dominated_by_outlier
from verdict import build_answer_cell
from notes_corrections import extract_corrections, apply_corrections_to_transactions
from related_parties import parse_related_parties, is_related_party


def find_csv(dataset_folder):
    csvs = glob.glob(os.path.join(dataset_folder, "*.csv"))
    if not csvs:
        raise FileNotFoundError("В папке датасета не найден .csv леджер")
    return csvs[0]


def run_pipeline(dataset_folder: str, output_path: str, team: str, email: str, model_name: str = "gpt-4o-mini"):
    # 1. Шаблон - сначала, чтобы знать полный список заёмщиков (scenario_id) заранее
    with open(os.path.join(dataset_folder, "submission_template.json")) as f:
        template = json.load(f)
    known_scenario_ids = list(template["answers"].keys())

    # 2. Леджер
    csv_path = find_csv(dataset_folder)
    txns = load_ledger(csv_path)
    scenario_to_account = build_scenario_account_map(txns, known_scenario_ids)
    print(f"[1/5] Леджер загружен: {len(txns)} транзакций, {len(scenario_to_account)} заёмщиков")

    # 3. Документы - ищем по точным account_id из леджера (любой формат: ACC-7801, TELE-4471...)
    docs_folder = os.path.join(dataset_folder, "documents")
    known_account_ids = list(scenario_to_account.values())
    routing, texts = route_documents(docs_folder, known_account_ids)
    print(f"[2/5] Документы разобраны: {len(texts)} PDF, "
          f"{len(routing['agreements'])} действующих договоров найдено")

    answers = {}
    debug_dump = {}
    for scenario_id, account_id in scenario_to_account.items():
        if scenario_id not in template["answers"]:
            continue  # не спрашивают про этого заёмщика - пропускаем

        agreement_fn = routing["agreements"].get(account_id)
        if not agreement_fn:
            print(f"  [!] Нет договора для {scenario_id} ({account_id}) — ячейки останутся пустыми")
            continue

        needed_clauses = list(template["answers"][scenario_id].keys())
        raw_clauses = extract_clauses(texts[agreement_fn], needed_clauses)

        scenario_txns = transactions_for_scenario(txns, scenario_id, account_id)

        # Правки из "Примечаний к отчётности" (пропущенные суммы, исключения, off-ledger)
        notes_fn = routing["financial_notes"].get(account_id)
        corrections = extract_corrections(texts[notes_fn]) if notes_fn else []
        if corrections:
            print(f"  [notes] {scenario_id}: применяю {len(corrections)} корректировок из примечаний")
        scenario_txns = apply_corrections_to_transactions(scenario_txns, corrections)

        # Связанные стороны - надёжно из таблицы KYC, а не угадывание LLM
        kyc_fn = routing["kyc_dossiers"].get(account_id)
        related_orgs = parse_related_parties(texts[kyc_fn])["related_orgs"] if kyc_fn else []

        classified = classify_transactions(scenario_txns)  # {txn_id: category}
        classified_txns = []
        for t in scenario_txns:
            category = classified.get(t["txn_id"], "Other")
            is_outbound = float(t["amount"] or 0) < 0
            if related_orgs and is_outbound and is_related_party(t.get("counterparty", ""), related_orgs):
                category = "RelatedPartyPayment"  # только исходящие платежи, не выручка ОТ связанной стороны
            classified_txns.append({"txn_id": t["txn_id"], "amount": t["amount"], "category": category})

        scenario_answers = {}
        for clause_id in template["answers"][scenario_id]:
            if clause_id not in raw_clauses:
                print(f"  [!] Пункт {clause_id} не найден в договоре {scenario_id} — пропуск")
                continue
            structured = parse_covenant(raw_clauses[clause_id])
            print(f"  {scenario_id}/{clause_id}: {structured['metric_description']} "
                  f"({structured['comparator']} {structured['threshold']})")

            try:
                if structured["formula_type"] == "sum":
                    actual = compute_actual_absolute(classified_txns, structured["numerator_category"])
                    evidence = find_evidence_transaction(classified_txns, structured["numerator_category"])
                    warn_if_dominated_by_outlier(classified_txns, structured["numerator_category"], scenario_id, clause_id)
                elif structured["formula_type"] == "ratio":
                    actual = compute_actual_ratio(
                        classified_txns, structured["numerator_category"], structured["denominator_category"]
                    )
                    evidence = None
                elif structured["formula_type"] == "share":
                    actual = compute_actual_share(
                        classified_txns, structured["numerator_category"], structured["denominator_category"]
                    )
                    evidence = find_evidence_transaction(classified_txns, structured["numerator_category"])
                elif structured["formula_type"] == "max_of":
                    actual, winner_cat = compute_actual_max_of(classified_txns, structured["max_of_categories"])
                    evidence = find_evidence_transaction(classified_txns, winner_cat)
                else:
                    raise ValueError(f"Неизвестный formula_type: {structured['formula_type']}")

                scenario_answers[clause_id] = build_answer_cell(
                    actual=actual,
                    threshold=structured["threshold"],
                    comparator=structured["comparator"],
                    evidence_txn_id=evidence,
                )
            except Exception as e:
                print(f"  [!] Не удалось посчитать {scenario_id}/{clause_id}: {e}")
                scenario_answers[clause_id] = {"status": None, "actual": None, "evidence_txn_id": None}

        answers[scenario_id] = scenario_answers
        debug_dump.setdefault(scenario_id, classified_txns)

    for scenario_id, clauses in answers.items():
        for clause_id, cell in clauses.items():
            template["answers"][scenario_id][clause_id] = cell

    template["team"] = team
    template["contact_email"] = email
    template["model"] = model_name
    # answers пока частично заполнены - реальная досборка на дне 3

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    print(f"[5/5] Черновик сохранён в {output_path}")

    debug_path = output_path.replace(".json", "_debug_classified.json")
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump(debug_dump, f, ensure_ascii=False, indent=2)
    print(f"       Классификация транзакций для отладки сохранена в {debug_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python3 pipeline.py <папка_датасета> <output.json>")
        sys.exit(1)
    run_pipeline(sys.argv[1], sys.argv[2], team="your-team-name", email="you@example.com")