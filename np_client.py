import os
import re
import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)


class NovaPoshtaError(Exception):
    pass


class NovaPoshtaTemporaryError(NovaPoshtaError):
    """A read or pre-shipment operation can safely be retried."""


class NovaPoshtaUncertainError(NovaPoshtaError):
    """Shipment save may have succeeded; never automatically repeat it."""


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    elif digits.startswith("80") and len(digits) == 11:
        digits = "3" + digits
    elif len(digits) == 9:
        digits = "380" + digits
    if not re.fullmatch(r"380\d{9}", digits):
        raise NovaPoshtaError("Телефон должен быть украинским номером в формате 380XXXXXXXXX.")
    return digits


def _norm_text(value: str) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value


def _warehouse_city_queries(value: str):
    """Possible exact city prefixes of a location ending in a street and house.

    These are directory queries, never a guessed city. This interpretation is
    used only when an explicit warehouse number determines the delivery route.
    """
    location = re.sub(r"\s+", " ", re.sub(r"[,;]", " ", value or "")).strip(" .,-")
    address = re.fullmatch(
        r"(.+?)\s+\d{1,5}(?:[A-Za-zА-Яа-яІіЇїЄєҐґ]|[/-][0-9A-Za-zА-Яа-яІіЇїЄєҐґ]+)?",
        location,
    )
    if not address:
        return []
    body = address.group(1).strip()
    street = re.search(
        r"(?i)\s+(?:вул(?:иця)?|улица|ул|просп(?:ект)?|пров(?:улок)?|переулок|шосе)\.?\s+",
        body,
    )
    if street:
        return [body[:street.start()].strip(" .,-")]
    words = body.split()
    # A house alone is not enough to shorten a city. At least one word must
    # remain as the street, and long unclear text requires a correction.
    if not 2 <= len(words) <= 7:
        return []
    return [" ".join(words[:end]) for end in range(len(words) - 1, 0, -1)]


def _normalize_city_input(value: str, *, warehouse_address: bool = False):
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return "", ""

    # Keep the original order text, but search the directory by the city alone.
    # Both "Киевская обл, Васильков" and "Васильков, Киевская обл" occur.
    parts = [part.strip(" .,-") for part in re.split(r"[,;]", raw) if part.strip(" .,-")]
    if len(parts) == 1:
        leading_area = re.match(r"(?i)^(.+?\s+(?:область|обл\.?))\s+(.+)$", raw)
        if leading_area:
            parts = [leading_area.group(1), leading_area.group(2)]
    areas = [part for part in parts if re.search(r"(?i)\s+(?:область|обл)$", part.rstrip(" ."))]
    cities = [part for part in parts if part not in areas and not re.search(r"(?i)\s+(?:район|р-н)$", part.rstrip(" ."))]
    combined_address = " ".join(cities)
    has_branch_address = warehouse_address and len(cities) > 1 and bool(_warehouse_city_queries(combined_address))
    if len(areas) > 1 or (areas and len(cities) != 1 and not has_branch_address):
        raise NovaPoshtaError("Уточни один город и одну область для этого заказа.")
    if has_branch_address:
        city, region_hint = combined_address, areas[0] if areas else ""
    elif areas:
        city, region_hint = cities[0], areas[0]
    else:
        city = parts[0] if parts else ""
        region_hint = parts[1] if len(parts) == 2 else ""
        if len(parts) > 2:
            raise NovaPoshtaError("Уточни один город и одну область для этого заказа.")

    city = re.sub(
        r"(?i)^(?:м(?:істо)?|г(?:ород)?|с(?:ело)?|смт|пгт|селище|пос(?:елок)?)\.?\s+",
        "",
        city,
    ).strip(" .,-")

    # Если написали "м.Сміла" без пробела.
    city = re.sub(
        r"(?i)^(?:м|г|с|смт|пгт)\.\s*",
        "",
        city,
    ).strip(" .,-")

    return city or raw, region_hint


def _normalize_region_hint(value: str) -> str:
    hint = _norm_text(value)
    hint = re.sub(
        r"\b(?:обл(?:асть|асті)?|район|р-н)\.?\b.*$",
        "",
        hint,
    ).strip(" .,-")
    return hint


def _split_name(full_name: str):
    parts = [p for p in re.split(r"\s+", (full_name or "").strip()) if p]
    if len(parts) < 2:
        raise NovaPoshtaError("Укажите минимум фамилию и имя получателя.")
    last_name = parts[0]
    first_name = parts[1]
    middle_name = " ".join(parts[2:]) if len(parts) > 2 else ""
    return first_name, middle_name, last_name


