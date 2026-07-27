"""Tespit arşivi sorgularını Tk ana thread'inden ayıran okuma servisi.

Servis iki bağımsız nesil alanı kullanır: tür ağacı istekleri sayfa
yenilemelerini, sayfa yenilemeleri de tür ağacını geçersiz kılmaz. Her alan
için yalnız en yeni bekleyen iş tutulur. Worker, PostgreSQL bağlantısının tek
sahibidir ve hiçbir UI ya da journal callback'i çağırmaz.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .archive import (
    DetectionFilter,
    DetectionPage,
    ModelNode,
    PageCursor,
    SortSpec,
    TypeCount,
    check_archive_schema,
    fetch_detections,
    fetch_type_counts,
    fetch_type_tree,
)
from .db import default_connection_factory


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """Worker'ın UI polling döngüsüne bıraktığı tek, değişmez sonuç."""

    kind: str
    generation: int
    tree: tuple[ModelNode, ...] | None = None
    page: DetectionPage | None = None
    counts: tuple[TypeCount, ...] | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class _TreeJob:
    generation: int
    sequence: int
    kind: str = "tree"


@dataclass(frozen=True, slots=True)
class _RefreshJob:
    generation: int
    sequence: int
    flt: DetectionFilter
    sort: SortSpec
    cursor: PageCursor | None
    page_size: int
    include_counts: bool
    kind: str = "refresh"


_ArchiveJob = _TreeJob | _RefreshJob


class _StaleJob(Exception):
    """İş, daha yeni bir istek veya kapanış nedeniyle artık uygulanamaz."""


