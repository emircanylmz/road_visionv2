"""Tespit karelerinin sınırlı ve asenkron kalıcı kaydı.

Bu modül torch/ultralytics bağımlılığı taşımaz. Engine yalnız ucuz kapı
kararını verir ve kabul edilen kareyi sınırlı kuyruğa bırakır; JPEG kodlama,
PostgreSQL yazımı ve depolama kotası recorder worker'ında gerçekleşir.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .config import MediaConfig
from .db import (
    MEDIA_ADVISORY_LOCK,
    default_connection_factory,
    ensure_schema,
    prune_media,
)
from .logbook import EventJournal, LogLevel, NullJournal


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes
    width: int
    height: int
    sha256: str
    mime: str = "image/jpeg"

    def __post_init__(self) -> None:
        if not self.data or self.width <= 0 or self.height <= 0:
            raise ValueError("EncodedImage veri ve boyutları pozitif olmalıdır.")
        expected = hashlib.sha256(self.data).hexdigest()
        if self.sha256 != expected:
            raise ValueError("EncodedImage sha256 değeri içerikle eşleşmiyor.")
        if not self.mime:
            raise ValueError("EncodedImage MIME türü boş olamaz.")

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class CaptureModel:
    model_id: str
    signature: Hashable
    object_count: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Bir fiziksel kare yakalaması ve onu tetikleyen modeller.

    Bir Snapshot iki blob'a (işlenmiş ham kare + ortak işaretli canvas)
    karşılık gelir. Çoklu modeller aynı ``capture_id`` altında tutulur.
    """

    capture_id: str
    timestamp: float
    run_id: int
    source_name: str
    source_kind: str
    frame_sequence: int
    is_reprocess: bool
    models: tuple[CaptureModel, ...]


class FrameEncoder:
    def __init__(self, *, max_edge: int = 1280, jpeg_quality: int = 80) -> None:
        if max_edge <= 0:
            raise ValueError("max_edge pozitif olmalıdır.")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality 1–100 aralığında olmalıdır.")
        self.max_edge = max_edge
        self.jpeg_quality = jpeg_quality

    def encode(self, frame: np.ndarray) -> EncodedImage:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("Kodlanacak kare boş bir numpy dizisi olamaz.")
        if frame.ndim not in (2, 3):
            raise ValueError("Kare 2 veya 3 boyutlu olmalıdır.")

        height, width = frame.shape[:2]
        longest = max(width, height)
        encoded_frame = frame
        if longest > self.max_edge:
            scale = self.max_edge / float(longest)
            resized_width = max(1, round(width * scale))
            resized_height = max(1, round(height * scale))
            encoded_frame = cv2.resize(
                frame,
                (resized_width, resized_height),
                interpolation=cv2.INTER_AREA,
            )
        encoded_frame = np.ascontiguousarray(encoded_frame)
        ok, buffer = cv2.imencode(
            ".jpg",
            encoded_frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
        )
        if not ok:
            raise RuntimeError("OpenCV JPEG kodlaması başarısız oldu.")
        data = buffer.tobytes()
        encoded_height, encoded_width = encoded_frame.shape[:2]
        return EncodedImage(
            data=data,
            width=encoded_width,
            height=encoded_height,
            sha256=hashlib.sha256(data).hexdigest(),
        )


def snapshot_signature(
    objects: Sequence[Any],
    object_count: int,
    frame_shape: Sequence[int],
    *,
    spatial_buckets: int = 20,
    area_buckets: int = 50,
) -> Hashable:
    """Günlük imzasından daha zengin, titreşime toleranslı medya imzası.

    Sınıf kompozisyonuna ek olarak kutuların normalize konum/boyutları ve
    semantic alan oranı kuantize edilir. Böylece aynı sayıda nesne hareket
    ettiğinde yeni görüntü alınır; birkaç piksellik titreşim kayıt fırtınası
    üretmez. Nesne çıkarımı yoksa toplam sayı geriye uyumlu fallback'tir.
    """

    if not objects:
        return ("spatial-v1", ("count", int(object_count)))
    height = max(1, int(frame_shape[0]))
    width = max(1, int(frame_shape[1]))
    items: list[tuple[Any, ...]] = []
    for item in objects:
        class_name = str(getattr(item, "class_name", "?"))
        bbox = getattr(item, "bbox", None)
        area_ratio = getattr(item, "area_ratio", None)
        if bbox is not None and len(bbox) == 4:
            x1, y1, x2, y2 = (float(value) for value in bbox)
            quantized = (
                round(max(0.0, min(1.0, x1 / width)) * spatial_buckets),
                round(max(0.0, min(1.0, y1 / height)) * spatial_buckets),
                round(max(0.0, min(1.0, x2 / width)) * spatial_buckets),
                round(max(0.0, min(1.0, y2 / height)) * spatial_buckets),
            )
            items.append((class_name, "bbox", *quantized))
        elif area_ratio is not None:
            area = round(max(0.0, min(1.0, float(area_ratio))) * area_buckets)
            items.append((class_name, "area", area))
        else:
            items.append((class_name, "present"))
    return ("spatial-v1", tuple(sorted(items)))


