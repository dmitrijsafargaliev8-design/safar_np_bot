"""Loopback-only browser fixture using the REAL Flask blueprint and journal.

Never imports bot.py, starts a worker, loads .env, or calls a remote service.
Every record and image is invented. No token/database URL comes from the host.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import struct
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests
from flask import Flask, jsonify, request
from order_pipeline import OrderPipeline
from safar_auth import AuthService
import safar_app

USER = 100
TOKEN = "123456:local-browser-fixture-only"
SECRET = "local-browser-fixture-secret-only"


def png():
    """Draw a fictional burgundy product package; stdlib only, no media download."""
    width, height = 280, 280
    def chunk(kind, data):
        return (struct.pack("!I", len(data)) + kind + data
                + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            if 65 < x < 215 and 65 < y < 220:
                rgb = (132, 63, 79) if x < 145 else (95, 42, 59)
                if 130 < x < 150:
                    rgb = (184, 153, 124)
            else:
                rgb = (27 + y // 35, 30 + y // 45, 35 + y // 45)
            pixels.extend(rgb)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class LocalCarrier:
    api_key = "local-fixture-no-carrier-key"
    sender_ref = "fixture-sender"
    contact_ref = "fixture-contact"
    address_ref = "fixture-address"
    sender_city_ref = "fixture-city"
    sender_phone = "380000000000"

    def create_ttn(self, *_args, **_kwargs):
        raise AssertionError("Carrier creation is forbidden in browser tests")

    def get_sender_info(self):
        return {"sender_ref": self.sender_ref, "contact_ref": self.contact_ref,
                "address_ref": self.address_ref, "city_ref": self.sender_city_ref,
                "phone": self.sender_phone}

    def track_ttn(self, number, **_kwargs):
        return {"number": number, "status_code": "1", "status": "ТТН створено",
                "deleted": False, "delivered": False}

    get_tracking = track_ttn
    tracking = track_ttn

    def get_ttn_status(self, number, **_kwargs):
        return {"Number": number, "StatusCode": "1", "Status": "ТТН створено"}

    def is_ttn_deleted(self, *_args, **_kwargs):
        return False


class LocalTelegram:
    def get_file(self, _file_id, **_kwargs):
        return SimpleNamespace(file_path="photos/local-fixture.png")

    def __getattr__(self, name):
        def blocked(*_args, **_kwargs):
            raise AssertionError(f"Telegram mutation {name} is forbidden in browser tests")
        return blocked


class LocalMedia:
    status_code = 200
    headers = {"Content-Type": "image/png"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, **_kwargs):
        yield png()


def denied_network(*_args, **_kwargs):
    raise AssertionError("Remote network is disabled in the browser fixture")


requests.sessions.Session.request = denied_network
safar_app.requests.get = lambda *_args, **_kwargs: LocalMedia()

carrier = LocalCarrier()
telegram = LocalTelegram()
fixture_orders = {}


def parse_fixture(text):
    match = re.search(r"запис (\d{2})", text)
    if not match or int(match[1]) not in fixture_orders:
        raise ValueError("Only synthetic fixture captions can be parsed")
    return dict(fixture_orders[int(match[1])])


# The pipeline is never started or ticked: operations exercise the journal only.
pipeline = OrderPipeline(":memory:", parse_fixture, carrier, telegram)
states = ["created", "invalid", "collecting", "failed", "uncertain", "created"]
now = time.time()
with pipeline.lock, pipeline.db:
    for index in range(1, 46):
        state = states[(index - 1) % len(states)]
        order = {"full_name": f"Тестовий Одержувач {index:02d}",
                 "city": "Київ" if index % 2 else "Одеса", "area": "Київська",
                 "warehouse": str(index), "phone": "380000000001", "cost": 1200 + index * 25,
                 "cod_amount": 0 if index % 4 else 500, "weight": 1.2,
                 "description": "Синтетичний товар · browser fixture"}
        fixture_orders[index] = dict(order)
        key = f"fixture-{index:02d}"
        images = [{"file_id": f"local-fixture-photo-{index}-{part}",
                   "file_unique_id": f"fixture-unique-{index}-{part}",
                   "width": 280, "height": 280, "file_size": len(png())}
                  for part in range(2)]
        source = {"message_id": index, "date": int(now - index * 60),
                  "chat": {"id": USER, "type": "private"}, "from": {"id": USER},
                  "caption": f"Синтетичний тестовий запис {index:02d}", "photo": images[:1]}
        second = {**source, "message_id": index + 1000, "photo": images[1:]}
        job = {"key": key, "chat_id": USER, "owner_id": USER, "thread_id": 0,
               "anchor_id": index, "messages": [source, second], "state": state,
               "due": now + 3600, "created": now - index * 60, "created_at": now - index * 60,
               "attempts": 0, "notified": True, "sender_profile": "default", "order": order,
               "result": {"ttn": f"2040000000{index:04d}"} if state == "created" else None,
               "identity": None, "error": "Перевірте адресу" if state == "invalid" else ""}
        pipeline._write(job)
        pipeline.db.execute("UPDATE jobs SET updated=? WHERE key=?", (now - index * 60, key))
    # Deliberately foreign record proves that API pages cannot leak another owner.
    foreign = {**job, "key": "foreign-secret", "owner_id": 999, "chat_id": 999,
               "order": {**order, "full_name": "FOREIGN OWNER MUST NEVER APPEAR"}}
    pipeline._write(foreign)

app = Flask(__name__)
app.register_blueprint(safar_app.create_safar_blueprint(
    telegram_token=TOKEN, webhook_secret=SECRET,
    allowed=lambda chat, user: chat == user == USER,
    get_pipeline=lambda: pipeline, telegram_bot=telegram,
    admin_check=lambda user_id: user_id == USER,
))


@app.get("/__test__/health")
def health():
    return jsonify(ok=True, synthetic=True, carrier_writes=False)


@app.get("/__test__/init-data")
def init_data():
    fields = {"auth_date": str(int(time.time())), "query_id": f"local-fixture-{time.time_ns()}",
              "user": json.dumps({"id": USER, "first_name": "Synthetic tester"}, separators=(",", ":"))}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return jsonify(initData=urlencode(fields))


@app.post("/__test__/approve-pairing")
def approve_pairing():
    """Exercise signed-webhook approval logic without sending Telegram messages."""
    auth = AuthService(telegram_token=TOKEN, webhook_secret=SECRET,
                       allowed=lambda chat, user: chat == user == USER,
                       get_pipeline=lambda: pipeline)
    body = request.get_json()
    return jsonify(approved=auth.approve_pairing(body["code"], USER, USER))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)