class NovaPoshtaClient:
    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        self.base_url = "https://api.novaposhta.ua/v2.0/json/"
        self.timeout = 10

        self.sender_ref = os.getenv("NP_SENDER_REF", "").strip()
        self.contact_ref = os.getenv("NP_SENDER_CONTACT_REF", "").strip()
        self.address_ref = os.getenv("NP_SENDER_ADDRESS_REF", "").strip()
        self.sender_city_ref = os.getenv("NP_SENDER_CITY_REF", "").strip()
        self.sender_phone = os.getenv("NP_SENDER_PHONE", "").strip()
        self._area_rows = None

        if not self.api_key:
            raise NovaPoshtaError("NOVA_POSHTA_API_KEY не задан.")

    def _call(self, model_name: str, called_method: str, properties=None):
        payload = {
            "apiKey": self.api_key,
            "modelName": model_name,
            "calledMethod": called_method,
            "methodProperties": properties or {},
        }
        try:
            response = requests.post(self.base_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (RequestException, ValueError) as exc:
            logger.warning("Nova Poshta transport failure: model=%s method=%s type=%s", model_name, called_method, type(exc).__name__)
            if model_name == "InternetDocument" and called_method == "save":
                raise NovaPoshtaUncertainError(
                    "Новая Почта не подтвердила результат создания. Проверь накладные в её приложении; повторное создание заблокировано."
                ) from exc
            raise NovaPoshtaTemporaryError("Новая Почта временно не отвечает. Заказ сохранён.") from exc

        if not isinstance(body, dict) or type(body.get("success")) is not bool or not isinstance(body.get("data", []), list):
            if model_name == "InternetDocument" and called_method == "save":
                raise NovaPoshtaUncertainError("Новая Почта вернула неполный ответ. Проверь созданные накладные.")
            raise NovaPoshtaTemporaryError("Некорректный ответ Новой Почты.")

        if not body.get("success"):
            details = (
                body.get("errors")
                or body.get("errorCodes")
                or body.get("warnings")
                or ["Неизвестная ошибка Nova Poshta API"]
            )
            if not isinstance(details, list):
                details = [str(details)]
            raise NovaPoshtaError("; ".join(str(x) for x in details))

        return body.get("data") or []

    def ping(self):
        rows = self._call("Address", "getCities", {"FindByString": "Одеса", "Limit": "1", "Page": "1"})
        return bool(rows)

    def _area_info(self, name):
        if self._area_rows is None:
            rows = self._call("Address", "getAreas", {})
            if not rows:
                raise NovaPoshtaTemporaryError("Новая Почта не вернула справочник областей. Заказ сохранён.")
            self._area_rows = rows
        wanted = _normalize_region_hint(name)
        matches = [row for row in self._area_rows if wanted in {
            _normalize_region_hint(row.get("Description")),
            _normalize_region_hint(row.get("DescriptionRu")),
        } and row.get("Ref")]
        if len({row["Ref"] for row in matches}) != 1:
            raise NovaPoshtaError(f"Область не найдена однозначно: {name}")
        return matches[0]

    def get_city_ref(self, city_name: str, *, area: str = "", warehouse: str = "") -> str:
        raw_query = (city_name or "").strip()
        if not raw_query:
            raise NovaPoshtaError("Не указан город.")

        query, region_hint = _normalize_city_input(raw_query, warehouse_address=bool(warehouse))
        search_queries = [query] + (_warehouse_city_queries(query) if warehouse else [])
        exact = []
        for search_query in dict.fromkeys(search_queries):
            rows = self._call(
                "Address", "getCities",
                {"FindByString": search_query, "Limit": "100", "Page": "1"},
            )
            wanted = _norm_text(search_query)
            for row in rows:
                candidates = {
                    _norm_text(re.sub(r"\s*\([^)]*\)\s*$", "", row.get("Description") or "")),
                    _norm_text(re.sub(r"\s*\([^)]*\)\s*$", "", row.get("DescriptionRu") or "")),
                }
                if wanted in candidates:
                    exact.append(row)
            if exact:
                query = search_query
                break

        if not rows:
            raise NovaPoshtaError(f"Город не найден: {raw_query}")

        if not exact:
            raise NovaPoshtaError(f"Город не найден точно: {raw_query}. Уточни название.")
        matches = exact
        area_hint = area or region_hint
        if area_hint:
            selected_area = self._area_info(area_hint)
            if area and region_hint and self._area_info(region_hint)["Ref"] != selected_area["Ref"]:
                raise NovaPoshtaError("В заказе указаны разные области. Уточни область получателя.")
            area_names = {_normalize_region_hint(selected_area.get(key)) for key in ("Description", "DescriptionRu")}
            area_names.discard("")
            matches = [row for row in matches if row.get("Area") == selected_area["Ref"] or
                       (not row.get("Area") and any(_normalize_region_hint(row.get(key)) in area_names
                         for key in ("AreaDescription", "AreaDescriptionRu")))]
            if not matches:
                raise NovaPoshtaError(f"Город {query} не найден в области {area_hint}. Проверь область.")

        refs = {r.get("Ref") for r in matches if r.get("Ref")}
        if len(refs) != 1:
            variants = []
            for row in matches[:5]:
                city_label = row.get("Description") or row.get("DescriptionRu") or ""
                area_label = row.get("AreaDescription") or row.get("AreaDescriptionRu") or ""
                label = city_label
                if area_label:
                    label += f" ({area_label})"
                if label:
                    variants.append(label)

            hint = ", ".join(variants)
            raise NovaPoshtaError(
                f"Город найден неоднозначно: {raw_query}. "
                + (f"Уточните: {hint}" if hint else "Уточните название.")
            )

        if query != raw_query or area_hint:
            logger.info(
                "CITY_NORMALIZED raw=%r query=%r region_hint=%r",
                raw_query,
                query,
                region_hint,
            )
        return next(iter(refs))

    def get_settlement_ref(
        self,
        city_name: str,
        *,
        area: str = "",
        region: str = "",
        settlement_type: str = "",
    ) -> str:
        raw_city = (city_name or "").strip()
        if not raw_city:
            raise NovaPoshtaError("Не указан населённый пункт.")

        query, inline_area = _normalize_city_input(raw_city)
        area = area or inline_area
        rows = self._call(
            "Address",
            "searchSettlements",
            {
                "CityName": query,
                "Limit": "150",
                "Page": "1",
            },
        )

        addresses = []
        for row in rows:
            if isinstance(row, dict):
                nested = row.get("Addresses")
                if isinstance(nested, list):
                    addresses.extend(x for x in nested if isinstance(x, dict))
                elif row.get("Ref"):
                    addresses.append(row)

        if not addresses:
            raise NovaPoshtaError(f"Населённый пункт не найден: {raw_city}")

        wanted_city = _norm_text(query)
        wanted_area = _norm_text(area)
        wanted_region = _norm_text(region)
        wanted_type = _norm_text(settlement_type)

        def _clean_geo(value: str) -> str:
            v = _norm_text(value)
            v = re.sub(
                r"\b(?:область|обл\.?|район|р-н\.?)\b",
                "",
                v,
            )
            return re.sub(r"\s+", " ", v).strip(" .,-")

        wanted_area = _clean_geo(wanted_area)
        wanted_region = _clean_geo(wanted_region)

        # Сначала оставляем точное совпадение названия населённого пункта.
        exact_city = [
            row
            for row in addresses
            if _norm_text(row.get("MainDescription")) == wanted_city
        ]
        matches = exact_city or addresses

        if wanted_area:
            selected_area = self._area_info(area)
            if inline_area and self._area_info(inline_area)["Ref"] != selected_area["Ref"]:
                raise NovaPoshtaError("В заказе указаны разные области. Уточни область получателя.")
            area_names = {_clean_geo(selected_area.get(key)) for key in ("Description", "DescriptionRu")}
            area_names.discard("")
            regional = [
                row
                for row in matches
                if _clean_geo(row.get("Area")) in area_names
            ]
            if not regional:
                raise NovaPoshtaError(f"Населённый пункт {query} не найден в области {area}.")
            matches = regional

        if wanted_region:
            district = [
                row
                for row in matches
                if _clean_geo(row.get("Region")) == wanted_region
            ]
            if district:
                matches = district

        if wanted_type:
            type_aliases = {
                "село": {"с.", "с"},
                "місто": {"м.", "м"},
                "селище міського типу": {"смт", "сmt", "пгт"},
            }
            allowed_codes = type_aliases.get(wanted_type, set())
            if allowed_codes:
                typed = [
                    row
                    for row in matches
                    if _norm_text(row.get("SettlementTypeCode")) in allowed_codes
                ]
                if typed:
                    matches = typed

        # Для доставки до двери предпочитаем населённые пункты,
        # где справочник прямо разрешает AddressDelivery.
        delivery_allowed = [
            row
            for row in matches
            if row.get("AddressDeliveryAllowed") is True
            or str(row.get("AddressDeliveryAllowed")).lower() == "true"
        ]
        if delivery_allowed:
            matches = delivery_allowed

        refs = {row.get("Ref") for row in matches if row.get("Ref")}
        if len(refs) != 1:
            variants = []
            for row in matches[:8]:
                label = row.get("Present") or row.get("MainDescription") or ""
                if label:
                    variants.append(label)
            hint = ", ".join(variants)
            raise NovaPoshtaError(
                f"Населённый пункт найден неоднозначно: {raw_city}. "
                + (f"Варианты: {hint}" if hint else "Уточните область/район.")
            )

        selected_ref = next(iter(refs))
        selected = next(
            (row for row in matches if row.get("Ref") == selected_ref),
            {},
        )
        logger.info(
            "SETTLEMENT_RESOLVED city=%r area=%r region=%r ref=%s present=%r",
            raw_city,
            area,
            region,
            selected_ref,
            selected.get("Present"),
        )
        return selected_ref


    def get_warehouse_ref(self, city_ref: str, warehouse_value: str) -> str:
        raw = (warehouse_value or "").strip()
        if not raw:
            raise NovaPoshtaError("Не указано отделение/почтомат.")

        rows = self._call(
            "Address",
            "getWarehouses",
            {
                "CityRef": city_ref,
                "FindByString": raw,
                "Limit": "500",
                "Page": "1",
            },
        )
        if not rows:
            rows = self._call(
                "Address",
                "getWarehouses",
                {"CityRef": city_ref, "Limit": "500", "Page": "1"},
            )
        if not rows:
            raise NovaPoshtaError(f"Отделение не найдено: {raw}")

        number_match = re.search(r"\d+", raw)
        if number_match:
            wanted_number = number_match.group(0).lstrip("0") or "0"
            exact = []
            for row in rows:
                row_number = str(row.get("Number") or "").lstrip("0") or "0"
                if row_number == wanted_number:
                    exact.append(row)
            if len(exact) == 1 and exact[0].get("Ref"):
                return exact[0]["Ref"]
            if len(exact) > 1:
                non_postomat = [
                    r for r in exact
                    if "поштомат" not in _norm_text(r.get("Description"))
                    and "почтомат" not in _norm_text(r.get("DescriptionRu"))
                ]
                if len(non_postomat) == 1 and non_postomat[0].get("Ref"):
                    return non_postomat[0]["Ref"]

        wanted = _norm_text(raw)
        exact_text = []
        for row in rows:
            candidates = [
                _norm_text(row.get("Description")),
                _norm_text(row.get("DescriptionRu")),
                _norm_text(row.get("ShortAddress")),
                _norm_text(row.get("ShortAddressRu")),
            ]
            if any(wanted == c or wanted in c for c in candidates if c):
                exact_text.append(row)

        refs = {r.get("Ref") for r in exact_text if r.get("Ref")}
        if len(refs) == 1:
            return next(iter(refs))

        variants = [r.get("Description") or r.get("DescriptionRu") for r in (exact_text or rows)[:5]]
        variants = [v for v in variants if v]
        raise NovaPoshtaError(
            f"Отделение найдено неоднозначно: {raw}. "
            + (f"Уточните: {', '.join(variants)}" if variants else "Уточните номер/тип отделения.")
        )

    def get_sender_info(self):
        configured = {
            "sender_ref": self.sender_ref,
            "contact_ref": self.contact_ref,
            "address_ref": self.address_ref,
            "city_ref": self.sender_city_ref,
            "phone": normalize_phone(self.sender_phone) if self.sender_phone else "",
        }
        if all(configured.values()):
            return configured

        senders = self._call(
            "Counterparty",
            "getCounterparties",
            {"CounterpartyProperty": "Sender", "Page": "1"},
        )
        senders = [x for x in senders if x.get("Ref")]
        if len(senders) != 1:
            raise NovaPoshtaError(
                "Не удалось однозначно определить отправителя. "
                "Заполните NP_SENDER_REF, NP_SENDER_CONTACT_REF, NP_SENDER_ADDRESS_REF, "
                "NP_SENDER_CITY_REF и NP_SENDER_PHONE."
            )

        sender = senders[0]
        sender_ref = self.sender_ref or sender.get("Ref")

        contacts = self._call(
            "Counterparty",
            "getCounterpartyContactPersons",
            {"Ref": sender_ref, "Page": "1"},
        )
        contacts = [x for x in contacts if x.get("Ref")]
        if self.contact_ref:
            contact_ref = self.contact_ref
        elif len(contacts) == 1:
            contact_ref = contacts[0]["Ref"]
        else:
            raise NovaPoshtaError(
                "Контакт отправителя неоднозначен. Укажите NP_SENDER_CONTACT_REF."
            )

        addresses = self._call(
            "Counterparty",
            "getCounterpartyAddresses",
            {
                "Ref": sender_ref,
                "CounterpartyProperty": "Sender",
                "Page": "1",
            },
        )
        addresses = [x for x in addresses if x.get("Ref")]
        if self.address_ref:
            address_ref = self.address_ref
            address_row = next((x for x in addresses if x.get("Ref") == address_ref), {})
        elif len(addresses) == 1:
            address_ref = addresses[0]["Ref"]
            address_row = addresses[0]
        else:
            raise NovaPoshtaError(
                "Адрес/отделение отправителя неоднозначно. Укажите NP_SENDER_ADDRESS_REF."
            )

        city_ref = (
            self.sender_city_ref
            or address_row.get("CityRef")
            or sender.get("CityRef")
            or sender.get("City")
        )
        if isinstance(city_ref, dict):
            city_ref = city_ref.get("Ref")

        phone = self.sender_phone
        if not phone and contacts:
            phone = contacts[0].get("Phones") or contacts[0].get("Phone") or ""
        phone = normalize_phone(phone) if phone else ""

        if not city_ref or not phone:
            raise NovaPoshtaError(
                "Не удалось определить CityRef/телефон отправителя. "
                "Укажите NP_SENDER_CITY_REF и NP_SENDER_PHONE."
            )

        return {
            "sender_ref": sender_ref,
            "contact_ref": contact_ref,
            "address_ref": address_ref,
            "city_ref": city_ref,
            "phone": phone,
        }

    def get_or_create_recipient(self, full_name: str, phone: str, city_ref: str, email: str = ""):
        first_name, middle_name, last_name = _split_name(full_name)
        phone = normalize_phone(phone)

        rows = self._call(
            "Counterparty",
            "save",
            {
                "FirstName": first_name,
                "MiddleName": middle_name,
                "LastName": last_name,
                "Phone": phone,
                "Email": (email or "").strip(),
                "CounterpartyType": "PrivatePerson",
                "CounterpartyProperty": "Recipient",
                "CityRef": city_ref,
            },
        )
        if not rows:
            raise NovaPoshtaError("Nova Poshta не вернула данные получателя.")

        recipient = rows[0]
        recipient_ref = recipient.get("Ref")
        if not recipient_ref:
            raise NovaPoshtaError("Nova Poshta не вернула Recipient Ref.")

        contact_ref = ""
        contact = recipient.get("ContactPerson")
        if isinstance(contact, dict):
            data = contact.get("data")
            if isinstance(data, list) and data:
                contact_ref = (data[0] or {}).get("Ref", "")
        elif isinstance(contact, list) and contact:
            contact_ref = (contact[0] or {}).get("Ref", "")

        if not contact_ref:
            contacts = self._call(
                "Counterparty",
                "getCounterpartyContactPersons",
                {"Ref": recipient_ref, "Page": "1"},
            )
            contacts = [x for x in contacts if x.get("Ref")]
            if len(contacts) == 1:
                contact_ref = contacts[0]["Ref"]
            elif len(contacts) > 1:
                by_phone = [
                    x for x in contacts
                    if re.sub(r"\D+", "", str(x.get("Phones") or x.get("Phone") or "")) == phone
                ]
                if len(by_phone) == 1:
                    contact_ref = by_phone[0]["Ref"]

        if not contact_ref:
            raise NovaPoshtaError("Nova Poshta не вернула ContactRecipient Ref.")

        return recipient_ref, contact_ref, phone

    def create_ttn(
        self,
        *,
        full_name: str,
        phone: str,
        city: str,
        warehouse: str = "",
        street: str = "",
        house: str = "",
        flat: str = "",
        area: str = "",
        region: str = "",
        settlement_type: str = "",
        weight: float = 1.0,
        description: str = "Одяг та взуття",
        cost: float = 200.0,
        cod_amount: float = 0.0,
        payer_type: str = "Recipient",
        payment_method: str = "Cash",
        email: str = "",
    ):
        sender = self.get_sender_info()
        phone = normalize_phone(phone)

        payer_type = (
            "Sender"
            if _norm_text(payer_type) in {"sender", "отправитель", "відправник"}
            else "Recipient"
        )
        payment_method = (
            "NonCash"
            if _norm_text(payment_method) in {"noncash", "безнал", "безготівковий", "безготівка"}
            else "Cash"
        )

        if not math.isfinite(weight) or weight <= 0:
            raise NovaPoshtaError("Вес должен быть больше 0.")
        if not math.isfinite(cost) or cost <= 0:
            raise NovaPoshtaError("Объявленная стоимость должна быть больше 0.")
        if not math.isfinite(cod_amount) or cod_amount < 0:
            raise NovaPoshtaError("Сумма наложенного платежа не может быть отрицательной.")

        # Отделение имеет приоритет: присланная улица может описывать
        # местоположение отделения, а не адресную доставку получателю.
        address_delivery = bool(
            not (warehouse or "").strip()
            and (street or "").strip()
            and (house or "").strip()
        )

        props = {
            "PayerType": payer_type,
            "PaymentMethod": payment_method,
            "DateTime": datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y"),
            "CargoType": "Cargo",
            "Weight": f"{weight:g}",
            "SeatsAmount": "1",
            "Description": (description or "Одяг та взуття").strip()[:100],
            "Cost": f"{cost:.2f}".rstrip("0").rstrip("."),
            "CitySender": sender["city_ref"],
            "Sender": sender["sender_ref"],
            "SenderAddress": sender["address_ref"],
            "ContactSender": sender["contact_ref"],
            "SendersPhone": sender["phone"],
        }

        if address_delivery:
            # Для сёл и небольших населённых пунктов нельзя надёжно передавать
            # только строку RecipientCityName. Сначала получаем уникальный Ref
            # из Address/searchSettlements и используем RecipientCityRef.
            settlement_ref = self.get_settlement_ref(
                city,
                area=area,
                region=region,
                settlement_type=settlement_type,
            )
            props.update(
                {
                    "ServiceType": "WarehouseDoors",
                    "RecipientCityRef": settlement_ref,
                    "RecipientAddressName": (street or "").strip(),
                    "RecipientHouse": (house or "").strip(),
                    "RecipientFlat": (flat or "").strip(),
                    "RecipientType": "PrivatePerson",
                    "RecipientName": (full_name or "").strip(),
                    "RecipientContactName": (full_name or "").strip(),
                    "RecipientsPhone": phone,
                    "NewAddress": "1",
                }
            )
        else:
            if not (warehouse or "").strip():
                raise NovaPoshtaError("Не указано отделение НП или адрес доставки.")

            city_ref = self.get_city_ref(city, area=area, warehouse=warehouse)
            warehouse_ref = self.get_warehouse_ref(city_ref, warehouse)
            recipient_ref, contact_ref, recipient_phone = self.get_or_create_recipient(
                full_name, phone, city_ref, email=email
            )

            props.update(
                {
                    "ServiceType": "WarehouseWarehouse",
                    "CityRecipient": city_ref,
                    "Recipient": recipient_ref,
                    "RecipientAddress": warehouse_ref,
                    "ContactRecipient": contact_ref,
                    "RecipientsPhone": recipient_phone,
                }
            )

        if cod_amount > 0:
            props["BackwardDeliveryData"] = [
                {
                    "PayerType": "Recipient",
                    "CargoType": "Money",
                    "RedeliveryString": f"{cod_amount:.2f}".rstrip("0").rstrip("."),
                }
            ]

        rows = self._call("InternetDocument", "save", props)
        if not rows:
            raise NovaPoshtaUncertainError("Новая Почта не вернула номер накладной. Проверь созданные ТТН.")

        doc = rows[0]
        ttn = doc.get("IntDocNumber") or doc.get("IntDocNumberNew")
        doc_ref = doc.get("Ref")
        if not ttn:
            raise NovaPoshtaUncertainError("ТТН создана, но номер не получен. Проверь накладные в Новой Почте.")

        return {
            "ttn": str(ttn),
            "ref": doc_ref or "",
            "cost_on_site": doc.get("CostOnSite"),
            "estimated_delivery_date": doc.get("EstimatedDeliveryDate"),
        }

