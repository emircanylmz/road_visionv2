"""YOLO sonuçlarından tekil tespit nesnelerinin çıkarılması.

Bu modül bilinçli olarak `ultralytics`/`torch` import etmez: sonuç nesnesini
duck-typing ile okur. Böylece hem birim testleri ağır bağımlılıklar olmadan
çalışır hem de günlük/veritabanı katmanı model kütüphanesine bağlanmaz.

`DetectedObject`, "tespit türü + doğruluk + konum + zaman" gereksiniminin
veri modelidir: `class_name` türü, `confidence` doğruluğu taşır; zaman damgası
kaydı yapan katmanda (journal/DB) eklenir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class DetectedObject:
    class_name: str
    confidence: float | None  # semantic maskede güven skoru yoktur → None
    bbox: tuple[float, float, float, float] | None = None  # xyxy, piksel
    area_ratio: float | None = None  # semantic: maskenin kareye oranı

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"class": self.class_name}
        if self.confidence is not None:
            payload["confidence"] = round(float(self.confidence), 4)
        if self.bbox is not None:
            payload["bbox"] = [round(float(v), 1) for v in self.bbox]
        if self.area_ratio is not None:
            payload["area_ratio"] = round(float(self.area_ratio), 4)
        return payload


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def extract_objects(result: Any, fallback_class: str) -> tuple[DetectedObject, ...]:
    """Ultralytics Results nesnesinden tekil tespitleri çıkarır.

    - Semantic maske: maske doluysa tek bir nesne döner; tür `fallback_class`
      (model kimliği), doğruluk None, `area_ratio` maskenin kapladığı oran.
    - Kutulu görevler (detect/segment/obb): her kutu için tür adı
      (`result.names` haritasından), güven skoru ve xyxy koordinatı döner.

    Sonuç beklenmedik biçimdeyse boş demet döner; çıkarım hattı hiçbir zaman
    inference'ı düşürmemelidir.
    """
    try:
        semantic_mask = getattr(result, "semantic_mask", None)
        if semantic_mask is not None:
            data = _to_numpy(semantic_mask.data)
            foreground = data > 0
            if not np.any(foreground):
                return ()
            # Tek kanallı footprint'e indir ve maske konumunu da koru.
            # Böylece medya kapısı yalnız alan oranını değil, yol çizgisi/
            # yüzey maskesinin sahnedeki belirgin hareketini de ayırt eder.
            while foreground.ndim > 2:
                foreground = np.any(foreground, axis=0)
            ys, xs = np.nonzero(foreground)
            mask_height, mask_width = foreground.shape
            original_shape = getattr(result, "orig_shape", None)
            if original_shape is not None and len(original_shape) >= 2:
                original_height, original_width = original_shape[:2]
            else:
                original_height, original_width = mask_height, mask_width
            scale_x = float(original_width) / float(mask_width)
            scale_y = float(original_height) / float(mask_height)
            bbox = (
                float(xs.min()) * scale_x,
                float(ys.min()) * scale_y,
                float(xs.max() + 1) * scale_x,
                float(ys.max() + 1) * scale_y,
            )
            return (
                DetectedObject(
                    class_name=fallback_class,
                    confidence=None,
                    bbox=bbox,
                    area_ratio=float(np.count_nonzero(foreground)) / float(foreground.size),
                ),
            )

        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return ()
        names = getattr(result, "names", None) or {}
        cls = _to_numpy(boxes.cls).reshape(-1)
        conf = _to_numpy(boxes.conf).reshape(-1)
        xyxy = _to_numpy(boxes.xyxy).reshape(-1, 4)
        objects: list[DetectedObject] = []
        for index in range(len(cls)):
            class_id = int(cls[index])
            objects.append(
                DetectedObject(
                    class_name=str(names.get(class_id, class_id)),
                    confidence=float(conf[index]) if index < len(conf) else None,
                    bbox=tuple(float(v) for v in xyxy[index]) if index < len(xyxy) else None,
                )
            )
        return tuple(objects)
    except Exception:
        return ()
