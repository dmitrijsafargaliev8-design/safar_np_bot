"""Inspect only senders visible to an existing Nova Poshta API credential.

This module cannot create invoices, register contacts, or modify counterparty
records. It deliberately never includes API credentials or counterparty Ref IDs
in chat output.
"""
import re

from np_client import NovaPoshtaError


def _safe_label(row):
    raw = row.get("Description") or row.get("DescriptionRu") or row.get("FirstName")
    if not isinstance(raw, str):
        return "Название не указано"
    text = re.sub(r"[\x00-\x1f\u200e\u200f]", " ", raw)
    return text.strip()[:72] or "Название не указано"


def read_only_sender_access(client, *, max_pages=3, max_display=12):
    """Return a display report scoped to the supplied existing key.

    List 'Sender' counterparties only; do not infer the legal owner from
    contact persons or pretend a visible name grants permission to ship.
    """
    unique = {}
    for page in range(1, max_pages + 1):
        rows = client._call(
            "Counterparty", "getCounterparties",
            {"CounterpartyProperty": "Sender", "Page": str(page)},
        )
        if not isinstance(rows, list):
            raise NovaPoshtaError("НП вернула некорректный список отправителей.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            ref = row.get("Ref")
            if not isinstance(ref, str) or not ref.strip():
                continue
            unique.setdefault(ref, _safe_label(row))
        if not rows:
            break
        # Avoid an unnecessary extra request for a single-sender account.
        if len(rows) < 20:
            break

    if not unique:
        return (
            "🔍 ДОСТУП ПО ТЕКУЩЕМУ КЛЮЧУ\n"
            "Новая Почта не вернула доступных отправителей.\n"
            "Это не подтверждает доступ к отдельному ФОП.\n"
            "ТТН не создавалась."
        )
    names = list(unique.values())
    lines = [
        "🔍 ДОСТУП ПО ТЕКУЩЕМУ КЛЮЧУ",
        "НП вернула отправителей:",
    ]
    lines.extend(f"• {label}" for label in names[:max_display])
    if len(names) > max_display:
        lines.append(f"…ещё {len(names) - max_display}")
    lines.extend((
        "Это не проверка юридического владельца кабинета.",
        "Если её ФОП здесь нет, подключить именно её отдельный кабинет"
        " через этот ключ не подтверждено.",
        "ТТН не создавалась. API-ключ нигде не показывается.",
    ))
    return "\n".join(lines)
