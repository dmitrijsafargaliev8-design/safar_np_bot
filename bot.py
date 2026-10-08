import os
import re
import hmac
import logging
import math
import threading

from access_policy import AccessPolicy
from sender_profiles import SenderProfiles

from dotenv import load_dotenv
from flask import Flask, jsonify, request
import telebot

from np_client import NovaPoshtaClient, NovaPoshtaError, normalize_phone

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("safar_np_bot")


TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("Bot_taking")
    or ""
).strip()
NOVA_POSHTA_API_KEY = (
    os.getenv("NOVA_POSHTA_API_KEY")
    or os.getenv("NP_API_KEY")
    or os.getenv("Apy_key_np")
    or ""
).strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("WEBHOOK_BASE_URL")
    or os.getenv("WEBHOOK_URL")
    or (
        f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}"
        if os.getenv("RENDER_EXTERNAL_HOSTNAME")
        else ""
    )
).rstrip("/")

ACCESS_POLICY = AccessPolicy(
    chats=os.getenv("ALLOWED_CHAT_IDS", ""),
    users=os.getenv("ALLOWED_USER_IDS", ""),
    strict=os.getenv("STRICT_ACCESS_POLICY", "0").strip() == "1",
)
ALLOWED_CHAT_IDS = ACCESS_POLICY.chats

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN/BOT_TOKEN is missing")
if not NOVA_POSHTA_API_KEY:
    logger.error("NOVA_POSHTA_API_KEY/NP_API_KEY is missing")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
RELEASE_VERSION = "2026.10.08-smila-geo-rc4"
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False) if TELEGRAM_BOT_TOKEN else None
np_client = NovaPoshtaClient(NOVA_POSHTA_API_KEY) if NOVA_POSHTA_API_KEY else None
sender_profiles = SenderProfiles(np_client) if np_client else None

def _access_permits(chat_id, user_id):
    """Apply both legacy chat restrictions and the stricter actor policy."""
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        return False
    return ACCESS_POLICY.permits(chat_id, user_id)


def _is_allowed(message, *, actor_id=None) -> bool:
    """Validate both chat and the real human initiating a command/callback."""
    chat = getattr(message, "chat", None)
    sender = getattr(message, "from_user", None)
    user_id = actor_id if actor_id is not None else getattr(sender, "id", None)
    return _access_permits(getattr(chat, "id", None), user_id)


def _canonical_key(raw: str) -> str:
    key = re.sub(r"\s+", " ", (raw or "").strip().lower().replace("ё", "е"))
    aliases = {
        "фио": "full_name",
        "пиб": "full_name",
        "піб": "full_name",
        "имя": "full_name",
        "отримувач": "full_name",
        "получатель": "full_name",
        "телефон": "phone",
        "номер": "phone",
        "phone": "phone",
        "город": "city",
        "місто": "city",
        "city": "city",
        "отделение": "warehouse",
        "відділення": "warehouse",
        "почтомат": "warehouse",
        "поштомат": "warehouse",
        "warehouse": "warehouse",
        "вес": "weight",
        "вага": "weight",
        "weight": "weight",
        "описание": "description",
        "опис": "description",
        "товар": "description",
        "стоимость": "cost",
        "вартість": "cost",
        "цена": "cost",
        "ціна": "cost",
        "объявленная стоимость": "cost",
        "оголошена вартість": "cost",
        "оценка": "cost",
        "оцінка": "cost",
        "наложка": "cod_amount",
        "наложенный платеж": "cod_amount",
        "накладений платіж": "cod_amount",
        "післяплата": "cod_amount",
        "наложенный платёж": "cod_amount",
        "cod": "cod_amount",
        "плательщик": "payer_type",
        "платник": "payer_type",
        "оплата доставки": "payer_type",
        "способ оплаты": "payment_method",
        "спосіб оплати": "payment_method",
        "email": "email",
        "e-mail": "email",
        "почта": "email",
        "область": "area",
        "район": "region",
        "улица": "street",
        "вулиця": "street",
        "дом": "house",
        "будинок": "house",
        "квартира": "flat",
    }
    return aliases.get(key, key)


PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?38[\s().-]*)?0[\s().-]*\d(?:[\s().-]*\d){8}(?!\d)"
)
WAREHOUSE_RE = re.compile(
    r"(?i)\b(?:н\.?п\.?|нова\s*пошта|новая\s*почта|отд(?:еление)?|відд(?:ілення)?|почтомат|поштомат)"
    r"\s*(?:(?:№|#)\s*|(?:номер|ном\.?|number|no?\.?)\s*)?[:\-]?\s*(\d{1,5})\b"
)
COST_RE = re.compile(
    r"(?i)\b(?:оценка|оцінка|стоимость|вартість|объявленная\s+стоимость|оголошена\s+вартість)"
    r"\b[ \t]*:?[ \t]*(-?[\d \t.,]+)"
)
COD_RE = re.compile(
    r"(?i)\b(?:наложка|наложенный\s+плат[её]ж|накладений\s+платіж|післяплата|cod)"
    r"\b[ \t]*:?[ \t]*(-?[\d \t.,]+)"
)
AREA_RE = re.compile(r"(?i)^(.+?)\s+(?:область|області|обл\.?)$")
# Composite city+region lines are common in forwarded Ukrainian orders:
# "м. Сміла, Черкаська обл" / "Черкаська обл, м. Сміла".
# They MUST be separated before applying AREA_RE; otherwise the entire
# "м. Сміла, Черкаська" is accidentally interpreted as an oblast.
CITY_AREA_RE = re.compile(
    r"(?i)^(?P<city>.+?)\s*[,;]\s*(?P<area>[^,;]+?)\s+(?:область|області|обл\.?)$"
)
AREA_CITY_RE = re.compile(
    r"(?i)^(?P<area>[^,;]+?)\s+(?:область|області|обл\.?)\s*[,;]\s*(?P<city>.+)$"
)
PREFIXED_CITY_AREA_RE = re.compile(
    r"(?i)^(?P<city>(?:м(?:істо)?|г(?:ород)?|с(?:ело)?|смт|пгт)\.?\s*"
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+(?:\s+[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+)?)"
    r"\s+(?P<area>[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+)\s+(?:область|області|обл\.?)$"
)
REGION_RE = re.compile(r"(?i)^(.+?)\s+(?:район|р-н\.?)$")
SETTLEMENT_RE = re.compile(
    r"(?i)^(м(?:істо)?|г(?:ород)?|с(?:ело)?|смт|пгт|селище|пос(?:елок)?)"
    r"(?:\.\s*|\s+)(.+)$"
)
ADDRESS_RE = re.compile(
    r"(?i)^(?:вул(?:иця)?|улица|ул|просп(?:ект)?|проспект|пров(?:улок)?|переулок|"
    r"шосе|наб(?:ережна)?|набережная)\s*[.,:;\-]?\s+(.+)$"
)
FLAT_RE = re.compile(
    r"(?i)(?:,\s*|\s+)(?:кв(?:артира)?|кв\.?|apt\.?)\s*[:№#-]?\s*"
    r"([0-9]+[0-9A-Za-zА-Яа-яІіЇїЄєҐґ/-]*)\s*$"
)
HOUSE_RE = re.compile(
    r"(?i)(?:,\s*|\s+)(?:(?:буд(?:инок)?|дом|д)\.?\s*)?"
    r"([0-9]+[0-9A-Za-zА-Яа-яІіЇїЄєҐґ/-]*)\s*$"
)