class ArchiveFetcher:
    """Arşiv SELECT'lerini tek, salt-okunur worker bağlantısında çalıştırır.

    İstek tarafı yalnız kısa bir ``Condition`` kritik bölümüne girer; kuyrukta
    yer beklemez. Bekleyen tree/refresh işleri ayrı slotlarda tutulduğu için
    hızlı UI değişiklikleri veritabanında backlog oluşturmaz. Sonuçlar da tür
    başına tek slotta birleştirilir.
    """

    enabled = True

    def __init__(
        self,
        dsn: str,
        *,
        connection_factory: Callable[[str], Any] = default_connection_factory,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        if not str(dsn).strip():
            raise ValueError("ArchiveFetcher için DSN boş olamaz.")
        if statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms pozitif olmalıdır.")

        self.dsn = str(dsn)
        self._connection_factory = connection_factory
        self._statement_timeout_ms = int(statement_timeout_ms)

        self._condition = threading.Condition(threading.Lock())
        self._pending_tree: _TreeJob | None = None
        self._pending_refresh: _RefreshJob | None = None
        self._results: OrderedDict[str, ArchiveResult] = OrderedDict()
        self._tree_generation = 0
        self._refresh_generation = 0
        self._sequence = 0
        self._closed = False
        self._worker: threading.Thread | None = None

        # Yalnız worker thread'i okur/yazar. close() bağlantıyı başka
        # thread'den kapatmaz; zaman aşımında worker güvenle devam eder.
        self._conn: Any = None

    @property
    def latest_tree_generation(self) -> int:
        with self._condition:
            return self._tree_generation

    @property
    def latest_refresh_generation(self) -> int:
        with self._condition:
            return self._refresh_generation

    def request_tree(self) -> int:
        """En güncel tür ağacı isteğini kaydeder ve neslini döndürür."""

        with self._condition:
            self._raise_if_closed_locked()
            self._tree_generation += 1
            self._sequence += 1
            generation = self._tree_generation
            self._pending_tree = _TreeJob(generation, self._sequence)
            self._results.pop("tree", None)
            self._ensure_worker_locked()
            self._condition.notify()
            return generation

    def request_refresh(
        self,
        flt: DetectionFilter,
        sort: SortSpec,
        cursor: PageCursor | None,
        page_size: int,
        include_counts: bool = True,
    ) -> int:
        """Sayfa ve isteğe bağlı sayımı aynı revision/transaction ile ister."""

        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size not in {25, 50, 100}
        ):
            raise ValueError("page_size yalnız 25, 50 veya 100 olabilir.")
        with self._condition:
            self._raise_if_closed_locked()
            self._refresh_generation += 1
            self._sequence += 1
            generation = self._refresh_generation
            self._pending_refresh = _RefreshJob(
                generation=generation,
                sequence=self._sequence,
                flt=flt,
                sort=sort,
                cursor=cursor,
                page_size=int(page_size),
                include_counts=bool(include_counts),
            )
            self._results.pop("refresh", None)
            self._ensure_worker_locked()
            self._condition.notify()
            return generation

    def poll(self) -> list[ArchiveResult]:
        """Bekleyen sonuçları üretim sırasıyla, bloklamadan döndürür."""

        with self._condition:
            results = list(self._results.values())
            self._results.clear()
            return results

    def close(self, timeout: float = 3.0) -> bool:
        """Yeni işi atomik olarak kapatır ve worker'ı en çok ``timeout`` bekler.

        ``False`` dönmesi worker'ın hâlâ, örneğin statement timeout ile
        sınırlanmış bir libpq çağrısında olduğunu belirtir. Bu durumda bağlantı
        caller thread'den kapatılmaz; worker ``finally`` bloğunda bırakır.
        """

        with self._condition:
            self._closed = True
            self._pending_tree = None
            self._pending_refresh = None
            self._results.clear()
            worker = self._worker
            self._condition.notify_all()

        if worker is None:
            return True
        if worker is threading.current_thread():
            return False
        worker.join(timeout=max(0.0, float(timeout)))
        return not worker.is_alive()

    def _raise_if_closed_locked(self) -> None:
        if self._closed:
            raise RuntimeError("ArchiveFetcher kapatıldı.")

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None:
            return
        worker = threading.Thread(
            target=self._worker_loop,
            name="roadvision-archive-fetcher",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _worker_loop(self) -> None:
        try:
            while True:
                with self._condition:
                    while (
                        not self._closed
                        and self._pending_tree is None
                        and self._pending_refresh is None
                    ):
                        self._condition.wait()
                    if self._closed:
                        return
                    job = self._take_next_job_locked()

                result = self._execute_with_retry(job)
                if result is not None:
                    self._publish_if_current(job, result)
        finally:
            self._discard_connection()
            with self._condition:
                if self._worker is threading.current_thread():
                    self._worker = None
                self._condition.notify_all()

    def _take_next_job_locked(self) -> _ArchiveJob:
        candidates = [
            job
            for job in (self._pending_tree, self._pending_refresh)
            if job is not None
        ]
        job = min(candidates, key=lambda item: item.sequence)
        if job.kind == "tree":
            self._pending_tree = None
        else:
            self._pending_refresh = None
        return job

    def _execute_with_retry(self, job: _ArchiveJob) -> ArchiveResult | None:
        for attempt in range(2):
            if not self._is_current(job):
                return None
            try:
                return self._execute_once(job)
            except _StaleJob:
                return None
            except Exception as exc:
                retryable = self._is_retryable_connection_error(exc)
                self._discard_connection()
                if attempt == 0 and retryable and self._is_current(job):
                    continue
                if not self._is_current(job):
                    return None
                detail = str(exc).strip()
                return ArchiveResult(
                    kind=job.kind,
                    generation=job.generation,
                    error=detail or exc.__class__.__name__,
                )
        return None

    def _execute_once(self, job: _ArchiveJob) -> ArchiveResult:
        conn = self._ensure_connection()
        if not self._is_current(job):
            raise _StaleJob

        try:
            self._begin_read_transaction(conn)
            if isinstance(job, _TreeJob):
                tree = tuple(fetch_type_tree(conn))
                if not self._is_current(job):
                    raise _StaleJob
                return ArchiveResult(
                    kind="tree",
                    generation=job.generation,
                    tree=tree,
                )

            page = fetch_detections(
                conn,
                job.flt,
                job.sort,
                job.cursor,
                job.page_size,
            )
            if not self._is_current(job):
                raise _StaleJob

            counts: tuple[TypeCount, ...] | None = None
            if job.include_counts:
                # Condition sorgu boyunca tutulmaz: UI yeni revision'ı hiçbir
                # zaman DB süresince beklemez. Bu kontrol, başlayabilecek eski
                # count sorgularını olabildiğince erken eler.
                if not self._is_current(job):
                    raise _StaleJob
                counts = tuple(fetch_type_counts(conn, job.flt))
                if not self._is_current(job):
                    raise _StaleJob

            return ArchiveResult(
                kind="refresh",
                generation=job.generation,
                page=page,
                counts=counts,
            )
        finally:
            # Başarılı SELECT de transaction açar. Her işin sonunda rollback,
            # persistent bağlantının idle-in-transaction kalmasını engeller.
            conn.rollback()

    def _ensure_connection(self) -> Any:
        if self._conn is not None:
            return self._conn

        conn = self._connection_factory(self.dsn)
        try:
            try:
                conn.read_only = True
            except (AttributeError, TypeError):
                # Basit test double'ları bu DB-API özelliğini sunmayabilir.
                pass

            # Şema doğrulaması yazma/migration yapmaz ve her yeni bağlantıda
            # yalnız bir kez çalışır.
            try:
                self._begin_read_transaction(conn)
                check_archive_schema(conn)
            finally:
                conn.rollback()
        except Exception:
            self._safe_rollback(conn)
            self._safe_close(conn)
            raise

        self._conn = conn
        return conn

    def _begin_read_transaction(self, conn: Any) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            # Değer uygulama sabitinden gelir; SQL'e kullanıcı girdisi eklenmez.
            cur.execute(
                f"SET LOCAL statement_timeout = {self._statement_timeout_ms}"
            )

    def _is_current(self, job: _ArchiveJob) -> bool:
        with self._condition:
            if self._closed:
                return False
            if job.kind == "tree":
                return job.generation == self._tree_generation
            return job.generation == self._refresh_generation

    def _publish_if_current(
        self,
        job: _ArchiveJob,
        result: ArchiveResult,
    ) -> None:
        with self._condition:
            if self._closed:
                return
            latest = (
                self._tree_generation
                if job.kind == "tree"
                else self._refresh_generation
            )
            if job.generation != latest:
                return
            # Tür başına bir sonuç slotu: UI polling'i durmuş olsa bile bellek
            # büyümez ve yeni sonuç eski sonucu atomik biçimde değiştirir.
            self._results.pop(job.kind, None)
            self._results[job.kind] = result

    @staticmethod
    def _is_retryable_connection_error(exc: Exception) -> bool:
        try:
            import psycopg
        except ImportError:  # pragma: no cover - factory zaten açıklayıcı hata verir
            return False

        if isinstance(exc, psycopg.InterfaceError):
            return True
        if not isinstance(exc, psycopg.OperationalError):
            return False

        sqlstate = getattr(exc, "sqlstate", None)
        if sqlstate is None:
            # DNS, socket, connect ve beklenmedik bağlantı kapanmaları çoğu kez
            # sunucudan SQLSTATE alınamadan OperationalError üretir.
            return True
        rendered = str(sqlstate)
        return rendered.startswith("08") or rendered in {
            "57P01",  # admin_shutdown
            "57P02",  # crash_shutdown
            "57P03",  # cannot_connect_now
        }

    def _discard_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        self._safe_rollback(conn)
        self._safe_close(conn)

    @staticmethod
    def _safe_rollback(conn: Any) -> None:
        try:
            conn.rollback()
        except Exception:
            pass

    @staticmethod
    def _safe_close(conn: Any) -> None:
        try:
            conn.close()
        except Exception:
            pass


def create_default_archive_fetcher(
    *,
    environ: Mapping[str, str] | None = None,
) -> ArchiveFetcher | None:
    """ROADVISION_DB_DSN yoksa arşiv özelliğini zarifçe devre dışı bırakır."""

    source = os.environ if environ is None else environ
    dsn = source.get("ROADVISION_DB_DSN", "").strip()
    return ArchiveFetcher(dsn) if dsn else None
