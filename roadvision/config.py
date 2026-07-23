from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PerformanceProfile(str, Enum):
    QUALITY = "quality"
    BALANCED = "balanced"
    SPEED = "speed"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    display_name: str
    short_name: str
    task: str
    weights: Path
    input_size: int
    color_bgr: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class AppConfig:
    title: str = "RoadVision"
    build: str = "v1.2.0"
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps: int = 30
    max_camera_index: int = 8
    confidence: float = 0.35
    performance_profile: PerformanceProfile = PerformanceProfile.QUALITY
    image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
    video_extensions: tuple[str, ...] = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm")


APP_CONFIG = AppConfig()


@dataclass(frozen=True, slots=True)
class MediaConfig:
    """Tespit görüntüsü kaydının ortam değişkenleriyle ayarlanabilen sınırları."""

    backend: str = "db"
    jpeg_quality: int = 80
    max_edge: int = 1280
    min_interval_s: float = 2.0
    max_per_run: int = 200
    max_per_hour: int = 500
    queue_size: int = 8
    queue_max_mb: int = 256
    retention_days: int = 30
    max_total_mb: int = 2048
    shutdown_timeout_s: float = 10.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "MediaConfig":
        source = os.environ if environ is None else environ
        backend = source.get("ROADVISION_MEDIA", "db").strip().lower()
        if backend not in {"db", "off"}:
            raise ValueError("ROADVISION_MEDIA yalnız 'db' veya 'off' olabilir.")

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = source.get(name)
            try:
                value = default if raw is None else int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} tam sayı olmalıdır.") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{name}, {minimum}–{maximum} aralığında olmalıdır.")
            return value

        def number(name: str, default: float, minimum: float, maximum: float) -> float:
            raw = source.get(name)
            try:
                value = default if raw is None else float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} sayı olmalıdır.") from exc
            if not minimum <= value <= maximum:
                raise ValueError(f"{name}, {minimum}–{maximum} aralığında olmalıdır.")
            return value

        return cls(
            backend=backend,
            jpeg_quality=integer("ROADVISION_MEDIA_JPEG_QUALITY", 80, 1, 100),
            max_edge=integer("ROADVISION_MEDIA_MAX_EDGE", 1280, 64, 16_384),
            min_interval_s=number("ROADVISION_MEDIA_MIN_INTERVAL_S", 2.0, 0.0, 3600.0),
            max_per_run=integer("ROADVISION_MEDIA_MAX_PER_RUN", 200, 1, 1_000_000),
            max_per_hour=integer("ROADVISION_MEDIA_MAX_PER_HOUR", 500, 1, 1_000_000),
            queue_size=integer("ROADVISION_MEDIA_QUEUE_SIZE", 8, 1, 1024),
            queue_max_mb=integer("ROADVISION_MEDIA_QUEUE_MAX_MB", 256, 8, 16_384),
            retention_days=integer("ROADVISION_MEDIA_RETENTION_DAYS", 30, 1, 36_500),
            max_total_mb=integer("ROADVISION_MEDIA_MAX_TOTAL_MB", 2048, 1, 10_000_000),
            shutdown_timeout_s=number(
                "ROADVISION_MEDIA_SHUTDOWN_TIMEOUT_S", 10.0, 0.1, 300.0
            ),
        )


class ModelConfigError(ValueError):
    """Raised when the external model catalog is malformed."""


class ModelConfigLoader:
    REQUIRED_FIELDS = {
        "id",
        "display_name",
        "short_name",
        "task",
        "weights",
        "input_size",
        "color_bgr",
    }

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).expanduser().resolve()

    def load_model_specs(self) -> tuple[ModelSpec, ...]:
        payload = self._read_json()
        schema_version = payload.get("schema_version")
        if schema_version != 1:
            raise ModelConfigError(
                f"Desteklenmeyen model JSON schema_version: {schema_version!r}. Beklenen: 1"
            )
        raw_models = payload.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ModelConfigError("Model JSON dosyasında boş olmayan bir 'models' listesi bulunmalıdır.")

        specs: list[ModelSpec] = []
        seen_ids: set[str] = set()
        for position, raw_model in enumerate(raw_models, start=1):
            if not isinstance(raw_model, dict):
                raise ModelConfigError(f"models[{position - 1}] bir JSON nesnesi olmalıdır.")
            if "enabled" in raw_model and not isinstance(raw_model["enabled"], bool):
                raise ModelConfigError(f"models[{position - 1}].enabled true veya false olmalıdır.")
            if raw_model.get("enabled", True) is False:
                continue
            spec = self._parse_model(raw_model, position)
            if spec.id in seen_ids:
                raise ModelConfigError(f"Tekrarlanan model id: {spec.id!r}")
            seen_ids.add(spec.id)
            specs.append(spec)
        if not specs:
            raise ModelConfigError("Model JSON dosyasında etkin model bulunamadı.")
        return tuple(specs)

    def _read_json(self) -> dict[str, Any]:
        try:
            with self.config_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except FileNotFoundError as exc:
            raise ModelConfigError(f"Model ayar dosyası bulunamadı: {self.config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ModelConfigError(
                f"Model JSON geçersiz ({self.config_path.name}, satır {exc.lineno}): {exc.msg}"
            ) from exc
        except OSError as exc:
            raise ModelConfigError(f"Model ayar dosyası okunamadı: {self.config_path}") from exc
        if not isinstance(payload, dict):
            raise ModelConfigError("Model JSON kökü bir nesne olmalıdır.")
        return payload

    def _parse_model(self, raw_model: dict[str, Any], position: int) -> ModelSpec:
        missing = self.REQUIRED_FIELDS.difference(raw_model)
        if missing:
            raise ModelConfigError(
                f"models[{position - 1}] eksik alanlar: {', '.join(sorted(missing))}"
            )

        model_id = self._non_empty_string(raw_model["id"], position, "id")
        display_name = self._non_empty_string(raw_model["display_name"], position, "display_name")
        short_name = self._non_empty_string(raw_model["short_name"], position, "short_name")
        task = self._non_empty_string(raw_model["task"], position, "task")
        weights_value = self._non_empty_string(raw_model["weights"], position, "weights")

        input_size = raw_model["input_size"]
        if not isinstance(input_size, int) or isinstance(input_size, bool) or input_size <= 0:
            raise ModelConfigError(f"models[{position - 1}].input_size pozitif bir tam sayı olmalıdır.")

        color = raw_model["color_bgr"]
        if (
            not isinstance(color, list)
            or len(color) != 3
            or any(not isinstance(channel, int) or isinstance(channel, bool) or not 0 <= channel <= 255 for channel in color)
        ):
            raise ModelConfigError(
                f"models[{position - 1}].color_bgr, 0-255 arasında üç tam sayı içermelidir."
            )

        weights = Path(weights_value).expanduser()
        if not weights.is_absolute():
            weights = self.config_path.parent / weights

        return ModelSpec(
            id=model_id,
            display_name=display_name,
            short_name=short_name,
            task=task,
            weights=weights.resolve(),
            input_size=input_size,
            color_bgr=(color[0], color[1], color[2]),
        )

    @staticmethod
    def _non_empty_string(value: Any, position: int, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ModelConfigError(f"models[{position - 1}].{field} boş olmayan bir metin olmalıdır.")
        return value.strip()


MODEL_CONFIG_PATH = Path(
    os.environ.get("ROADVISION_MODEL_CONFIG", str(PROJECT_ROOT / "models.json"))
)
MODEL_SPECS: tuple[ModelSpec, ...] = ModelConfigLoader(MODEL_CONFIG_PATH).load_model_specs()
