from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from PIL import Image, ImageOps

from .camera import Camera


class SourceKind(str, Enum):
    CAMERA = "camera"
    IMAGE = "image"
    VIDEO = "video"


class MediaSource(ABC):
    kind: SourceKind
    display_name: str

    @abstractmethod
    def prepare_source(self) -> None: ...

    @abstractmethod
    def get_stream(self, stop_event: threading.Event) -> Iterator[np.ndarray]: ...

    @abstractmethod
    def release_source(self) -> None: ...

    @property
    def is_static(self) -> bool:
        return False


class CameraSource(MediaSource):
    kind = SourceKind.CAMERA

    def __init__(self, index: int, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self.display_name = f"Kamera {index}"
        self._camera = Camera()

    def prepare_source(self) -> None:
        self._camera.prepare_camera(self.index, self.width, self.height, self.fps)

    def get_stream(self, stop_event: threading.Event) -> Iterator[np.ndarray]:
        yield from self._camera.get_stream(stop_event)

    def release_source(self) -> None:
        self._camera.release_camera()


def _read_image(path: Path) -> np.ndarray:
    try:
        # Telefon fotoğrafları çoğu zaman pikselleri yatay saklayıp doğru yönü
        # yalnızca EXIF Orientation alanında belirtir. Yönü piksel verisine
        # uygulamak, inference ve overlay'in aynı koordinat sistemini kullanmasını sağlar.
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Dosya okunamadı: {path}") from exc
    if frame is None:
        raise RuntimeError(f"Geçerli bir görüntü değil: {path.name}")
    return frame


class ImageSource(MediaSource):
    kind = SourceKind.IMAGE

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.display_name = self.path.name
        self._frame: np.ndarray | None = None

    @property
    def is_static(self) -> bool:
        return True

    def prepare_source(self) -> None:
        if not self.path.is_file():
            raise RuntimeError(f"Fotoğraf bulunamadı: {self.path}")
        self._frame = _read_image(self.path)

    def get_stream(self, stop_event: threading.Event) -> Iterator[np.ndarray]:
        if self._frame is not None and not stop_event.is_set():
            yield self._frame.copy()

    def release_source(self) -> None:
        self._frame = None


class VideoSource(MediaSource):
    kind = SourceKind.VIDEO

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.display_name = self.path.name
        self._capture: cv2.VideoCapture | None = None
        self.fps = 0.0
        self.frame_count = 0

    def prepare_source(self) -> None:
        if not self.path.is_file():
            raise RuntimeError(f"Video bulunamadı: {self.path}")
        self.release_source()
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Video açılamadı: {self.path.name}")
        self._capture = capture
        self.fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        self.frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def get_stream(self, stop_event: threading.Event) -> Iterator[np.ndarray]:
        frame_interval = 1.0 / self.fps if self.fps > 0 else 0.0
        next_frame_at = time.perf_counter()
        while not stop_event.is_set():
            capture = self._capture
            if capture is None:
                break
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            yield frame
            if frame_interval:
                next_frame_at += frame_interval
                delay = next_frame_at - time.perf_counter()
                if delay > 0:
                    stop_event.wait(delay)
                elif delay < -(frame_interval * 3):
                    next_frame_at = time.perf_counter()

    def release_source(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None


class SourceFactory:
    @staticmethod
    def create_camera(index: int, width: int = 1280, height: int = 720, fps: int = 30) -> CameraSource:
        return CameraSource(index, width, height, fps)

    @staticmethod
    def create_image(path: str | Path) -> ImageSource:
        return ImageSource(path)

    @staticmethod
    def create_video(path: str | Path) -> VideoSource:
        return VideoSource(path)
