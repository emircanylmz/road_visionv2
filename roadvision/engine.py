from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from .config import PerformanceProfile
from .models.manager import AnalysisResult, ModelManager
from .sources import MediaSource


class EngineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EngineEvent:
    kind: str
    message: str = ""
    frame: np.ndarray | None = None
    stats: tuple[Any, ...] = ()
    inference_fps: float = 0.0
    total_ms: float = 0.0
    source_name: str = ""
    run_id: int = 0


@dataclass(frozen=True, slots=True)
class FramePacket:
    frame: np.ndarray
    sequence: int
    captured_at: float = field(default_factory=time.perf_counter)


@dataclass(slots=True)
class _RunContext:
    run_id: int
    source: MediaSource
    selected_models: frozenset[str]
    stop_event: threading.Event = field(default_factory=threading.Event)
    frame_queue: queue.Queue[FramePacket] = field(default_factory=lambda: queue.Queue(maxsize=1))
    finished_event: threading.Event = field(default_factory=threading.Event)
    last_frame: np.ndarray | None = None
    capture_thread: threading.Thread | None = None
    inference_thread: threading.Thread | None = None
    reaper_thread: threading.Thread | None = None
    models_ready_event: threading.Event = field(default_factory=threading.Event)
    stop_requested: bool = False
    failed: bool = False


