from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from roadvision.config import ModelConfigError, ModelConfigLoader


def model_entry(model_id: str = "sample") -> dict:
    return {
        "id": model_id,
        "display_name": "Örnek Model",
        "short_name": "Örnek",
        "task": "detect",
        "weights": "weights/model.pt",
        "input_size": 640,
        "color_bgr": [10, 20, 30],
        "enabled": True,
    }


class ModelConfigLoaderTests(unittest.TestCase):
    def _write_config(self, directory: str, payload: dict) -> Path:
        path = Path(directory) / "models.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_relative_weight_path_is_resolved_from_json_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(directory, {"schema_version": 1, "models": [model_entry()]})
            specs = ModelConfigLoader(path).load_model_specs()

            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].weights, Path(directory).resolve() / "weights" / "model.pt")
            self.assertEqual(specs[0].color_bgr, (10, 20, 30))

    def test_disabled_models_are_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            disabled = model_entry("disabled")
            disabled["enabled"] = False
            path = self._write_config(
                directory,
                {"schema_version": 1, "models": [disabled, model_entry("active")]},
            )
            specs = ModelConfigLoader(path).load_model_specs()
            self.assertEqual([spec.id for spec in specs], ["active"])

    def test_duplicate_model_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_config(
                directory,
                {"schema_version": 1, "models": [model_entry(), model_entry()]},
            )
            with self.assertRaisesRegex(ModelConfigError, "Tekrarlanan model id"):
                ModelConfigLoader(path).load_model_specs()

    def test_missing_required_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = model_entry()
            del entry["weights"]
            path = self._write_config(directory, {"schema_version": 1, "models": [entry]})
            with self.assertRaisesRegex(ModelConfigError, "eksik alanlar: weights"):
                ModelConfigLoader(path).load_model_specs()


if __name__ == "__main__":
    unittest.main()
