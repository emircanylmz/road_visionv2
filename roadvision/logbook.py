"""RoadVision olay ve tespit günlüğü.

Tasarım, projedeki `Camera` ve `MediaSource` sınıflarıyla aynı yaşam
döngüsünü izler: `prepare → use → release`. Üç katman vardır:

- `LogRecord`: tek bir günlük kaydının yapılandırılmış, JSON'a çevrilebilir
  modeli. Alanlar bilinçli olarak bir veritabanı satırına birebir eşlenecek
  biçimde seçildi; ileride `DatabaseSink` eklemek şema değişikliği gerektirmez.
- `LogSink`: kayıtların yazıldığı hedefin soyut sözleşmesi. `JsonlFileSink`
  ve `ConsoleSink` bugünkü uygulamalardır; veritabanı, HTTP, MQTT gibi yeni
  hedefler yalnız bu sınıfı uygulayarak eklenir.
- `EventJournal`: uygulamanın kullandığı cephe (facade). Kayıtları sınırlı
  bir kuyruğa bırakır; tek bir yazıcı thread kuyruğu boşaltıp tekrar
  bastırmayı uygular ve sink'lere yazar. Böylece inference/capture
  thread'leri hiçbir zaman disk I/O beklemez.

Tespit tekrarları `DetectionSuppressor` ile bastırılır: aynı çalışmada aynı
modelin imzası (varsayılan olarak nesne sayısı) değişmediği sürece yeni kayıt
yazılmaz; seri bittiğinde kaç kare sürdüğü özetlenir, uzun sabit durumlar
için isteğe bağlı kalp atışı kaydı düşülür.
"""

from __future__ import annotations

import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Hashable, Sequence


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_LEVEL_ORDER = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
}


def _new_ingest_key() -> str:
    return f"live:{uuid.uuid4().hex}"


def detection_signature(objects: Sequence[Any], object_count: int) -> Hashable:
    """Journal tekrar bastırmasının tek ortak imza tanımı."""

    if objects:
        return tuple(
            sorted(Counter(getattr(item, "class_name", "?") for item in objects).items())
        )
    return int(object_count)


class LogCategory(str, Enum):
    APP = "app"
    DETECTION = "detection"


