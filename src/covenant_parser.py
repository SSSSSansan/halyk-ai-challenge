"""
Берёт сырой текст пункта договора (6.1 / 6.2 / 6.3) и просит LLM превратить его
в чёткую структуру: что за показатель, лимит, направление сравнения, оговорки.

Ковенанты у разных заёмщиков РАЗНЫЕ по формулировке (это подтверждено на дне 1),
поэтому регуляркой смысл не вытащить — нужен LLM.
"""
import re
from llm_client import call_json

CATEGORIES = [
    "Revenue", "CapEx", "OpEx", "Payroll", "Utilities",
    "Rent", "Interest", "Insurance", "RelatedPartyPayment", "Other",
]
# EBITDA - расчётная категория (Revenue - OpEx), доступна LLM как numerator/denominator,
# но её нельзя классифицировать транзакцию В неё - это агрегат, не тип операции.
EXTENDED_CATEGORIES_FOR_FORMULA = CATEGORIES + ["EBITDA"]

SYSTEM_PROMPT = f"""\
Ты — финансовый аналитик банка, разбираешь пункты кредитного договора (ковенанты).
Текст пункта может быть на русском ИЛИ на английском языке - работай с обоими.
Тебе дают текст одного пункта. Верни СТРОГО валидный JSON со следующими полями,
СТРОГО В ЭТОМ ПОРЯДКЕ (сначала reasoning, потом остальное - сначала подумай, потом отвечай):

{{
  "reasoning": "2-4 предложения: что измеряет пункт, откуда взять числитель/знаменатель,
                есть ли особые условия (springing, carve-outs) - подумай вслух, прежде чем
                заполнять поля ниже",

  "clause": "номер пункта, например 6.1",
  "metric_description": "что именно измеряется, простыми словами по-русски",
  "comparator": "max" | "min",
  "threshold": число (лимит),
  "threshold_unit": "ratio" | "usd" | "share",
  "period_hint": "период теста, как написано в тексте",
  "special_conditions": "оговорки/условия применения, или null если их нет",

  "formula_type": "sum" | "ratio" | "share" | "max_of",
  "numerator_category": одна из категорий {EXTENDED_CATEGORIES_FOR_FORMULA},
  "denominator_category": одна из категорий {EXTENDED_CATEGORIES_FOR_FORMULA}, или null если formula_type != "ratio"/"share",
  "max_of_categories": список категорий из {EXTENDED_CATEGORIES_FOR_FORMULA}, ТОЛЬКО если formula_type == "max_of", иначе null
}}

"max_of" используй, когда в тексте явно сказано, что несколько статей проверяются ПО ОТДЕЛЬНОСТИ
("по отдельности, а не в совокупности", "наибольшая из указанных сумм", "любая отдельная статья")
- то есть берём category с максимальной суммой из списка, а НЕ их сумму.

"EBITDA" - это Выручка минус Операционные расходы, используй её, когда в тексте пункта
явно упоминается EBITDA заёмщика. Если в тексте пункта показатель определяется через
консолидированную отчётность материнской компании ("Группы"), которой у тебя нет -
всё равно верни ближайшую по смыслу категорию заёмщика (например CapEx) и укажи это
ограничение в special_conditions.

Категории транзакций для numerator_category/denominator_category СТРОГО из этого списка:
{EXTENDED_CATEGORIES_FOR_FORMULA}.
Не добавляй никаких полей сверх перечисленных. Не пиши пояснений вне JSON.
"""


def _is_inconsistent(structured: dict) -> str | None:
    """
    Возвращает текст причины несогласованности, если структура внутренне противоречива
    или неполна для своего formula_type. Возвращает None, если всё согласовано.
    """
    unit = structured.get("threshold_unit")
    formula = structured.get("formula_type")
    numerator = structured.get("numerator_category")
    denominator = structured.get("denominator_category")
    max_of = structured.get("max_of_categories")

    if unit == "usd" and formula in ("ratio", "share"):
        return (f"threshold_unit='usd' (абсолютная сумма в долларах), но formula_type='{formula}' "
                f"(безразмерное отношение) - это несовместимо. Абсолютная сумма в долларах "
                f"должна считаться через formula_type='sum' (или 'max_of'), не 'ratio'/'share'.")
    if unit in ("ratio", "share") and formula == "sum":
        return (f"threshold_unit='{unit}', но formula_type='sum' (абсолютная сумма) - это "
                f"несовместимо. threshold_unit='{unit}' должен считаться через "
                f"formula_type='{unit}'.")
    if formula == "sum" and not numerator:
        return "formula_type='sum', но numerator_category не заполнен (null) - нужно указать конкретную категорию."
    if formula in ("ratio", "share") and (not numerator or not denominator):
        return (f"formula_type='{formula}', но numerator_category и/или denominator_category "
                f"не заполнены (null) - для '{formula}' нужны ОБЕ категории.")
    if formula == "max_of" and not max_of:
        return "formula_type='max_of', но max_of_categories пуст/null - нужно перечислить минимум 2 категории списком."
    if formula == "max_of" and max_of is not None and len(max_of) < 2:
        return "formula_type='max_of', но max_of_categories содержит меньше 2 категорий - 'max_of' имеет смысл только для нескольких статей."
    return None


