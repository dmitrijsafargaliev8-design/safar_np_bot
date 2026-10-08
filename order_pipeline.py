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

logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OrderPipeline:
    def __init__(self, path, parser, client, bot, *, album_wait=3.0, clock=time.time, database_url=None):
        self.parser, self.client, self.bot = parser, client, bot
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
                           attempts=0, notified=False, result=None, order=None, identity=None)
            previous_media = self.attachments(job)
            # Preserve source images when a complete correction is sent as a reply.
            if linked and (message.get("text") or message.get("caption")) and not retry:
                job["override"] = message.get("text") or message.get("caption")
            old = next((m for m in job["messages"] if m["message_id"] == message_id), None)
            if not retry:
                if old:
                    job["messages"].remove(old)
                job["messages"].append(message)
                job["messages"].sort(key=lambda m: m["message_id"])
            text = message.get("text") or message.get("caption") or ""
            if text and not retry:
                job["anchor_id"] = message_id
            if job["state"] == "created":
                if linked or (edited and old and text != (old.get("text") or old.get("caption") or "")):
                    job["notice"] = "ТТН уже создана. Изменение текста не меняет готовую накладную."
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
        if job["state"] != "created" or not job.get("order") or not job.get("result"):
            return
        identity = self._identity(job, job["order"])
        if identity != job.get("identity"):
            self.db.execute(
                "INSERT INTO receipts VALUES (?,?,?,?) ON CONFLICT(identity) DO NOTHING",
                (identity, "created", _json({"result": job["result"], "order": job["order"]}), self.clock()),
            )

    def _parse(self, job):
        if job.get("override"):
            return self.parser(job["override"])
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
        return parsed[0] if parsed else order

    def _process(self, job):
        try:
            order = self._parse(job)
        except (ValueError, NovaPoshtaError) as exc:
            job.update(state="invalid", error=str(exc), due=self.clock(), notified=False)
            return
        job["order"] = order
        identity = job["identity"] = self._identity(job, order)
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM receipts WHERE identity=?", (identity,)).fetchone()
            if row:
                if row["state"] == "created":
                    receipt = json.loads(row["result"])
                    original_order = receipt.get("order") or order
                    if original_order != order:
                        job["notice"] = "Этот исходный заказ уже обработан. Показаны данные ранее созданной ТТН."
                    job.update(state="created", result=receipt.get("result") or receipt, order=original_order, duplicate=True,
                               due=self.clock(), notified=False)
                else:
                    job.update(state="uncertain", error="По этому заказу уже было создание ТТН без подтверждённого результата. Проверь накладные в Новой Почте.",
                               due=self.clock(), notified=False)
                return
            self.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (identity, "creating", None, self.clock()))
            self._write(job)
        try:
            result = self.client.create_ttn(**order)
            if not isinstance(result, dict) or not result.get("ttn"):
                raise NovaPoshtaUncertainError("Не получен номер ТТН. Проверь накладные в Новой Почте.")
            # Record success before sending Telegram: a failed send never creates
            # another shipment on retry.
            with self.lock, self.db:
                self.db.execute("UPDATE receipts SET state='created', result=?, updated=? WHERE identity=?",
                                (_json({"result": result, "order": order}), self.clock(), identity))
            job.update(state="created", result=result, due=self.clock(), notified=False)
        except NovaPoshtaTemporaryError as exc:
            with self.lock, self.db:
                self.db.execute("DELETE FROM receipts WHERE identity=?", (identity,))
            job["attempts"] += 1
            if job["attempts"] < 3:
                job.update(state="collecting", due=self.clock() + 3 * 2 ** job["attempts"])
            else:
                job.update(state="failed", error=str(exc), due=self.clock(), notified=False)
        except NovaPoshtaUncertainError as exc:
            with self.lock, self.db:
                self.db.execute("UPDATE receipts SET state='uncertain' WHERE identity=?", (identity,))
            job.update(state="uncertain", error=str(exc), due=self.clock(), notified=False)
        except NovaPoshtaError as exc:
            with self.lock, self.db:
                self.db.execute("DELETE FROM receipts WHERE identity=?", (identity,))
            job.update(state="failed", error=str(exc), due=self.clock(), notified=False)
        except Exception:
            logger.exception("Unexpected shipment failure for order %s", job["key"])
            with self.lock, self.db:
                self.db.execute("UPDATE receipts SET state='uncertain' WHERE identity=?", (identity,))
            job.update(state="uncertain", error="Не удалось подтвердить результат создания ТТН. Проверь накладные в Новой Почте.",
                       due=self.clock(), notified=False)

    def _notify(self, job):
        media = self.attachments(job)
        if job["state"] == "created":
            order, result = job["order"], job["result"]
            destination = (f"{order['city']} · НП {order['warehouse']}" if order["warehouse"]
                           else f"{order['city']} · {order['street']} {order['house']}" + (f", кв. {order['flat']}" if order["flat"] else ""))
            text = (f"✅ ТТН: {result['ttn']}\n{destination}\n{order['full_name']}\n{order['phone']}\n"
                    f"Оценка: {order['cost']:g} грн\n"
                    + (f"Наложка: {order['cod_amount']:g} грн" if order["cod_amount"] else "Без наложки"))
            if job.get("duplicate"):
                text += "\nЭтот заказ уже обработан; повторная ТТН не создавалась."
            if job.get("notice"):
                text += "\n" + job["notice"]
            if result.get("estimated_delivery_date"):
                text += f"\nДоставка: {result['estimated_delivery_date']}"
        else:
            text = "⚠️ " + job.get("error", "Не удалось обработать заказ.")
            if job["state"] != "uncertain":
                text += "\n\nОтветь на это сообщение полным исправленным заказом. Фото сохранятся."
                if job["state"] == "failed":
                    text += "\nДля повтора без изменений ответь командой /retry."
        kwargs = dict(chat_id=job["chat_id"], reply_to_message_id=job["anchor_id"],
                      allow_sending_without_reply=True, timeout=15)
        if job.get("thread_id"):
            kwargs["message_thread_id"] = job["thread_id"]
        sent = []
        if job["state"] == "created" and media:
            # Albums may mix photo/video. Documents must form their own album;
            # individual sends retain all other attachments and their caption.
            visual = [a for a in media if a["kind"] in {"photo", "video"}]
            if len(visual) > 1:
                for start in range(0, len(visual), 10):
                    batch = visual[start:start + 10]
                    if len(batch) == 1:
                        sent.append(self.bot.send_photo(photo=batch[0]["file_id"], caption=text[:1024], **kwargs)
                                    if batch[0]["kind"] == "photo" else self.bot.send_video(video=batch[0]["file_id"], caption=text[:1024], **kwargs))
                    else:
                        items = [(types.InputMediaPhoto if a["kind"] == "photo" else types.InputMediaVideo)(
                            a["file_id"], caption=text[:1024] if i == 0 else None) for i, a in enumerate(batch)]
                        sent.extend(self.bot.send_media_group(media=items, **kwargs))
                remaining = [a for a in media if a["kind"] not in {"photo", "video"}]
            else:
                remaining = media
            for a in remaining:
                send = getattr(self.bot, "send_" + a["kind"])
                sent.append(send(**{a["kind"]: a["file_id"]}, caption=text[:1024], **kwargs))
        else:
            sent.append(self.bot.send_message(text=text[:4096], **kwargs))
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

    def list_orders(self, chat_id, owner_id, limit=10):
        with self.lock:
            rows = self.db.execute("SELECT body FROM jobs WHERE chat_id=? AND owner_id=? ORDER BY updated DESC LIMIT ?",
                                   (chat_id, owner_id, limit)).fetchall()
            return [json.loads(row["body"]) for row in rows]

    def close(self):
        self.stop.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.db.close()