class PersistenceCheckpoint:
    """Asenkron kalıcılık sınırının sonucunu thread-safe taşır."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._success: bool | None = None
        self._callbacks: list[
            Callable[[PersistenceCheckpoint], None]
        ] = []

    @classmethod
    def completed(cls, success: bool = True) -> PersistenceCheckpoint:
        checkpoint = cls()
        checkpoint.resolve(success)
        return checkpoint

    def resolve(self, success: bool) -> None:
        callbacks: list[Callable[[PersistenceCheckpoint], None]]
        with self._lock:
            if self._success is not None:
                return
            self._success = bool(success)
            callbacks, self._callbacks = self._callbacks, []
            self._event.set()
        for callback in callbacks:
            try:
                callback(self)
            except Exception:
                pass

    def add_done_callback(
        self,
        callback: Callable[[PersistenceCheckpoint], None],
    ) -> None:
        invoke_now = False
        with self._lock:
            if self._success is None:
                self._callbacks.append(callback)
            else:
                invoke_now = True
        if invoke_now:
            try:
                callback(self)
            except Exception:
                pass

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout) and self.success

    @property
    def done(self) -> bool:
        return self._event.is_set()

    @property
    def success(self) -> bool:
        with self._lock:
            return self._success is True


@dataclass(frozen=True, slots=True)
class LogRecord:
    """Tek günlük kaydı. Tüm alanlar JSON/veritabanı dostudur."""

    timestamp: float
    level: LogLevel
    category: LogCategory
    message: str
    run_id: int | None = None
    model_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ingest_key: str | None = field(default_factory=_new_ingest_key)

    @property
    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.iso_time,
            "level": self.level.value,
            "category": self.category.value,
            "message": self.message,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "payload": self.payload,
            "ingest_key": self.ingest_key,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class LogSink(ABC):
    """Günlük hedeflerinin sözleşmesi.

    Yeni bir hedef (ör. veritabanı) eklemek için yalnız bu sınıf uygulanır ve
    `EventJournal.add_sink` ile kaydedilir; günlük üreten kod değişmez.
    Tüm sink çağrıları journal'ın TEK yazıcı thread'inden gelir; sink'lerin
    kendi içinde kilit tutması gerekmez.
    """

    min_level: LogLevel = LogLevel.DEBUG

    def accepts(self, record: LogRecord) -> bool:
        return _LEVEL_ORDER[record.level] >= _LEVEL_ORDER[self.min_level]

    @abstractmethod
    def prepare_sink(self) -> None: ...

    @abstractmethod
    def write_record(self, record: LogRecord) -> None: ...

    def flush(self) -> None:  # noqa: B027 - isteğe bağlı kanca
        """Alt sınıflar tamponlarını boşaltmak için ezebilir."""

    def request_checkpoint(self) -> PersistenceCheckpoint:
        """Önceki yazıların bu sink açısından tamamlandığını bildirir."""

        try:
            self.flush()
        except Exception:
            return PersistenceCheckpoint.completed(False)
        return PersistenceCheckpoint.completed()

    @abstractmethod
    def release_sink(self) -> None: ...


class JsonlFileSink(LogSink):
    """Kayıtları satır başına bir JSON nesnesi olarak dosyaya yazar.

    JSONL biçimi hem insan tarafından incelenebilir hem de ileride toplu
    olarak veritabanına aktarılabilir. Basit boyut tabanlı döndürme vardır:
    dosya `max_bytes` sınırını aşınca `.1` uzantısına taşınır (tek yedek).
    """

    def __init__(
        self,
        path: str | Path,
        min_level: LogLevel = LogLevel.DEBUG,
        max_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.min_level = min_level
        self.max_bytes = max_bytes
        self._stream: io.TextIOWrapper | None = None

    def prepare_sink(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a", encoding="utf-8")

    def write_record(self, record: LogRecord) -> None:
        if self._stream is None:
            return
        self._stream.write(record.to_json() + "\n")
        if self.max_bytes > 0 and self._stream.tell() >= self.max_bytes:
            self._rotate()

    def flush(self) -> None:
        if self._stream is not None:
            self._stream.flush()

    def _rotate(self) -> None:
        assert self._stream is not None
        self._stream.close()
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            backup.unlink(missing_ok=True)
            self.path.rename(backup)
        except OSError:
            # Döndürme başarısızsa mevcut dosyaya yazmaya devam etmek,
            # günlüğü tamamen kaybetmekten iyidir.
            pass
        self._stream = self.path.open("a", encoding="utf-8")

    def release_sink(self) -> None:
        if self._stream is not None:
            self._stream.flush()
            self._stream.close()
            self._stream = None


class ConsoleSink(LogSink):
    """Uyarı ve hataları stderr'e basar; geliştirme sırasında görünürlük sağlar."""

    def __init__(self, min_level: LogLevel = LogLevel.WARNING) -> None:
        self.min_level = min_level

    def prepare_sink(self) -> None:  # durum yok
        return

    def write_record(self, record: LogRecord) -> None:
        prefix = f"[{record.level.value.upper()}] {record.iso_time}"
        run = f" run={record.run_id}" if record.run_id is not None else ""
        model = f" model={record.model_id}" if record.model_id else ""
        print(f"{prefix}{run}{model} {record.message}", file=sys.stderr)

    def release_sink(self) -> None:
        return


class SessionLogSink(LogSink):
    """UI'ın bu oturumdaki kayıtları güvenle tüketmesi için kuyruk sink'i.

    Kayıtlar journal'ın yazıcı thread'inden gelir, UI ise Tk ana thread'inde
    ``drain`` ile kuyruğu boşaltır. Kuyruk dolarsa en eski kayıt atılır; disk
    günlüğü bundan etkilenmez ve arayüz hiçbir üretici thread'i bloklamaz.
    """

    def __init__(
        self,
        max_records: int = 2000,
        min_level: LogLevel = LogLevel.DEBUG,
    ) -> None:
        if max_records <= 0:
            raise ValueError("max_records pozitif olmalıdır.")
        self.min_level = min_level
        self._records: queue.Queue[LogRecord] = queue.Queue(maxsize=max_records)

    def prepare_sink(self) -> None:
        return

    def write_record(self, record: LogRecord) -> None:
        while True:
            try:
                self._records.put_nowait(record)
                return
            except queue.Full:
                try:
                    self._records.get_nowait()
                except queue.Empty:
                    continue

    def drain(self, limit: int | None = None) -> list[LogRecord]:
        """Bekleyen kayıtları eskiden yeniye döndürür."""
        records: list[LogRecord] = []
        while limit is None or len(records) < limit:
            try:
                records.append(self._records.get_nowait())
            except queue.Empty:
                break
        return records

    def release_sink(self) -> None:
        return


# ---------------------------------------------------------------------------
# Tespit tekrarlarının bastırılması


@dataclass(slots=True)
class _Streak:
    signature: Hashable
    started_at: float
    last_logged_at: float
    frames: int = 1