def _clean_lines(text: str):
    return [
        re.sub(r"^[^\w+№#]+", "", re.sub(r"\s+", " ", raw_line)).strip()
        for raw_line in (text or "").splitlines()
        if raw_line.strip()
    ]


def _settlement_type(prefix: str) -> str:
    p = (prefix or "").strip().lower().replace(".", "")
    if p.startswith("село") or p == "с":
        return "село"
    if p in {"смт", "пгт"} or p.startswith("селище") or p.startswith("пос"):
        return "селище міського типу"
    return "місто"


def _split_city_area_line(line: str):
    """Split explicitly qualified city/oblast pairs without guessing geography.

    Do not invent a location or change spelling. Nova Poshta still checks the
    exact city+area via its address directory before a TTN can be created.
    """
    # A line containing a branch, a phone or a price is a FULL order segment,
    # not just a location. Leave its parsing to the existing full-order path.
    # For example: "Киевская обл, Васильков, нп 4, ...".
    if any(pattern.search(line or "") for pattern in (
        WAREHOUSE_RE, PHONE_RE, COST_RE, COD_RE,
    )):
        return None
    match = (CITY_AREA_RE.fullmatch(line or "")
             or AREA_CITY_RE.fullmatch(line or "")
             or PREFIXED_CITY_AREA_RE.fullmatch(line or ""))
    if not match:
        return None
    raw_city = match.group("city").strip(" ,.")
    raw_area = match.group("area").strip(" ,.")
    city_match = SETTLEMENT_RE.fullmatch(raw_city)
    city = city_match.group(2).strip(" ,.") if city_match else raw_city
    if not city or not raw_area:
        return None
    return {
        "city": city,
        "area": raw_area,
        "settlement_type": (_settlement_type(city_match.group(1)) if city_match else ""),
    }


def _parse_street_line(line: str):
    match = ADDRESS_RE.match(line or "")
    if not match:
        return None

    body = match.group(1).strip(" ,")
    flat = ""

    flat_match = FLAT_RE.search(body)
    if flat_match:
        flat = flat_match.group(1)
        body = body[:flat_match.start()].strip(" ,")

    house_match = HOUSE_RE.search(body)
    if not house_match:
        return None

    house = house_match.group(1)
    street = body[:house_match.start()].strip(" ,")
    if not street:
        return None

    return {
        "street": street,
        "house": house,
        "flat": flat,
    }