@dataclass(frozen=True, slots=True)
class GateObservation:
    model_id: str
    signature: Hashable
    object_count: int


@dataclass(frozen=True, slots=True)
class GateDecision:
    models: tuple[GateObservation, ...] = ()
    reason: str = "empty"
    warning: str | None = None

    @property
    def capture(self) -> bool:
        return bool(self.models)


@dataclass(slots=True)
class _GateState:
    last_captured_signature: Hashable
    last_capture_monotonic: float


class SnapshotGate:
    """Kare-grubu kotası ve model-başına değişim kapısı.

    ``evaluate`` durumu değiştirmez. Yalnız recorder kuyruğu işi kabul
    ettiğinde ``commit`` çağrılır; böylece kuyruk taşması aynı yeni sahnenin
    daha sonra tekrar denenmesini engellemez.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float = 2.0,
        max_captures_per_run: int = 200,
        max_captures_per_hour: int = 500,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds negatif olamaz.")
        if max_captures_per_run <= 0 or max_captures_per_hour <= 0:
            raise ValueError("Yakalama tavanları pozitif olmalıdır.")
        self.min_interval_seconds = min_interval_seconds
        self.max_captures_per_run = max_captures_per_run
        self.max_captures_per_hour = max_captures_per_hour
        self._clock = clock
        self._states: dict[tuple[int, str], _GateState] = {}
        self._run_counts: dict[int, int] = {}
        self._hourly: deque[float] = deque()
        self._run_warned: set[int] = set()
        self._hour_warning_active = False
        self._lock = threading.RLock()

    def evaluate(
        self,
        run_id: int,
        observations: Sequence[GateObservation],
        *,
        is_static: bool,
        now: float | None = None,
    ) -> GateDecision:
        current = self._clock() if now is None else now
        with self._lock:
            self._prune_hour(current)
            eligible: list[GateObservation] = []
            saw_objects = False
            saw_interval = False
            saw_same = False
            for observation in observations:
                if observation.object_count <= 0:
                    continue
                saw_objects = True
                state = self._states.get((run_id, observation.model_id))
                if state is not None and state.last_captured_signature == observation.signature:
                    saw_same = True
                    continue
                if (
                    not is_static
                    and state is not None
                    and current - state.last_capture_monotonic < self.min_interval_seconds
                ):
                    # last_captured_signature değişmez: yeni durum aralık
                    # dolduğunda hâlâ görünüyorsa yeniden değerlendirilir.
                    saw_interval = True
                    continue
                eligible.append(observation)

            if not eligible:
                if not saw_objects:
                    return GateDecision(reason="empty")
                if saw_interval:
                    return GateDecision(reason="min_interval")
                if saw_same:
                    return GateDecision(reason="same_signature")
                return GateDecision(reason="not_eligible")

            if self._run_counts.get(run_id, 0) >= self.max_captures_per_run:
                warning = None
                if run_id not in self._run_warned:
                    self._run_warned.add(run_id)
                    warning = "run_limit"
                return GateDecision(reason="run_limit", warning=warning)

            if len(self._hourly) >= self.max_captures_per_hour:
                warning = None
                if not self._hour_warning_active:
                    self._hour_warning_active = True
                    warning = "hour_limit"
                return GateDecision(reason="hour_limit", warning=warning)

            self._hour_warning_active = False
            return GateDecision(models=tuple(eligible), reason="changed")

    def commit(
        self,
        run_id: int,
        observations: Sequence[GateObservation],
        *,
        now: float | None = None,
    ) -> None:
        if not observations:
            return
        current = self._clock() if now is None else now
        with self._lock:
            self._prune_hour(current)
            for observation in observations:
                self._states[(run_id, observation.model_id)] = _GateState(
                    last_captured_signature=observation.signature,
                    last_capture_monotonic=current,
                )
            # Fiziksel kota model sayısını değil, tek JPEG çiftini sayar.
            self._run_counts[run_id] = self._run_counts.get(run_id, 0) + 1
            self._hourly.append(current)

    def finish_run(self, run_id: int) -> None:
        with self._lock:
            self._run_counts.pop(run_id, None)
            self._run_warned.discard(run_id)
            for key in [key for key in self._states if key[0] == run_id]:
                self._states.pop(key, None)

    def _prune_hour(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._hourly and self._hourly[0] <= cutoff:
            self._hourly.popleft()
        if len(self._hourly) < self.max_captures_per_hour:
            self._hour_warning_active = False


class MediaSink(ABC):
    @abstractmethod
    def prepare_sink(self) -> None: ...

    @abstractmethod
    def store(
        self,
        original: EncodedImage,
        annotated: EncodedImage,
        snapshot: Snapshot,
    ) -> None: ...

    @abstractmethod
    def release_sink(self) -> None: ...


class DbMediaSink(MediaSink):
    """İki JPEG blob'u ve kare/model ilişkisini idempotent transaction'la yazar."""

    def __init__(
        self,
        dsn: str,
        *,
        retention_days: int = 30,
        max_total_mb: int = 2048,
        connection_factory: Callable[[str], Any] = default_connection_factory,
        max_attempts: int = 2,
        retry_delay: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.dsn = dsn
        self.retention_days = retention_days
        self.max_total_bytes = max_total_mb * 1024 * 1024
        self._connection_factory = connection_factory
        self._max_attempts = max(1, max_attempts)
        self._retry_delay = max(0.0, retry_delay)
        self._sleeper = sleeper
        self._conn: Any = None

    def prepare_sink(self) -> None:
        # Bağlantı yalnız recorder worker'ında kurulur; UI/engine açılışı ağ
        # yüzünden beklemez.
        return None

    def store(
        self,
        original: EncodedImage,
        annotated: EncodedImage,
        snapshot: Snapshot,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                conn = self._ensure_connection()
                self._store_once(conn, original, annotated, snapshot)
                # Manuel script'e ek olarak her başarılı yazımdan sonra
                # kalıcı süre/boyut kotasını uygula.
                prune_media(
                    conn,
                    retention_days=self.retention_days,
                    max_total_bytes=self.max_total_bytes,
                )
                return
            except Exception as exc:
                last_error = exc
                self._discard_connection()
                if attempt + 1 < self._max_attempts and self._retry_delay:
                    self._sleeper(self._retry_delay * (2**attempt))
        assert last_error is not None
        raise last_error

    def release_sink(self) -> None:
        self._discard_connection()

    def _ensure_connection(self) -> Any:
        if self._conn is None:
            conn = self._connection_factory(self.dsn)
            try:
                ensure_schema(conn)
            except Exception:
                try:
                    conn.close()
                finally:
                    raise
            self._conn = conn
        return self._conn

    @staticmethod
    def _insert_blob(cur: Any, image: EncodedImage) -> int:
        cur.execute(
            """
            INSERT INTO media_blobs
                (sha256, mime, width, height, byte_size, data)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (sha256) DO UPDATE SET sha256 = EXCLUDED.sha256
            RETURNING id
            """,
            (
                image.sha256,
                image.mime,
                image.width,
                image.height,
                image.byte_size,
                image.data,
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Blob kimliği alınamadı.")
        return int(row[0])

    def _store_once(
        self,
        conn: Any,
        original: EncodedImage,
        annotated: EncodedImage,
        snapshot: Snapshot,
    ) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (MEDIA_ADVISORY_LOCK,))
                original_id = self._insert_blob(cur, original)
                annotated_id = (
                    original_id
                    if annotated.sha256 == original.sha256
                    else self._insert_blob(cur, annotated)
                )
                cur.execute(
                    """
                    INSERT INTO media_captures
                        (capture_id, ts, run_id, source_name, source_kind,
                         frame_sequence, is_reprocess, original_media_id,
                         annotated_media_id)
                    VALUES
                        (%s, to_timestamp(%s), %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (capture_id) DO NOTHING
                    """,
                    (
                        snapshot.capture_id,
                        snapshot.timestamp,
                        snapshot.run_id,
                        snapshot.source_name,
                        snapshot.source_kind,
                        snapshot.frame_sequence,
                        snapshot.is_reprocess,
                        original_id,
                        annotated_id,
                    ),
                )
                for model in snapshot.models:
                    cur.execute(
                        """
                        INSERT INTO media_capture_models
                            (capture_id, model_id, signature, object_count)
                        VALUES (%s, %s, %s::jsonb, %s)
                        ON CONFLICT (capture_id, model_id) DO NOTHING
                        """,
                        (
                            snapshot.capture_id,
                            model.model_id,
                            json.dumps(model.signature, ensure_ascii=False, default=str),
                            model.object_count,
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _discard_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass


@dataclass(slots=True)
class _CaptureJob:
    raw_frame: np.ndarray
    annotated_frame: np.ndarray
    snapshot: Snapshot
    memory_bytes: int


class MediaRecorder:
    """Sınırlı bellekli tek-worker JPEG ve sink boru hattı."""

    enabled = True

    def __init__(
        self,
        sink: MediaSink,
        *,
        encoder: FrameEncoder | None = None,
        gate: SnapshotGate | None = None,
        journal: EventJournal | None = None,
        queue_size: int = 8,
        queue_max_bytes: int = 256 * 1024 * 1024,
        shutdown_timeout: float = 10.0,
    ) -> None:
        if queue_size <= 0 or queue_max_bytes <= 0:
            raise ValueError("Recorder kuyruk sınırları pozitif olmalıdır.")
        self.gate = gate or SnapshotGate()
        self._sink = sink
        self._encoder = encoder or FrameEncoder()
        self._journal = journal or NullJournal()
        self._queue: queue.Queue[_CaptureJob] = queue.Queue(maxsize=queue_size)
        self._queue_max_bytes = queue_max_bytes
        self._shutdown_timeout = shutdown_timeout
        self._lock = threading.RLock()
        self._pending_bytes = 0
        self._dropped_unreported = 0
        self._prepared = False
        self._accepting = False
        self._released = False
        self._failure_active = False
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def prepare_recorder(self) -> None:
        with self._lock:
            if self._prepared or self._released:
                return
            self._sink.prepare_sink()
            self._stop.clear()
            self._accepting = True
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="roadvision-media",
                daemon=True,
            )
            self._prepared = True
            self._worker.start()

    def submit(
        self,
        raw_frame: np.ndarray,
        annotated_frame: np.ndarray,
        snapshot: Snapshot,
    ) -> bool:
        """İşi beklemeden kuyruğa bırakır; hiçbir hata engine'e kaçmaz."""

        if not snapshot.models:
            return False
        try:
            required_bytes = int(raw_frame.nbytes + annotated_frame.nbytes)
            with self._lock:
                if (
                    not self._prepared
                    or not self._accepting
                    or self._released
                    or self._queue.full()
                    or self._pending_bytes + required_bytes > self._queue_max_bytes
                ):
                    self._dropped_unreported += 1
                    return False
                # Kopyalama sürerken başka submit'lerin bütçeyi aşmaması için
                # önce rezervasyon yap. Asenkron sınırda sahiplik bize geçer.
                self._pending_bytes += required_bytes
            try:
                raw_owned = np.ascontiguousarray(raw_frame).copy()
                annotated_owned = np.ascontiguousarray(annotated_frame).copy()
                job = _CaptureJob(
                    raw_frame=raw_owned,
                    annotated_frame=annotated_owned,
                    snapshot=snapshot,
                    memory_bytes=required_bytes,
                )
                self._queue.put_nowait(job)
                return True
            except Exception:
                with self._lock:
                    self._pending_bytes -= required_bytes
                    self._dropped_unreported += 1
                return False
        except Exception:
            with self._lock:
                self._dropped_unreported += 1
            return False

    def release_recorder(self, timeout: float | None = None) -> bool:
        with self._lock:
            if self._released:
                return self._worker is None or not self._worker.is_alive()
            self._accepting = False
            self._stop.set()
            worker = self._worker
            wait_for = self._shutdown_timeout if timeout is None else timeout
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=wait_for)
        alive = worker is not None and worker.is_alive()
        if alive:
            self._report(
                LogLevel.WARNING,
                "Medya kuyruğu kapanış süresinde boşaltılamadı.",
                media_shutdown_timeout=wait_for,
            )
            return False
        self._report_dropped()
        try:
            self._sink.release_sink()
        except Exception as exc:
            self._report(
                LogLevel.WARNING,
                "Medya sink'i kapatılamadı.",
                media_error=str(exc),
            )
        with self._lock:
            self._released = True
            self._worker = None
        return True

    @property
    def pending_bytes(self) -> int:
        with self._lock:
            return self._pending_bytes

    def _worker_loop(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.10)
            except queue.Empty:
                continue
            try:
                original = self._encoder.encode(job.raw_frame)
                annotated = self._encoder.encode(job.annotated_frame)
                self._sink.store(original, annotated, job.snapshot)
                self._failure_active = False
                self._report_dropped()
            except Exception as exc:
                if not self._failure_active:
                    self._failure_active = True
                    self._report(
                        LogLevel.WARNING,
                        "Tespit görüntüsü kaydedilemedi; uygulama devam ediyor.",
                        media_error=str(exc),
                        capture_id=job.snapshot.capture_id,
                    )
            finally:
                with self._lock:
                    self._pending_bytes = max(0, self._pending_bytes - job.memory_bytes)
                self._queue.task_done()

    def _report_dropped(self) -> None:
        with self._lock:
            dropped, self._dropped_unreported = self._dropped_unreported, 0
        if dropped:
            self._report(
                LogLevel.WARNING,
                "Medya kuyruğu/bellek sınırı nedeniyle görüntü yakalamaları düşürüldü.",
                media_dropped=dropped,
            )

    def _report(self, level: LogLevel, message: str, **payload: Any) -> None:
        try:
            self._journal.app_event(level, message, **payload)
        except Exception:
            pass


