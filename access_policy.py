"""Telegram allowlists for shipment creation, inspection and callbacks.

STRICT_ACCESS_POLICY=1 requires BOTH chat and user allowlists, preventing
an accidental open bot when production environment variables are missing.
"""
import re


def _ids(csv, *, negative=False):
    values = set()
    for raw in (csv or "").split(","):
        token = raw.strip()
        if not token:
            continue
        if not re.fullmatch(r"-?\d+", token):
            raise ValueError("Invalid Telegram allowlist ID")
        value = int(token)
        if value == 0 or (not negative and value < 0):
            raise ValueError("Invalid Telegram allowlist ID")
        values.add(value)
    return frozenset(values)


class AccessPolicy:
    def __init__(self, chats="", users="", *, strict=False):
        self.chats = _ids(chats, negative=True)
        self.users = _ids(users)
        self.strict = bool(strict)

    def permits(self, chat_id, user_id):
        if self.strict and (not self.chats or not self.users):
            return False
        try:
            chat = int(chat_id)
            user = int(user_id)
        except (TypeError, ValueError):
            return False
        if not chat or user <= 0:
            return False
        if self.chats and chat not in self.chats:
            return False
        if self.users and user not in self.users:
            return False
        return True

    def status(self):
        # Safe for health checks; never disclose Telegram IDs.
        return dict(strict=self.strict, chats_configured=bool(self.chats),
                    users_configured=bool(self.users))