def parse_order(text: str):
    raw_text = (text or "").replace("\u00a0", " ").replace("\u200b", "").strip()
    if not raw_text:
        raise ValueError("В сообщении нет текста заказа.")

    data = {}
    lines = _clean_lines(raw_text)
    phones = {normalize_phone(m.group(0)) for m in PHONE_RE.finditer(raw_text)}
    if len(phones) > 1:
        raise ValueError("В сообщении несколько телефонов. Перешли каждый заказ отдельно.")

    # Формат "Ключ: Значение" по-прежнему поддерживается.
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = _canonical_key(key)
        value = value.strip()
        if value and key in {
            "full_name", "phone", "city", "warehouse", "weight", "cost", "cod_amount",
            "payer_type", "payment_method", "description", "email", "area", "region",
            "street", "house", "flat",
        }:
            if key in data and data[key] != value:
                raise ValueError(f"В заказе разные значения поля «{key}». Уточни его ответом.")
            data[key] = value

    if not data.get("phone"):
        match = PHONE_RE.search(raw_text)
        if match:
            data["phone"] = match.group(0)

    warehouse_line_index = None
    area_line_index = None
    region_line_index = None
    settlement_line_index = None
    address_line_index = None

    # Отделение НП.
    for i, line in enumerate(lines):
        match = WAREHOUSE_RE.search(line)
        if not match:
            continue
        warehouse_line_index = i
        data.setdefault("warehouse", match.group(1))
        if re.search(r"(?i)почтомат|поштомат", line):
            data["delivery_point_type"] = "postomat"

        # Также понимает "Одесса НП 142" в одной строке.
        city_prefix = line[:match.start()].strip(" ,;-")
        if city_prefix and not data.get("city") and len(city_prefix) <= 80:
            settlement_prefix = SETTLEMENT_RE.match(city_prefix)
            if settlement_prefix and not ADDRESS_RE.match(city_prefix):
                data["city"] = settlement_prefix.group(2).strip(" ,.")
                data["settlement_type"] = _settlement_type(settlement_prefix.group(1))
            else:
                data["city"] = city_prefix
        break

    # Адресная доставка: область / район / населённый пункт / улица+дом.
    # A combined location line must be checked FIRST, even for warehouse
    # deliveries: "м. Сміла, Черкаська обл" is city+oblast, not an oblast name.
    for i, line in enumerate(lines):
        inline_location = _split_city_area_line(line)
        if inline_location:
            if data.get("area") and data["area"] != inline_location["area"]:
                raise ValueError("В заказе указаны разные области. Уточни область получателя.")
            if data.get("city") and data["city"] != inline_location["city"]:
                raise ValueError("В заказе указаны разные города. Уточни город получателя.")
            data["city"] = inline_location["city"]
            data["area"] = inline_location["area"]
            if inline_location["settlement_type"]:
                data["settlement_type"] = inline_location["settlement_type"]
            area_line_index = i
            settlement_line_index = i
            continue

        area_match = AREA_RE.match(line)
        if area_match and not data.get("area"):
            data["area"] = area_match.group(1).strip(" ,.")
            area_line_index = i
            continue

        region_match = REGION_RE.match(line)
        if region_match and not data.get("region"):
            data["region"] = region_match.group(1).strip(" ,.")
            region_line_index = i
            continue

        settlement_match = SETTLEMENT_RE.match(line)
        if settlement_match and not data.get("city"):
            candidate = settlement_match.group(2).strip(" ,.")
            # Не спутать "вул." / "ул." с населённым пунктом.
            if candidate and not ADDRESS_RE.match(line):
                data["city"] = candidate
                data["settlement_type"] = _settlement_type(settlement_match.group(1))
                settlement_line_index = i
                continue

        address_parts = _parse_street_line(line)
        # Адрес на строке ниже "відділення №8" — это адрес самого
        # отделения, а не запрос на курьерскую доставку.
        if address_parts and not data.get("street") and not data.get("warehouse"):
            data.update(address_parts)
            address_line_index = i

    if not data.get("cost"):
        match = COST_RE.search(raw_text)
        if match:
            data["cost"] = match.group(1).strip()

    if not data.get("cod_amount"):
        match = COD_RE.search(raw_text)
        if match:
            data["cod_amount"] = match.group(1).strip()

    phone_line_index = None
    for i, line in enumerate(lines):
        if PHONE_RE.search(line):
            phone_line_index = i
            break

    # Для отправления на отделение город обычно стоит перед "НП 142".
    if not data.get("city") and warehouse_line_index is not None and warehouse_line_index > 0:
        candidate = lines[warehouse_line_index - 1]
        if (
            ":" not in candidate
            and not PHONE_RE.search(candidate)
            and not WAREHOUSE_RE.search(candidate)
            and not COST_RE.search(candidate)
            and not COD_RE.search(candidate)
        ):
            data["city"] = candidate

    # Для адресной доставки без "село/м./г." берём строку перед улицей.
    if not data.get("city") and address_line_index is not None and address_line_index > 0:
        for j in range(address_line_index - 1, -1, -1):
            if j in {area_line_index, region_line_index}:
                continue
            candidate = lines[j]
            if (
                candidate
                and not PHONE_RE.search(candidate)
                and not COST_RE.search(candidate)
                and not COD_RE.search(candidate)
                and not AREA_RE.match(candidate)
                and not REGION_RE.match(candidate)
            ):
                data["city"] = candidate.strip(" ,.")
                break

    # ФИО может стоять как до, так и после телефона.
    # Это важно для пересланных заказов вида:
    # "м. Коростень / Нова пошта 7 / 096... / Синяк Віта / Оценка 900"
    # и для того же заказа, собранного Telegram в одну строку.
    if not data.get("full_name") and phone_line_index is not None:
        phone_line = lines[phone_line_index]
        phone_match = PHONE_RE.search(phone_line)

        def _name_candidate(value: str) -> str:
            candidate = (value or "").strip(" .,;-:")
            if not candidate:
                return ""

            # Если после имени в той же строке идёт "Оценка ..." или "Наложка ...",
            # отрезаем служебную часть.
            cut_positions = []
            for pattern in (COST_RE, COD_RE):
                marker = pattern.search(candidate)
                if marker:
                    cut_positions.append(marker.start())
            if cut_positions:
                candidate = candidate[:min(cut_positions)].strip(" .,;-:")

            if (
                not candidate
                or PHONE_RE.search(candidate)
                or WAREHOUSE_RE.search(candidate)
                or COST_RE.search(candidate)
                or COD_RE.search(candidate)
                or AREA_RE.match(candidate)
                or REGION_RE.match(candidate)
                or ADDRESS_RE.match(candidate)
            ):
                return ""

            if candidate == data.get("city"):
                return ""

            # Имя получателя для НП обычно содержит минимум 2 словесные части.
            words = re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+", candidate)
            if len(words) < 2 or len(words) > 4 or re.search(r"\d", candidate):
                return ""
            if not re.fullmatch(r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+(?:\s+[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+){1,3}", candidate):
                return ""
            if any(word.lower() in {"кроссовки", "кросівки", "обувь", "взуття", "одяг", "размер", "розмір", "оплачено", "сплачено", "наложка", "післяплата", "доставка", "товар", "артикул"} for word in words):
                return ""
            return candidate

        if phone_match:
            # Самый частый формат из группы: телефон, затем ФИО.
            same_line_suffix = _name_candidate(phone_line[phone_match.end():])
            if same_line_suffix:
                data["full_name"] = same_line_suffix

            # Старый формат тоже оставляем: ФИО перед телефоном.
            if not data.get("full_name"):
                prefix = phone_line[:phone_match.start()]
                branch = WAREHOUSE_RE.search(prefix)
                if branch:
                    prefix = prefix[branch.end():]
                same_line_prefix = _name_candidate(prefix)
                if same_line_prefix:
                    data["full_name"] = same_line_prefix

        if not data.get("full_name"):
            skip_indices = {
                x for x in (
                    area_line_index,
                    region_line_index,
                    settlement_line_index,
                    address_line_index,
                )
                if x is not None
            }

            # Сначала строки ПОСЛЕ телефона, затем ДО него.
            # Так "096... / Синяк Віта / Оценка 900" разбирается без шаблонов.
            nearby_indices = list(range(phone_line_index + 1, len(lines)))
            nearby_indices += list(range(phone_line_index - 1, -1, -1))

            for j in nearby_indices:
                if j in skip_indices:
                    continue
                candidate = lines[j].strip()
                if not candidate:
                    continue

                # Подпись может содержать адрес, отделение и ФИО в одной
                # строке, а телефон — на следующей. Проверяем только хвост
                # после номера отделения, не весь адрес как имя.
                if j == warehouse_line_index:
                    branch = WAREHOUSE_RE.search(candidate)
                    candidate = candidate[branch.end():]

                if ":" in candidate:
                    candidate_key = _canonical_key(candidate.split(":", 1)[0])
                    if candidate_key in {
                        "phone",
                        "city",
                        "warehouse",
                        "cost",
                        "cod_amount",
                        "weight",
                        "description",
                    }:
                        continue

                candidate = _name_candidate(candidate)
                if candidate:
                    data["full_name"] = candidate
                    break

    if data.get("street") and not data.get("house") and not data.get("warehouse"):
        parts = _parse_street_line("ул. " + data["street"])
        if parts:
            data.update(parts)
    if data.get("warehouse"):
        data["street"] = data["house"] = data["flat"] = ""

    missing = [
        label
        for key, label in (
            ("full_name", "ФИО"),
            ("phone", "Телефон"),
            ("city", "Город/населённый пункт"),
            ("cost", "Оценка/стоимость"),
        )
        if not data.get(key)
    ]

    has_warehouse = bool(data.get("warehouse"))
    has_address = bool(data.get("street") and data.get("house"))
    if not has_warehouse and not has_address:
        missing.append("Отделение НП или адрес")

    if missing:
        raise ValueError("Не хватает полей: " + ", ".join(missing))

    def num(name, default):
        raw = data.get(name)
        if raw is None:
            return default
        if name == "cod_amount" and re.fullmatch(r"(?i)(?:нет|немає|нема|без|без наложки|оплачено|сплачено)", str(raw).strip()):
            return 0.0
        cleaned = re.sub(r"[^\d,.\-]", "", str(raw)).replace(",", ".")
        if name in {"cost", "cod_amount"}:
            compact = re.sub(r"[^\d,.-]", "", str(raw))
            if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?", compact):
                groups = re.split(r"[.,]", compact)
                cleaned = "".join(groups) if len(groups[-1]) == 3 else "".join(groups[:-1]) + "." + groups[-1]
        try:
            value = float(cleaned)
        except ValueError:
            raise ValueError(f"Поле {name} должно быть числом.")
        if not math.isfinite(value) or value < 0 or (name != "cod_amount" and value == 0):
            raise ValueError(f"Поле {name} должно содержать корректное положительное число.")
        return value

    data["weight"] = num("weight", 1.0)
    data["cost"] = num("cost", 200.0)
    data["cod_amount"] = num("cod_amount", 0.0)
    data["phone"] = normalize_phone(data["phone"])
    no_cod = bool(re.search(r"(?i)\b(?:без\s+наложки|без\s+післяплати|без\s+накладеного\s+платежу)\b", raw_text))
    if no_cod and data["cod_amount"] > 0:
        raise ValueError("Одновременно указаны «без наложки» и сумма наложки. Уточни оплату.")
    if re.search(r"(?i)\b(?:наложка|післяплата|наложенный\s+плат[её]ж)\b", raw_text) and not data.get("cod_amount"):
        if not re.search(r"(?i)\b(?:без|нет|немає|нема|оплачено|сплачено|0)\b", raw_text):
            raise ValueError("Указана наложка без суммы. Напиши сумму или «Без наложки».")

    data.setdefault("warehouse", "")
    data.setdefault("delivery_point_type", "")
    data.setdefault("street", "")
    data.setdefault("house", "")
    data.setdefault("flat", "")
    data.setdefault("area", "")
    data.setdefault("region", "")
    data.setdefault("settlement_type", "")
    data.setdefault("description", "Одяг та взуття")
    data.setdefault("payer_type", "Recipient")
    data.setdefault("payment_method", "Cash")
    data.setdefault("email", "")
    return data


BOT_COMMANDS = (
    ("start", "Начать работу с ботом"),
    ("menu", "Все команды и быстрые подсказки"),
    ("help", "Как переслать заказ с фото"),
    ("example", "Образец заполненного заказа"),
    ("orders", "Мои последние 10 заказов"),
    ("queue", "Заказы в очереди и с ошибками"),
    ("stats", "Статистика обработки заказов"),
    ("sender", "Выбрать профиль отправителя"),
    ("sendercheck", "Проверка основного отправителя без создания ТТН"),
    ("whoami", "Мой Telegram ID для настройки доступа"),
    ("track", "Проверить статус ТТН"),
    ("retry", "Повторить заказ или пересоздать удалённую ТТН"),
    ("status", "Проверить работу бота и Новой почты"),
)
MENU_TEXT = (
    "SAFAR NP BOT\n\n" + "\n".join(f"/{command} — {description}" for command, description in BOT_COMMANDS)
    + "\n\nЗаказ с фото просто перешли сюда из группы."
    + "\n/track — последний заказ, ответ на карточку или /track НОМЕР_ТТН."
    + "\n/retry отправляй ответом на карточку заказа или сообщение с ошибкой."
    + "\n/whoami отправляй боту в личные сообщения для безопасной настройки доступа."
)
ORDER_EXAMPLE = (
    "Одесса\n"
    "НП 142\n"
    "Тестовий Отримувач\n"
    "+380 50 000 00 01\n"
    "Оценка 1600"
)
HELP_TEXT = (
    "Перешли заказ из группы вместе с фото или альбомом. Текст заказа может быть в подписи к любому фото.\n\n"
    "Нужны: ФИО, телефон, город, отделение НП и оценка. Для адресной доставки — город, улица и дом.\n\n"
    "Оценка — объявленная стоимость. Наложка включается только отдельной строкой «Наложка 1600».\n\n"
    "Если нужна правка, ответь на сообщение с ошибкой полным исправленным заказом: фото сохранятся.\n"
    "Для повтора ответь /retry. Если прежняя ТТН удалена в Новой почте, бот создаст новую; действующую повторно не создаёт.\n\n"
    "Для исправления одного поля ответь на карточку: Телефон: +380... или Оценка: 1600.\n"
    "/sender — текущий профиль; /sender ID — переключить для будущих заказов.\n"
    "/sendercheck — безопасная проверка ФОП/кабинета НП без отправки.\n"
    "/example — образец заказа.\n/menu — все команды."
)
_telegram_commands = []


def register_command_handlers(telegram_bot):
    @telegram_bot.message_handler(commands=["whoami"])
    def whoami_handler(message):
        # Configure allowlists before strict mode. Never respond in blocked chats.
        if not _is_allowed(message) or message.chat.type not in {"private", "group", "supergroup"}:
            return
        telegram_bot.reply_to(
            message,
            f"Твой Telegram User ID: {message.from_user.id}\n"
            f"Chat ID: {message.chat.id}\n\n"
            "Для доступа из группы также потребуется её Chat ID. "
            "Не отправляй API-ключи или пароли в Telegram.",
        )

    @telegram_bot.message_handler(commands=["start", "menu"])
    def start_handler(message):
        if not _is_allowed(message):
            return
        telegram_bot.reply_to(message, MENU_TEXT)

    @telegram_bot.message_handler(commands=["help"])
    def help_handler(message):
        if _is_allowed(message):
            telegram_bot.reply_to(message, HELP_TEXT)

    @telegram_bot.message_handler(commands=["example"])
    def example_handler(message):
        if _is_allowed(message):
            telegram_bot.reply_to(message, "Образец — замени данные получателя:\n\n" + ORDER_EXAMPLE
                                  + "\n\nЕсли нужна наложка, добавь отдельную строку: Наложка 1600."
                                  + "\nЗатем отправь заказ вместе с фото товара.")

    @telegram_bot.message_handler(commands=["status"])
    def status_handler(message):
        if not _is_allowed(message):
            return

        telegram_ok = False
        np_ok = False
        sender_status = "не проверен"
        storage_status = "ERROR"
        try:
            pipeline = get_pipeline()
            if pipeline:
                with pipeline.lock:
                    if pipeline.db.ping():
                        storage_status = ("PostgreSQL (постоянное)" if pipeline.db.persistent
                                          else "SQLite (непостоянное на Render Free)")
        except Exception:
            logger.warning("Order journal status check failed")

        try:
            telegram_ok = bool(telegram_bot.get_me().id)
        except Exception as exc:
            logger.warning("Telegram status failed: %s", exc)

        active_client = (sender_profiles.get(sender_profiles.primary_profile)
                         if sender_profiles else np_client)
        if active_client:
            try:
                np_ok = active_client.ping()
                try:
                    active_client.get_sender_info()
                    sender_status = "OK"
                except Exception as exc:
                    sender_status = f"нужна настройка: {exc}"
            except Exception as exc:
                logger.warning("Nova Poshta status failed: %s", type(exc).__name__)

        telegram_bot.reply_to(
            message,
            "SAFAR NP BOT\n"
            f"Telegram: {'OK' if telegram_ok else 'ERROR'}\n"
            f"Nova Poshta API: {'OK' if np_ok else 'ERROR'}\n"
            f"Sender: {sender_status}\n"
            f"Order journal: {storage_status}",
        )

    @telegram_bot.message_handler(commands=["orders"])
    def orders_handler(message):
        if not _is_allowed(message):
            return
        pipeline = get_pipeline()
        if not pipeline:
            telegram_bot.reply_to(message, "Сервис обработки заказов пока недоступен.")
            return
        jobs = pipeline.list_orders(message.chat.id, message.from_user.id)
        labels = {"collecting": "в очереди", "processing": "обрабатывается", "created": "готово",
                  "invalid": "нужны данные", "failed": "ошибка", "uncertain": "проверить в НП",
                  "deleted": "удалена в НП"}
        lines = []
        for job in jobs:
            order = job.get("order") or {}
            result = job.get("result") or {}
            lines.append(f"{result.get('ttn') or '—'} · {order.get('full_name') or 'Заказ'} · {labels[job['state']]}")
        telegram_bot.reply_to(message, "Последние заказы:\n" + "\n".join(lines) if lines else "Заказов пока нет.")

    @telegram_bot.message_handler(commands=["queue"])
    def queue_handler(message):
        if not _is_allowed(message):
            return
        pipeline = get_pipeline()
        if not pipeline:
            telegram_bot.reply_to(message, "Очередь пока недоступна.")
            return
        jobs = pipeline.order_queue(message.chat.id, message.from_user.id)
        if not jobs:
            telegram_bot.reply_to(message, "✅ Нет ожидающих обработки или проблемных заказов.")
            return
        names = {"collecting": "ожидает", "processing": "обрабатывается",
                 "invalid": "нужны данные", "failed": "ошибка",
                 "uncertain": "проверить в НП"}
        lines = ["📦 ОЧЕРЕДЬ И ПРОБЛЕМНЫЕ ЗАКАЗЫ"]
        for job in jobs:
            order = job.get("order") or {}
            lines.append(
                f"• {order.get('full_name') or 'Новый заказ'} · "
                f"{names.get(job['state'], job['state'])}"
            )
        telegram_bot.reply_to(message, "\n".join(lines))

    @telegram_bot.message_handler(commands=["stats"])
    def stats_handler(message):
        if not _is_allowed(message):
            return
        pipeline = get_pipeline()
        if not pipeline:
            telegram_bot.reply_to(message, "Статистика пока недоступна.")
            return
        counts = pipeline.order_state_counts(message.chat.id, message.from_user.id)
        pending = sum(counts.get(k, 0) for k in ("collecting", "processing"))
        problems = sum(counts.get(k, 0) for k in ("invalid", "failed", "uncertain"))
        telegram_bot.reply_to(
            message,
            "📊 SAFAR — СТАТИСТИКА ЗАКАЗОВ\n"
            f"Всего записей: {sum(counts.values())}\n"
            f"Созданные: {counts.get('created', 0)}\n"
            f"В обработке: {pending}\n"
            f"Требуют внимания: {problems}\n"
            f"Удалённые ТТН: {counts.get('deleted', 0)}\n\n"
            "Показаны записи текущего пользователя и чата, не финансовая выручка.",
        )

    @telegram_bot.message_handler(commands=["sender"])
    def sender_handler(message):
        if not _is_allowed(message):
            return
        pipeline = get_pipeline()
        if not pipeline or not sender_profiles:
            telegram_bot.reply_to(message, "Профили отправителей недоступны.")
            return
        profiles = sender_profiles.available()
        current = pipeline.sender_preference(message.chat.id, message.from_user.id)
        parts = (message.text or "").split(None, 1)
        if len(parts) == 1:
            lines = ["📦 ОТПРАВИТЕЛЬ",
                     f"Основной по умолчанию: {sender_profiles.primary_profile}",
                     f"Текущий: {profiles.get(current, 'профиль недоступен')} ({current})",
                     "Доступные профили:"]
            lines += [f"• {pid} — {label}" for pid, label in profiles.items()]
            lines += ["Выбрать для НОВЫХ заказов: /sender ID.",
                      "Для другого человека сначала настрой подтверждённые реквизиты в Render."]
            telegram_bot.reply_to(message, "\n".join(lines))
            return
        profile_id = parts[1].strip().lower()
        if profile_id not in profiles:
            telegram_bot.reply_to(message, "Неизвестный профиль. Напиши /sender для списка.")
            return
        # Check API-linked sender data BEFORE switching; no shipments are created.
        try:
            sender_profiles.get(profile_id).get_sender_info()
        except NovaPoshtaError as exc:
            telegram_bot.reply_to(message, "⚠️ Профиль не активирован: " + str(exc)[:320])
            return
        pipeline.set_sender_preference(message.chat.id, message.from_user.id, profile_id)
        telegram_bot.reply_to(
            message, f"✅ Отправитель для будущих заказов: {profiles[profile_id]} ({profile_id}).\n"
                     "Ранее пересланные и оформленные заказы не изменены."
        )

    @telegram_bot.message_handler(commands=["sendercheck"])
    def sendercheck_handler(message):
        if not _is_allowed(message):
            return
        if not sender_profiles:
            telegram_bot.reply_to(message, "Отправители ещё не настроены.")
            return
        primary = sender_profiles.primary_profile
        try:
            sender_profiles.get(primary).get_sender_info()
            telegram_bot.reply_to(
                message, f"✅ Основной отправитель: {primary}. "
                         "Контрагент, контакт, отделение и телефон определены. "
                         "ТТН не создавалась."
            )
        except NovaPoshtaError as exc:
            telegram_bot.reply_to(
                message, f"⚠️ Основной отправитель {primary}: " + str(exc)[:320]
                + "\nТТН не создавалась."
            )

    @telegram_bot.message_handler(commands=["track"])
    def track_handler(message):
        if not _is_allowed(message):
            return
        parts = (message.text or "").split(None, 1)
        number = re.sub(r"\s+", "", parts[1]) if len(parts) == 2 else ""
        order = None
        if len(parts) == 1:
            pipeline = get_pipeline()
            if pipeline:
                if message.reply_to_message:
                    order = pipeline.order_for_message(message.chat.id, message.from_user.id,
                                                       message.reply_to_message.message_id)
                else:
                    order = next((job for job in pipeline.list_orders(message.chat.id, message.from_user.id)
                                  if job["state"] == "created" and (job.get("result") or {}).get("ttn")), None)
            number = ((order or {}).get("result") or {}).get("ttn", "")
        if not re.fullmatch(r"\d{14}", number):
            telegram_bot.reply_to(message, "Отправь /track и номер ТТН из 14 цифр или ответь /track на карточку своего заказа.")
            return
        if not np_client:
            telegram_bot.reply_to(message, "Проверка ТТН пока недоступна.")
            return
        try:
            profile = ((order or {}).get("sender_profile")
                       or (sender_profiles.primary_profile if sender_profiles else "default"))
            client = sender_profiles.get(profile) if sender_profiles else np_client
            row = client.get_ttn_status(number, phone=((order or {}).get("order") or {}).get("phone", ""))
        except NovaPoshtaError:
            telegram_bot.reply_to(message, "Не удалось получить статус из Новой почты. Повтори /track позже.")
            return
        text = f"ТТН: {number}\nСтатус: {row.get('Status') or 'Статус пока не указан'}"
        code = str(row.get("StatusCode"))
        if code in {"9", "10", "11"} and row.get("DateReceived"):
            text += f"\nПолучено: {row['DateReceived']}"
        elif code not in {"2", "3"} and row.get("ScheduledDeliveryDate"):
            text += f"\nОжидаемая доставка: {row['ScheduledDeliveryDate']}"
        telegram_bot.reply_to(message, text)

    @telegram_bot.message_handler(commands=["retry"])
    def retry_hint_handler(message):
        if _is_allowed(message):
            telegram_bot.reply_to(message, "Ответь командой /retry на карточку заказа или сообщение с ошибкой. Фото сохранятся.")

    @telegram_bot.callback_query_handler(func=lambda call: str(getattr(call, "data", "") or "").startswith("safar:"))
    def card_action_handler(call):
        """Actions never mutate shipments and are scoped to card owner."""
        action = (call.data or "").split(":", 1)[-1]
        if action not in {"track", "edit", "history"} or not getattr(call, "message", None):
            telegram_bot.answer_callback_query(call.id, "Неизвестная кнопка", show_alert=True)
            return
        message = call.message
        if not _is_allowed(message, actor_id=call.from_user.id):
            telegram_bot.answer_callback_query(call.id, "Нет доступа", show_alert=True)
            return
        pipeline = get_pipeline()
        job = (pipeline.order_for_message(message.chat.id, call.from_user.id, message.message_id)
               if pipeline else None)
        if not job:
            telegram_bot.answer_callback_query(call.id, "Заказ доступен только его отправителю", show_alert=True)
            return
        history = pipeline.history_for_message(message.chat.id, call.from_user.id, message.message_id)
        result = (history or {}).get("current") or job.get("result") or {}
        ttn = str(result.get("ttn") or "")
        order = job.get("order") or {}
        telegram_bot.answer_callback_query(call.id)
        reply = dict(chat_id=message.chat.id, reply_to_message_id=message.message_id,
                     allow_sending_without_reply=True)
        if getattr(message, "message_thread_id", None):
            reply["message_thread_id"] = message.message_thread_id
        if action == "edit":
            if job["state"] == "created":
                text = (
                    "✏️ Исправить оформленную ТТН:\n"
                    "1. Сначала удали действующую ТТН в Новой почте.\n"
                    "2. Ответь НА ЭТУ карточку: Телефон: +380... или Оценка: 1600.\n"
                    "3. Отправь /retry ответом на карточку.\n\n"
                    "Пока удаление не подтверждено НП, бот не создаст вторую ТТН. Фото сохранятся."
                )
            else:
                text = ("✏️ Ответь НА ЭТУ карточку полным заказом или одним полем, "
                        "например: Телефон: +380... Фото сохранятся.")
            telegram_bot.send_message(text=text, **reply)
        elif action == "history":
            records = (history or {}).get("history") or []
            lines = ["🕘 ИСТОРИЯ ЗАКАЗА"]
            for index, entry in enumerate(records, 1):
                previous = (entry.get("result") or {}).get("ttn")
                if previous:
                    lines.append(f"{index}. ТТН {previous} — удалена")
            if ttn:
                lines.append(f"Текущая ТТН: {ttn} · {job['state']}")
            if len(lines) == 1:
                lines.append("История отправок пока отсутствует.")
            telegram_bot.send_message(text="\n".join(lines), **reply)
        else:
            if not ttn or not np_client:
                telegram_bot.send_message(text="Статус ТТН пока недоступен.", **reply)
                return
            try:
                profile = job.get("sender_profile", "default")
                client = sender_profiles.get(profile) if sender_profiles else np_client
                row = client.get_ttn_status(ttn, phone=order.get("phone", ""))
                status = row.get("Status") or "Статус пока не указан"
                text = f"🚚 ТТН: {ttn}\nСтатус: {status}"
                code = str(row.get("StatusCode"))
                if code in {"9", "10", "11"} and row.get("DateReceived"):
                    text += f"\nПолучено: {row['DateReceived']}"
                elif code not in {"2", "3"} and row.get("ScheduledDeliveryDate"):
                    text += f"\nОжидаемая доставка: {row['ScheduledDeliveryDate']}"
            except NovaPoshtaError:
                text = "Новая почта временно недоступна. Нажми «Статус доставки» позже."
            telegram_bot.send_message(text=text, **reply)

    @telegram_bot.message_handler(func=lambda message: bool((message.text or "").startswith("/")))
    def unknown_command_handler(message):
        if _is_allowed(message):
            telegram_bot.reply_to(message, "Эта команда не найдена.\n\n" + MENU_TEXT)


if bot:
    register_command_handlers(bot)


_pipeline = None
_pipeline_lock = threading.Lock()


def storage_error_details(exc):
    """Keep connection diagnostics useful without writing credentials to logs."""
    from urllib.parse import unquote, urlsplit
    detail = str(exc)
    secrets = [value for key, value in os.environ.items() if value and any(
        word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "DATABASE_URL")
    )]
    try:
        password = urlsplit(os.getenv("STATE_DATABASE_URL", "")).password
        if password:
            secrets.extend((password, unquote(password)))
    except ValueError:
        pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        detail = detail.replace(secret, "[redacted]")
    detail = re.sub(r"postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", detail)
    detail = re.sub(r"(?i)(password\s*=\s*)(?:'[^']*'|\"[^\"]*\"|[^\s]+)", r"\1[redacted]", detail)
    return f"{type(exc).__name__} [{getattr(exc, 'sqlstate', None) or 'no SQLSTATE'}]: {detail[:1000]}"


def get_pipeline():
    global _pipeline
    if not bot or not np_client:
        return None
    with _pipeline_lock:
        if _pipeline is None:
            from order_pipeline import OrderPipeline
            database_url = os.getenv("STATE_DATABASE_URL", "").strip()
            if os.getenv("STATE_REQUIRE_PERSISTENT") == "1" and not database_url:
                raise RuntimeError("Persistent order storage is required")
            _pipeline = OrderPipeline(
                os.getenv("STATE_DB_PATH", ".state/orders.sqlite3"), parse_order, np_client, bot,
                database_url=database_url or None,
                sender_clients=sender_profiles.clients if sender_profiles else None,
                default_sender_profile=sender_profiles.primary_profile if sender_profiles else "default",
            )
        return _pipeline


@app.before_request
def start_order_processor():
    # Start after the Gunicorn fork, never in its master process.
    try:
        pipeline = get_pipeline()
        if pipeline and os.getenv("SAFAR_DISABLE_BACKGROUND") != "1":
            pipeline.start()
    except Exception as exc:
        # Fail closed: Telegram retries instead of losing an acknowledged order.
        logger.error("Order storage unavailable: %s", storage_error_details(exc))
        return jsonify(error="order storage unavailable"), 503


@app.get("/")
def root():
    return jsonify(service="SAFAR NP BOT", status="ok", version=RELEASE_VERSION)


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        version=RELEASE_VERSION,
        order_processor_running=bool(_pipeline and _pipeline.thread and _pipeline.thread.is_alive()),
        telegram_configured=bool(TELEGRAM_BOT_TOKEN),
        nova_poshta_configured=bool(NOVA_POSHTA_API_KEY),
        webhook_secret_configured=bool(WEBHOOK_SECRET),
        telegram_commands_configured=bool(_telegram_commands),
        telegram_commands=_telegram_commands,
        order_storage=_pipeline.db.backend if _pipeline else None,
        order_storage_persistent=bool(_pipeline and _pipeline.db.persistent),
        access_policy=ACCESS_POLICY.status(),
    )


