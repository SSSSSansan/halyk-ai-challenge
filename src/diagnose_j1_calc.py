"""
Прогоняет ОДИН пункт (по умолчанию J1/6.1) через parse_covenant + весь расчёт,
как это делает pipeline.py, но печатает ПОЛНЫЙ traceback вместо того, чтобы
его проглатывать. Делает 1 LLM-вызов (parse_covenant) - дёшево.

Запуск (из src/):
    python3 diagnose_j1_calc.py ../agentic-bank-hidden J1 6.1
"""
import sys
import os
import json
import glob
import traceback

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


def main():
    dataset_folder = sys.argv[1]
    scenario_id = sys.argv[2] if len(sys.argv) > 2 else "J1"
    clause_id = sys.argv[3] if len(sys.argv) > 3 else "6.1"

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
    print(f"--- Сырой текст пункта {clause_id} ({len(raw_clauses.get(clause_id, ''))} симв.) ---")
    print(raw_clauses.get(clause_id, "!! ПУСТО")[:500])

    print(f"\n--- parse_covenant({clause_id}) ---")
    try:
        structured = parse_covenant(raw_clauses[clause_id])
        print(json.dumps(structured, ensure_ascii=False, indent=2))
    except Exception:
        print("!! parse_covenant УПАЛ:")
        traceback.print_exc()
        return

    scenario_txns = transactions_for_scenario(txns, scenario_id, account_id)
    print(f"\nТранзакций у {scenario_id}: {len(scenario_txns)}")

    notes_fn = routing["financial_notes"].get(account_id)
    corrections = extract_corrections(texts[notes_fn]) if notes_fn else []
    scenario_txns = apply_corrections_to_transactions(scenario_txns, corrections)
    print(f"Корректировок из примечаний: {len(corrections)}")

    kyc_fn = routing["kyc_dossiers"].get(account_id)
    related_orgs = parse_related_parties(texts[kyc_fn])["related_orgs"] if kyc_fn else []
    print(f"Связанных сторон (KYC): {related_orgs}")

    classified = classify_transactions(scenario_txns)
    classified_txns = []
    for t in scenario_txns:
        category = classified.get(t["txn_id"], "Other")
        is_outbound = float(t["amount"] or 0) < 0
        if related_orgs and is_outbound and is_related_party(t.get("counterparty", ""), related_orgs):
            category = "RelatedPartyPayment"
        classified_txns.append({"txn_id": t["txn_id"], "amount": t["amount"], "category": category})

    from collections import Counter
    print(f"Разбивка по категориям: {Counter(c['category'] for c in classified_txns)}")

    print(f"\n--- Расчёт actual (formula_type={structured['formula_type']}) ---")
    try:
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

        cell = build_answer_cell(
            actual=actual, threshold=structured["threshold"],
            comparator=structured["comparator"], evidence_txn_id=evidence,
        )
        print("УСПЕХ:", json.dumps(cell, ensure_ascii=False, indent=2))
    except Exception:
        print("!! Расчёт УПАЛ:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