@dataclass(frozen=True, slots=True)
class SuppressionDecision:
    should_log: bool
    reason: str  # "changed" | "capture" | "heartbeat" | "suppressed"
    repeated_frames: int
    previous_signature: Hashable | None = None
    previous_frames: int = 0
    previous_seconds: float = 0.0


class DetectionSuppressor:
    """Art arda gelen özdeş tespitleri bastırır.

    Anahtar `(run_id, model_id)`, imza varsayılan olarak nesne sayısıdır;
    çağıran daha zengin bir imza (ör. sınıf-başına sayım demeti) verebilir.
    Yalnız journal'ın yazıcı thread'inden çağrılır; bu yüzden kilitsizdir.

    Kurallar:
    - İmza değiştiğinde kayıt yazılır; biten serinin kaç kare ve kaç saniye
      sürdüğü karara eklenir.
    - İmza aynı kaldığı sürece kayıt yazılmaz; `heartbeat_seconds` doluysa
      seri sürüyor bilgisiyle bir kalp atışı kaydına izin verilir (0 veya
      negatif değer kalp atışını kapatır).
    """

    def __init__(self, heartbeat_seconds: float = 30.0) -> None:
        self.heartbeat_seconds = heartbeat_seconds
        self._streaks: dict[tuple[int, str], _Streak] = {}

    def observe(
        self,
        run_id: int,
        model_id: str,
        signature: Hashable,
        now: float | None = None,
        force_log: bool = False,
    ) -> SuppressionDecision:
        now = time.time() if now is None else now
        key = (run_id, model_id)
        streak = self._streaks.get(key)

        if streak is None or streak.signature != signature:
            decision = SuppressionDecision(
                should_log=True,
                reason="changed",
                repeated_frames=1,
                previous_signature=streak.signature if streak else None,
                previous_frames=streak.frames if streak else 0,
                previous_seconds=(now - streak.started_at) if streak else 0.0,
            )
            self._streaks[key] = _Streak(signature, now, now)
            return decision

        streak.frames += 1
        if force_log:
            # Medya kapısı journal'dan daha zengin (mekânsal) imza kullanır.
            # Aynı sınıf kompozisyonunda yeni bir kare yakalandıysa olay↔medya
            # korelasyon satırının bastırılmasına izin verme.
            streak.last_logged_at = now
            return SuppressionDecision(
                should_log=True,
                reason="capture",
                repeated_frames=streak.frames,
            )
        if self.heartbeat_seconds > 0 and (now - streak.last_logged_at) >= self.heartbeat_seconds:
            streak.last_logged_at = now
            return SuppressionDecision(
                should_log=True, reason="heartbeat", repeated_frames=streak.frames
            )
        return SuppressionDecision(
            should_log=False, reason="suppressed", repeated_frames=streak.frames
        )

    def finish_run(self, run_id: int, now: float | None = None) -> list[LogRecord]:
        """Çalışma bitince açık serileri özet kayıtlarına çevirir ve unutur."""
        now = time.time() if now is None else now
        records: list[LogRecord] = []
        for key in [key for key in self._streaks if key[0] == run_id]:
            streak = self._streaks.pop(key)
            records.append(
                LogRecord(
                    timestamp=now,
                    level=LogLevel.INFO,
                    category=LogCategory.DETECTION,
                    message="Tespit serisi çalışma sonunda kapandı.",
                    run_id=key[0],
                    model_id=key[1],
                    payload={
                        "signature": streak.signature,
                        "frames": streak.frames,
                        "seconds": round(now - streak.started_at, 3),
                        "closed_by": "run_finished",
                    },
                )
            )
        return records


# ---------------------------------------------------------------------------
# Cephe


