from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import cv2
import torch

from ..config import PerformanceProfile
from .base import ModelRunStat
from .detections import extract_objects
from .registry import ModelRegistry
from .yolo import YoloModelAdapter


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    frame: np.ndarray
    stats: tuple[ModelRunStat, ...]
    # Medya kaydı etkinse UI görünürlük seçiminden bağımsız, tüm seçili
    # modellerin işaretlerini taşıyan ortak canvas.
    annotated_frame: np.ndarray | None = None


def select_device() -> str:
    if torch.cuda.is_available():
        return "cuda:0"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class ModelManager:
    """Owns lazy-loaded model adapters. Called from one inference worker."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        device: str | None = None,
        confidence: float = 0.35,
        performance_profile: PerformanceProfile = PerformanceProfile.BALANCED,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry or ModelRegistry()
        self.device = device or select_device()
        self.confidence = confidence
        self.performance_profile = performance_profile
        self.status_callback = status_callback
        self._models: dict[str, YoloModelAdapter] = {}
        self._lock = threading.RLock()
        self._model_confidences = {
            spec.id: self.confidence for spec in self.registry.get_available_models()
        }
        self._annotation_enabled = {
            spec.id: True for spec in self.registry.get_available_models()
        }
        logical_cpus = os.cpu_count() or 4
        self._max_cpu_workers = min(4, max(1, logical_cpus // 3))
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_cpu_workers,
            thread_name_prefix="roadvision-model",
        )
        # Model worker'larının yanında OpenCV'nin ayrıca tüm çekirdekleri açıp
        # oversubscription oluşturmasını engelle.
        cv2.setNumThreads(1)
        self._configured_torch_threads: int | None = None

    def get_available_models(self):
        return self.registry.get_available_models()

    def prepare_model(self, model_id: str) -> YoloModelAdapter:
        with self._lock:
            adapter = self._models.get(model_id)
            if adapter is None:
                spec = self.registry.get_model(model_id)
                adapter_device = self._device_for_spec(spec)
                if self.status_callback:
                    suffix = " (CPU uyumluluk modu)" if adapter_device == "cpu" and self.device == "mps" else ""
                    self.status_callback(f"{spec.display_name}{suffix} yükleniyor…")
                adapter = YoloModelAdapter(
                    spec,
                    adapter_device,
                    self._model_confidences.get(model_id, self.confidence),
                )
                adapter.input_size = self._profile_input_size(spec.input_size)
                adapter.prepare_model()
                self._models[model_id] = adapter
            return adapter

    def _device_for_spec(self, spec) -> str:
        # torchvision NMS'in MPS fallback yolu bazı macOS torch/torchvision
        # kombinasyonlarında kutu koordinatlarını bozabiliyor (x2, x1'e kayıyor).
        # NMS kullanan görevleri CPU'da çalıştırmak doğru xyxy sonucunu garanti eder.
        if self.device == "mps" and spec.task in {"detect", "segment", "obb", "pose"}:
            return "cpu"
        return self.device

    @property
    def device_label(self) -> str:
        return "mps + cpu(det)" if self.device == "mps" else self.device

    def prepare_models(self, model_ids: frozenset[str]) -> None:
        self.registry.validate_models(model_ids)
        for model_id in model_ids:
            self.prepare_model(model_id)

    def run_models(
        self,
        frame: np.ndarray,
        model_ids: frozenset[str],
        *,
        capture_annotations: bool = False,
    ) -> AnalysisResult:
        self.registry.validate_models(model_ids)
        if not model_ids:
            copied = frame.copy()
            return AnalysisResult(copied, (), copied if capture_annotations else None)
        canvas = frame.copy()
        stats: list[ModelRunStat] = []
        ordered_ids = [spec.id for spec in self.registry.get_available_models() if spec.id in model_ids]
        adapters = [self.prepare_model(model_id) for model_id in ordered_ids]
        # Tek inference boyunca görünürlük snapshot'ı sabittir. Hepsi görünürse
        # UI canvas aynı zamanda eksiksiz medya canvas'ıdır; ek annotate yoktur.
        with self._lock:
            annotation_visibility = {
                model_id: self._annotation_enabled.get(model_id, True)
                for model_id in ordered_ids
            }
        shared_capture_canvas = capture_annotations and all(annotation_visibility.values())
        capture_canvas = (
            canvas
            if shared_capture_canvas
            else frame.copy()
            if capture_annotations
            else None
        )

        all_cpu = all(adapter.device == "cpu" for adapter in adapters)
        if all_cpu and len(adapters) > 1:
            self._configure_cpu_threads(len(adapters))
            futures = [self._executor.submit(self._predict_timed, adapter, frame) for adapter in adapters]
            predictions = [future.result() for future in futures]
            for adapter, (prediction, elapsed_ms) in zip(adapters, predictions):
                canvas, capture_canvas, count = self._render_or_count(
                    canvas,
                    capture_canvas,
                    adapter,
                    prediction,
                    annotation_enabled=annotation_visibility[adapter.spec.id],
                    capture_annotations=capture_annotations,
                    shared_capture_canvas=shared_capture_canvas,
                )
                stats.append(
                    ModelRunStat(
                        adapter.spec.id,
                        adapter.spec.display_name,
                        count,
                        elapsed_ms,
                        objects=extract_objects(prediction, adapter.spec.id),
                    )
                )
        else:
            if all_cpu:
                self._configure_cpu_threads(1)
            for adapter in adapters:
                if adapter.device == "cpu" and not all_cpu:
                    self._configure_cpu_threads(1)
                prediction, elapsed_ms = self._predict_timed(adapter, frame)
                self._apply_device_fallback(adapter)
                canvas, capture_canvas, count = self._render_or_count(
                    canvas,
                    capture_canvas,
                    adapter,
                    prediction,
                    annotation_enabled=annotation_visibility[adapter.spec.id],
                    capture_annotations=capture_annotations,
                    shared_capture_canvas=shared_capture_canvas,
                )
                stats.append(
                    ModelRunStat(
                        adapter.spec.id,
                        adapter.spec.display_name,
                        count,
                        elapsed_ms,
                        objects=extract_objects(prediction, adapter.spec.id),
                    )
                )
        return AnalysisResult(canvas, tuple(stats), capture_canvas)

    def _render_or_count(
        self,
        canvas,
        capture_canvas,
        adapter: YoloModelAdapter,
        prediction,
        *,
        annotation_enabled: bool,
        capture_annotations: bool,
        shared_capture_canvas: bool,
    ):
        if annotation_enabled:
            canvas, count = adapter.annotate(canvas, prediction)
        else:
            count = adapter.count_detections(prediction)
        if capture_annotations:
            if shared_capture_canvas:
                capture_canvas = canvas
            else:
                capture_canvas, _ = adapter.annotate(capture_canvas, prediction)
        return canvas, capture_canvas, count

    @staticmethod
    def _predict_timed(adapter: YoloModelAdapter, frame: np.ndarray):
        started = time.perf_counter()
        prediction = adapter.predict(frame)
        return prediction, (time.perf_counter() - started) * 1000

    def _configure_cpu_threads(self, model_count: int) -> None:
        logical_cpus = os.cpu_count() or 4
        if model_count <= 1:
            thread_count = min(8, max(1, logical_cpus - 2))
        else:
            workers = min(model_count, self._max_cpu_workers)
            # Bir çekirdeği capture/UI için ayırıp kalanları worker'lara böl.
            thread_count = max(1, (logical_cpus - 1) // (workers + 1))
        if self._configured_torch_threads != thread_count:
            torch.set_num_threads(thread_count)
            self._configured_torch_threads = thread_count

    def _apply_device_fallback(self, adapter: YoloModelAdapter) -> None:
        expected_device = self._device_for_spec(adapter.spec)
        if adapter.device == expected_device:
            return
        self.device = adapter.device
        with self._lock:
            for cached_adapter in self._models.values():
                cached_adapter.device = self.device
        if self.status_callback:
            self.status_callback(
                "torchvision NMS, MPS üzerinde desteklenmediği için modeller CPU moduna geçirildi."
            )

    def set_confidence(self, confidence: float) -> None:
        self.confidence = min(1.0, max(0.01, confidence))
        with self._lock:
            for model_id in self._model_confidences:
                self._model_confidences[model_id] = self.confidence
            for adapter in self._models.values():
                adapter.confidence = self.confidence

    def set_model_confidence(self, model_id: str, confidence: float) -> None:
        self.registry.get_model(model_id)
        value = min(1.0, max(0.01, confidence))
        with self._lock:
            self._model_confidences[model_id] = value
            adapter = self._models.get(model_id)
            if adapter is not None:
                adapter.confidence = value

    def set_annotation_enabled(self, model_id: str, enabled: bool) -> None:
        self.registry.get_model(model_id)
        with self._lock:
            self._annotation_enabled[model_id] = bool(enabled)

    def set_performance_profile(self, profile: PerformanceProfile | str) -> None:
        self.performance_profile = PerformanceProfile(profile)
        with self._lock:
            for adapter in self._models.values():
                adapter.input_size = self._profile_input_size(adapter.spec.input_size)

    def _profile_input_size(self, native_size: int) -> int:
        if self.performance_profile == PerformanceProfile.QUALITY:
            return native_size
        if self.performance_profile == PerformanceProfile.BALANCED:
            return min(native_size, 768 if native_size > 768 else 640)
        return min(native_size, 640 if native_size > 768 else 512)

    def release_models(self) -> None:
        with self._lock:
            for adapter in self._models.values():
                adapter.release_model()
            self._models.clear()
        if self.device.startswith("cuda"):
            torch.cuda.empty_cache()
        self._executor.shutdown(wait=True, cancel_futures=True)
