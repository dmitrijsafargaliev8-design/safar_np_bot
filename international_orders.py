"""Lossless intake for international Nova Post orders.

This module parses a forwarding message into an operator-review draft. It never
creates a waybill or infers customs/declared values from the product photo.
The domestic Nova Poshta v2 client is deliberately not used for this route.
"""
import re

_COUNTRY_NAMES = {
    "PL": ("Польша", {"polska", "poland", "польша", "польща", "pl"}),
    "DE": ("Германия", {"germany", "deutschland", "германия", "німеччина", "de"}),
    "CZ": ("Чехия", {"czechia", "cesko", "česko", "чехия", "чехія", "cz"}),
    "SK": ("Словакия", {"slovakia", "slovensko", "словакия", "словаччина", "sk"}),
    "RO": ("Румыния", {"romania", "românia", "румыния", "румунія", "ro"}),
    "MD": ("Молдова", {"moldova", "молдова", "md"}),
    "LT": ("Литва", {"lithuania", "lietuva", "литва", "литва", "lt"}),
    "LV": ("Латвия", {"latvia", "latvija", "латвия", "латвія", "lv"}),
    "EE": ("Эстония", {"estonia", "eesti", "эстония", "естонія", "ee"}),
    "FR": ("Франция", {"france", "франция", "франція", "fr"}),
    "IT": ("Италия", {"italy", "italia", "италия", "італія", "it"}),
    "ES": ("Испания", {"spain", "españa", "испания", "іспанія", "es"}),
    "GB": ("Великобритания", {"united kingdom", "great britain", "uk", "gb", "британия", "великобританія"}),
    "US": ("США", {"usa", "united states", "сша", "us"}),
}
_COUNTRY_LOOKUP = {alias.casefold(): code for code, (_, aliases) in _COUNTRY_NAMES.items() for alias in aliases}
_PHONE = re.compile(r"(?<!\d)\+(?:\d[\s().-]*){8,15}(?!\d)")
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?!\w)")
_BRANCH = re.compile(
    r"(?i)\b(?:nova\s*post|нова\s*пошта|новая\s*почта)\s*[.,-]?\s*"
    r"(?:oddzia[łl]|відділення|отделение|branch)\s*[№#:]?\s*(\d{1,5})\b|"
    r"\b(?:oddzia[łl]|branch)\s*[№#:]?\s*(\d{1,5})\b"
)
_PL_CITY_ZIP = re.compile(r"^([A-Za-zÀ-žА-Яа-яІіЇїЄєҐґ'’ .-]{2,100}?)\s+(?:PL[-\s]?)?(\d{2}-\d{3})$")
_NAME = re.compile(r"^[^\W\d_]+(?:[ '\-’][^\W\d_]+){1,5}$", re.UNICODE)
_NUMBER = re.compile(r"^\d+(?:[.,]\d{1,2})?$")
_FIELDS = {
    "фио": "full_name", "фіо": "full_name", "піб": "full_name",
    "получатель": "full_name", "отримувач": "full_name", "recipient": "full_name",
    "phone": "phone", "телефон": "phone", "номер": "phone",
    "city": "city", "город": "city", "місто": "city",
    "индекс": "postal_code", "індекс": "postal_code", "postal code": "postal_code",
    "zip": "postal_code", "kod pocztowy": "postal_code",
    "отделение": "warehouse", "відділення": "warehouse", "oddział": "warehouse",
    "branch": "warehouse", "email": "email", "e-mail": "email", "почта": "email",
    "вес": "weight", "вага": "weight", "weight": "weight",
    "товар": "description", "опис": "description", "описание": "description",
    "contents": "description", "description": "description",
    "оценка": "cost", "оцінка": "cost", "стоимость": "cost", "вартість": "cost",
    "value": "cost", "declared value": "cost",
    "валюта": "currency", "валюта оцінки": "currency", "currency": "currency",
    "адрес": "street_address", "адреса": "street_address", "address": "street_address",
    "страна происхождения": "origin_country", "країна походження": "origin_country",
    "country of origin": "origin_country",
    "количество": "quantity", "кількість": "quantity", "quantity": "quantity",
}
_AMOUNT = re.compile(r"(?i)^([\d\s.,]+?)\s*(UAH|PLN|EUR|USD|GBP|грн|zł|€|\$)?$")
_CURRENCY = {"ГРН": "UAH", "ZŁ": "PLN", "€": "EUR", "$": "USD"}


def _lines(text):
    return [line.strip(" \t•📍📦👤☎️") for line in (text or "").replace("\u00a0", " ").splitlines() if line.strip()]


def detect_country(text):
    """Recognise a destination only when explicitly identified, not in product descriptions."""
    for line in _lines(text):
        lower = line.strip().casefold()
        if lower in _COUNTRY_LOOKUP:
            return _COUNTRY_LOOKUP[lower]
        match = re.fullmatch(r"(?i)(?:страна|країна|country|destination)\s*:\s*(.+)", line)
        if match and match.group(1).strip().casefold() in _COUNTRY_LOOKUP:
            return _COUNTRY_LOOKUP[match.group(1).strip().casefold()]
    return None


def normalize_international_phone(value):
    """Keep explicit E.164 country code; never rewrite a foreign number as +380."""
    digits = re.sub(r"\D", "", value or "")
    if not (value or "").strip().startswith("+") or not 8 <= len(digits) <= 15:
        raise ValueError("Международный телефон укажи с кодом страны, например +48XXXXXXXXX.")
    return "+" + digits