class EventJournal:
    """Uygulamanın günlük cephesi.

    Üretici taraf (`app_event`, `detection`, `run_finished`) yalnız sınırlı
    bir kuyruğa `put_nowait` yapar ve asla bloklamaz; kuyruk doluysa kayıt
    düşürülür ve düşen sayısı bir sonraki uygun kayda iliştirilir. Tek yazıcı
    thread bastırma kararını verir ve tüm sink'lere yazar.
    """

    def __init__(
        self,
        sinks: list[LogSink] | None = None,
        suppressor: DetectionSuppressor | None = None,
        queue_size: int = 1000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sinks: list[LogSink] = list(sinks or [])
        self._suppressor = suppressor or DetectionSuppressor()
        self._clock = clock
        self._queue: queue.Queue[object] = queue.Queue(maxsize=queue_size)
        self._worker: threading.Thread | None = None
        self._closed = threading.Event()
        self._dropped = 0
        self._dropped_lock = threading.Lock()

    # -- yaşam döngüsü ------------------------------------------------------

    def prepare_journal(self) -> None:
        for sink in self._sinks:
            sink.prepare_sink()
        self._closed.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="roadvision-journal", daemon=True
        )
        self._worker.start()
        self.app_event(LogLevel.INFO, "Günlük başlatıldı.")

    def add_sink(self, sink: LogSink) -> None:
        """Yeni hedef ekler (ör. ileride DatabaseSink).

        Yazıcı thread'i ile yarışmamak için sink hazırlanıp kuyruk üzerinden
        eklenir; ekleme sırası korunur.
        """
        sink.prepare_sink()
        self._enqueue(("add_sink", sink))

    def release_journal(self, timeout: float = 5.0) -> None:
        if self._worker is None:
            return
        self.app_event(LogLevel.INFO, "Günlük kapatılıyor.")
        self._enqueue(("close", None))
        self._worker.join(timeout=timeout)
        self._worker = None
        for sink in self._sinks:
            try:
                sink.flush()
                sink.release_sink()
            except Exception:
                pass

    # -- üretici API ---------------------------------------------------------

    def app_event(
        self,
        level: LogLevel,
        message: str,
        run_id: int | None = None,
        **payload: Any,
    ) -> None:
        self._enqueue(
            LogRecord(
                timestamp=self._clock(),
                level=level,
                category=LogCategory.APP,
                message=message,
                run_id=run_id,
                payload=payload,
            )
        )

    def detection(
        self,
        run_id: int,
        model_id: str,
        display_name: str,
        object_count: int,
        elapsed_ms: float,
        signature: Hashable | None = None,
        objects: Sequence[Any] = (),
        **payload: Any,
    ) -> None:
        """Tek modelin tek karedeki tespit özetini bildirir.

        `objects` (DetectedObject dizisi) verilirse imza sınıf-başına sayım
        demetidir: aynı toplam sayıda kalsa bile tür bileşimi değiştiğinde
        (ör. 2 çukur → 1 çukur + 1 rögar) yeni kayıt yazılır. Tekil nesneler
        tür, doğruluk ve konumlarıyla payload'a eklenir; veritabanı sink'i
        `detected_objects` tablosunu bu alandan doldurur. `signature`
        parametresi verilirse her ikisine de üstün gelir.
        """
        if signature is None:
            signature = detection_signature(objects, object_count)
        record_payload: dict[str, Any] = {
            "object_count": object_count,
            "elapsed_ms": round(elapsed_ms, 1),
            "signature": signature,
            **payload,
        }
        if objects:
            record_payload["objects"] = [
                o.to_payload() if hasattr(o, "to_payload") else dict(o) for o in objects
            ]
        self._enqueue(
            (
                "detection",
                LogRecord(
                    timestamp=self._clock(),
                    level=LogLevel.INFO,
                    category=LogCategory.DETECTION,
                    message=f"{display_name}: {object_count} tespit",
                    run_id=run_id,
                    model_id=model_id,
                    payload=record_payload,
                ),
            )
        )

    def run_finished(self, run_id: int) -> None:
        self._enqueue(("run_finished", run_id))

    def request_checkpoint(self) -> PersistenceCheckpoint:
        """O ana dek kuyruğa alınan kayıtlar sink'lere kalıcı yazılınca çözülür."""

        checkpoint = PersistenceCheckpoint()
        if self._worker is None or not self._enqueue(("checkpoint", checkpoint)):
            checkpoint.resolve(False)
        return checkpoint

    @property
    def dropped_records(self) -> int:
        with self._dropped_lock:
            return self._dropped

    # -- iç işleyiş ----------------------------------------------------------

    def _enqueue(self, item: object) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1
            return False

    def _take_dropped(self) -> int:
        with self._dropped_lock:
            dropped, self._dropped = self._dropped, 0
            return dropped

    def _worker_loop(self) -> None:
        while not self._closed.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._process(item)
            except Exception:
                # Günlük hattındaki bir hata uygulamayı asla düşürmemeli.
                pass
            finally:
                self._queue.task_done()

    def _process(self, item: object) -> None:
        if isinstance(item, LogRecord):
            self._write(item)
            return
        kind, value = item  # type: ignore[misc]
        if kind == "detection":
            self._process_detection(value)
        elif kind == "run_finished":
            for record in self._suppressor.finish_run(int(value), now=self._clock()):
                self._write(record)
        elif kind == "add_sink":
            self._sinks.append(value)
        elif kind == "checkpoint":
            self._dispatch_checkpoint(value)
        elif kind == "close":
            self._closed.set()

    def _dispatch_checkpoint(self, checkpoint: PersistenceCheckpoint) -> None:
        sink_checkpoints: list[PersistenceCheckpoint] = []
        for sink in self._sinks:
            try:
                sink_checkpoints.append(sink.request_checkpoint())
            except Exception:
                sink_checkpoints.append(PersistenceCheckpoint.completed(False))

        if not sink_checkpoints:
            checkpoint.resolve(True)
            return
        callback_lock = threading.Lock()
        remaining = len(sink_checkpoints)
        successful = True

        def sink_done(item: PersistenceCheckpoint) -> None:
            nonlocal remaining, successful
            resolve = False
            with callback_lock:
                successful = successful and item.success
                remaining -= 1
                resolve = remaining == 0
            if resolve:
                checkpoint.resolve(successful)

        for sink_checkpoint in sink_checkpoints:
            sink_checkpoint.add_done_callback(sink_done)

    def _process_detection(self, record: LogRecord) -> None:
        decision = self._suppressor.observe(
            run_id=record.run_id if record.run_id is not None else -1,
            model_id=record.model_id or "?",
            signature=record.payload.get("signature"),
            now=record.timestamp,
            force_log=bool(record.payload.get("capture_id")),
        )
        if not decision.should_log:
            return
        payload = dict(record.payload)
        payload["dedup"] = decision.reason
        payload["repeated_frames"] = decision.repeated_frames
        if decision.reason == "changed" and decision.previous_signature is not None:
            payload["previous"] = {
                "signature": decision.previous_signature,
                "frames": decision.previous_frames,
                "seconds": round(decision.previous_seconds, 3),
            }
        self._write(
            LogRecord(
                timestamp=record.timestamp,
                level=record.level,
                category=record.category,
                message=record.message,
                run_id=record.run_id,
                model_id=record.model_id,
                payload=payload,
                ingest_key=record.ingest_key,
            )
        )

    def _write(self, record: LogRecord) -> None:
        dropped = self._take_dropped()
        if dropped:
            record = LogRecord(
                timestamp=record.timestamp,
                level=record.level,
                category=record.category,
                message=record.message,
                run_id=record.run_id,
                model_id=record.model_id,
                payload={**record.payload, "dropped_before_this": dropped},
                ingest_key=record.ingest_key,
            )
        for sink in self._sinks:
            if sink.accepts(record):
                try:
                    sink.write_record(record)
                except Exception:
                    # Tek sink'in hatası diğer hedeflere yazmayı engellememeli.
                    pass


