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
        self.labels = {"default": "Основной отправитель"}
        env = os.environ if environ is None else environ
        source = env.get("NP_SENDER_PROFILES_JSON", "") if raw is None else raw
        if not source:
            return
        try:
            profiles = json.loads(source)
        except (TypeError, ValueError) as exc:
            raise NovaPoshtaError("NP_SENDER_PROFILES_JSON содержит некорректный JSON.") from exc
        if not isinstance(profiles, dict) or len(profiles) > 10:
            raise NovaPoshtaError("Список профилей отправителя должен быть объектом (до 10 профилей).")
        required = ("sender_ref", "contact_ref", "address_ref", "city_ref", "phone")
        for profile_id, cfg in profiles.items():
            if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", profile_id) or profile_id == "default":
                raise NovaPoshtaError("Недопустимый идентификатор профиля отправителя.")
            if not isinstance(cfg, dict) or any(not cfg.get(k) for k in required):
                raise NovaPoshtaError(f"Профиль {profile_id}: заполните все реквизиты отправителя.")
            for name in required[:-1]:
                try:
                    uuid.UUID(str(cfg[name]))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise NovaPoshtaError(f"Профиль {profile_id}: недопустимый {name}.") from exc
            api_var = cfg.get("api_key_env", "")
            if api_var and (not isinstance(api_var, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", api_var)):
                raise NovaPoshtaError(f"Профиль {profile_id}: некорректное имя переменной API.")
            api_key = env.get(api_var, "") if api_var else default_client.api_key
            if not api_key:
                raise NovaPoshtaError(f"Профиль {profile_id}: указанный API-ключ не настроен.")
            client = NovaPoshtaClient(api_key)
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

    def get(self, profile):
        if profile not in self.clients:
            raise NovaPoshtaError("Профиль отправителя больше не настроен. Создание ТТН заблокировано.")
        return self.clients[profile]

    def available(self):
        return dict(self.labels)