class NullRecorder:
    enabled = False

    def __init__(self) -> None:
        self.gate = SnapshotGate()

    def prepare_recorder(self) -> None:
        return None

    def submit(
        self,
        raw_frame: np.ndarray,
        annotated_frame: np.ndarray,
        snapshot: Snapshot,
    ) -> bool:
        return False

    def release_recorder(self, timeout: float | None = None) -> bool:
        return True


def create_default_recorder(
    journal: EventJournal | None = None,
    *,
    config: MediaConfig | None = None,
    environ: Mapping[str, str] | None = None,
) -> MediaRecorder | NullRecorder:
    """Ortam ayarlarından DB recorder kurar; eksik/hatalı ayarda no-op döner."""

    target_journal = journal or NullJournal()
    source = os.environ if environ is None else environ
    try:
        settings = config or MediaConfig.from_env(dict(source))
    except ValueError as exc:
        target_journal.app_event(
            LogLevel.WARNING,
            "Medya ayarları geçersiz; görüntü kaydı kapatıldı.",
            media_error=str(exc),
        )
        return NullRecorder()
    dsn = source.get("ROADVISION_DB_DSN", "").strip()
    if settings.backend == "off" or not dsn:
        return NullRecorder()
    gate = SnapshotGate(
        min_interval_seconds=settings.min_interval_s,
        max_captures_per_run=settings.max_per_run,
        max_captures_per_hour=settings.max_per_hour,
    )
    sink = DbMediaSink(
        dsn,
        retention_days=settings.retention_days,
        max_total_mb=settings.max_total_mb,
    )
    return MediaRecorder(
        sink,
        encoder=FrameEncoder(
            max_edge=settings.max_edge,
            jpeg_quality=settings.jpeg_quality,
        ),
        gate=gate,
        journal=target_journal,
        queue_size=settings.queue_size,
        queue_max_bytes=settings.queue_max_mb * 1024 * 1024,
        shutdown_timeout=settings.shutdown_timeout_s,
    )