class NullJournal(EventJournal):
    """Günlük istenmediğinde kullanılan etkisiz uygulama (test/başsız kullanım)."""

    def __init__(self) -> None:
        super().__init__(sinks=[], queue_size=1)

    def prepare_journal(self) -> None:
        return

    def release_journal(self, timeout: float = 5.0) -> None:
        return

    def request_checkpoint(self) -> PersistenceCheckpoint:
        return PersistenceCheckpoint.completed()

    def _enqueue(self, item: object) -> bool:
        return False


def create_default_journal(log_dir: str | Path | None = None) -> EventJournal:
    """Uygulamanın varsayılan günlüğünü kurar.

    JSONL dosya sink'i için sırasıyla verilen dizin, kullanıcı cache dizini ve
    geçici dizin denenir; hiçbiri yazılamazsa yalnız konsol sink'i ile devam
    edilir. Uygulama, günlük kurulamadı diye asla açılmamazlık etmez.
    """
    candidates: list[Path] = []
    if log_dir is not None:
        candidates.append(Path(log_dir))
    candidates.append(Path.home() / ".cache" / "roadvision" / "logs")
    candidates.append(Path(tempfile.gettempdir()) / "roadvision-logs")

    sinks: list[LogSink] = []
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError:
            continue
        sinks.append(JsonlFileSink(candidate / "roadvision.jsonl"))
        break
    sinks.append(ConsoleSink(min_level=LogLevel.WARNING))

    dsn = os.environ.get("ROADVISION_DB_DSN")
    if dsn:
        # Döngüsel import yok: db yalnız logbook'a bağımlıdır. psycopg kurulu
        # değilse ya da bağlantı kurulamazsa asenkron sink uyarı verir ve
        # yeniden dener; JSONL dayanıklı kayıttır ve sonradan aktarılabilir.
        try:
            from .db import PostgresSink

            sinks.append(PostgresSink(dsn))
        except Exception as exc:
            print(f"[WARNING] PostgreSQL sink kurulamadı, DB'siz devam: {exc}", file=sys.stderr)
    return EventJournal(sinks=sinks)
