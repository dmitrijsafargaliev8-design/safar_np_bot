"""Journal incoming Telegram orders, collect albums and send shipment cards.

Postgres keeps the queue, photo identifiers and shipment receipts across host
replacement. SQLite remains available for development or a persistent disk.
"""
import hashlib
import json
import logging
import math
import re
import threading
import time
from datetime import datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo

from telebot import types

from np_client import NovaPoshtaError, NovaPoshtaTemporaryError, NovaPoshtaUncertainError
from order_journal import OrderJournal
from order_edits import parse_field_patch, apply_field_patch, patch_labels

logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OrderCorrectionConflict(ValueError):
    """A stale editor or an in-flight shipment cannot alter this order."""


class OrderPipeline:
    def __init__(self, path, parser, client, bot, *, album_wait=3.0, clock=time.time, database_url=None,
                 sender_clients=None, default_sender_profile="default"):
        self.parser, self.client, self.bot = parser, client, bot
        self.sender_clients = dict(sender_clients or {"default": client})
        self.sender_clients.setdefault("default", client)
        if default_sender_profile not in self.sender_clients:
            raise ValueError("Основной профиль отправителя не настроен.")
        self.default_sender_profile = default_sender_profile
        self.album_wait, self.clock = album_wait, clock
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.db = OrderJournal(path, database_url)
        if self.db.backend == "sqlite":
            # SQLite LOWER only supports ASCII. Match Ukrainian/Russian names
            # consistently without loading the whole journal into the app.
            self.db._connection.create_function(
                "safar_casefold", 1, lambda value: str(value or "").casefold(), deterministic=True,
            )
        self._recovered_epoch = 0

    def _recover(self):
        with self.lock, self.db:
            self.db.execute("UPDATE receipts SET state='uncertain' WHERE state='creating'")
            for row in self.db.execute("SELECT body FROM jobs WHERE state='processing'").fetchall():
                job = json.loads(row["body"])
                job.update(state="collecting", due=self.clock())
                self._write(job)

    def _write(self, job):
        self.db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "chat_id=excluded.chat_id, owner_id=excluded.owner_id, state=excluded.state, "
            "due=excluded.due, updated=excluded.updated, body=excluded.body",
            (job["key"], job["chat_id"], job["owner_id"], job["state"], job["due"], self.clock(), _json(job)),
        )

    def _job(self, key):
        row = self.db.execute("SELECT body FROM jobs WHERE key=?", (key,)).fetchone()
        return json.loads(row["body"]) if row else None

    def has_update(self, update_id):
        with self.lock:
            return self.db.execute("SELECT 1 FROM updates WHERE id=?", (update_id,)).fetchone() is not None

    def remember_update(self, update_id):
        with self.lock, self.db:
            self.db.execute("INSERT INTO updates VALUES (?,?) ON CONFLICT(id) DO NOTHING", (update_id, self.clock()))

    def _sender_client(self, profile_id):
        if profile_id == "default":
            return self.client
        if profile_id not in self.sender_clients:
            raise NovaPoshtaError("Профиль отправителя недоступен; ТТН не создавалась.")
        return self.sender_clients[profile_id]

    def sender_preference(self, chat_id, owner_id):
        with self.lock:
            if self.db.backend == "postgres":
                # Check before querying: an UndefinedTable exception would
                # poison the surrounding journal transaction in PostgreSQL.
                exists = self.db.execute(
                    "SELECT to_regclass('safar_orders.sender_preferences') AS relation"
                ).fetchone()["relation"]
                if not exists:
                    if set(self.sender_clients) == {"default"}:
                        return self.default_sender_profile  # legacy single-sender test database
                    raise RuntimeError("Sender preference migration is required")
            row = self.db.execute(
                "SELECT profile_id FROM sender_preferences WHERE chat_id=? AND owner_id=?",
                (chat_id, owner_id),
            ).fetchone()
        return row["profile_id"] if row else self.default_sender_profile

    def set_sender_preference(self, chat_id, owner_id, profile_id):
        """Persist future selection; never change already ingested orders."""
        self._sender_client(profile_id)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO sender_preferences (chat_id,owner_id,profile_id,updated) "
                "VALUES (?,?,?,?) ON CONFLICT(chat_id,owner_id) "
                "DO UPDATE SET profile_id=excluded.profile_id,updated=excluded.updated",
                (chat_id, owner_id, profile_id, self.clock()),
            )

    def ingest_app_text(self, chat_id, owner_id, text, request_id):
        """Durably intake one app-supplied source, without forged Telegram updates.

        The client-generated request ID is stable across network retries. A
        distinct intentional order requires a distinct request ID. Workers
        perform validation and carrier issuance under the existing receipt lock.
        """
        if type(chat_id) is not int or type(owner_id) is not int or owner_id <= 0:
            raise ValueError("Invalid app actor")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id):
            raise ValueError("Invalid idempotency key")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 8000:
            raise ValueError("Order source text must be 1-8000 characters")
        if "\x00" in text or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise ValueError("Invalid control character in source")
        key = f"{chat_id}:0:{owner_id}:app:{request_id}"
        # Short-lived replay guard protects against accidental double-submits
        # with a *new* browser request ID. Phone/name are NOT identity: a
        # customer may legitimately place many separate orders.
        source_fingerprint = hashlib.sha256(" ".join(text.split()).casefold().encode()).hexdigest()
        with self.lock, self.db:
            job = self._job(key)
            if job is not None:
                if job.get("source") != "app" or job.get("source_text") != text:
                    raise OrderCorrectionConflict("Request ID was already used for different content")
                return job, False
            now = self.clock()
            sender_profile = self.sender_preference(chat_id, owner_id)
            recent = self.db.execute(
                "SELECT body FROM jobs WHERE chat_id=? AND owner_id=? AND updated>=? "
                "AND " + self._order_value("source_fingerprint") + "=? "
                "ORDER BY updated DESC LIMIT 20",
                (chat_id, owner_id, now - 120, source_fingerprint),
            ).fetchall()
            for row in recent:
                earlier = json.loads(row["body"])
                # Deleted shipments need a separate reviewed reissue; never
                # mistakenly claim the old deleted receipt as a new order.
                if (earlier.get("source") == "app"
                        and earlier.get("sender_profile") == sender_profile
                        and earlier.get("state") not in {"deleted"}
                        and 0 <= now - float(earlier.get("created_at") or 0) <= 120
                        and " ".join((earlier.get("source_text") or "").split()).casefold()
                            == " ".join(text.split()).casefold()):
                    return earlier, False
            job = {
                "key": key, "chat_id": chat_id, "owner_id": owner_id,
                "source": "app", "source_text": text,
                "source_fingerprint": source_fingerprint,
                "thread_id": 0, "anchor_id": None,
                "messages": [{"text": text, "date": now}],
                "state": "collecting", "due": now, "created_at": now,
                "attempts": 0, "notified": True, "result": None,
                "order": None, "identity": None,
                "sender_profile": sender_profile,
            }
            self._write(job)
            return job, True

    def ingest(self, message, update_id, *, edited=False, retry=False):
        """One transaction saves the entire message before acknowledging Telegram."""
        chat_id = int(message["chat"]["id"])
        message_id = int(message["message_id"])
        owner_id = int((message.get("from") or {}).get("id", 0))
        thread = message.get("message_thread_id", 0)
        group = message.get("media_group_id")
        with self.lock, self.db:
            if self.has_update(update_id):
                return False
            linked = None
            reply = message.get("reply_to_message")
            if reply:
                linked = self.db.execute(
                    "SELECT key FROM messages WHERE chat_id=? AND message_id=?", (chat_id, reply["message_id"])
                ).fetchone()
            if linked:
                existing = self._job(linked["key"])
                if existing and existing["owner_id"] == owner_id:
                    key = existing["key"]
                else:
                    linked = None
            if not linked:
                key = f"{chat_id}:{thread}:{owner_id}:" + (f"album:{group}" if group else f"message:{message_id}")
            job = self._job(key)
            fresh = job is None
            if fresh:
                if retry:
                    raise ValueError("Retry must refer to a saved order")
                job = dict(key=key, chat_id=chat_id, owner_id=owner_id, thread_id=thread,
                           anchor_id=message_id, messages=[], state="collecting", due=0,
                           created_at=self.clock(),
                           attempts=0, notified=False, result=None, order=None, identity=None,
                           sender_profile=self.sender_preference(chat_id, owner_id))
            previous_media = self.attachments(job)
            if retry and job.get("app_correction"):
                draft = job["app_correction"]
                if job["state"] in {"processing", "uncertain"}:
                    raise OrderCorrectionConflict("Unconfirmed shipment creation blocks corrections")
                if draft.get("baseline_revision") != self.order_edit_baseline(job):
                    raise OrderCorrectionConflict("Order changed; review the app draft before retrying")
                proposed = draft["proposed_order"]
                if job.get("order"):
                    pending = dict(job.get("pending_edits") or {})
                    pending.update(draft["fields"])
                    apply_field_patch(self._parse(job), pending)
                    job["pending_edits"] = pending
                else:
                    # A full validated repair is adopted only at this explicit
                    # Telegram retry, not by an already scheduled queue worker.
                    job["pending_base"] = proposed
                    job["pending_edits"] = dict(draft["fields"])
                    job.pop("override", None)
                job.pop("app_correction", None)
                job["correction_version"] = int(job.get("correction_version", 0)) + 1
            # Explicit short corrections are staged; live receipt remains unchanged
            # until deleted by the user and independently verified via Nova Poshta.
            if linked and (message.get("text") or message.get("caption")) and not retry:
                correction = message.get("text") or message.get("caption")
                patch = parse_field_patch(correction)
                if patch is not None:
                    if job["state"] in {"processing", "uncertain"}:
                        raise ValueError("Создание ТТН не подтверждено. Исправления временно заблокированы.")
                    if job.get("order") is not None:
                        base = self._parse(job)
                        pending = dict(job.get("pending_edits") or {})
                        pending.update(patch)
                        apply_field_patch(base, pending)  # validate before writing anything
                        job["pending_edits"] = pending
                        if job["state"] == "created":
                            job["notice"] = ("Сохранена правка: " + patch_labels(pending)
                                             + ". Удали действующую ТТН в НП и ответь /retry на карточку.")
                    else:
                        # If an incomplete forwarded order has no parsed record,
                        # append explicit field labels instead of replacing the
                        # original text/photos with a one-line correction.
                        previous = job.get("override") or "\n".join(
                            dict.fromkeys((m.get("text") or m.get("caption") or "").strip()
                                              for m in job["messages"]
                                              if (m.get("text") or m.get("caption") or "").strip())
                        )
                        job["override"] = previous.rstrip() + "\n" + correction
                else:
                    job["override"] = correction
                    job.pop("pending_edits", None)
                    job.pop("pending_base", None)
                job["correction_version"] = int(job.get("correction_version", 0)) + 1
            old = next((m for m in job["messages"] if m["message_id"] == message_id), None)
            if not retry:
                if old:
                    job["messages"].remove(old)
                job["messages"].append(message)
                job["messages"].sort(key=lambda m: m["message_id"])
            text = message.get("text") or message.get("caption") or ""
            if text and not retry:
                job["anchor_id"] = message_id
            if job["state"] == "deleted" and self.attachments(job) != previous_media:
                self._remember_completed_bundle(job)
            if job["state"] == "created" and retry:
                job.update(state="collecting", notified=False, attempts=0, due=self.clock())
            elif job["state"] == "created":
                if linked or (edited and old and text != (old.get("text") or old.get("caption") or "")):
                    job.setdefault("notice", "ТТН уже создана. Изменение текста не меняет готовую накладную.")
                    job["notified"] = False
                    job["due"] = self.clock()
                if self.attachments(job) != previous_media:
                    job.update(notified=False, due=self.clock() + self.album_wait)
                    self._remember_completed_bundle(job)
            elif job["state"] not in {"processing", "uncertain"}:
                job.update(state="collecting", notified=False, attempts=0)
                job["due"] = self.clock() + (self.album_wait if group else 0.2)
            elif job["state"] == "uncertain" and (linked or retry):
                job.update(notified=False, due=self.clock())
            if job["state"] == "processing" and (linked or edited):
                job["notice"] = "Исправление пришло во время создания ТТН; проверь данные готовой накладной."
            self._write(job)
            self.db.execute("INSERT INTO messages VALUES (?,?,?) ON CONFLICT(chat_id,message_id) DO UPDATE SET key=excluded.key", (chat_id, message_id, key))
            self.db.execute("INSERT INTO updates VALUES (?,?)", (update_id, self.clock()))
        self.wakeup.set()
        return True

    @staticmethod
    def attachments(job):
        result, seen = [], set()
        for msg in job["messages"]:
            if msg.get("photo"):
                file = max(msg["photo"], key=lambda f: f.get("width", 0) * f.get("height", 0))
                kind = "photo"
            else:
                kind = next((k for k in ("video", "document", "animation") if msg.get(k)), None)
                if not kind:
                    continue
                file = msg[kind]
            unique = file.get("file_unique_id") or file["file_id"]
            if unique not in seen:
                result.append(dict(kind=kind, file_id=file["file_id"], unique_id=unique))
                seen.add(unique)
        return result

    def _identity(self, job, order):
        if job.get("source") == "app":
            # No Telegram message identity exists for a web submission.
            # A unique scoped source/request ID is immutable across retries.
            return hashlib.sha256(_json(["app", job["chat_id"], job["owner_id"],
                                         job["key"]]).encode()).hexdigest()
        origins = []
        for msg in job["messages"]:
            if not (msg.get("text") or msg.get("caption")):
                continue
            origin = msg.get("forward_origin") or {}
            source_chat = origin.get("chat") or msg.get("forward_from_chat")
            source_id = origin.get("message_id") or msg.get("forward_from_message_id")
            if source_chat and source_id:
                origins.append(["message", source_chat["id"], source_id])
            elif origin or msg.get("forward_date"):
                sender = origin.get("sender_user") or msg.get("forward_from") or {}
                origins.append(["forward", origin.get("date") or msg.get("forward_date"),
                                sender.get("id") or origin.get("sender_user_name") or msg.get("forward_sender_name")])
        day = datetime.fromtimestamp(self.clock(), ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d")
        strong_origins = [origin for origin in origins if origin[0] == "message"]
        if strong_origins:
            return hashlib.sha256(_json([job["chat_id"], job["owner_id"], min(strong_origins, key=_json)]).encode()).hexdigest()
        # Same source and unchanged recipient/amount return the earlier TTN. A
        # different photo or distinct source remains a distinct order.
        raw_content = job.get("override") or "\n".join(m.get("text") or m.get("caption") or "" for m in job["messages"])
        scope = [job["chat_id"], job["owner_id"], sorted(origins, key=_json) or ["direct", day],
                 order, sorted(a["unique_id"] for a in self.attachments(job)),
                 None if origins else " ".join(raw_content.split()).casefold()]
        return hashlib.sha256(_json(scope).encode()).hexdigest()

    def _remember_completed_bundle(self, job):
        """A fuller album is still the same shipment when it is forwarded again."""
        if job["state"] not in {"created", "deleted"} or not job.get("order") or not job.get("result"):
            return
        identity = self._identity(job, job["order"])
        if identity != job.get("identity"):
            # New album identities follow the current receipt even when an old
            # card receives another photo while its deleted TTN is replaced.
            current = self.db.execute("SELECT * FROM receipts WHERE identity=?", (job.get("identity"),)).fetchone()
            self.db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?) ON CONFLICT(identity) DO NOTHING",
                (identity, current["state"] if current else job["state"],
                 current["result"] if current else _json({"result": job["result"], "order": job["order"]}), self.clock()),
            )

    def _save_receipt(self, job, state, receipt):
        """Move every album alias to the same replacement, in one transaction."""
        previous = job.get("replaced_ttn")
        aliases = self.db.receipts_for_ttn(previous) if previous else []
        identities = {job["identity"], *(row["identity"] for row in aliases)}
        for identity in identities:
            self.db.execute("UPDATE receipts SET state=?, result=?, updated=? WHERE identity=?",
                            (state, _json(receipt), self.clock(), identity))

    def _retry_status_check(self, job, error):
        job["attempts"] += 1
        if job["attempts"] < 3:
            job.update(state="collecting", due=self.clock() + 3 * 2 ** job["attempts"])
        else:
            job.update(state="failed", error=error, due=self.clock(), notified=False)

    def _parse(self, job):
        # A short correction message must NOT be parsed together with the old
        # caption: it would create contradictory duplicate values.
        if job.get("pending_base") and not job.get("order") and not job.get("override"):
            return apply_field_patch(job["pending_base"], job.get("pending_edits") or {})
        if job.get("pending_edits") and job.get("order") and not job.get("override"):
            return apply_field_patch(job["order"], job["pending_edits"])
        if job.get("override"):
            order = self.parser(job["override"])
            return apply_field_patch(order, job["pending_edits"]) if job.get("pending_edits") else order
        captions = list(dict.fromkeys(
            (m.get("text") or m.get("caption") or "").strip() for m in job["messages"]
            if (m.get("text") or m.get("caption") or "").strip()
        ))
        if not captions:
            raise ValueError("К фото не пришёл текст заказа. Перешли фото с подписью или ответь текстом заказа на это фото.")
        parsed = []
        for caption in captions:
            try:
                parsed.append(self.parser(caption))
            except ValueError:
                pass
        if len(parsed) > 1 and any(p != parsed[0] for p in parsed[1:]):
            raise ValueError("В альбоме разные заказы. Перешли каждый заказ отдельным альбомом.")
        # Combine split fields, but still validate the whole bundle so a second
        # recipient/phone cannot be discarded as an incomplete caption.
        combined = "\n".join(captions)
        order = self.parser(combined)
        order = parsed[0] if parsed else order
        return apply_field_patch(order, job["pending_edits"]) if job.get("pending_edits") else order

    def _process(self, job):
        try:
            order = self._parse(job)
        except (ValueError, NovaPoshtaError) as exc:
            job.update(state="invalid", error=str(exc), due=self.clock(), notified=False)
            return
        job["order"] = order
        job.pop("duplicate", None)
        job.pop("replaced_ttn", None)
        job.pop("notice", None)
        # A correction to an already-issued card must stay bound to its
        # original receipt. Otherwise changed fields would form a new hash and
        # bypass the existing-TTN deletion check.
        previous_identity = job.get("identity") if job.get("result") else None
        identity = job["identity"] = previous_identity or self._identity(job, order)
        with self.lock:
            row = self.db.execute("SELECT * FROM receipts WHERE identity=?", (identity,)).fetchone()
        receipt = {"order": order}
        if row:
            if row["state"] not in {"created", "deleted"}:
                job.update(state="uncertain", error="По этому заказу уже было создание ТТН без подтверждённого результата. Проверь накладные в Новой Почте.",
                           due=self.clock(), notified=False)
                return
            receipt = json.loads(row["result"])
            previous_result = receipt.get("result") or receipt
            receipt.setdefault("sender_profile", "default")
            # An unchanged forwarded source can resolve to an old receipt even
            # after future sender preference changes. Replacement and tracking
            # must stay in that receipt's original carrier account.
            job["sender_profile"] = receipt["sender_profile"]
            original_order = receipt.get("order") or order
            if row["state"] == "created":
                try:
                    previous_client = self._sender_client(receipt.get("sender_profile", "default"))
                    deleted = previous_client.is_ttn_deleted(previous_result.get("ttn"), phone=original_order.get("phone", ""))
                    if type(deleted) is not bool:
                        raise NovaPoshtaTemporaryError("Новая Почта не подтвердила статус ранее созданной ТТН.")
                except NovaPoshtaError as exc:
                    self._retry_status_check(job, str(exc))
                    return
                except Exception:
                    logger.exception("Shipment deletion check failed for order %s", job["key"])
                    self._retry_status_check(job, "Не удалось проверить удаление ТТН. Повтори позже.")
                    return
                if not deleted:
                    if original_order != order:
                        job["notice"] = "Этот исходный заказ уже обработан. Показаны данные ранее созданной ТТН."
                    job.update(state="created", result=previous_result, order=original_order, duplicate=True,
                               sender_profile=receipt.get("sender_profile", "default"),
                               due=self.clock(), notified=False)
                    return
                history = list(receipt.get("history") or [])
                history.append({"result": previous_result, "order": original_order, "deleted_at": self.clock(),
                                "sender_profile": receipt["sender_profile"]})
                receipt = {"result": previous_result, "order": order, "history": history,
                           "sender_profile": job["sender_profile"]}
            job["replaced_ttn"] = previous_result["ttn"]
        try:
            shipment_client = self._sender_client(job.get("sender_profile", "default"))
        except NovaPoshtaError as exc:
            job.update(state="failed", error=str(exc), due=self.clock(), notified=False)
            return
        with self.lock, self.db:
            if row:
                self._save_receipt(job, "creating", receipt)
                for old in self.db.jobs_for_ttn(job["replaced_ttn"]):
                    archived = json.loads(old["body"])
                    if archived["key"] != job["key"] and archived["state"] == "created":
                        archived.update(state="deleted", notified=True)
                        self.db.execute("UPDATE jobs SET state='deleted', body=? WHERE key=?",
                                        (_json(archived), archived["key"]))
            else:
                self.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (identity, "creating", None, self.clock()))
            self._write(job)
        try:
            result = shipment_client.create_ttn(**order)
            if not isinstance(result, dict) or not result.get("ttn"):
                raise NovaPoshtaUncertainError("Не получен номер ТТН. Проверь накладные в Новой Почте.")
            # Record success before sending Telegram: a failed send never creates
            # another shipment on retry.
            with self.lock, self.db:
                self._save_receipt(job, "created", {
                    **receipt, "result": result, "order": order,
                    "sender_profile": job.get("sender_profile", "default"),
                })
            job.update(state="created", result=result, due=self.clock(), notified=False)
            job.pop("pending_edits", None)
            job.pop("pending_base", None)
        except NovaPoshtaTemporaryError as exc:
            with self.lock, self.db:
                if job.get("replaced_ttn"):
                    self._save_receipt(job, "deleted", receipt)
                else:
                    self.db.execute("DELETE FROM receipts WHERE identity=?", (identity,))
            job["attempts"] += 1
            if job["attempts"] < 3:
                job.update(state="collecting", due=self.clock() + 3 * 2 ** job["attempts"])
            else:
                job.update(state="failed", error=str(exc), due=self.clock(), notified=False)
        except NovaPoshtaUncertainError as exc:
            with self.lock, self.db:
                self._save_receipt(job, "uncertain", receipt)
            job.update(state="uncertain", error=str(exc), due=self.clock(), notified=False)
        except NovaPoshtaError as exc:
            with self.lock, self.db:
                if job.get("replaced_ttn"):
                    self._save_receipt(job, "deleted", receipt)
                else:
                    self.db.execute("DELETE FROM receipts WHERE identity=?", (identity,))
            job.update(state="failed", error=str(exc), due=self.clock(), notified=False)
        except Exception:
            logger.exception("Unexpected shipment failure for order %s", job["key"])
            with self.lock, self.db:
                self._save_receipt(job, "uncertain", receipt)
            job.update(state="uncertain", error="Не удалось подтвердить результат создания ТТН. Проверь накладные в Новой Почте.",
                       due=self.clock(), notified=False)

    def _notify(self, job):
        """Send a photo-first shipment card with a native 2×2 inline keyboard."""
        media = self.attachments(job)
        kwargs = dict(chat_id=job["chat_id"], reply_to_message_id=job["anchor_id"],
                      allow_sending_without_reply=True, timeout=15)
        if job.get("thread_id"):
            kwargs["message_thread_id"] = job["thread_id"]
        sent = []
        if job["state"] == "created":
            from order_card import build_card_keyboard, format_order_card
            result = job["result"]
            text = format_order_card(
                job["order"], result, sum(a["kind"] == "photo" for a in media),
                duplicate=job.get("duplicate", False), replaced_ttn=job.get("replaced_ttn"),
                notice=job.get("notice"),
            )
            markup = build_card_keyboard(result["ttn"])
            if media:
                # Telegram does not permit inline keyboards on sendMediaGroup:
                # attach the 2x2 controls to a lead photo, then send any extras.
                lead = next((a for a in media if a["kind"] == "photo"), media[0])
                sent.append(getattr(self.bot, "send_" + lead["kind"])(
                    **{lead["kind"]: lead["file_id"]}, caption=text[:1024],
                    reply_markup=markup, **kwargs,
                ))
                extras = [a for a in media if a is not lead]
                visuals = [a for a in extras if a["kind"] in {"photo", "video"}]
                for start in range(0, len(visuals), 10):
                    batch = visuals[start:start + 10]
                    if len(batch) == 1:
                        item = batch[0]
                        sent.append(getattr(self.bot, "send_" + item["kind"])(
                            **{item["kind"]: item["file_id"]}, **kwargs,
                        ))
                    else:
                        items = [
                            (types.InputMediaPhoto if item["kind"] == "photo" else types.InputMediaVideo)(item["file_id"])
                            for item in batch
                        ]
                        sent.extend(self.bot.send_media_group(media=items, **kwargs))
                for item in extras:
                    if item["kind"] not in {"photo", "video"}:
                        sent.append(getattr(self.bot, "send_" + item["kind"])(
                            **{item["kind"]: item["file_id"]}, **kwargs,
                        ))
            else:
                sent.append(self.bot.send_message(text=text[:4096], reply_markup=markup, **kwargs))
        else:
            text = "⚠️ " + job.get("error", "Не удалось обработать заказ.")
            if job["state"] != "uncertain":
                text += "\n\nОтветь на это сообщение полным исправленным заказом. Фото сохранятся."
                if job["state"] == "failed":
                    text += "\nДля повтора без изменений ответь командой /retry."
            sent.append(self.bot.send_message(text=text[:4096], **kwargs))
        # The lead photo and every attachment can resolve the same owner-scoped order.
        with self.lock, self.db:
            for message in sent:
                self.db.execute("INSERT INTO messages VALUES (?,?,?) ON CONFLICT(chat_id,message_id) DO UPDATE SET key=excluded.key",
                                (job["chat_id"], message.message_id, job["key"]))
        job["notified"] = True

    def tick(self):
        """Process one due job; exposed for deterministic end-to-end tests."""
        with self.lock:
            if not self.db.acquire_worker():
                return False
            if self._recovered_epoch != self.db.worker_epoch:
                self._recover()
                self._recovered_epoch = self.db.worker_epoch
        with self.lock, self.db:
            rows = self.db.execute(self.db.pending_query, (self.clock(),)).fetchall()
            job = next((json.loads(row["body"]) for row in rows
                        if json.loads(row["body"])["state"] == "collecting" or not json.loads(row["body"]).get("notified")), None)
            if job is None:
                return False
            if job["state"] == "collecting":
                job["state"] = "processing"
                self._write(job)
                process = True
            else:
                process = False
        if process:
            self._process(job)
        self._commit_result(job)
        if job["state"] != "collecting":
            if job.get("source") == "app":
                # App-origin jobs are read from the journal, not announced as
                # replies to fabricated Telegram messages.
                job["notified"] = True
            else:
                try:
                    self._notify(job)
                except Exception as exc:
                    logger.warning("Telegram order notification failed: %s", type(exc).__name__)
                    job["due"] = self.clock() + 15
            self._commit_result(job, after_notification=True)
        return True

    def _commit_result(self, job, *, after_notification=False):
        """Keep updates received while a shipment or notification was in flight."""
        with self.lock, self.db:
            current = self._job(job["key"])
            previous_media = self.attachments(job)
            previous_notice = job.get("notice")
            if current.get("correction_version", 0) != job.get("correction_version", 0):
                # An app edit can arrive while an existing Telegram notification
                # is in flight. Retain that draft without enqueueing a shipment.
                job["correction_version"] = current["correction_version"]
                for field in ("pending_edits", "pending_base", "app_correction"):
                    if field in current:
                        job[field] = current[field]
                    else:
                        job.pop(field, None)
            source_changed = (current["messages"] != job["messages"] or
                              current.get("override") != job.get("override"))
            if current["state"] == "collecting" and (
                    after_notification or job["state"] not in {"created", "uncertain"}):
                # A correction or /retry arrived while an error was being sent.
                job.clear()
                job.update(current)
            else:
                job["messages"] = current["messages"]
                job["anchor_id"] = current["anchor_id"]
                if "override" in current:
                    job["override"] = current["override"]
                if current.get("notice"):
                    job["notice"] = current["notice"]
                if source_changed and job["state"] in {"invalid", "failed"}:
                    job.update(state="collecting", notified=False,
                               due=self.clock() + self.album_wait, attempts=0)
                elif after_notification and job["state"] == "created" and (
                        self.attachments(job) != previous_media or job.get("notice") != previous_notice):
                    # Deliver the added photos with the existing TTN, never a new one.
                    job.update(notified=False, due=self.clock() + self.album_wait)
            if source_changed and job["state"] == "created":
                self._remember_completed_bundle(job)
            self._write(job)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="order-processor", daemon=True)
        self.thread.start()

    def _run(self):
        logger.info("Order processor started")
        while not self.stop.is_set():
            try:
                if self.tick():
                    continue
            except Exception:
                # Recover a job left processing after a database interruption.
                self._recovered_epoch = 0
                logger.exception("Order journal processing failed")
            self.wakeup.wait(0.5)
            self.wakeup.clear()

    def order_state_counts(self, chat_id, owner_id):
        """Owner-scoped totals across the entire journal, not just recent jobs."""
        with self.lock:
            rows = self.db.execute(
                "SELECT state, COUNT(*) AS quantity FROM jobs "
                "WHERE chat_id=? AND owner_id=? GROUP BY state",
                (chat_id, owner_id),
            ).fetchall()
        return {row["state"]: int(row["quantity"]) for row in rows}

    def order_queue(self, chat_id, owner_id, limit=10):
        """Only outstanding/failed jobs for this Telegram sender."""
        with self.lock:
            rows = self.db.execute(
                "SELECT body FROM jobs WHERE chat_id=? AND owner_id=? "
                "AND state IN ('collecting','processing','invalid','failed','uncertain') "
                "ORDER BY updated DESC LIMIT ?",
                (chat_id, owner_id, limit),
            ).fetchall()
        return [json.loads(row["body"]) for row in rows]

    def _order_value(self, *path):
        """Build an expression from server-owned JSON field names only."""
        if self.db.backend == "postgres":
            return "body::jsonb #>> '{" + ",".join(path) + "}'"
        return "json_extract(body, '$." + ".".join(path) + "')"

    def _fold(self, value):
        return ("LOWER(" if self.db.backend == "postgres" else "safar_casefold(") + value + ")"

    def _order_filters(self, chat_id, owner_id, *, status="", search="", sender_profile="",
                       date_from=None, date_to=None, has_ttn=None):
        """Apply every filter in SQL before pagination and scope all queries."""
        if status not in {"", "all", "attention", "collecting", "processing", "invalid",
                          "failed", "uncertain", "created", "deleted"}:
            raise ValueError("Invalid order status")
        if not isinstance(search, str) or len(search) > 160:
            raise ValueError("Invalid order search")
        if not isinstance(sender_profile, str) or len(sender_profile) > 64:
            raise ValueError("Invalid sender filter")
        if has_ttn is not None and type(has_ttn) is not bool:
            raise ValueError("Invalid TTN filter")
        for value in (date_from, date_to):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid activity date")
        if date_from is not None and date_to is not None and date_from >= date_to:
            raise ValueError("Invalid activity date range")
        clauses, values = ["chat_id=?", "owner_id=?"], [chat_id, owner_id]
        if status == "attention":
            clauses.append("state IN ('invalid','failed','uncertain')")
        elif status and status != "all":
            clauses.append("state=?")
            values.append(status)
        if sender_profile:
            clauses.append("COALESCE(" + self._order_value("sender_profile") + ",'default')=?")
            values.append(sender_profile)
        if date_from is not None:
            clauses.append("updated>=?")
            values.append(date_from)
        if date_to is not None:
            clauses.append("updated<?")
            values.append(date_to)
        ttn = "COALESCE(" + self._order_value("result", "ttn") + ",'')"
        if has_ttn is not None:
            clauses.append(ttn + ("!=''" if has_ttn else "=''"))
        needle = search.strip().casefold()
        if needle:
            fields = [self._order_value("order", key) for key in ("full_name", "city", "warehouse")]
            fields.append(self._order_value("result", "ttn"))
            # Incomplete captions remain searchable before parsing succeeds.
            if self.db.backend == "postgres":
                captions = ("(SELECT string_agg(COALESCE(m->>'caption',m->>'text',''),' ') "
                            "FROM jsonb_array_elements(COALESCE(body::jsonb->'messages','[]'::jsonb)) AS m)")
            else:
                captions = ("(SELECT group_concat(COALESCE(json_extract(value,'$.caption'),"
                            "json_extract(value,'$.text'),''),' ') FROM json_each(body,'$.messages'))")
            haystack = " || ' ' || ".join("COALESCE(" + field + ",'')" for field in [*fields, captions])
            if self.db.backend == "postgres":
                clauses.append("POSITION(? IN " + self._fold(haystack) + ")>0")
            else:
                clauses.append("instr(" + self._fold(haystack) + ",?)>0")
            values.append(needle)
        return " AND ".join(clauses), values

    @staticmethod
    def order_revision(job):
        """Revision of persisted fields; derived list/detail metadata is excluded."""
        persisted = {key: value for key, value in job.items()
                     if key not in {"updated_at", "revision", "app_correction_stale"}}
        return hashlib.sha256(_json(persisted).encode()).hexdigest()

    @staticmethod
    def order_edit_baseline(job):
        """The order/source/receipt a draft was reviewed against, not queue timers."""
        fields = ("messages", "order", "override", "pending_edits", "pending_base", "identity",
                  "result", "sender_profile", "state")
        return hashlib.sha256(_json({key: job.get(key) for key in fields}).encode()).hexdigest()

    def _order_row(self, row):
        job = json.loads(row["body"])
        job["revision"] = self.order_revision(job)
        job["updated_at"] = row["updated"]
        if job.get("app_correction"):
            job["app_correction_stale"] = (job["app_correction"].get("baseline_revision") != self.order_edit_baseline(job))
        return job

    def list_orders_page(self, chat_id, owner_id, *, limit=20, offset=0, sort="updated_desc", **filters):
        """Filter and paginate the whole private journal, with a stable tie-break.

        Dates refer to journal activity (`updated_at`), never inferred delivery.
        Legacy list_orders remains unchanged for Telegram /orders commands.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 60:
            raise ValueError("Invalid page limit")
        if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 2 ** 31 - 1:
            raise ValueError("Invalid page offset")
        field_sort = {
            "recipient_asc": ("full_name", "ASC"), "recipient_desc": ("full_name", "DESC"),
            "city_asc": ("city", "ASC"), "city_desc": ("city", "DESC"),
        }
        if sort in {"updated_desc", "updated_asc"}:
            direction = "DESC" if sort == "updated_desc" else "ASC"
            ordering = "updated " + direction + ", key " + direction
        elif sort in field_sort:
            field, direction = field_sort[sort]
            ordering = (self._fold("COALESCE(" + self._order_value("order", field) + ",'')")
                        + " " + direction + ", updated DESC, key DESC")
        else:
            raise ValueError("Invalid order sorting")
        where, values = self._order_filters(chat_id, owner_id, **filters)
        with self.lock:
            rows = self.db.execute(
                "SELECT body, updated FROM jobs WHERE " + where + " ORDER BY " + ordering + " LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return [self._order_row(row) for row in rows]

    def order_page_count(self, chat_id, owner_id, **filters):
        where, values = self._order_filters(chat_id, owner_id, **filters)
        with self.lock:
            row = self.db.execute("SELECT COUNT(*) AS quantity FROM jobs WHERE " + where, values).fetchone()
        return int(row["quantity"])

    def order_for_key(self, chat_id, owner_id, key):
        with self.lock:
            row = self.db.execute(
                "SELECT body,updated FROM jobs WHERE key=? AND chat_id=? AND owner_id=?",
                (key, chat_id, owner_id),
            ).fetchone()
        return self._order_row(row) if row else None

    def stage_order_correction(self, chat_id, owner_id, key, changes, *, expected_revision):
        """Persist a validated draft only. Never enqueue or edit a carrier receipt.

        A later explicit Telegram /retry continues through the existing deletion
        confirmation and idempotency guards; this method makes no network calls.
        """
        fields = {"full_name", "phone", "city", "warehouse", "cost", "cod_amount", "weight", "description"}
        if not isinstance(changes, dict) or not changes or not set(changes) <= fields:
            raise ValueError("Unsupported correction fields")
        patch = {}
        for field, value in changes.items():
            if type(value) not in {str, int, float} or (type(value) is float and not math.isfinite(value)):
                raise ValueError("Invalid correction value")
            text = str(value).strip()
            if not text or len(text) > 160 or "\n" in text or "\r" in text:
                raise ValueError("Invalid correction value")
            patch[field] = text
        if not isinstance(expected_revision, str) or len(expected_revision) != 64:
            raise OrderCorrectionConflict("Refresh the order before editing")
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT body,updated FROM jobs WHERE key=? AND chat_id=? AND owner_id=?",
                (key, chat_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            job = json.loads(row["body"])
            if self.order_revision(job) != expected_revision:
                raise OrderCorrectionConflict("Order changed; refresh before editing")
            if job["state"] in {"processing", "uncertain"}:
                raise OrderCorrectionConflict("Unconfirmed shipment creation blocks corrections")
            existing_draft = job.get("app_correction") or {}
            pending = (dict(existing_draft.get("fields") or {})
                       if existing_draft.get("baseline_revision") == self.order_edit_baseline(job) else {})
            pending.update(patch)
            try:
                base = self._parse(job)
            except (ValueError, NovaPoshtaError):
                # An invalid caption may have no usable parsed record. Require a
                # complete valid proposal instead of guessing missing fields.
                labels = {"full_name": "Получатель", "phone": "Телефон", "city": "Город",
                          "warehouse": "Отделение", "cost": "Оценка", "cod_amount": "Наложка",
                          "weight": "Вес", "description": "Описание"}
                if not {"full_name", "phone", "city", "warehouse", "cost"} <= set(pending):
                    raise ValueError("A complete corrected order is required")
                base = self.parser("\n".join(labels[field] + ": " + value for field, value in pending.items()))
            proposed = apply_field_patch(base, pending)  # validate all fields before any write
            job["app_correction"] = {
                "fields": pending, "proposed_order": proposed,
                "baseline_revision": self.order_edit_baseline(job), "staged_at": self.clock(),
            }
            job["correction_version"] = int(job.get("correction_version", 0)) + 1
            self._write(job)
            updated = self.db.execute("SELECT body,updated FROM jobs WHERE key=?", (key,)).fetchone()
        return {"job": self._order_row(updated), "staged": True, "enqueued": False, "ttn_modified": False}

    def order_analytics(self, chat_id, owner_id, *, date_from=None, date_to=None, days=7):
        """Exact owner/chat totals and Kyiv-day journal activity, never revenue.

        Current attempts are queue counters; they are not a lifetime retry count.
        Created means TTN issued, not delivered. Drafts keep their current state.
        """
        if type(days) is not int or not 1 <= days <= 31:
            raise ValueError("Invalid analytics period")
        where, values = self._order_filters(chat_id, owner_id, date_from=date_from, date_to=date_to)
        zone = ZoneInfo("Europe/Kyiv")
        today = datetime.fromtimestamp(self.clock(), zone).date()
        dates = [today - timedelta(days=days - 1 - index) for index in range(days)]
        buckets = []
        bucket_values = []
        for index, date in enumerate(dates):
            start = datetime.combine(date, day_time.min, zone).timestamp()
            end = datetime.combine(date + timedelta(days=1), day_time.min, zone).timestamp()
            buckets.append("SUM(CASE WHEN updated>=? AND updated<? THEN 1 ELSE 0 END) AS day_" + str(index))
            bucket_values.extend((start, end))
        ttn = "COALESCE(" + self._order_value("result", "ttn") + ",'')"
        attempts = "COALESCE(CAST(" + self._order_value("attempts") + " AS INTEGER),0)"
        with self.lock:
            state_rows = self.db.execute("SELECT state,COUNT(*) AS quantity FROM jobs WHERE " + where + " GROUP BY state", values).fetchall()
            metrics = self.db.execute(
                "SELECT COUNT(*) AS total,SUM(CASE WHEN " + ttn + "!='' THEN 1 ELSE 0 END) AS with_ttn,"
                "SUM(" + attempts + ") AS current_attempts," + ",".join(buckets) + " FROM jobs WHERE " + where,
                (*bucket_values, *values),
            ).fetchone()
        counts = {row["state"]: int(row["quantity"]) for row in state_rows}
        return {
            "counts": counts, "total": int(metrics["total"]),
            "attention": sum(counts.get(state, 0) for state in ("invalid", "failed", "uncertain")),
            "with_ttn": int(metrics["with_ttn"] or 0), "current_attempts": int(metrics["current_attempts"] or 0),
            "daily_activity": [{"date": date.isoformat(), "count": int(metrics["day_" + str(index)] or 0)}
                               for index, date in enumerate(dates)],
            "timezone": "Europe/Kyiv", "date_basis": "updated_at",
        }

    def list_orders(self, chat_id, owner_id, limit=10):
        with self.lock:
            rows = self.db.execute("SELECT body FROM jobs WHERE chat_id=? AND owner_id=? ORDER BY updated DESC LIMIT ?",
                                   (chat_id, owner_id, limit)).fetchall()
            return [json.loads(row["body"]) for row in rows]

    def order_for_message(self, chat_id, owner_id, message_id):
        with self.lock:
            row = self.db.execute(
                "SELECT j.body FROM messages m JOIN jobs j ON j.key=m.key "
                "WHERE m.chat_id=? AND m.message_id=? AND j.owner_id=?",
                (chat_id, message_id, owner_id),
            ).fetchone()
            return json.loads(row["body"]) if row else None

    def history_for_message(self, chat_id, owner_id, message_id):
        """Shipment lifecycle for the card owner only; no cross-user lookups."""
        job = self.order_for_message(chat_id, owner_id, message_id)
        if job is None:
            return None
        identity = job.get("identity")
        history = []
        current = job.get("result") or {}
        if identity:
            with self.lock:
                row = self.db.execute("SELECT result FROM receipts WHERE identity=?", (identity,)).fetchone()
            if row and row["result"]:
                data = json.loads(row["result"])
                history = list(data.get("history") or [])
                current = data.get("result") or current
        return dict(history=history, current=current, state=job["state"])

    def close(self):
        self.stop.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.db.close()