def _amount(raw):
    match = _AMOUNT.fullmatch(raw.strip())
    if not match:
        raise ValueError("Международная оценка: укажи сумму и валюту, например Оценка: 100 EUR.")
    value = match.group(1).replace(" ", "").replace(",", ".")
    if not _NUMBER.fullmatch(value) or not 0 < float(value) <= 100000000:
        raise ValueError("Международная оценка должна быть положительным числом.")
    unit = (match.group(2) or "").upper()
    return float(value), _CURRENCY.get(unit, unit)


def parse_international_order(text):
    country_code = detect_country(text)
    if not country_code:
        raise ValueError("Укажи страну назначения отдельной строкой, например Polska.")
    lines = _lines(text)
    order = dict(
        shipment_scope="international", country_code=country_code,
        country=_COUNTRY_NAMES[country_code][0], full_name="", phone="",
        city="", postal_code="", warehouse="", delivery_point_type="",
        branch_address="", street_address="", street="", house="", flat="",
        email="", description="", cost=None, currency="", weight=None,
        cod_amount=0.0, quantity=None, origin_country="",
    )

    # Named fields are explicit overrides. A correction to a draft can append
    # another named line without silently replacing photographs or other fields.
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        field = _FIELDS.get(key.strip().casefold())
        value = value.strip()
        if not field or not value:
            continue
        if field == "cost":
            order["cost"], amount_currency = _amount(value)
            if amount_currency:
                order["currency"] = amount_currency
        elif field == "weight":
            match = re.fullmatch(r"(?i)(\d+(?:[.,]\d{1,2})?)\s*(?:kg|кг)?", value)
            if not match or float(match.group(1).replace(",", ".")) <= 0:
                raise ValueError("Вес международного заказа укажи в кг, например Вес: 1.2 кг.")
            order["weight"] = float(match.group(1).replace(",", "."))
        elif field == "quantity":
            if not re.fullmatch(r"\d{1,4}", value) or int(value) == 0:
                raise ValueError("Количество должно быть положительным целым числом.")
            order["quantity"] = int(value)
        elif field == "currency":
            order["currency"] = _CURRENCY.get(value.upper(), value.upper())
        elif field == "phone":
            order["phone"] = normalize_international_phone(value)
        elif field == "warehouse":
            number = re.fullmatch(r"(?:№|#)?\s*(\d{1,5})", value)
            if not number:
                raise ValueError("Отделение: укажи номер, например Отделение: 1.")
            order["warehouse"] = str(int(number.group(1)))
        elif field in order:
            order[field] = value

    phones = {normalize_international_phone(m.group(0)) for m in _PHONE.finditer(text)}
    if len(phones) > 1 or (phones and order["phone"] and order["phone"] not in phones):
        raise ValueError("Найдено несколько телефонов: уточни получателя.")
    if phones and not order["phone"]:
        order["phone"] = next(iter(phones))
    if not order["email"]:
        emails = set(m.group(0) for m in _EMAIL.finditer(text))
        if len(emails) > 1:
            raise ValueError("Найдено несколько email: уточни адрес получателя.")
        if emails:
            order["email"] = next(iter(emails))
    for line in lines:
        if not order["warehouse"]:
            branch = _BRANCH.search(line)
            if branch:
                order["warehouse"] = branch.group(1) or branch.group(2)
                order["delivery_point_type"] = "branch"
        if not order["city"] and country_code == "PL":
            match = _PL_CITY_ZIP.fullmatch(line)
            if match:
                order["city"], order["postal_code"] = match.group(1).strip(), match.group(2)
        if not order["full_name"] and _NAME.fullmatch(line) and (
            2 <= len(line.split()) <= 4 and not any(ch.isdigit() for ch in line)
        ):
            # An explicit country line, key-value label or postal line is not a person.
            if detect_country(line) is None and ":" not in line and not _BRANCH.search(line):
                order["full_name"] = line
    # Polish branch's street address is informational, NOT a home delivery address.
    for line in lines:
        stripped = _PHONE.sub("", line).strip(" ,;-")
        if country_code == "PL" and re.search(r"\b\d{1,4}(?:/[A-Za-z0-9]+)?\b", stripped):
            if _PL_CITY_ZIP.fullmatch(stripped) or _BRANCH.search(stripped) or ":" in stripped:
                continue
            if order["warehouse"] and not order["branch_address"]:
                order["branch_address"] = stripped
            elif not order["warehouse"] and not order["street_address"]:
                order["street_address"] = stripped
    if order["warehouse"]:
        order["delivery_point_type"] = "branch"
    if order["postal_code"] and country_code == "PL" and not re.fullmatch(r"\d{2}-\d{3}", order["postal_code"]):
        raise ValueError("Польский индекс должен быть в формате 00-000.")
    return order


def international_missing(order):
    """Data needed for operator review; no implied permission to call a carrier."""
    needed = [
        ("full_name", "ФИО"), ("phone", "Телефон с кодом страны"),
        ("city", "Город"), ("postal_code", "Почтовый индекс"),
        ("email", "Email"), ("cost", "Оценка товара"), ("currency", "Валюта оценки"),
        ("weight", "Вес в кг"), ("description", "Описание товара"),
        ("quantity", "Количество"), ("origin_country", "Страна происхождения товара"),
    ]
    missing = [label for key, label in needed if order.get(key) in (None, "")]
    if not order.get("warehouse") and not order.get("street_address"):
        missing.append("Отделение Nova Post или адрес доставки")
    return missing


def is_international_supplement(text):
    """Recognise correction fields only, rather than treating a complete new order as a patch."""
    lines = _lines(text)
    return bool(lines) and len(lines) <= 12 and all(
        ":" in line and line.split(":", 1)[0].strip().casefold() in _FIELDS
        for line in lines
    )
