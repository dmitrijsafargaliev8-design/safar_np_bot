"""Validated short corrections to saved Telegram orders.

A partial correction is accepted only as an explicit field/value instruction.
No carrier API calls or shipment creation happen in this module.
"""
import math
import re

from np_client import normalize_phone

_FIELDS = {
    "фио": "full_name", "фіо": "full_name", "піб": "full_name",
    "получатель": "full_name", "отримувач": "full_name",
    "телефон": "phone", "phone": "phone", "номер телефона": "phone",
    "город": "city", "місто": "city", "city": "city",
    "отделение": "warehouse", "відділення": "warehouse",
    "почтомат": "warehouse", "поштомат": "warehouse", "warehouse": "warehouse",
    "оценка": "cost", "оцінка": "cost", "стоимость": "cost",
    "вартість": "cost", "оголошена вартість": "cost",
    "наложка": "cod_amount", "післяплата": "cod_amount",
    "накладений платіж": "cod_amount", "cod": "cod_amount",
    "вес": "weight", "вага": "weight", "weight": "weight",
    "товар": "description", "опис": "description", "описание": "description",
}
_LABELS = {
    "full_name": "ФИО", "phone": "Телефон", "city": "Город",
    "warehouse": "Отделение", "cost": "Оценка",
    "cod_amount": "Наложка", "weight": "Вес", "description": "Товар",
}


def parse_field_patch(text):
    """Return a 1–2 field patch, or None for a complete/unknown order message."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not 1 <= len(lines) <= 2:
        return None
    changes = {}
    for line in lines:
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        key = re.sub(r"\s+", " ", key.lower().replace("ё", "е").strip())
        field = _FIELDS.get(key)
        if not field:
            return None
        value = value.strip()
        if not value:
            raise ValueError(f"Поле «{_LABELS[field]}» не может быть пустым.")
        if field in changes and changes[field] != value:
            raise ValueError(f"Разные значения для поля «{_LABELS[field]}».")
        changes[field] = value
    return changes


def _number(value, field, *, allow_zero=False):
    cleaned = re.sub(r"[\s\u00a0]", "", value).replace(",", ".")
    if not re.fullmatch(r"\d{1,8}(?:\.\d{1,2})?", cleaned):
        raise ValueError(f"Поле «{_LABELS[field]}»: требуется число.")
    result = float(cleaned)
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"Поле «{_LABELS[field]}»: недопустимая сумма.")
    return result


def apply_field_patch(base, changes):
    """Never mutate the issued receipt or the caller's original order."""
    if not base or not isinstance(base, dict):
        raise ValueError("Для исправления одного поля нужен сохранённый заказ. Пришли полный исправленный заказ.")
    result = dict(base)
    for key, raw in changes.items():
        value = raw.strip()
        if key == "phone":
            result[key] = normalize_phone(value)
        elif key == "full_name":
            words = value.split()
            if not 2 <= len(words) <= 4 or any(
                not re.fullmatch(r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+", word) for word in words
            ):
                raise ValueError("ФИО должно содержать 2–4 слова.")
            result[key] = " ".join(words)
        elif key == "city":
            if len(value) > 100 or re.search(r"[\n\r]", value):
                raise ValueError("Название города слишком длинное.")
            result[key] = value
        elif key == "warehouse":
            match = re.fullmatch(r"(?:№|#)?\s*(\d{1,5})", value)
            if not match or int(match.group(1)) == 0:
                raise ValueError("Напиши номер отделения, например: Отделение: 142.")
            result[key] = str(int(match.group(1)))
            result["delivery_point_type"] = ""
            result["street"] = result["house"] = result["flat"] = ""
        elif key in {"cost", "weight", "cod_amount"}:
            if key == "cod_amount" and value.casefold() in {
                "нет", "нема", "немає", "без наложки", "без післяплати", "оплачено", "сплачено"
            }:
                result[key] = 0.0
            else:
                result[key] = _number(value, key, allow_zero=(key == "cod_amount"))
        elif key == "description":
            if len(value) > 100:
                raise ValueError("Описание товара должно быть не длиннее 100 символов.")
            result[key] = value
        else:
            raise ValueError("Неподдерживаемое поле исправления.")
    return result


def patch_labels(changes):
    return ", ".join(_LABELS.get(key, key) for key in changes)
