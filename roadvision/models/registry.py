from __future__ import annotations

from ..config import MODEL_SPECS, ModelSpec


class ModelRegistry:
    def __init__(self, specs: tuple[ModelSpec, ...] = MODEL_SPECS) -> None:
        self._specs = {spec.id: spec for spec in specs}

    def get_available_models(self) -> tuple[ModelSpec, ...]:
        return tuple(self._specs.values())

    def get_model(self, model_id: str) -> ModelSpec:
        try:
            return self._specs[model_id]
        except KeyError as exc:
            raise KeyError(f"Bilinmeyen model: {model_id}") from exc

    def validate_models(self, model_ids: set[str] | frozenset[str]) -> None:
        unknown = set(model_ids).difference(self._specs)
        if unknown:
            raise ValueError(f"Bilinmeyen model kimlikleri: {', '.join(sorted(unknown))}")
        missing = [self._specs[item].weights for item in model_ids if not self._specs[item].weights.is_file()]
        if missing:
            raise FileNotFoundError(f"Model ağırlığı bulunamadı: {missing[0]}")
