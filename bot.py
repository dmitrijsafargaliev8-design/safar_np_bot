import os
import re
import hmac
import logging
from collections import deque

from dotenv import load_dotenv
from flask import Flask, jsonify, request
import telebot

from np_client import NovaPoshtaClient, NovaPoshtaError

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

ALLOWED_CHAT_IDS = {
    int(x.strip())
    for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN/BOT_TOKEN is missing")
if not NOVA_POSHTA_API_KEY:
    logger.error("NOVA_POSHTA_API_KEY/NP_API_KEY is missing")

app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=False) if TELEGRAM_BOT_TOKEN else None
np_client = NovaPoshtaClient(NOVA_POSHTA_API_KEY) if NOVA_POSHTA_API_KEY else None

_processed_updates = set()
_processed_order = deque(maxlen=2000)


def _remember_update(update_id: int) -> bool:
    if update_id in _processed_updates:
        return False
    if len(_processed_order) == _processed_order.maxlen:
        oldest = _processed_order.popleft()
        _processed_updates.discard(oldest)
    _processed_updates.add(update_id)
    _processed_order.append(update_id)
    return True


def _is_allowed(message) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return message.chat.id in ALLOWED_CHAT_IDS


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
        "наложка": "cod_amount",
        "наложенный платеж": "cod_amount",
        "накладений платіж": "cod_amount",
        "cod": "cod_amount",
        "сумма": "cod_amount",
        "сума": "cod_amount",
        "плательщик": "payer_type",
        "платник": "payer_type",
        "оплата доставки": "payer_type",
        "способ оплаты": "payment_method",
        "спосіб оплати": "payment_method",
        "email": "email",
        "e-mail": "email",
        "почта": "email",
    }
    return aliases.get(key, key)


PHONE_RE = re.compile(
    r"(?<!\\d)(?:\\+?38[\\s().-]*)?0[\\s().-]*\\d(?:[\\s().-]*\\d){8}(?!\\d)"
)
WAREHOUSE_RE = re.compile(
    r"(?i)\\b(?:нп|нова\\s*пошта|новая\\s*почта|отд(?:еление)?|відд(?:ілення)?)"
    r"\\s*(?:№|#|n)?\\s*[:\\-]?\\s*(\\d{1,5})\\b"
)
COST_RE = re.compile(
    r"(?i)\\b(?:оценка|оцінка|стоимость|вартість|объявленная\\s+стоимость|оголошена\\s+вартість)"
    r"\\b\\s*[:\\-]?\\s*([\\d\\s.,]+)"
)
COD_RE = re.compile(
    r"(?i)\\b(?:наложка|наложенный\\s+платеж|накладений\\s+платіж|післяплата|cod)"
    r"\\b\\s*[:\\-]?\\s*([\\d\\s.,]+)"
)


def _clean_lines(text: str):
    return [
        re.sub(r"\\s+", " ", raw_line).strip()
        for raw_line in (text or "").splitlines()
        if raw_line.strip()
    ]


def parse_order(text: str):
    raw_text = (text or "").strip()
    if not raw_text:
        raise ValueError("В сообщении нет текста заказа.")

    data = {}
    lines = _clean_lines(raw_text)

    # 1) Старый формат "Ключ: Значение" по-прежнему поддерживается.
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = _canonical_key(key)
        value = value.strip()
        if value:
            data[key] = value

    # 2) Свободный формат из рабочей Telegram-группы:
    # Одесса
    # НП 142
    # Погорельцева Наталья
    # +380 95 947 7703
    # Оценка 1600
    if not data.get("phone"):
        match = PHONE_RE.search(raw_text)
        if match:
            data["phone"] = match.group(0)

    warehouse_line_index = None
    for i, line in enumerate(lines):
        match = WAREHOUSE_RE.search(line)
        if not match:
            continue
        warehouse_line_index = i
        if not data.get("warehouse"):
            data["warehouse"] = match.group(1)

        # Поддержка строки "Одесса НП 142".
        city_prefix = line[:match.start()].strip(" ,;-")
        if city_prefix and not data.get("city") and len(city_prefix) <= 80:
            data["city"] = city_prefix
        break

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

    # Обычно город стоит строкой прямо перед "НП 142".
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

    # Обычно ФИО стоит прямо перед телефоном.
    if not data.get("full_name") and phone_line_index is not None:
        phone_line = lines[phone_line_index]
        phone_match = PHONE_RE.search(phone_line)
        same_line_prefix = (
            phone_line[:phone_match.start()].strip(" ,;-:")
            if phone_match
            else ""
        )

        if (
            same_line_prefix
            and not WAREHOUSE_RE.search(same_line_prefix)
            and not COST_RE.search(same_line_prefix)
            and not COD_RE.search(same_line_prefix)
        ):
            data["full_name"] = same_line_prefix
        else:
            for j in range(phone_line_index - 1, -1, -1):
                candidate = lines[j].strip()
                if not candidate:
                    continue
                if j == warehouse_line_index:
                    continue
                if candidate == data.get("city"):
                    continue
                if (
                    PHONE_RE.search(candidate)
                    or WAREHOUSE_RE.search(candidate)
                    or COST_RE.search(candidate)
                    or COD_RE.search(candidate)
                ):
                    continue
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
                if re.search(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]", candidate):
                    data["full_name"] = candidate
                    break

    missing = [
        label
        for key, label in (
            ("full_name", "ФИО"),
            ("phone", "Телефон"),
            ("city", "Город"),
            ("warehouse", "Отделение"),
        )
        if not data.get(key)
    ]
    if missing:
        raise ValueError("Не хватает полей: " + ", ".join(missing))

    def num(name, default):
        raw = data.get(name)
        if raw is None:
            return default
        cleaned = re.sub(r"[^\\d,.\\-]", "", str(raw)).replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            raise ValueError(f"Поле {name} должно быть числом.")

    data["weight"] = num("weight", 1.0)
    data["cost"] = num("cost", 200.0)
    data["cod_amount"] = num("cod_amount", 0.0)
    data.setdefault("description", "Одяг та взуття")
    data.setdefault("payer_type", "Recipient")
    data.setdefault("payment_method", "Cash")
    data.setdefault("email", "")
    return data


