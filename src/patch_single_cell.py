"""
Пересчитывает ОДНУ ячейку (scenario_id/clause_id) через настоящий пайплайн
(extract_clauses -> parse_covenant -> classify -> metric_engine -> verdict) и
подставляет результат в уже существующий submission.json, не трогая остальные
ячейки. Использует те же модули, что и pipeline.py - то есть ответ всё равно
считает агент, а не человек руками.

Запуск (из src/):
    python3 patch_single_cell.py ../agentic-bank-hidden ../final_submission.json B2 6.1
"""
import sys
import os
import json
import glob

from ledger_loader import load_ledger, build_scenario_account_map, transactions_for_scenario
from document_router import route_documents
from covenant_parser import extract_clauses, parse_covenant
from ledger_classifier import classify_transactions
from metric_engine import (
    compute_actual_absolute, compute_actual_ratio, compute_actual_share,
    compute_actual_max_of, find_evidence_transaction, warn_if_dominated_by_outlier,
)
from verdict import build_answer_cell
from notes_corrections import extract_corrections, apply_corrections_to_transactions
from related_parties import parse_related_parties, is_related_party


def compute_cell(dataset_folder, scenario_id, clause_id):
    with open(os.path.join(dataset_folder, "submission_template.json")) as f:
        template = json.load(f)
    known_scenario_ids = list(template["answers"].keys())

    csvs = glob.glob(os.path.join(dataset_folder, "*.csv"))
    txns = load_ledger(csvs[0])
    scenario_to_account = build_scenario_account_map(txns, known_scenario_ids)
    account_id = scenario_to_account[scenario_id]

    docs_folder = os.path.join(dataset_folder, "documents")
    known_account_ids = list(scenario_to_account.values())
    routing, texts = route_documents(docs_folder, known_account_ids)

    agreement_fn = routing["agreements"].get(account_id)
    raw_clauses = extract_clauses(texts[agreement_fn], [clause_id])
    structured = parse_covenant(raw_clauses[clause_id])
    print("structured:", json.dumps(structured, ensure_ascii=False, indent=2))

    scenario_txns = transactions_for_scenario(txns, scenario_id, account_id)

    notes_fn = routing["financial_notes"].get(account_id)
    corrections = extract_corrections(texts[notes_fn]) if notes_fn else []
    scenario_txns = apply_corrections_to_transactions(scenario_txns, corrections)

    kyc_fn = routing["kyc_dossiers"].get(account_id)
    related_orgs = parse_related_parties(texts[kyc_fn])["related_orgs"] if kyc_fn else []

    classified = classify_transactions(scenario_txns)
    classified_txns = []
    for t in scenario_txns:
        category = classified.get(t["txn_id"], "Other")
        is_outbound = float(t["amount"] or 0) < 0
        if related_orgs and is_outbound and is_related_party(t.get("counterparty", ""), related_orgs):
            category = "RelatedPartyPayment"
        classified_txns.append({"txn_id": t["txn_id"], "amount": t["amount"], "category": category})

    if structured["formula_type"] == "sum":
        actual = compute_actual_absolute(classified_txns, structured["numerator_category"])
        evidence = find_evidence_transaction(classified_txns, structured["numerator_category"])
        warn_if_dominated_by_outlier(classified_txns, structured["numerator_category"], scenario_id, clause_id)
    elif structured["formula_type"] == "ratio":
        actual = compute_actual_ratio(classified_txns, structured["numerator_category"], structured["denominator_category"])
        evidence = None
    elif structured["formula_type"] == "share":
        actual = compute_actual_share(classified_txns, structured["numerator_category"], structured["denominator_category"])
        evidence = find_evidence_transaction(classified_txns, structured["numerator_category"])
    elif structured["formula_type"] == "max_of":
        actual, winner_cat = compute_actual_max_of(classified_txns, structured["max_of_categories"])
        evidence = find_evidence_transaction(classified_txns, winner_cat)
    else:
        raise ValueError(f"Неизвестный formula_type: {structured['formula_type']}")

    return build_answer_cell(
        actual=actual, threshold=structured["threshold"],
        comparator=structured["comparator"], evidence_txn_id=evidence,
    )


def main():
    dataset_folder, submission_path, scenario_id, clause_id = sys.argv[1:5]

    cell = compute_cell(dataset_folder, scenario_id, clause_id)
    print(f"\nНовый результат {scenario_id}/{clause_id}: {json.dumps(cell, ensure_ascii=False)}")

    with open(submission_path, encoding="utf-8") as f:
        submission = json.load(f)

    old = submission["answers"].get(scenario_id, {}).get(clause_id)
    print(f"Было: {json.dumps(old, ensure_ascii=False)}")

    submission["answers"].setdefault(scenario_id, {})[clause_id] = cell
    with open(submission_path, "w", encoding="utf-8") as f:
        json.dump(submission, f, ensure_ascii=False, indent=2)
    print(f"\n{submission_path} обновлён.")


if __name__ == "__main__":
    main()