@app.get("/bot-info")
def bot_info():
    if not bot:
        return jsonify(error="Telegram bot is not configured"), 503
    me = bot.get_me()
    return jsonify(username=me.username or "", first_name=me.first_name or "")


@app.get("/ready")
def ready():
    telegram_ok = False
    nova_poshta_ok = False
    sender_ok = False
    errors = {}

    if bot:
        try:
            telegram_ok = bool(bot.get_me().id)
        except Exception as exc:
            errors["telegram"] = type(exc).__name__

    primary_client = (sender_profiles.get(sender_profiles.primary_profile)
                      if sender_profiles else np_client)
    if primary_client:
        try:
            nova_poshta_ok = bool(primary_client.ping())
        except Exception as exc:
            errors["nova_poshta"] = type(exc).__name__

        if nova_poshta_ok:
            try:
                primary_client.get_sender_info()
                sender_ok = True
            except Exception as exc:
                errors["sender"] = type(exc).__name__
                try:
                    senders = primary_client._call(
                        "Counterparty",
                        "getCounterparties",
                        {"CounterpartyProperty": "Sender", "Page": "1"},
                    )
                    if len(senders) == 1 and senders[0].get("Ref"):
                        sender_ref = senders[0]["Ref"]
                        addresses = primary_client._call(
                            "Counterparty",
                            "getCounterpartyAddresses",
                            {
                                "Ref": sender_ref,
                                "CounterpartyProperty": "Sender",
                                "Page": "1",
                            },
                        )
                        safe_options = [
                            {
                                "Ref": a.get("Ref"),
                                "Description": a.get("Description"),
                                "ShortAddress": a.get("ShortAddress"),
                                "CityRef": a.get("CityRef"),
                            }
                            for a in addresses
                        ]
                        logger.info("SENDER_ADDRESS_OPTIONS=%s", safe_options)
                except Exception as diag_exc:
                    logger.warning("Could not list sender address options: %s", diag_exc)

    storage_ok = False
    try:
        pipeline = get_pipeline()
        if pipeline:
            with pipeline.lock:
                storage_ok = pipeline.db.ping()
    except Exception as exc:
        errors["order_storage"] = type(exc).__name__
    overall = telegram_ok and nova_poshta_ok and sender_ok and storage_ok
    logger.info(
        "READY_CHECK telegram_ok=%s nova_poshta_ok=%s sender_ok=%s errors=%s",
        telegram_ok, nova_poshta_ok, sender_ok, errors
    )
    return jsonify(
        status="ready" if overall else "not_ready",
        order_storage_ok=storage_ok,
        telegram_ok=telegram_ok,
        nova_poshta_ok=nova_poshta_ok,
        sender_ok=sender_ok,
        errors=errors,
    ), (200 if overall else 503)


