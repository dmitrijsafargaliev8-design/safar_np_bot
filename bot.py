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


def parse_order(text: str):
    data = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = _canonical_key(key)
        value = value.strip()
        if value:
            data[key] = value

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
        cleaned = re.sub(r"[^\d,.\-]", "", raw).replace(",", ".")
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
    "Отправь заказ одним сообщением в формате:\n\n"
    "ФИО: Иван Иванов\n"
    "Телефон: 380931234567\n"
    "Город: Одесса\n"
    "Отделение: 5\n"
    "Вес: 1\n"
    "Описание: обувь\n"
    "Стоимость: 2000\n"
    "Наложка: 2000\n\n"
    "Обязательные поля: ФИО, Телефон, Город, Отделение.\n"
    "Если наложки нет — строку «Наложка» можно не писать."
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

    @bot.message_handler(content_types=["text"])
    def text_handler(message):
        if not _is_allowed(message):
            return
        if (message.text or "").startswith("/"):
            return
        if not np_client:
            bot.reply_to(message, "Ошибка конфигурации: NOVA_POSHTA_API_KEY не задан.")
            return

        try:
            order = parse_order(message.text)
            result = np_client.create_ttn(**order)
            reply = f"✅ ТТН создана: {result['ttn']}"
            if result.get("estimated_delivery_date"):
                reply += f"\nОриентировочная доставка: {result['estimated_delivery_date']}"
            bot.reply_to(message, reply)
        except (ValueError, NovaPoshtaError) as exc:
            bot.reply_to(message, f"❌ {exc}\n\n{HELP_TEXT}")
        except Exception:
            logger.exception("Unhandled order error")
            bot.reply_to(message, "❌ Внутренняя ошибка. Детали записаны в лог сервиса.")


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

    overall = telegram_ok and nova_poshta_ok and sender_ok
    return jsonify(
        status="ready" if overall else "not_ready",
        telegram_ok=telegram_ok,
        nova_poshta_ok=nova_poshta_ok,
        sender_ok=sender_ok,
        errors=errors,
    ), (200 if overall else 503)


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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
