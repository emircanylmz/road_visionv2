from __future__ import annotations

import platform
import threading
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class CameraInfo:
    index: int
    name: str
    width: int = 0
    height: int = 0

    def __str__(self) -> str:
        resolution = f" • {self.width}×{self.height}" if self.width and self.height else ""
        return f"Kamera {self.index}{resolution}"


class Camera:
    """OpenCV cameras behind a small, testable lifecycle API."""

    def __init__(self) -> None:
        self._capture: cv2.VideoCapture | None = None
        self._index: int | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _backend() -> int:
        if platform.system() == "Darwin":
            return cv2.CAP_AVFOUNDATION
        if platform.system() == "Windows":
            return cv2.CAP_DSHOW
        return cv2.CAP_ANY

    @classmethod
    def get_camera_indexes(cls, max_index: int = 8) -> list[CameraInfo]:
        cameras: list[CameraInfo] = []
        backend = cls._backend()
        is_macos = platform.system() == "Darwin"
        for index in range(max_index):
            capture = cv2.VideoCapture(index, backend)
            try:
                if not capture.isOpened():
                    # AVFoundation aygıt indekslerini ardışık verir ve sınır dışı
                    # her denemeyi stderr'e iki uyarı olarak basar. İlk boş
                    # indekste durmak 1..7 gibi var olmayan aygıtların tekrar
                    # tekrar açılmasını ve terminal gürültüsünü engeller.
                    if is_macos:
                        break
                    continue
                ok, frame = capture.read()
                if not ok or frame is None:
                    if is_macos:
                        break
                    continue
                height, width = frame.shape[:2]
                cameras.append(CameraInfo(index=index, name=f"Kamera {index}", width=width, height=height))
            finally:
                capture.release()
        return cameras

    @classmethod
    def get_camera_index(cls, max_index: int = 8) -> list[CameraInfo]:
        """Compatibility alias matching the requested Camera API name."""
        return cls.get_camera_indexes(max_index)

    def prepare_camera(self, index: int, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        with self._lock:
            self.release_camera()
            capture = cv2.VideoCapture(index, self._backend())
            if not capture.isOpened():
                capture.release()
                raise RuntimeError(f"Kamera {index} açılamadı.")
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            capture.set(cv2.CAP_PROP_FPS, fps)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._capture = capture
            self._index = index

    def read_frame(self) -> np.ndarray | None:
        with self._lock:
            capture = self._capture
            if capture is None or not capture.isOpened():
                return None
            ok, frame = capture.read()
        return frame if ok else None

    def get_stream(self, stop_event: threading.Event | None = None) -> Iterator[np.ndarray]:
        while stop_event is None or not stop_event.is_set():
            frame = self.read_frame()
            if frame is None:
                break
            yield frame

    def release_camera(self) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.release()
            self._capture = None
            self._index = None

    @property
    def index(self) -> int | None:
        return self._index

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, *_: object) -> None:
        self.release_camera()
