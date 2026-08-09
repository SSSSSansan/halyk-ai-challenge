"""
Тонкая обёртка над OpenAI API. Используем gpt-4o-mini — дешёвая модель,
которой достаточно для извлечения структуры из текста и классификации.

ВАЖНО: ключ берём из переменной окружения OPENAI_API_KEY. Никогда не хардкодим
ключ в коде. Перед запуском:  export OPENAI_API_KEY="sk-..."
"""
import os
import json
from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"

_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Не найден OPENAI_API_KEY в переменных окружения. "
                "Сделай: export OPENAI_API_KEY='твой ключ' перед запуском пайплайна."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def call_json(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.0) -> dict:
    """
    Вызывает модель и просит вернуть СТРОГО валидный JSON (без markdown-обёртки).
    Возвращает уже распарсенный dict/list.
    """
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = response.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Модель вернула невалидный JSON: {raw[:500]}") from e


if __name__ == "__main__":
    # Мини-тест на реальном ключе (запускать вручную, тут в песочнице сеть закрыта)
    result = call_json(
        system_prompt="Ты отвечаешь только валидным JSON.",
        user_prompt='Верни JSON вида {"ok": true, "echo": "<повтори эту фразу>: привет"}',
    )
    print(result)
