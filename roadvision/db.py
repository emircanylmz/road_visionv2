"""RoadVision günlüklerinin PostgreSQL'e kalıcı yazımı.

`PostgresSink`, `LogSink` sözleşmesini uygular ve `EventJournal`'a
`add_sink` ile takılır; günlük üreten hiçbir kod değişmez. İki kural:

1. Journal'ın yazıcı thread'i asla ağ beklememeli: `write_record` yalnız
   sınırlı bir iç kuyruğa bırakır. Ayrı bir "flusher" thread bağlantıyı
   kurar, kopunca üstel geri çekilmeyle yeniden dener ve kayıtları toplu
   (batch) yazar. Veritabanı kapalıyken uygulama etkilenmez; JSONL sink'i
   zaten dayanıklı kayıttır ve sonradan `scripts/backfill_jsonl.py` ile
   içeri alınabilir.
2. Kayıt düzeni: TÜM kayıtlar olduğu gibi `log_records` tablosuna gider;
   tespit kayıtları ek olarak yapılandırılmış `detection_events` +
   `detected_objects` tablolarına açılır. `detected_objects` satırı tür
   (`class_name`), doğruluk (`confidence`) ve zaman (`ts`) taşır — türe
   göre sorgular `class_name` indeksinden çalışır.

`psycopg` (v3) tembel import edilir; kurulu değilse modül yine import
edilebilir. Bağlantı ve yazma hataları uygulamayı durdurmadan flusher
thread'inden bir kez raporlanır. Testler `connection_factory` enjeksiyonu
ile gerçek veritabanı olmadan çalışır.
"""

from __future__ import annotations

import hashlib
import json
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .logbook import LogCategory, LogLevel, LogRecord, LogSink

SCHEMA_VERSION = 2
SCHEMA_ADVISORY_LOCK = 1_385_428_466
MEDIA_ADVISORY_LOCK = 1_385_428_467

SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS log_records (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    run_id INTEGER,
    model_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingest_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_log_records_ts ON log_records (ts);
CREATE INDEX IF NOT EXISTS idx_log_records_level_ts ON log_records (level, ts);
CREATE INDEX IF NOT EXISTS idx_log_records_category_ts ON log_records (category, ts);

CREATE TABLE IF NOT EXISTS detection_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    model_id TEXT NOT NULL,
    object_count INTEGER NOT NULL,
    elapsed_ms REAL,
    dedup TEXT,
    repeated_frames INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingest_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_detection_events_model_ts ON detection_events (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detection_events_run ON detection_events (run_id);

CREATE TABLE IF NOT EXISTS detected_objects (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES detection_events(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    model_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    confidence REAL,
    bbox REAL[],
    area_ratio REAL
);
CREATE INDEX IF NOT EXISTS idx_detected_objects_class_ts ON detected_objects (class_name, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_model_ts ON detected_objects (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_event ON detected_objects (event_id);
"""

SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS media_blobs (
    id BIGSERIAL PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    mime TEXT NOT NULL DEFAULT 'image/jpeg',
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_captures (
    capture_id UUID PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    source_name TEXT,
    source_kind TEXT,
    frame_sequence INTEGER,
    is_reprocess BOOLEAN NOT NULL DEFAULT FALSE,
    original_media_id BIGINT NOT NULL REFERENCES media_blobs(id) ON DELETE RESTRICT,
    annotated_media_id BIGINT NOT NULL REFERENCES media_blobs(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_media_captures_ts
    ON media_captures (ts);
CREATE INDEX IF NOT EXISTS idx_media_captures_original
    ON media_captures (original_media_id);
CREATE INDEX IF NOT EXISTS idx_media_captures_annotated
    ON media_captures (annotated_media_id);

CREATE TABLE IF NOT EXISTS media_capture_models (
    capture_id UUID NOT NULL REFERENCES media_captures(capture_id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    signature JSONB,
    object_count INTEGER NOT NULL CHECK (object_count > 0),
    PRIMARY KEY (capture_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_media_capture_models_model
    ON media_capture_models (model_id, capture_id);

ALTER TABLE detection_events
    ADD COLUMN IF NOT EXISTS capture_id UUID;
CREATE INDEX IF NOT EXISTS idx_detection_events_capture
    ON detection_events (capture_id);
"""

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, SCHEMA_V1_SQL),
    (2, SCHEMA_V2_SQL),
)

# En güncel şemayı tek seferde kurmak isteyen araçlarla geriye uyumluluk.
SCHEMA_SQL = "\n".join(sql for _, sql in MIGRATIONS)


def default_connection_factory(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - ortam bağımlı
        raise RuntimeError(
            "PostgreSQL kaydı için 'psycopg[binary]' kurulmalıdır: "
            "pip install 'psycopg[binary]>=3.1,<4'"
        ) from exc
    # Kapanışın ağ arızasında sonsuza kadar beklememesi için DSN'de daha kısa
    # bir değer verilmediyse libpq bağlantı kurma süresini sınırla.
    return psycopg.connect(dsn, connect_timeout=3)


def ensure_schema(conn: Any) -> None:
    """Eksik migration'ları tek transaction içinde, sırayla uygular.

    PostgreSQL advisory transaction lock aynı veritabanına eşzamanlı başlayan
    iki RoadVision sürecinin migration yarışına girmesini engeller. Her sürüm
    kendi satırıyla kaydedilir; böylece v1 kurulumlar v2'ye veri kaybetmeden
    yükseltilir ve tekrar çağrı no-op olur.
    """

    try:
        with conn.cursor() as cur:
            # Kilit schema_info tablosundan bağımsızdır; temiz veritabanında
            # eşzamanlı CREATE TABLE katalog yarışını da önlemek için önce al.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_ADVISORY_LOCK,))
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_info")
            row = cur.fetchone()
            current_version = int(row[0]) if row is not None else 0
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    "Veritabanı şeması bu RoadVision sürümünden daha yeni: "
                    f"{current_version} > {SCHEMA_VERSION}"
                )
            for version, sql in MIGRATIONS:
                if version <= current_version:
                    continue
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO schema_info (version)
                    VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (version,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@dataclass(frozen=True, slots=True)
class MediaPruneResult:
    captures_deleted: int
    blobs_deleted: int
    bytes_before: int
    bytes_after: int


@dataclass(frozen=True, slots=True)
class CaptureMedia:
    data: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CaptureBundle:
    capture_id: str
    ts: datetime
    source_name: str | None
    source_kind: str | None
    frame_sequence: int | None
    is_reprocess: bool
    original: CaptureMedia
    annotated: CaptureMedia
    models: tuple[tuple[str, int, Any], ...]


def fetch_capture(conn: Any, capture_id: str) -> CaptureBundle | None:
    """Bir capture'ın iki görüntüsünü ve model özetlerini tek PK bakışıyla okur.

    Transaction yaşam döngüsü çağırana aittir. Bu işlev commit/rollback
    yapmadığından sahte bağlantıyla ve salt-okunur worker bağlantısıyla
    kullanılabilir.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.ts, c.source_name, c.source_kind, c.frame_sequence,
                   c.is_reprocess,
                   o.data, o.width, o.height,
                   a.data, a.width, a.height
            FROM media_captures c
            JOIN media_blobs o ON o.id = c.original_media_id
            JOIN media_blobs a ON a.id = c.annotated_media_id
            WHERE c.capture_id = %s::uuid
            """,
            (str(capture_id),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            """
            SELECT model_id, object_count, signature
            FROM media_capture_models
            WHERE capture_id = %s::uuid
            ORDER BY model_id
            """,
            (str(capture_id),),
        )
        models = tuple(
            (str(model_id), int(object_count), signature)
            for model_id, object_count, signature in cur.fetchall()
        )

    return CaptureBundle(
        capture_id=str(capture_id),
        ts=row[0],
        source_name=row[1],
        source_kind=row[2],
        frame_sequence=row[3],
        is_reprocess=bool(row[4]),
        original=CaptureMedia(data=bytes(row[5]), width=int(row[6]), height=int(row[7])),
        annotated=CaptureMedia(data=bytes(row[8]), width=int(row[9]), height=int(row[10])),
        models=models,
    )


def prune_media(
    conn: Any,
    *,
    retention_days: int,
    max_total_bytes: int,
    commit: bool = True,
) -> MediaPruneResult:
    """Eski yakalamaları ve referanssız blob'ları kare grubu halinde temizler.

    Kota `media_blobs.byte_size` toplamıdır; PostgreSQL tablo/WAL/indeks
    overhead'ini temsil etmez. Capture satırı bütün olarak silindiğinden ham
    ve işaretli çiftlerden biri tek başına kalmaz. Paylaşılan blob ancak iki
    FK'den de referansı kalmadığında silinir.
    """

    if retention_days <= 0:
        raise ValueError("retention_days pozitif olmalıdır.")
    if max_total_bytes <= 0:
        raise ValueError("max_total_bytes pozitif olmalıdır.")

    captures_deleted = 0
    blobs_deleted = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (MEDIA_ADVISORY_LOCK,))
            cur.execute("SELECT COALESCE(SUM(byte_size), 0) FROM media_blobs")
            row = cur.fetchone()
            bytes_before = int(row[0]) if row is not None else 0

            cur.execute(
                """
                DELETE FROM media_captures
                WHERE ts < now() - (%s * interval '1 day')
                """,
                (retention_days,),
            )
            captures_deleted += max(0, cur.rowcount)

            cur.execute(
                """
                DELETE FROM media_blobs b
                WHERE NOT EXISTS (
                    SELECT 1 FROM media_captures c
                    WHERE c.original_media_id = b.id OR c.annotated_media_id = b.id
                )
                """
            )
            blobs_deleted += max(0, cur.rowcount)

            cur.execute("SELECT COALESCE(SUM(byte_size), 0) FROM media_blobs")
            row = cur.fetchone()
            total_bytes = int(row[0]) if row is not None else 0

            # Normal akışta sınır yalnız birkaç capture aşılır. Tek tek grup
            # silmek, paylaşılan blob'ların gerçek boşalan boyutunu her turda
            # yeniden ölçerek kotanın altına gereksiz fazla inmeyi engeller.
            while total_bytes > max_total_bytes:
                cur.execute(
                    """
                    DELETE FROM media_captures
                    WHERE capture_id = (
                        SELECT capture_id
                        FROM media_captures
                        ORDER BY ts, capture_id
                        LIMIT 1
                    )
                    RETURNING capture_id
                    """
                )
                if cur.fetchone() is None:
                    break
                captures_deleted += 1
                cur.execute(
                    """
                    DELETE FROM media_blobs b
                    WHERE NOT EXISTS (
                        SELECT 1 FROM media_captures c
                        WHERE c.original_media_id = b.id OR c.annotated_media_id = b.id
                    )
                    """
                )
                blobs_deleted += max(0, cur.rowcount)
                cur.execute("SELECT COALESCE(SUM(byte_size), 0) FROM media_blobs")
                row = cur.fetchone()
                total_bytes = int(row[0]) if row is not None else 0

        if commit:
            conn.commit()
        return MediaPruneResult(
            captures_deleted=captures_deleted,
            blobs_deleted=blobs_deleted,
            bytes_before=bytes_before,
            bytes_after=total_bytes,
        )
    except Exception:
        conn.rollback()
        raise


def write_batch(conn: Any, records: Sequence[tuple[LogRecord, str | None]]) -> int:
    """Bir kayıt grubunu tek transaction içinde yazar.

    Her öğe `(record, ingest_key)` çiftidir. Canlı akış anahtarı LogRecord
    oluşturulurken bir kez üretilir ve retry boyunca korunur; eski JSONL
    kayıtlarının backfill anahtarı dosya+satırdan türetilir.
    `ON CONFLICT DO NOTHING` her iki akışı da idempotent yapar. Dönüş
    değeri gerçekten eklenen `log_records` satırı sayısıdır.
    """
    inserted = 0
    with conn.cursor() as cur:
        for record, ingest_key in records:
            cur.execute(
                """
                INSERT INTO log_records
                    (ts, level, category, message, run_id, model_id, payload, ingest_key)
                VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (ingest_key) DO NOTHING
                RETURNING id
                """,
                (
                    record.timestamp,
                    record.level.value,
                    record.category.value,
                    record.message,
                    record.run_id,
                    record.model_id,
                    json.dumps(record.payload, ensure_ascii=False, default=str),
                    ingest_key,
                ),
            )
            if cur.fetchone() is None:
                continue
            inserted += 1
            if record.category == LogCategory.DETECTION:
                _write_detection(cur, record, ingest_key)
    conn.commit()
    return inserted


def _write_detection(cur: Any, record: LogRecord, ingest_key: str | None) -> None:
    payload = record.payload
    capture_id = payload.get("capture_id")
    if capture_id is not None:
        try:
            capture_id = str(uuid.UUID(str(capture_id)))
        except (ValueError, TypeError, AttributeError):
            # Bozuk/eski bir JSONL satırı bütün batch'i zehirlemesin.
            capture_id = None
    cur.execute(
        """
        INSERT INTO detection_events
            (ts, run_id, model_id, object_count, elapsed_ms, dedup,
             repeated_frames, capture_id, payload, ingest_key)
        VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (ingest_key) DO NOTHING
        RETURNING id
        """,
        (
            record.timestamp,
            record.run_id,
            record.model_id or "?",
            int(payload.get("object_count", 0)),
            payload.get("elapsed_ms"),
            payload.get("dedup") or payload.get("closed_by"),
            payload.get("repeated_frames") or payload.get("frames"),
            capture_id,
            json.dumps(payload, ensure_ascii=False, default=str),
            ingest_key,
        ),
    )
    row = cur.fetchone()
    if row is None:  # ingest_key çakıştı: olay ve nesneleri zaten yazılmış
        return
    event_id = row[0]
    objects: Iterable[dict[str, Any]] = payload.get("objects") or ()
    params = [
        (
            event_id,
            record.timestamp,
            record.run_id,
            record.model_id or "?",
            str(item.get("class", "?")),
            item.get("confidence"),
            list(item["bbox"]) if item.get("bbox") is not None else None,
            item.get("area_ratio"),
        )
        for item in objects
    ]
    if params:
        cur.executemany(
            """
            INSERT INTO detected_objects
                (event_id, ts, run_id, model_id, class_name, confidence, bbox, area_ratio)
            VALUES (%s, to_timestamp(%s), %s, %s, %s, %s, %s, %s)
            """,
            params,
        )


def ingest_key_for(source: str, line_no: int, line: str) -> str:
    return hashlib.sha1(f"{source}:{line_no}:{line}".encode("utf-8")).hexdigest()


def record_from_json_line(line: str) -> LogRecord | None:
    """JSONL satırını LogRecord'a çevirir (backfill için)."""
    try:
        data = json.loads(line)
        from datetime import datetime

        timestamp = datetime.fromisoformat(data["time"]).timestamp()
        return LogRecord(
            timestamp=timestamp,
            level=LogLevel(data["level"]),
            category=LogCategory(data["category"]),
            message=data["message"],
            run_id=data.get("run_id"),
            model_id=data.get("model_id"),
            payload=data.get("payload") or {},
            ingest_key=data.get("ingest_key"),
        )
    except (ValueError, KeyError, TypeError):
        return None


def _stderr_error_reporter(message: str) -> None:
    print(f"[WARNING] {message}", file=sys.stderr)


_PendingRecord = tuple[LogRecord, str]


class PostgresSink(LogSink):
    """Kayıtları PostgreSQL'e toplu ve asenkron yazan sink.

    `write_record` (journal yazıcı thread'i) yalnız iç kuyruğa bırakır;
    kuyruk dolarsa EN ESKİ kayıt atılır ve atılan sayısı bir sonraki başarılı
    grupla `db_dropped` app kaydı olarak veritabanına düşülür. Flusher thread
    bağlantı kopunca 1→30 sn üstel geri çekilme ile yeniden dener; başarısız
    grup kuyruğun önüne geri konur (veri kaybı yalnız kuyruk taşarsa olur).
    """

    def __init__(
        self,
        dsn: str,
        connection_factory: Callable[[str], Any] = default_connection_factory,
        batch_size: int = 100,
        flush_interval: float = 2.0,
        queue_size: int = 5000,
        min_level: LogLevel = LogLevel.DEBUG,
        clock: Callable[[], float] = time.time,
        error_reporter: Callable[[str], None] = _stderr_error_reporter,
    ) -> None:
        self.dsn = dsn
        self.min_level = min_level
        self._connection_factory = connection_factory
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._clock = clock
        self._error_reporter = error_reporter
        self._queue: queue.Queue[_PendingRecord] = queue.Queue(maxsize=queue_size)
        self._retry: list[_PendingRecord] = []  # başarısız grup, öncelikli
        self._conn: Any = None
        self._flusher: threading.Thread | None = None
        self._stop = threading.Event()
        self._flush_now = threading.Event()
        self._backoff = 1.0
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._failure_reported = False

    # -- LogSink sözleşmesi ---------------------------------------------------

    def prepare_sink(self) -> None:
        self._stop.clear()
        self._failure_reported = False
        self._flusher = threading.Thread(
            target=self._flusher_loop, name="roadvision-pg", daemon=True
        )
        self._flusher.start()

    def write_record(self, record: LogRecord) -> None:
        pending = self._pending_record(record)
        while True:
            try:
                self._queue.put_nowait(pending)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    with self._dropped_lock:
                        self._dropped += 1
                except queue.Empty:
                    continue

    def flush(self) -> None:
        self._flush_now.set()

    def release_sink(self) -> None:
        if self._flusher is None:
            return
        self._stop.set()
        self._flush_now.set()
        self._flusher.join(timeout=10.0)
        self._flusher = None
        self._close_connection()

    @property
    def dropped_records(self) -> int:
        with self._dropped_lock:
            return self._dropped

    # -- iç işleyiş ------------------------------------------------------------

    def _flusher_loop(self) -> None:
        while True:
            self._flush_now.wait(timeout=self._flush_interval)
            self._flush_now.clear()
            self._drain_once()
            if self._stop.is_set():
                self._drain_once()  # kapanışta son kalanlar
                return

    def _collect_batch(self) -> list[_PendingRecord]:
        batch = self._retry
        self._retry = []
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _drain_once(self) -> None:
        while True:
            batch = self._collect_batch()
            if not batch:
                return
            if not self._write(batch):
                return  # bağlantı yok; batch _retry'da bekliyor

    def _write(self, batch: list[_PendingRecord]) -> bool:
        conn = self._ensure_connection()
        if conn is None:
            self._retry = batch
            return False
        records = list(batch)
        dropped = self._take_dropped()
        if dropped:
            records.append(
                self._pending_record(
                    LogRecord(
                        timestamp=self._clock(),
                        level=LogLevel.WARNING,
                        category=LogCategory.APP,
                        message="Veritabanı kuyruğu taştı; eski kayıtlar atıldı.",
                        payload={"db_dropped": dropped},
                    )
                )
            )
        try:
            write_batch(conn, records)
            self._backoff = 1.0
            self._failure_reported = False
            return True
        except Exception as exc:
            self._report_failure("PostgreSQL batch yazımı başarısız", exc)
            self._safe_rollback(conn)
            self._close_connection()
            # `records`, varsa db_dropped uyarısını ve bütün idempotency
            # anahtarlarını içerir; belirsiz commit sonucu güvenle denenebilir.
            self._retry = records
            return False

    def _ensure_connection(self) -> Any:
        if self._conn is not None:
            return self._conn
        conn: Any = None
        try:
            conn = self._connection_factory(self.dsn)
            ensure_schema(conn)
            self._conn = conn
            self._backoff = 1.0
            return conn
        except Exception as exc:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            self._report_failure("PostgreSQL bağlantısı kurulamadı", exc)
            # Üstel geri çekilme: veritabanı kapalıyken CPU'yu meşgul etme.
            self._stop.wait(timeout=self._backoff)
            self._backoff = min(self._backoff * 2, 30.0)
            return None

    @staticmethod
    def _pending_record(record: LogRecord) -> _PendingRecord:
        return record, record.ingest_key or f"live:{uuid.uuid4().hex}"

    def _report_failure(self, message: str, exc: Exception) -> None:
        if self._failure_reported:
            return
        self._failure_reported = True
        detail = str(exc).strip()
        rendered = f"{message}: {detail}" if detail else message
        try:
            self._error_reporter(rendered)
        except Exception:
            # Hata raporlama yolu da günlük/inference hattını etkileyemez.
            pass

    def _safe_rollback(self, conn: Any) -> None:
        try:
            conn.rollback()
        except Exception:
            pass

    def _close_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _take_dropped(self) -> int:
        with self._dropped_lock:
            dropped, self._dropped = self._dropped, 0
            return dropped


def export_schema(path: str | Path) -> None:
    Path(path).write_text(SCHEMA_SQL, encoding="utf-8")