@app.route("/ops/create-one-time-ttn", methods=["GET", "POST"])
def create_one_time_ttn():
    """Retired: in-memory one-off creation is unsafe after worker restart.

    A lost response or reboot could create an uncontrolled duplicate TTN.
    All shipments must pass through the persistent receipt/identity journal.
    """
    return jsonify(error="retired; forward the order through the signed Telegram webhook"), 410


@app.post("/webhook")
def webhook():
    if not bot:
        return jsonify(error="Telegram bot token is not configured"), 503
    if not WEBHOOK_SECRET:
        return jsonify(error="WEBHOOK_SECRET is not configured"), 503

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        return jsonify(error="forbidden"), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or type(payload.get("update_id")) is not int:
        return jsonify(error="invalid update"), 400
    pipeline = get_pipeline()
    if not pipeline:
        return jsonify(error="order processor not configured"), 503
    update_id = payload["update_id"]
    try:
        if pipeline.has_update(update_id):
            return jsonify(ok=True, duplicate=True)
        if payload.get("callback_query"):
            update = telebot.types.Update.de_json(payload)
            bot.process_new_updates([update])
            pipeline.remember_update(update_id)
            return jsonify(ok=True)
        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict):
            pipeline.remember_update(update_id)
            return jsonify(ok=True, ignored=True)
        chat_id = (message.get("chat") or {}).get("id")
        if (message.get("from") or {}).get("is_bot"):
            pipeline.remember_update(update_id)
            return jsonify(ok=True, ignored=True)
        text = message.get("text") or ""
        command = text.split(None, 1)[0].split("@", 1)[0] if text.startswith("/") else ""
        if not _access_permits(chat_id, (message.get("from") or {}).get("id")):
            pipeline.remember_update(update_id)
            return jsonify(ok=True, ignored=True)
        if command == "/retry":
            if not message.get("reply_to_message"):
                bot.send_message(chat_id, "Ответь командой /retry на карточку заказа. Если ТТН удалена в НП, создам новую.")
                pipeline.remember_update(update_id)
            else:
                try:
                    pipeline.ingest(message, update_id, retry=True)
                except ValueError:
                    bot.send_message(chat_id, "Заказ для повтора не найден. Перешли исходный заказ с фото.")
                    pipeline.remember_update(update_id)
        elif command:
            update = telebot.types.Update.de_json(payload)
            bot.process_new_updates([update])
            pipeline.remember_update(update_id)
        elif any(message.get(k) for k in ("text", "photo", "video", "document", "animation")):
            pipeline.ingest(message, update_id, edited="edited_message" in payload)
        else:
            pipeline.remember_update(update_id)
        return jsonify(ok=True)
    except ValueError as exc:
        # A signed, authorized correction with an invalid field should receive
        # actionable feedback rather than disappearing as a Telegram 400.
        if isinstance(message, dict) and _access_permits(
            (message.get("chat") or {}).get("id"), (message.get("from") or {}).get("id")
        ):
            try:
                bot.send_message(chat_id, "⚠️ Исправление не принято: " + str(exc)[:350])
                pipeline.remember_update(update_id)
                return jsonify(ok=True, validation_error=True)
            except Exception:
                logger.exception("Could not acknowledge invalid correction")
                return jsonify(error="temporary message failure"), 503
        return jsonify(error="invalid message"), 400
    except (KeyError, TypeError):
        logger.warning("Malformed Telegram update id=%s", update_id)
        return jsonify(error="invalid message"), 400
    except Exception:
        logger.exception("Webhook journal write failed")
        # No success acknowledgement if the order could not be saved: Telegram
        # will retry the same update.
        return jsonify(error="webhook processing failed"), 503