HELP_TEXT = (
    "Просто перешли заказ из группы в этот бот — можно вместе с фото.\\n\\n"
    "Поддерживается обычный формат из группы:\\n"
    "Одесса\\n"
    "НП 142\\n"
    "Погорельцева Наталья\\n"
    "+380 95 947 7703\\n\\n"
    "Оценка 1600\\n\\n"
    "Также работает формат «Ключ: Значение». "
    "Обязательны: ФИО, телефон, город и отделение."
)


if bot:
    @bot.message_handler(commands=["start", "help"])
    def start_handler(message):
        if not _is_allowed(message):
            return
        bot.reply_to(message, HELP_TEXT)

    @bot.message_handler(commands=["status"])
    def status_handler(message):
        if not _is_allowed(message):
            return

        telegram_ok = False
        np_ok = False
        sender_status = "не проверен"

        try:
            telegram_ok = bool(bot.get_me().id)
        except Exception as exc:
            logger.warning("Telegram status failed: %s", exc)

        if np_client:
            try:
                np_ok = np_client.ping()
                try:
                    np_client.get_sender_info()
                    sender_status = "OK"
                except Exception as exc:
                    sender_status = f"нужна настройка: {exc}"
            except Exception as exc:
                logger.warning("Nova Poshta status failed: %s", exc)

        bot.reply_to(
            message,
            "SAFAR NP BOT\n"
            f"Telegram: {'OK' if telegram_ok else 'ERROR'}\n"
            f"Nova Poshta API: {'OK' if np_ok else 'ERROR'}\n"
            f"Sender: {sender_status}",
        )

    def _process_order_message(message, order_text: str):
        if not np_client:
            bot.reply_to(message, "Ошибка конфигурации: NOVA_POSHTA_API_KEY не задан.")
            return

        try:
            order = parse_order(order_text)
            result = np_client.create_ttn(**order)
            reply = (
                f"✅ ТТН создана: {result['ttn']}\\n"
                f"{order['city']} · НП {order['warehouse']}\\n"
                f"{order['full_name']} · {order['phone']}\\n"
                f"Оценка: {order['cost']:g} грн"
            )
            if order.get("cod_amount", 0) > 0:
                reply += f"\\nНаложка: {order['cod_amount']:g} грн"
            if result.get("estimated_delivery_date"):
                reply += f"\\nОриентировочная доставка: {result['estimated_delivery_date']}"
            bot.reply_to(message, reply)
        except (ValueError, NovaPoshtaError) as exc:
            bot.reply_to(message, f"❌ {exc}\\n\\n{HELP_TEXT}")
        except Exception:
            logger.exception("Unhandled order error")
            bot.reply_to(message, "❌ Внутренняя ошибка. Детали записаны в лог сервиса.")

    @bot.message_handler(content_types=["text"])
    def text_handler(message):
        if not _is_allowed(message):
            return
        if (message.text or "").startswith("/"):
            return
        _process_order_message(message, message.text or "")

    @bot.message_handler(content_types=["photo", "video", "document", "animation"])
    def media_handler(message):
        if not _is_allowed(message):
            return

        # При пересылке заказа из группы подпись Telegram приходит в message.caption.
        # Само фото/видео остаётся в пересланном сообщении, а ТТН создаётся по подписи.
        caption = (message.caption or "").strip()
        if not caption:
            # В медиагруппе часть фотографий может приходить без подписи.
            # Не спамим ошибками: обрабатывается элемент альбома, на котором есть caption.
            return
        _process_order_message(message, caption)


