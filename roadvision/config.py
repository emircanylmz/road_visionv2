from __future__ import annotations

import json
import os
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
    title: str = "RoadVision • Çoklu Model Test Arayüzü"
    build: str = "v1.0.0"
    camera_width: int = 1280
    camera_height: int = 720
    camera_fps: int = 30
    max_camera_index: int = 8
    confidence: float = 0.35
    performance_profile: PerformanceProfile = PerformanceProfile.QUALITY
    image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
    video_extensions: tuple[str, ...] = (".mp4", ".avi", ".mov", ".mkv", ".m4v", ".webm")


APP_CONFIG = AppConfig()


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
