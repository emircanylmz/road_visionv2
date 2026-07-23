from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import ModelSpec
from .detections import DetectedObject


@dataclass(frozen=True, slots=True)
class ModelRunStat:
    model_id: str
    display_name: str
    object_count: int
    elapsed_ms: float
    # Tekil tespitler: tür (class), doğruluk (confidence) ve konum. Günlük ve
    # veritabanı katmanı bu alandan beslenir; geriye dönük uyumluluk için
    # varsayılanı boştur.
    objects: tuple[DetectedObject, ...] = ()


class ModelAdapter(ABC):
    def __init__(self, spec: ModelSpec, device: str, confidence: float) -> None:
        self.spec = spec
        self.device = device
        self.confidence = confidence

    @abstractmethod
    def prepare_model(self) -> None: ...

    @abstractmethod
    def predict(self, frame: np.ndarray) -> Any: ...

    @abstractmethod
    def annotate(self, frame: np.ndarray, result: Any) -> tuple[np.ndarray, int]: ...

    @abstractmethod
    def release_model(self) -> None: ...
