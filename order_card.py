"""Telegram-native shipment card: media cover, concise details and 2x2 actions."""
from telebot import types


def build_card_keyboard(ttn: str):
    """Native clipboard button requires Telegram Bot API 7.11+."""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("🚚 Статус доставки", callback_data="safar:track"),
        types.InlineKeyboardButton("✏️ Исправить заказ", callback_data="safar:edit"),
    )
    markup.row(
        types.InlineKeyboardButton("📋 Копировать ТТН", copy_text=types.CopyTextButton(text=str(ttn))),
        types.InlineKeyboardButton("🕘 История заказа", callback_data="safar:history"),
    )
    return markup


def format_order_card(order: dict, result: dict, photo_count: int, *,
                      duplicate=False, replaced_ttn=None, notice=None) -> str:
    """Keep the most important fields in the first Telegram photo caption."""
    if order.get("warehouse"):
        label = "Поштомат" if order.get("delivery_point_type") == "postomat" else "Отделение"
        destination = f"{order['city']} · {label} №{order['warehouse']}"
    else:
        address = f"{order.get('street', '')} {order.get('house', '')}".strip()
        if order.get("flat"):
            address += f", кв. {order['flat']}"
        destination = f"{order.get('city', '')} · {address}"
    count = f"🖼 Фото товара · {photo_count} шт.\n" if photo_count else ""
    text = (
        "📦 SAFAR NP BOT\n"
        "✅ ЗАКАЗ ОБРАБОТАН\n"
        f"{count}\n"
        f"🔖 ТТН: {result['ttn']}\n"
        f"👤 Получатель: {order['full_name']}\n"
        f"📍 {destination}\n"
        f"📞 {order['phone']}\n"
        f"💰 Оценка: {order['cost']:g} грн\n"
        + (f"💵 Наложка: {order['cod_amount']:g} грн" if order.get("cod_amount") else "💳 Без наложки")
    )
    if result.get("estimated_delivery_date"):
        text += f"\n🚚 Доставка: {result['estimated_delivery_date']}"
    if duplicate:
        text += "\n♻️ Заказ уже обработан — повторная ТТН не создавалась."
    if replaced_ttn:
        text += f"\n♻️ Предыдущая ТТН {replaced_ttn} удалена в НП. Создана новая."
    if notice:
        text += "\n⚠️ " + str(notice)
    return text