def ensure_webhook():
    if not bot or not WEBHOOK_SECRET or not PUBLIC_BASE_URL:
        return
    url = f"{PUBLIC_BASE_URL}/webhook"
    try:
        bot.set_webhook(
            url=url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=False,
            max_connections=1,
            allowed_updates=["message", "edited_message", "callback_query"],
            timeout=15,
        )
        logger.info("Telegram webhook configured for orders and corrections")
    except Exception:
        logger.exception("Could not configure Telegram webhook automatically")


def ensure_command_menu():
    """Publish the slash menu and verify Telegram's stored default commands."""
    global _telegram_commands
    _telegram_commands = []
    if not bot:
        return False
    try:
        commands = [telebot.types.BotCommand(command, description) for command, description in BOT_COMMANDS]
        for language in ("", "ru", "uk"):
            if not bot.set_my_commands(commands, language_code=language):
                raise RuntimeError("Telegram did not accept the command menu")
        if not bot.set_chat_menu_button(menu_button=telebot.types.MenuButtonCommands(type="commands")):
            raise RuntimeError("Telegram did not accept the menu button")
        actual = bot.get_my_commands()
        if [(command.command, command.description) for command in actual] != list(BOT_COMMANDS):
            raise RuntimeError("Telegram command verification did not match")
        _telegram_commands = [command.command for command in actual]
        logger.info("Telegram command menu verified: %s", ", ".join(_telegram_commands))
        return True
    except Exception as exc:
        logger.warning("Could not configure Telegram command menu: %s", type(exc).__name__)
        return False


ensure_webhook()
ensure_command_menu()

# Temporary setup diagnostic for the confirmed sender branch:
# Nova Poshta mobile branch №778, Odesa, vul. Bazova 20.
try:
    if np_client and not os.getenv("NP_SENDER_ADDRESS_REF"):
        _sender_city_ref = np_client.get_city_ref("Одеса")
        _sender_wh_ref = np_client.get_warehouse_ref(_sender_city_ref, "778")
        logger.info(
            "CONFIRMED_SENDER_LOOKUP city_ref=%s warehouse_ref=%s",
            _sender_city_ref,
            _sender_wh_ref,
        )
except Exception as exc:
    logger.warning("CONFIRMED_SENDER_LOOKUP_FAILED=%s", exc)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

