from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO

from .base import ModelAdapter, ModelRunStat


class YoloModelAdapter(ModelAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._model: YOLO | None = None
        self.input_size = self.spec.input_size

    def prepare_model(self) -> None:
        if self._model is None:
            self._model = YOLO(str(self.spec.weights), task=self.spec.task)

    def predict(self, frame: np.ndarray) -> Any:
        self.prepare_model()
        assert self._model is not None
        try:
            return self._predict_once(frame)
        except RuntimeError as exc:
            message = str(exc)
            is_mps_nms_error = self.device == "mps" and (
                "torchvision::nms" in message or "not currently implemented for the MPS device" in message
            )
            if not is_mps_nms_error:
                raise
            # Bazı torch/torchvision kombinasyonları ortam fallback'ini NMS için
            # uygulamıyor. Bu durumda hatalı kareyi kaybetmeden CPU'da tekrar dene.
            self.device = "cpu"
            return self._predict_once(frame)

    def _predict_once(self, frame: np.ndarray) -> Any:
        assert self._model is not None
        predict_options: dict[str, Any] = dict(
            source=frame,
            imgsz=self.input_size,
            conf=self.confidence,
            device=self.device,
            verbose=False,
        )
        if self.device.startswith("cuda"):
            predict_options["half"] = True
        return self._model.predict(**predict_options)[0]

    def annotate(self, frame: np.ndarray, result: Any) -> tuple[np.ndarray, int]:
        semantic_mask = getattr(result, "semantic_mask", None)
        if semantic_mask is not None:
            return self._annotate_semantic(frame, semantic_mask)

        count = self.count_detections(result)

        # Koordinat ölçekleme ve letterbox geri dönüşünü Ultralytics'in kendi
        # Results/Annotator hattına bırak. Bu, telefon görüntülerindeki farklı
        # en-boy oranlarında özel xyxy çiziminden kaynaklanabilecek eksen kaymasını
        # ortadan kaldırır.
        # Sınıf adlarını değiştirmemek önemli: Türkçe/non-ASCII prefix PIL yazı
        # yolunu etkinleştiriyor ve görüntü kenarındaki uzun label'larda PIL
        # "x1 must be >= x0" hatası verebiliyor.
        annotated = result.plot(
            img=frame,
            conf=True,
            line_width=2,
            labels=True,
            boxes=True,
            masks=True,
        )
        return annotated, count

    def count_detections(self, result: Any) -> int:
        semantic_mask = getattr(result, "semantic_mask", None)
        if semantic_mask is not None:
            data = semantic_mask.data
            if hasattr(data, "detach"):
                data = data.detach().cpu().numpy()
            return int(np.any(np.asarray(data) > 0))

        boxes = getattr(result, "boxes", None)
        masks = getattr(result, "masks", None)
        boxes_count = len(boxes) if boxes is not None else 0
        masks_count = len(masks.data) if masks is not None else 0
        return max(boxes_count, masks_count)

    def _annotate_semantic(self, frame: np.ndarray, semantic_mask: Any) -> tuple[np.ndarray, int]:
        data = semantic_mask.data
        if hasattr(data, "detach"):
            data = data.detach().cpu().numpy()
        else:
            data = np.asarray(data)
        data = np.squeeze(data)
        if data.ndim != 2:
            raise RuntimeError(f"Beklenmeyen semantic maske boyutu: {data.shape}")
        if data.shape != frame.shape[:2]:
            data = cv2.resize(
                data.astype(np.uint8),
                (frame.shape[1], frame.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )

        foreground = data > 0
        if not np.any(foreground):
            return frame, 0

        color = np.asarray(self.spec.color_bgr, dtype=np.float32)
        pixels = frame[foreground].astype(np.float32)
        frame[foreground] = np.clip((pixels * 0.62) + (color * 0.38), 0, 255).astype(np.uint8)

        contours, _ = cv2.findContours(
            foreground.astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        meaningful = [contour for contour in contours if cv2.contourArea(contour) >= 16]
        if meaningful:
            cv2.drawContours(frame, meaningful, -1, self.spec.color_bgr, 2, cv2.LINE_AA)
        return frame, 1

    def run(self, frame: np.ndarray, canvas: np.ndarray) -> tuple[np.ndarray, ModelRunStat]:
        started = time.perf_counter()
        result = self.predict(frame)
        canvas, count = self.annotate(canvas, result)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return canvas, ModelRunStat(self.spec.id, self.spec.display_name, count, elapsed_ms)

    def release_model(self) -> None:
        self._model = None
