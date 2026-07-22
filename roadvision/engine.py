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


@dataclass(frozen=True, slots=True)
class FramePacket:
    frame: np.ndarray
    sequence: int
    captured_at: float = field(default_factory=time.perf_counter)


class ProcessingEngine:
    """Latest-frame capture/inference pipeline with dynamic model selection."""

    def __init__(
        self,
        event_callback: Callable[[EngineEvent], None],
        model_manager: ModelManager | None = None,
    ) -> None:
        self._event_callback = event_callback
        self._manager = model_manager or ModelManager(status_callback=self._emit_status)
        self._state = EngineState.IDLE
        self._state_lock = threading.RLock()
        self._selection_lock = threading.RLock()
        self._last_frame_lock = threading.RLock()
        self._selected_models: frozenset[str] = frozenset()
        self._source: MediaSource | None = None
        self._last_frame: np.ndarray | None = None
        self._frame_queue: queue.Queue[FramePacket] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._inference_thread: threading.Thread | None = None

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    @property
    def device(self) -> str:
        return self._manager.device_label

    def start(self, source: MediaSource, model_ids: set[str] | frozenset[str]) -> None:
        selected = frozenset(model_ids)
        if not selected:
            raise ValueError("Başlatmak için en az bir model seçin.")
        self._manager.registry.validate_models(selected)
        if self.state != EngineState.IDLE:
            self.stop()

        self._drain_queue()
        self._stop_event.clear()
        with self._selection_lock:
            self._selected_models = selected
        with self._last_frame_lock:
            self._last_frame = None
        self._source = source
        self._set_state(EngineState.STARTING)
        self._inference_thread = threading.Thread(target=self._inference_loop, name="roadvision-inference", daemon=True)
        self._capture_thread = threading.Thread(target=self._capture_loop, name="roadvision-capture", daemon=True)
        self._inference_thread.start()
        self._capture_thread.start()

    def update_models(self, model_ids: set[str] | frozenset[str]) -> None:
        selected = frozenset(model_ids)
        self._manager.registry.validate_models(selected)
        with self._selection_lock:
            self._selected_models = selected
        with self._last_frame_lock:
            last_frame = None if self._last_frame is None else self._last_frame.copy()
        if last_frame is not None and self.state in (EngineState.STARTING, EngineState.RUNNING):
            self._offer_frame(FramePacket(last_frame, -1))
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
        with self._last_frame_lock:
            last_frame = None if self._last_frame is None else self._last_frame.copy()
        if last_frame is not None and self.state == EngineState.RUNNING:
            self._offer_frame(FramePacket(last_frame, -1))

    def set_performance_profile(self, profile: PerformanceProfile | str) -> None:
        self._manager.set_performance_profile(profile)
        with self._last_frame_lock:
            last_frame = self._last_frame
        if last_frame is not None and self.state == EngineState.RUNNING:
            self._offer_frame(FramePacket(last_frame, -1))

    def stop(self) -> None:
        if self.state == EngineState.IDLE:
            return
        self._set_state(EngineState.STOPPING)
        self._stop_event.set()
        source = self._source
        current = threading.current_thread()
        for worker in (self._capture_thread, self._inference_thread):
            if worker is not None and worker is not current and worker.is_alive():
                worker.join(timeout=10.0)
        if source is not None:
            source.release_source()
        self._capture_thread = None
        self._inference_thread = None
        self._source = None
        self._drain_queue()
        self._set_state(EngineState.IDLE)
        self._emit(EngineEvent(kind="stopped", message="İşlem durduruldu."))

    def shutdown(self) -> None:
        self.stop()
        self._manager.release_models()

    def _capture_loop(self) -> None:
        source = self._source
        if source is None:
            return
        try:
            self._emit_status(f"{source.display_name} hazırlanıyor…")
            source.prepare_source()
            self._set_state(EngineState.RUNNING)
            self._emit(EngineEvent(kind="started", message="İşlem başladı.", source_name=source.display_name))
            for sequence, frame in enumerate(source.get_stream(self._stop_event)):
                if self._stop_event.is_set():
                    break
                with self._last_frame_lock:
                    # Kaynak her okumada yeni ndarray üretir; model katmanı ham
                    # kareyi değiştirmez. Referansı saklamak iki büyük kopyayı önler.
                    self._last_frame = frame
                self._offer_frame(FramePacket(frame, sequence))
            if not self._stop_event.is_set() and not source.is_static:
                self._emit(EngineEvent(kind="source_ended", message="Video/kamera akışı sona erdi."))
        except Exception as exc:
            self._set_state(EngineState.ERROR)
            self._emit(EngineEvent(kind="error", message=str(exc)))
            self._stop_event.set()
        finally:
            source.release_source()

    def _inference_loop(self) -> None:
        smoothed_ms: float | None = None
        while not self._stop_event.is_set():
            try:
                packet = self._frame_queue.get(timeout=0.10)
            except queue.Empty:
                continue
            try:
                with self._selection_lock:
                    selected = self._selected_models
                started = time.perf_counter()
                result: AnalysisResult = self._manager.run_models(packet.frame, selected)
                total_ms = (time.perf_counter() - started) * 1000
                smoothed_ms = total_ms if smoothed_ms is None else (0.22 * total_ms) + (0.78 * smoothed_ms)
                fps = 1000.0 / smoothed_ms if smoothed_ms > 0 else 0.0
                self._emit(
                    EngineEvent(
                        kind="frame",
                        frame=result.frame,
                        stats=result.stats,
                        inference_fps=fps,
                        total_ms=total_ms,
                        source_name=self._source.display_name if self._source else "",
                    )
                )
            except Exception as exc:
                self._set_state(EngineState.ERROR)
                self._emit(EngineEvent(kind="error", message=f"Model çalıştırma hatası: {exc}"))
                self._stop_event.set()
            finally:
                self._frame_queue.task_done()

    def _offer_frame(self, packet: FramePacket) -> None:
        try:
            self._frame_queue.put_nowait(packet)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.task_done()
            except queue.Empty:
                pass
            try:
                self._frame_queue.put_nowait(packet)
            except queue.Full:
                pass

    def _drain_queue(self) -> None:
        while True:
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.task_done()
            except queue.Empty:
                break

    def _set_state(self, state: EngineState) -> None:
        with self._state_lock:
            self._state = state

    def _emit_status(self, message: str) -> None:
        self._emit(EngineEvent(kind="status", message=message))

    def _emit(self, event: EngineEvent) -> None:
        try:
            self._event_callback(event)
        except Exception:
            pass
