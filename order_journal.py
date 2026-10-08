"""Private durable order journal with a local SQLite option for development."""
import sqlite3
from pathlib import Path

WORKER_LOCK = 731452910012
WRITE_LOCK = 731452910013


class OrderJournal:
    def __init__(self, path, database_url=None):
        self.database_url = database_url
        self.backend = "postgres" if database_url else "sqlite"
        self.persistent = bool(database_url)
        self._connection = None
        self._worker_connection = None
        self._transaction = None
        self._closed = False
        self._worker_owned = False
        self.worker_epoch = 0 if database_url else 1
        if database_url:
            from psycopg.conninfo import conninfo_to_dict
            config = conninfo_to_dict(database_url)
            # Session locks need a direct or session-pooler connection.
            if config.get("port") == "6543":
                raise ValueError("Use the Postgres direct connection or session pooler on port 5432")
            host = config.get("host", "")
            local = host in {"localhost", "127.0.0.1", "::1"}
            if not local and config.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
                raise ValueError("Remote order storage requires an encrypted Postgres connection")
            self._connect()
            # Schema is provisioned by a migration, never by the runtime role.
            try:
                self.execute("SELECT body FROM jobs LIMIT 0")
            except BaseException:
                self.close()
                raise
        else:
            if path != ":memory:":
                parent = Path(path).resolve().parent
                parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._connection = sqlite3.connect(path, check_same_thread=False, timeout=10)
            self._connection.row_factory = sqlite3.Row
            if path != ":memory:":
                import os
                os.chmod(path, 0o600)
            self._connection.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    key TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                    state TEXT NOT NULL, due REAL NOT NULL, updated REAL NOT NULL, body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state, due);
                CREATE TABLE IF NOT EXISTS messages (
                    chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, key TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    identity TEXT PRIMARY KEY, state TEXT NOT NULL, result TEXT, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sender_preferences (
                    chat_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                    profile_id TEXT NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY (chat_id, owner_id)
                );
                CREATE TABLE IF NOT EXISTS app_sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL CHECK(user_id>0),
                    csrf_hash TEXT NOT NULL, created REAL NOT NULL,
                    expires REAL NOT NULL CHECK(expires>created), revoked REAL
                );
                CREATE INDEX IF NOT EXISTS app_sessions_expiry ON app_sessions(expires);
                CREATE TABLE IF NOT EXISTS app_auth_replays (
                    replay_hash TEXT PRIMARY KEY, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_pairings (
                    pairing_hash TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
                    user_id INTEGER, approved_chat_id INTEGER, created REAL NOT NULL,
                    expires REAL NOT NULL CHECK(expires>created), approved REAL, consumed REAL,
                    CHECK((approved IS NULL AND user_id IS NULL AND approved_chat_id IS NULL)
                          OR (approved IS NOT NULL AND user_id>0 AND approved_chat_id=user_id))
                );
                CREATE INDEX IF NOT EXISTS app_pairings_expiry ON app_pairings(expires);
                CREATE TABLE IF NOT EXISTS return_cases (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    order_key TEXT NOT NULL,
                    outbound_ttn TEXT NOT NULL,
                    sender_profile TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    warehouse_state TEXT NOT NULL,
                    finance_state TEXT NOT NULL,
                    reverse_ttn TEXT,
                    created REAL NOT NULL,
                    updated REAL NOT NULL,
                    UNIQUE(chat_id,owner_id,order_key)
                );
                CREATE INDEX IF NOT EXISTS return_cases_owner_updated
                    ON return_cases(chat_id,owner_id,updated DESC);
                CREATE TABLE IF NOT EXISTS return_events (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES return_cases(id),
                    actor_id INTEGER NOT NULL,
                    at REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS return_events_case_time
                    ON return_events(case_id,at,id);
            """)
            self._connection.commit()

    def _new_postgres_connection(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        connection_options = {}
        config = conninfo_to_dict(self.database_url)
        if config.get("sslmode") == "verify-full" and not config.get("sslrootcert"):
            host = config.get("host", "")
            if host.endswith(".pooler.supabase.com") or (host.startswith("db.") and host.endswith(".supabase.co")):
                # Supabase signs database/pooler certificates with its own CA.
                connection_options["sslrootcert"] = str(
                    Path(__file__).resolve().parent / "certs" / "supabase-prod-ca-2021.crt"
                )
            else:
                from requests.certs import where
                connection_options["sslrootcert"] = where()
        connection = psycopg.connect(
            self.database_url, autocommit=True, row_factory=dict_row,
            prepare_threshold=None, connect_timeout=10,
            application_name="safar_np_bot",
            keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
            **connection_options,
        )
        try:
            connection.execute(
                "SET search_path TO safar_orders, pg_catalog; "
                "SET synchronous_commit TO on; "
                "SET statement_timeout TO 15000; SET lock_timeout TO 10000"
            )
            return connection
        except BaseException:
            connection.close()
            raise

    def _connect(self):
        if self._closed:
            raise RuntimeError("Order journal is closed")
        if self._transaction is not None:
            # Never reconnect in the middle of a transaction or replay its writes.
            return
        if self._connection is None or self._connection.closed:
            self._connection = self._new_postgres_connection()

    def execute(self, query, parameters=()):
        if self._closed:
            raise RuntimeError("Order journal is closed")
        if self.backend == "postgres":
            self._connect()
            query = query.replace("?", "%s")
        return self._connection.execute(query, parameters)

    def __enter__(self):
        if self._closed:
            raise RuntimeError("Order journal is closed")
        if self.backend == "sqlite":
            self._connection.__enter__()
            return self
        self._connect()
        transaction = self._connection.transaction()
        transaction.__enter__()
        self._transaction = transaction
        try:
            # Serialize journal read/modify/write transactions across deployments.
            self.execute("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK,))
        except BaseException:
            import sys
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.backend == "sqlite":
            return self._connection.__exit__(exc_type, exc_value, traceback)
        transaction = self._transaction
        try:
            return transaction.__exit__(exc_type, exc_value, traceback)
        finally:
            self._transaction = None

    @property
    def pending_query(self):
        missing_notice = ("body::jsonb->>'notified'='false'" if self.backend == "postgres"
                          else "json_extract(body,'$.notified')=0")
        return ("SELECT body FROM jobs WHERE due<=? AND state!='processing' AND "
                f"(state='collecting' OR {missing_notice}) ORDER BY due LIMIT 1")

    def acquire_worker(self):
        """Only the session owning this lock may recover or process the queue."""
        if self._closed:
            raise RuntimeError("Order journal is closed")
        if self.backend == "sqlite":
            return True
        connection = self._worker_connection
        if connection is not None and not connection.closed:
            try:
                connection.execute("SELECT 1")
                if self._worker_owned:
                    return True
            except Exception:
                connection.close()
                connection = None
                self._worker_owned = False
        if connection is None or connection.closed:
            self._worker_owned = False
            connection = self._new_postgres_connection()
            self._worker_connection = connection
        try:
            acquired = connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired", (WORKER_LOCK,)
            ).fetchone()["acquired"]
            if acquired:
                self._worker_owned = True
                self.worker_epoch += 1
            return acquired
        except BaseException:
            connection.close()
            self._worker_connection = None
            self._worker_owned = False
            raise

    def ping(self):
        self.execute("SELECT 1").fetchone()
        return True

    def receipts_for_ttn(self, ttn):
        value = ("COALESCE(result::jsonb->'result'->>'ttn',result::jsonb->>'ttn')"
                 if self.backend == "postgres" else
                 "COALESCE(json_extract(result,'$.result.ttn'),json_extract(result,'$.ttn'))")
        return self.execute(f"SELECT * FROM receipts WHERE {value}=?", (ttn,)).fetchall()

    def jobs_for_ttn(self, ttn):
        value = ("body::jsonb->'result'->>'ttn'" if self.backend == "postgres"
                 else "json_extract(body,'$.result.ttn')")
        return self.execute(f"SELECT body FROM jobs WHERE {value}=?", (ttn,)).fetchall()

    def close(self):
        self._closed = True
        if self._worker_connection is not None:
            if self._worker_owned and not self._worker_connection.closed:
                try:
                    self._worker_connection.execute(
                        "SELECT pg_advisory_unlock(%s)", (WORKER_LOCK,)
                    )
                except Exception:
                    pass
            self._worker_owned = False
            self._worker_connection.close()
            self._worker_connection = None
        if self._connection is not None:
            self._connection.close()