@app.get("/")
def root():
    return jsonify(service="SAFAR NP BOT", status="ok")


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        telegram_configured=bool(TELEGRAM_BOT_TOKEN),
        nova_poshta_configured=bool(NOVA_POSHTA_API_KEY),
        webhook_secret_configured=bool(WEBHOOK_SECRET),
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
            errors["telegram"] = str(exc)

    if np_client:
        try:
            nova_poshta_ok = bool(np_client.ping())
        except Exception as exc:
            errors["nova_poshta"] = str(exc)

        if nova_poshta_ok:
            try:
                np_client.get_sender_info()
                sender_ok = True
            except Exception as exc:
                errors["sender"] = str(exc)
                try:
                    senders = np_client._call(
                        "Counterparty",
                        "getCounterparties",
                        {"CounterpartyProperty": "Sender", "Page": "1"},
                    )
                    if len(senders) == 1 and senders[0].get("Ref"):
                        sender_ref = senders[0]["Ref"]
                        addresses = np_client._call(
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

    overall = telegram_ok and nova_poshta_ok and sender_ok
    logger.info(
        "READY_CHECK telegram_ok=%s nova_poshta_ok=%s sender_ok=%s errors=%s",
        telegram_ok, nova_poshta_ok, sender_ok, errors
    )
    return jsonify(
        status="ready" if overall else "not_ready",
        telegram_ok=telegram_ok,
        nova_poshta_ok=nova_poshta_ok,
        sender_ok=sender_ok,
        errors=errors,
    ), (200 if overall else 503)


_one_time_ttn_result = None

@app.get("/ops/create-one-time-ttn")
def create_one_time_ttn():
    global _one_time_ttn_result
    secret = os.getenv("ONE_TIME_TTN_SECRET", "")
    supplied = request.args.get("secret", "")
    if not secret or not hmac.compare_digest(supplied, secret):
        return jsonify(error="forbidden"), 403
    if _one_time_ttn_result is not None:
        return jsonify(ok=True, duplicate_blocked=True, result=_one_time_ttn_result)
    if not np_client:
        return jsonify(error="Nova Poshta client is not configured"), 503

    try:
        result = np_client.create_ttn(
            full_name=os.getenv("ONE_TIME_TTN_NAME", ""),
            phone=os.getenv("ONE_TIME_TTN_PHONE", ""),
            city=os.getenv("ONE_TIME_TTN_CITY", ""),
            warehouse=os.getenv("ONE_TIME_TTN_WAREHOUSE", ""),
            weight=float(os.getenv("ONE_TIME_TTN_WEIGHT", "1")),
            description=os.getenv("ONE_TIME_TTN_DESCRIPTION", "Одяг та взуття"),
            cost=float(os.getenv("ONE_TIME_TTN_COST", "200")),
            cod_amount=float(os.getenv("ONE_TIME_TTN_COD", "0")),
        )
        _one_time_ttn_result = result
        logger.info("ONE_TIME_TTN_CREATED number=%s", result.get("ttn"))
        return jsonify(ok=True, result=result)
    except Exception as exc:
        logger.exception("ONE_TIME_TTN_FAILED")
        return jsonify(ok=False, error=str(exc)), 500


@app.post("/webhook")
def webhook():
    if not bot:
        return jsonify(error="Telegram bot token is not configured"), 503
    if not WEBHOOK_SECRET:
        return jsonify(error="WEBHOOK_SECRET is not configured"), 503

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        return jsonify(error="forbidden"), 403

    try:
        payload = request.get_json(force=True, silent=False)
        update = telebot.types.Update.de_json(payload)
        if update is None:
            return jsonify(error="invalid update"), 400
        if not _remember_update(update.update_id):
            return jsonify(ok=True, duplicate=True)
        bot.process_new_updates([update])
        return jsonify(ok=True)
    except Exception:
        logger.exception("Webhook processing failed")
        return jsonify(error="webhook processing failed"), 500


def ensure_webhook():
    if not bot or not WEBHOOK_SECRET or not PUBLIC_BASE_URL:
        return
    url = f"{PUBLIC_BASE_URL}/webhook"
    try:
        current = bot.get_webhook_info()
        if current.url != url:
            bot.set_webhook(
                url=url,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=False,
                allowed_updates=["message"],
            )
            logger.info("Telegram webhook set to %s", url)
        else:
            logger.info("Telegram webhook already configured")
    except Exception:
        logger.exception("Could not configure Telegram webhook automatically")


ensure_webhook()

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
