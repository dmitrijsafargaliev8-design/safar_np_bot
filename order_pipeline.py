"""Journal incoming Telegram orders, collect albums and send shipment cards.

Postgres keeps the queue, photo identifiers and shipment receipts across host
replacement. SQLite remains available for development or a persistent disk.
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telebot import types

from np_client import NovaPoshtaError, NovaPoshtaTemporaryError, NovaPoshtaUncertainError
from order_journal import OrderJournal
from order_edits import parse_field_patch, apply_field_patch, patch_labels

logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OrderPipeline:
    def __init__(self, path, parser, client, bot, *, album_wait=3.0, clock=time.time, database_url=None,
                 sender_clients=None):
        self.parser, self.client, self.bot = parser, client, bot
        self.sender_clients = dict(sender_clients or {"default": client})
        self.sender_clients.setdefault("default", client)
        self.album_wait, self.clock = album_wait, clock
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.db = OrderJournal(path, database_url)
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
            row = self.db.execute(
                "SELECT profile_id FROM sender_preferences WHERE chat_id=? AND owner_id=?",
                (chat_id, owner_id),
            ).fetchone()
        return row["profile_id"] if row else "default"

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
                           attempts=0, notified=False, result=None, order=None, identity=None,
                           sender_profile=self.sender_preference(chat_id, owner_id))
            previous_media = self.attachments(job)
            # Explicit short corrections are staged; live receipt remains unchanged
            # until deleted by the user and independently verified via Nova Poshta.
            if linked and (message.get("text") or message.get("caption")) and not retry:
                correction = message.get("text") or message.get("caption")
                patch = parse_field_patch(correction)
                if patch is not None and job.get("order") is not None:
                    if job["state"] in {"processing", "uncertain"}:
                        raise ValueError("Создание ТТН не подтверждено. Исправления временно заблокированы.")
                    base = self._parse(job)
                    pending = dict(job.get("pending_edits") or {})
                    pending.update(patch)
                    apply_field_patch(base, pending)  # validate before writing anything
                    job["pending_edits"] = pending
                    job["notice"] = ("Сохранена правка: " + patch_labels(pending)
                                     + ". Удали действующую ТТН в НП и ответь /retry на карточку.")
                else:
                    job["override"] = correction
                    job.pop("pending_edits", None)
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
                history.append({"result": previous_result, "order": original_order, "deleted_at": self.clock()})
                receipt = {"result": previous_result, "order": order, "history": history}
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
            source_changed = (current["messages"] != job["messages"] or
                              current.get("override") != job.get("override"))
            if current["state"] == "collecting" and job["state"] not in {"created", "uncertain"}:
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
