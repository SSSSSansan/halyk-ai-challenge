"""
По содержимому (не по имени файла!) находит для каждого account_id:
  - актуальный кредитный договор (не устаревшую версию)
  - примечания к финансовой отчётности
  - KYC-досье связанных сторон
  - финальный отчёт аудитора (если есть, черновики отбрасываем)

Работает на паттернах фраз, которые повторяются в любом датасете такого формата.
"""
import os
import re
import pdfplumber
from datetime import datetime

def _has_any(text, phrases):
    return any(p in text for p in phrases)


AGREEMENT_MARKERS = ["ДОГОВОР БАНКОВСКОГО ЗАЙМА", "CREDIT AGREEMENT", "LOAN AGREEMENT"]
STALE_MARKERS = ["НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ", "НЕ ПРИМЕНЯЕТСЯ", "SUPERSEDED", "NOT OPERATIVE", "NOT IN EFFECT"]
NOTES_MARKERS = ["Примечания к финансовой отчётности", "Notes to the Financial Statements", "Notes to Financial Statements"]
KYC_MARKERS_1 = ["Знай своего клиента", "Know Your Customer", "KYC"]
KYC_MARKERS_2 = ["Проверка связанных сторон", "Related Party Review", "Related-Party Review"]
AUDIT_FINAL_MARKERS = ["отчёт о выполнении согласованных процедур проверки", "agreed-upon procedures report", "report on agreed-upon procedures"]
DRAFT_MARKERS = ["ПРОЕКТ", "DRAFT"]

EN_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})"
    r"|(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})"
)
EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"]
)}
RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
RU_DATE_RE = re.compile(r"(\d{1,2}) (\w+) (\d{4}) года")


def parse_any_date(text):
    """Пытаемся распарсить дату договора - и в русском, и в английском формате."""
    m = RU_DATE_RE.search(text)
    if m:
        day, month_word, year = m.groups()
        month = RU_MONTHS.get(month_word.lower())
        if month:
            try:
                return datetime(int(year), month, int(day))
            except ValueError:
                pass

    m = EN_DATE_RE.search(text)
    if m:
        groups = m.groups()
        if groups[0]:  # "January 1, 2025"
            month_word, day, year = groups[0], groups[1], groups[2]
        else:  # "1 January 2025"
            day, month_word, year = groups[3], groups[4], groups[5]
        month = EN_MONTHS.get(month_word)
        if month:
            try:
                return datetime(int(year), month, int(day))
            except ValueError:
                pass
    return None


def extract_all_text(documents_folder):
    """Извлекает текст всех PDF один раз, кэширует в память. Возвращает {filename: text}."""
    texts = {}
    for fn in sorted(os.listdir(documents_folder)):
        if not fn.lower().endswith(".pdf"):
            continue
        path = os.path.join(documents_folder, fn)
        try:
            with pdfplumber.open(path) as pdf:
                texts[fn] = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            texts[fn] = ""
            print(f"[warn] не смог прочитать {fn}: {e}")
    return texts


def _account_in_text(text, known_account_ids):
    """Ищем ТОЧНОЕ вхождение одного из известных account_id (любого формата: ACC-7801, TELE-4471...)."""
    for acc in known_account_ids:
        if acc in text:
            return acc
    return None


def find_active_agreements(texts, known_account_ids):
    """
    account_id -> filename действующего договора (RU или EN).
    Берём договор без пометки "устарел"/"superseded", а если версий
    несколько живых — с самой поздней датой.
    """
    candidates = {}  # account_id -> list of (date, filename)
    for fn, text in texts.items():
        if not _has_any(text, AGREEMENT_MARKERS):
            continue
        if _has_any(text, STALE_MARKERS):
            continue
        acc = _account_in_text(text, known_account_ids)
        if not acc:
            continue
        date = parse_any_date(text) or datetime.min
        candidates.setdefault(acc, []).append((date, fn))

    result = {}
    for acc, versions in candidates.items():
        versions.sort(key=lambda t: t[0], reverse=True)
        result[acc] = versions[0][1]
    return result


def find_financial_notes(texts, known_account_ids):
    """account_id -> filename примечаний к отчётности (RU или EN)."""
    result = {}
    for fn, text in texts.items():
        if not _has_any(text, NOTES_MARKERS):
            continue
        acc = _account_in_text(text, known_account_ids)
        if acc:
            result[acc] = fn
    return result


def find_kyc_dossiers(texts, known_account_ids):
    """account_id -> filename KYC-досье связанных сторон (RU или EN)."""
    result = {}
    for fn, text in texts.items():
        if not (_has_any(text, KYC_MARKERS_1) and _has_any(text, KYC_MARKERS_2)):
            continue
        acc = _account_in_text(text, known_account_ids)
        if acc:
            result[acc] = fn
    return result


def find_final_audit_reports(texts, known_account_ids):
    """
    account_id -> filename финального отчёта аудитора (RU или EN).
    Черновики ("ПРОЕКТ"/"DRAFT" в начале документа) отбрасываем.
    """
    result = {}
    for fn, text in texts.items():
        if not _has_any(text.lower(), [m.lower() for m in AUDIT_FINAL_MARKERS]):
            continue
        if _has_any(text[:300].upper(), DRAFT_MARKERS):
            continue
        acc = _account_in_text(text, known_account_ids)
        if acc:
            result[acc] = fn
    return result


def route_documents(documents_folder, known_account_ids):
    """Главная функция: всё сразу для всех account_id. known_account_ids - из леджера."""
    texts = extract_all_text(documents_folder)
    routing = {
        "agreements": find_active_agreements(texts, known_account_ids),
        "financial_notes": find_financial_notes(texts, known_account_ids),
        "kyc_dossiers": find_kyc_dossiers(texts, known_account_ids),
        "final_audit_reports": find_final_audit_reports(texts, known_account_ids),
    }
    return routing, texts


if __name__ == "__main__":
    import json
    from ledger_loader import load_ledger, build_scenario_account_map
    txns = load_ledger("/home/claude/halyk/data/master_ledger_2025.csv")
    template = json.load(open("/home/claude/halyk/data/submission_template.json"))
    scen_acc = build_scenario_account_map(txns, list(template["answers"].keys()))
    known_accounts = list(scen_acc.values())

    routing, texts = route_documents("/home/claude/halyk/data/documents", known_accounts)
    print("Всего PDF обработано:", len(texts))
    for kind, mapping in routing.items():
        print(f"\n{kind}: найдено для {len(mapping)} счетов")
        for acc, fn in sorted(mapping.items()):
            print(f"  {acc} -> {fn}")