class ProcessingEngine:
    """Latest-frame capture/inference pipeline with isolated run lifecycles."""

    def __init__(
        self,
        event_callback: Callable[[EngineEvent], None],
        model_manager: ModelManager | None = None,
    ) -> None:
        self._event_callback = event_callback
        self._state = EngineState.IDLE
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._selection_lock = threading.RLock()
        self._last_frame_lock = threading.RLock()
        self._active_run: _RunContext | None = None
        self._next_run_id = 1
        self._shutdown_requested = False
        self._shutdown_started = False
        self._shutdown_complete = threading.Event()
        self._shutdown_thread: threading.Thread | None = None
        self._manager = model_manager or ModelManager(status_callback=self._emit_status)

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    @property
    def active_run_id(self) -> int | None:
        with self._lifecycle_lock:
            return self._active_run.run_id if self._active_run is not None else None

    @property
    def device(self) -> str:
        return self._manager.device_label

    def start(self, source: MediaSource, model_ids: set[str] | frozenset[str]) -> int:
        selected = frozenset(model_ids)
        if not selected:
            raise ValueError("Başlatmak için en az bir model seçin.")
        self._manager.registry.validate_models(selected)

        with self._lifecycle_lock:
            if self._shutdown_requested:
                raise RuntimeError("Motor kapatılıyor; yeni işlem başlatılamaz.")
            if self._active_run is not None or self.state != EngineState.IDLE:
                raise RuntimeError("Mevcut işlem tamamen durmadan yeni işlem başlatılamaz.")

            context = _RunContext(
                run_id=self._next_run_id,
                source=source,
                selected_models=selected,
            )
            self._next_run_id += 1
            context.inference_thread = threading.Thread(
                target=self._inference_loop,
                args=(context,),
                name="roadvision-inference",
                daemon=True,
            )
            context.capture_thread = threading.Thread(
                target=self._capture_loop,
                args=(context,),
                name="roadvision-capture",
                daemon=True,
            )
            self._active_run = context
            self._set_state(EngineState.STARTING)
            try:
                context.inference_thread.start()
                context.capture_thread.start()
            except BaseException:
                context.stop_requested = True
                context.stop_event.set()
                self._set_state(EngineState.STOPPING)
                self._ensure_reaper_locked(context)
                raise
            return context.run_id

    def update_models(self, model_ids: set[str] | frozenset[str]) -> None:
        selected = frozenset(model_ids)
        self._manager.registry.validate_models(selected)
        context = self._get_active_run()
        if context is not None:
            with self._selection_lock:
                context.selected_models = selected
            with self._last_frame_lock:
                last_frame = None if context.last_frame is None else context.last_frame.copy()
            if last_frame is not None and self._context_has_state(context, EngineState.RUNNING):
                self._offer_frame(context, FramePacket(last_frame, -1))
        names = [self._manager.registry.get_model(item).short_name for item in selected]
        self._emit_status("Aktif: " + (", ".join(names) if names else "model seçilmedi"))

    def set_confidence(self, value: float) -> None:
        self._manager.set_confidence(value)
        self._reprocess_last_frame()

    def set_model_confidence(self, model_id: str, value: float) -> None:
        self._manager.set_model_confidence(model_id, value)
        self._reprocess_last_frame()

    def set_annotation_enabled(self, model_id: str, enabled: bool) -> None:
        self._manager.set_annotation_enabled(model_id, enabled)
        self._reprocess_last_frame()

    def _reprocess_last_frame(self) -> None:
        context = self._get_active_run()
        if context is None:
            return
        with self._last_frame_lock:
            last_frame = None if context.last_frame is None else context.last_frame.copy()
        if last_frame is not None and self._context_has_state(context, EngineState.RUNNING):
            self._offer_frame(context, FramePacket(last_frame, -1))

    def set_performance_profile(self, profile: PerformanceProfile | str) -> None:
        self._manager.set_performance_profile(profile)
        context = self._get_active_run()
        if context is None:
            return
        with self._last_frame_lock:
            last_frame = context.last_frame
        if last_frame is not None and self._context_has_state(context, EngineState.RUNNING):
            self._offer_frame(context, FramePacket(last_frame, -1))

    def request_stop(self) -> int | None:
        """Signal the active run to stop and return immediately.

        Repeated calls for the same run are idempotent and return the same run id.
        """

        context = self._request_stop_context()
        return context.run_id if context is not None else None

    def stop(self, timeout: float | None = 10.0) -> bool:
        """Stop the active run, waiting at most ``timeout`` seconds.

        A timeout never forces the engine to IDLE; cleanup continues in the
        background and ``False`` is returned. Calls made by one of the run's
        own workers become non-blocking to avoid a self-join deadlock.
        """

        context = self._request_stop_context()
        if context is None:
            return True
        if threading.current_thread() in (context.capture_thread, context.inference_thread):
            return False
        return context.finished_event.wait(timeout)

    def request_shutdown(self) -> None:
        """Asynchronously stop the run and release model resources afterwards."""

        with self._lifecycle_lock:
            self._shutdown_requested = True
            context = self._active_run
            if context is not None:
                self._request_stop_locked(context)
                return
            self._ensure_shutdown_worker_locked()

    def shutdown(self, timeout: float | None = 10.0) -> bool:
        """Request shutdown and wait at most ``timeout`` seconds for cleanup."""

        self.request_shutdown()
        with self._lifecycle_lock:
            context = self._active_run
            shutdown_thread = self._shutdown_thread
        if threading.current_thread() is shutdown_thread:
            return False
        if context is not None and threading.current_thread() in (
            context.capture_thread,
            context.inference_thread,
        ):
            return False
        return self._shutdown_complete.wait(timeout)

    def _request_stop_context(self) -> _RunContext | None:
        with self._lifecycle_lock:
            context = self._active_run
            if context is None:
                return None
            self._request_stop_locked(context)
            return context

    def _request_stop_locked(self, context: _RunContext) -> None:
        context.stop_requested = True
        context.stop_event.set()
        if not context.failed:
            self._set_state(EngineState.STOPPING)
        self._ensure_reaper_locked(context)

    def _ensure_reaper_locked(self, context: _RunContext) -> None:
        if context.reaper_thread is not None:
            return
        context.reaper_thread = threading.Thread(
            target=self._reap_run,
            args=(context,),
            name=f"roadvision-stop-{context.run_id}",
            daemon=True,
        )
        context.reaper_thread.start()

    def _reap_run(self, context: _RunContext) -> None:
        current = threading.current_thread()
        for worker in (context.capture_thread, context.inference_thread):
            if worker is not None and worker is not current and worker.ident is not None:
                worker.join()
        self._drain_queue(context)
        with self._last_frame_lock:
            context.last_frame = None

        emit_stopped = context.stop_requested and not context.failed
        with self._lifecycle_lock:
            if self._active_run is context:
                if emit_stopped:
                    # Bağlam STOPPING olarak bağlıyken terminal olay
                    # callback'e teslim edilir. Reentrant veya başka thread'den
                    # gelen start/shutdown, stopped olayını geçemez.
                    self._emit(
                        EngineEvent(
                            kind="stopped",
                            message="İşlem durduruldu.",
                            run_id=context.run_id,
                        )
                    )
                self._active_run = None
                self._set_state(EngineState.IDLE)
        context.finished_event.set()

        with self._lifecycle_lock:
            if self._shutdown_requested and self._active_run is None:
                self._ensure_shutdown_worker_locked()

    def _ensure_shutdown_worker_locked(self) -> None:
        if self._shutdown_started or self._active_run is not None:
            return
        self._shutdown_started = True
        self._shutdown_thread = threading.Thread(
            target=self._shutdown_loop,
            name="roadvision-shutdown",
            daemon=True,
        )
        self._shutdown_thread.start()

    def _shutdown_loop(self) -> None:
        try:
            self._manager.release_models()
        except Exception as exc:
            self._emit(
                EngineEvent(
                    kind="error",
                    message=f"Model kaynakları bırakılamadı: {exc}",
                )
            )
        finally:
            self._emit(EngineEvent(kind="shutdown_complete", message="Uygulama kapatılmaya hazır."))
            self._shutdown_complete.set()

    def _capture_loop(self, context: _RunContext) -> None:
        source = context.source
        try:
            self._emit_status(f"{source.display_name} hazırlanıyor…", context)
            if context.stop_event.is_set():
                return
            source.prepare_source()
            while not context.models_ready_event.wait(0.05):
                if context.stop_event.is_set():
                    return
            if context.stop_event.is_set() or not self._transition_context(
                context, EngineState.RUNNING
            ):
                return
            self._emit_for_context(
                context,
                EngineEvent(
                    kind="started",
                    message="İşlem başladı.",
                    source_name=source.display_name,
                    run_id=context.run_id,
                ),
            )
            for sequence, frame in enumerate(source.get_stream(context.stop_event)):
                if context.stop_event.is_set():
                    break
                with self._last_frame_lock:
                    # Kaynak her okumada yeni ndarray üretir; model katmanı ham
                    # kareyi değiştirmez. Referansı saklamak iki büyük kopyayı önler.
                    context.last_frame = frame
                self._offer_frame(context, FramePacket(frame, sequence))
            if not context.stop_event.is_set() and not source.is_static:
                self._emit_for_context(
                    context,
                    EngineEvent(
                        kind="source_ended",
                        message="Video/kamera akışı sona erdi.",
                        run_id=context.run_id,
                    ),
                )
        except Exception as exc:
            self._fail_run(context, str(exc))
        finally:
            try:
                source.release_source()
            except Exception as exc:
                self._fail_run(context, f"Kaynak bırakılamadı: {exc}")

    def _inference_loop(self, context: _RunContext) -> None:
        context.models_ready_event.set()

        smoothed_ms: float | None = None
        while not context.stop_event.is_set():
            try:
                packet = context.frame_queue.get(timeout=0.10)
            except queue.Empty:
                continue
            try:
                if context.stop_event.is_set():
                    continue
                with self._selection_lock:
                    selected = context.selected_models
                started = time.perf_counter()
                result: AnalysisResult = self._manager.run_models(packet.frame, selected)
                if context.stop_event.is_set():
                    continue
                total_ms = (time.perf_counter() - started) * 1000
                smoothed_ms = total_ms if smoothed_ms is None else (0.22 * total_ms) + (0.78 * smoothed_ms)
                fps = 1000.0 / smoothed_ms if smoothed_ms > 0 else 0.0
                self._emit_for_context(
                    context,
                    EngineEvent(
                        kind="frame",
                        frame=result.frame,
                        stats=result.stats,
                        inference_fps=fps,
                        total_ms=total_ms,
                        source_name=context.source.display_name,
                        run_id=context.run_id,
                    ),
                )
            except Exception as exc:
                self._fail_run(context, f"Model çalıştırma hatası: {exc}")
            finally:
                context.frame_queue.task_done()

    def _fail_run(self, context: _RunContext, message: str) -> None:
        with self._lifecycle_lock:
            if self._active_run is not context or context.stop_event.is_set():
                return
            context.failed = True
            context.stop_event.set()
            self._set_state(EngineState.ERROR)
            self._ensure_reaper_locked(context)
        self._emit(EngineEvent(kind="error", message=message, run_id=context.run_id))

    def _offer_frame(self, context: _RunContext, packet: FramePacket) -> None:
        if context.stop_event.is_set():
            return
        try:
            context.frame_queue.put_nowait(packet)
        except queue.Full:
            try:
                context.frame_queue.get_nowait()
                context.frame_queue.task_done()
            except queue.Empty:
                pass
            try:
                context.frame_queue.put_nowait(packet)
            except queue.Full:
                pass

    @staticmethod
    def _drain_queue(context: _RunContext) -> None:
        while True:
            try:
                context.frame_queue.get_nowait()
                context.frame_queue.task_done()
            except queue.Empty:
                break

    def _get_active_run(self) -> _RunContext | None:
        with self._lifecycle_lock:
            return self._active_run

    def _context_has_state(self, context: _RunContext, *states: EngineState) -> bool:
        with self._lifecycle_lock:
            if self._active_run is not context or context.stop_event.is_set():
                return False
            with self._state_lock:
                return self._state in states

    def _transition_context(self, context: _RunContext, state: EngineState) -> bool:
        with self._lifecycle_lock:
            if self._active_run is not context or context.stop_event.is_set():
                return False
            self._set_state(state)
            return True

    def _set_state(self, state: EngineState) -> None:
        with self._state_lock:
            self._state = state

    def _emit_status(self, message: str, context: _RunContext | None = None) -> None:
        target = context or self._get_active_run()
        if target is None:
            self._emit(EngineEvent(kind="status", message=message))
            return
        self._emit_for_context(
            target,
            EngineEvent(kind="status", message=message, run_id=target.run_id),
        )

    def _emit_for_context(self, context: _RunContext, event: EngineEvent) -> None:
        with self._lifecycle_lock:
            if self._active_run is not context or context.stop_event.is_set():
                return
            # Guard ile callback tek bir lifecycle kritik bölgesidir.
            # request_stop() bu callback tamamlanmadan stop flag'ini set
            # edemez; döndükten sonra eski run olayı yayımlanamaz.
            self._emit(event)

    def _emit(self, event: EngineEvent) -> None:
        try:
            self._event_callback(event)
        except Exception:
            pass
