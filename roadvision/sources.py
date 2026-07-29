from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class CsiCameraInfo:
    """Ortam değişkeninden UI kamera listesine eklenen Jetson CSI sensörü."""

    sensor_id: int
    width: int = 1280
    height: int = 720
    fps: int = 30
    flip_method: int = 0

    def __str__(self) -> str:
        return (
            f"CSI Kamera {self.sensor_id} • "
            f"{self.width}×{self.height} @ {self.fps} FPS"
        )


def configured_csi_cameras(
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    environ: Mapping[str, str] | None = None,
) -> tuple[CsiCameraInfo, ...]:
    """`ROADVISION_CSI_SENSORS=0,1` ayarını UI kamera girdilerine çevirir."""

    source = os.environ if environ is None else environ
    raw_sensors = source.get("ROADVISION_CSI_SENSORS", "").strip()
    if not raw_sensors:
        return ()
    try:
        flip_method = int(source.get("ROADVISION_CSI_FLIP_METHOD", "0"))
    except ValueError as exc:
        raise ValueError("ROADVISION_CSI_FLIP_METHOD tam sayı olmalıdır.") from exc
    if not 0 <= flip_method <= 7:
        raise ValueError("ROADVISION_CSI_FLIP_METHOD 0 ile 7 arasında olmalıdır.")

    sensor_ids: list[int] = []
    for item in raw_sensors.split(","):
        normalized = item.strip()
        if not normalized:
            raise ValueError(
                "ROADVISION_CSI_SENSORS virgülle ayrılmış sensör numaraları "
                "içermelidir."
            )
        try:
            sensor_id = int(normalized)
        except ValueError as exc:
            raise ValueError(
                "ROADVISION_CSI_SENSORS yalnız tam sayı sensör numaraları "
                "içermelidir."
            ) from exc
        if sensor_id < 0:
            raise ValueError("CSI sensör numarası negatif olamaz.")
        if sensor_id not in sensor_ids:
            sensor_ids.append(sensor_id)

    return tuple(
        CsiCameraInfo(
            sensor_id=sensor_id,
            width=width,
            height=height,
            fps=fps,
            flip_method=flip_method,
        )
        for sensor_id in sensor_ids
    )


def gstreamer_available() -> bool:
    """OpenCV derlemesinde GStreamer video-IO desteğinin olup olmadığı.

    pip'in `opencv-python` wheel'i GStreamer'sız derlenir; JetPack'in sistem
    OpenCV'si ise destekler. Açılış hatasında kullanıcıyı doğru yöne
    çevirebilmek için derleme bilgisinden okunur.
    """

    try:
        info = cv2.getBuildInformation()
    except Exception:
        return False
    for line in info.splitlines():
        if "GStreamer" in line:
            return "YES" in line.upper()
    return False


def build_nvargus_pipeline(
    sensor_id: int = 0,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    flip_method: int = 0,
) -> str:
    """Jetson CSI kamerası için `nvarguscamerasrc` → BGR appsink pipeline'ı.

    CSI sensörlere `cv2.VideoCapture(index)` V4L2 üzerinden ham Bayer
    formatında ulaşır ve kullanılabilir BGR kare üretmez; doğru yol Argus
    ISP hattıdır. `appsink drop=1 max-buffers=1`, motorun latest-frame
    kuyruğuyla aynı ilkeyi GStreamer tarafında uygular: inference
    yetişemezse eski kareler pipeline içinde birikip gecikme oluşturmaz.
    """

    sensor_id = int(sensor_id)
    width = int(width)
    height = int(height)
    fps = int(fps)
    flip_method = int(flip_method)
    if sensor_id < 0:
        raise ValueError("CSI sensor_id negatif olamaz.")
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError(
            "CSI genişlik, yükseklik ve FPS değerleri pozitif olmalıdır."
        )
    if not 0 <= flip_method <= 7:
        raise ValueError("CSI flip_method 0 ile 7 arasında olmalıdır.")

    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), width={width}, height={height}, "
        f"framerate={fps}/1, format=NV12 ! "
        f"nvvidconv flip-method={flip_method} ! "
        "video/x-raw, format=BGRx ! videoconvert ! "
        "video/x-raw, format=BGR ! "
        "appsink drop=1 max-buffers=1 sync=false"
    )


class GStreamerCameraSource(MediaSource):
    """GStreamer pipeline'ından (ör. Jetson CSI kamerası) BGR kare akışı."""

    kind = SourceKind.CAMERA

    def __init__(self, pipeline: str, display_name: str | None = None) -> None:
        pipeline = str(pipeline).strip()
        if not pipeline:
            raise ValueError("GStreamer pipeline boş olamaz.")
        self.pipeline = pipeline
        self.display_name = display_name or "GStreamer kamera"
        self._capture: cv2.VideoCapture | None = None

    def prepare_source(self) -> None:
        self.release_source()
        capture = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
        if not capture.isOpened():
            capture.release()
            if not gstreamer_available():
                raise RuntimeError(
                    "OpenCV bu ortamda GStreamer desteğiyle derlenmemiş; pip'in "
                    "opencv-python wheel'i GStreamer içermez. Jetson'da JetPack'in "
                    "sistem OpenCV'sini gören bir ortam kullanın (ör. sanal "
                    "ortamı --system-site-packages ile oluşturun) veya OpenCV'yi "
                    "GStreamer etkin derleyin."
                )
            raise RuntimeError(f"GStreamer pipeline açılamadı: {self.pipeline}")
        self._capture = capture

    def get_stream(self, stop_event: threading.Event) -> Iterator[np.ndarray]:
        while not stop_event.is_set():
            capture = self._capture
            if capture is None:
                break
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            yield frame

    def release_source(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None


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
    def create_gstreamer(
        pipeline: str,
        display_name: str | None = None,
    ) -> GStreamerCameraSource:
        return GStreamerCameraSource(pipeline, display_name)

    @staticmethod
    def create_csi_camera(
        sensor_id: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        flip_method: int = 0,
        display_name: str | None = None,
    ) -> GStreamerCameraSource:
        pipeline = build_nvargus_pipeline(
            sensor_id,
            width,
            height,
            fps,
            flip_method,
        )
        return GStreamerCameraSource(
            pipeline,
            display_name or f"CSI Kamera {sensor_id}",
        )

    @staticmethod
    def create_image(path: str | Path) -> ImageSource:
        return ImageSource(path)

    @staticmethod
    def create_video(path: str | Path) -> VideoSource:
        return VideoSource(path)
