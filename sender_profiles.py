"""Named, server-configured sender accounts. Never store API secrets in orders."""
import json
import os
import re
import uuid

from np_client import NovaPoshtaClient, NovaPoshtaError, normalize_phone


class SenderProfiles:
    """A sender is an authorized NP account plus a complete verified reference set."""

    def __init__(self, default_client, raw=None, environ=None):
        self.clients = {"default": default_client}
        self.labels = {"default": "Прежний кабинет НП"}
        env = os.environ if environ is None else environ
        source = env.get("NP_SENDER_PROFILES_JSON", "") if raw is None else raw
        if source:
            try:
                profiles = json.loads(source)
            except (TypeError, ValueError) as exc:
                raise NovaPoshtaError("NP_SENDER_PROFILES_JSON содержит некорректный JSON.") from exc
        else:
            profiles = {}
        if not isinstance(profiles, dict) or len(profiles) > 10:
            raise NovaPoshtaError("Список профилей отправителя должен быть объектом (до 10 профилей).")

        # First-class FOP account requires only her OWN API key in a Render
        # secret. Discover a uniquely registered sender/contact/address via NP.
        # In particular, NEVER inherit NP_SENDER_* of the legacy default client.
        if env.get("NP_FOP_API_KEY", "").strip():
            profiles = dict(profiles)
            if "fop" in profiles:
                cfg = profiles["fop"]
                if not isinstance(cfg, dict) or cfg.get("api_key_env", "NP_FOP_API_KEY") != "NP_FOP_API_KEY":
                    raise NovaPoshtaError("Профиль fop должен использовать только NP_FOP_API_KEY.")
                profiles["fop"] = {**cfg, "api_key_env": "NP_FOP_API_KEY"}
            else:
                profiles["fop"] = {"label": "ФОП · основной кабинет", "api_key_env": "NP_FOP_API_KEY"}

        required = ("sender_ref", "contact_ref", "address_ref", "city_ref", "phone")
        for profile_id, cfg in profiles.items():
            if (not isinstance(profile_id, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", profile_id)
                    or profile_id == "default"):
                raise NovaPoshtaError("Недопустимый идентификатор профиля отправителя.")
            if not isinstance(cfg, dict):
                raise NovaPoshtaError(f"Профиль {profile_id}: необходим объект настроек.")
            provided = [bool(cfg.get(k)) for k in required]
            if any(provided) and not all(provided):
                raise NovaPoshtaError(
                    f"Профиль {profile_id}: все пять реквизитов должны быть заполнены вместе."
                )
            if all(provided):
                for name in required[:-1]:
                    try:
                        uuid.UUID(str(cfg[name]))
                    except (ValueError, TypeError, AttributeError) as exc:
                        raise NovaPoshtaError(f"Профиль {profile_id}: недопустимый {name}.") from exc
            api_var = cfg.get("api_key_env", "")
            if api_var and (
                not isinstance(api_var, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", api_var)
            ):
                raise NovaPoshtaError(f"Профиль {profile_id}: некорректное имя переменной API.")
            # Key-only discovery is safe solely for an explicitly designated
            # account. A bare alias must not silently inherit the legacy key.
            if not any(provided) and not api_var:
                raise NovaPoshtaError(
                    f"Профиль {profile_id}: без явных Ref нужен собственный api_key_env."
                )
            api_key = env.get(api_var, "").strip() if api_var else default_client.api_key
            if not api_key:
                raise NovaPoshtaError(f"Профиль {profile_id}: указанный API-ключ не настроен.")
            client = NovaPoshtaClient(api_key)
            # NovaPoshtaClient reads legacy NP_SENDER_* at construction.
            # Clear them before any alternate sender is discoverable/usable.
            client.sender_ref = ""
            client.contact_ref = ""
            client.address_ref = ""
            client.sender_city_ref = ""
            client.sender_phone = ""
            if all(provided):
                client.sender_ref = str(cfg["sender_ref"])
                client.contact_ref = str(cfg["contact_ref"])
                client.address_ref = str(cfg["address_ref"])
                client.sender_city_ref = str(cfg["city_ref"])
                client.sender_phone = normalize_phone(str(cfg["phone"]))
            label = str(cfg.get("label") or profile_id).strip()
            if not label or len(label) > 60:
                raise NovaPoshtaError("Неверное имя профиля отправителя.")
            self.clients[profile_id] = client
            self.labels[profile_id] = label

        self.primary_profile = env.get("NP_PRIMARY_SENDER_PROFILE", "default").strip().lower() or "default"
        if self.primary_profile not in self.clients:
            raise NovaPoshtaError(
                "Основной профиль отправителя не настроен. "
                "Добавь его API-ключ в Render до переключения."
            )

    def get(self, profile):
        if profile not in self.clients:
            raise NovaPoshtaError("Профиль отправителя больше не настроен. Создание ТТН заблокировано.")
        return self.clients[profile]

    def available(self):
        return dict(self.labels)