def parse_covenant(clause_text: str, _attempt: int = 0, _max_attempts: int = 3) -> dict:
    """
    Просит LLM разобрать пункт в структуру. Делает до _max_attempts попыток:
    - если сам вызов LLM упал (битый JSON и т.п.) - повторяет запрос как есть;
    - если JSON пришёл валидный, но threshold_unit/formula_type противоречат друг
      другу (см. _is_inconsistent) - повторяет запрос с explicit-подсказкой о
      найденном противоречии, чтобы LLM перечитал текст пункта внимательнее.
    После исчерпания попыток возвращает последний результат (или пробрасывает
    последнее исключение) - pipeline.py уже умеет обработать оставшуюся ошибку.
    """
    prompt = clause_text
    last_exception = None
    for attempt in range(_max_attempts):
        try:
            structured = call_json(system_prompt=SYSTEM_PROMPT, user_prompt=prompt)
        except Exception as e:
            last_exception = e
            print(f"    [retry {attempt + 1}/{_max_attempts}] parse_covenant LLM-вызов упал: {e}")
            continue

        reason = _is_inconsistent(structured)
        if reason is None:
            return structured

        print(f"    [retry {attempt + 1}/{_max_attempts}] несогласованный разбор: {reason}")
        prompt = (
            f"{clause_text}\n\n"
            f"[ПОВТОРНАЯ ПОПЫТКА - в прошлый раз ты вернул несогласованный результат: {reason} "
            f"Перечитай текст пункта внимательно ещё раз и верни согласованные "
            f"threshold_unit и formula_type.]"
        )
        last_exception = None
        last_bad_result = structured

    if last_exception is not None:
        raise last_exception
    # все попытки дали несогласованный, но валидный JSON - возвращаем последнюю попытку,
    # это лучше, чем упасть в null (pipeline.py всё равно досчитает, что сможет)
    print(f"    [!] parse_covenant: не удалось согласовать за {_max_attempts} попыток, беру последний результат")
    return last_bad_result


CLAUSE_HEADER_RE = re.compile(r"(?:Пункт|Section|Clause|Article)\s+(\d+\.\d+)\b")


def extract_clauses(agreement_text: str, needed_clause_ids: list[str]) -> dict:
    """
    Достаёт из ПОЛНОГО текста договора именно те пункты, которые запрошены в
    submission_template.json для этого заёмщика - независимо от языка (RU/EN)
    и от того, какая это статья по счёту (5.x, 6.x, что угодно).

    В начале договора обычно есть оглавление с теми же номерами пунктов, но
    коротким текстом - поэтому для каждого номера берём ПОСЛЕДНЕЕ вхождение
    (настоящий текст пункта всегда идёт после оглавления и заметно длиннее).
    """
    matches = list(CLAUSE_HEADER_RE.finditer(agreement_text))
    positions_by_id = {}
    for m in matches:
        if m.group(1) in needed_clause_ids:
            positions_by_id[m.group(1)] = m.start()  # перезаписываем - остаётся последнее

    sorted_positions = sorted(positions_by_id.items(), key=lambda kv: kv[1])
    result = {}
    for i, (clause_id, start) in enumerate(sorted_positions):
        # конец пункта - начало следующего найденного заголовка (любого номера), не только нужного
        next_headers = [m.start() for m in matches if m.start() > start]
        end = min(next_headers) if next_headers else len(agreement_text)
        result[clause_id] = agreement_text[start:end].strip()
    return result


def parse_all_covenants_for_scenario(article_text: str) -> dict:
    """Статья 6 целиком -> {"6.1": {structured}, "6.2": {...}, "6.3": {...}}"""
    raw_clauses = split_covenant_article(article_text)
    structured = {}
    for clause_id, text in raw_clauses.items():
        structured[clause_id] = parse_covenant(text)
    return structured


if __name__ == "__main__":
    import json
    with open("/home/claude/halyk/output/covenant_texts.json") as f:
        covenant_texts = json.load(f)

    # Проверяем разбивку по пунктам (без LLM) на одном заёмщике
    sample_text = covenant_texts["P5"]["text"]
    parts = split_covenant_article(sample_text)
    print("Найдено пунктов:", list(parts.keys()))
    for clause_id, text in parts.items():
        print(f"\n--- {clause_id} ({len(text)} симв.) ---")
        print(text[:200])