from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from roadvision.config import ModelSpec
from roadvision.models.yolo import verify_weights_file


def spec_for(path: Path, sha256: str | None = None) -> ModelSpec:
    return ModelSpec(
        id="sample",
        display_name="Örnek Model",
        short_name="Örnek",
        task="detect",
        weights=path,
        input_size=640,
        color_bgr=(10, 20, 30),
        sha256=sha256,
    )


class VerifyWeightsFileTests(unittest.TestCase):
    def test_missing_file_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "yok.pt"
            with self.assertRaisesRegex(RuntimeError, "bulunamadı"):
                verify_weights_file(spec_for(missing))

    def test_lfs_pointer_is_detected_before_torch_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pointer = Path(directory) / "model.pt"
            pointer.write_bytes(
                b"version https://git-lfs.github.com/spec/v1\n"
                b"oid sha256:" + b"0" * 64 + b"\nsize 123\n"
            )
            with self.assertRaisesRegex(RuntimeError, "git lfs pull"):
                verify_weights_file(spec_for(pointer))

    def test_matching_sha256_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / "model.pt"
            payload = b"sahte-agirlik-icerigi" * 512
            weights.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()

            verify_weights_file(spec_for(weights, sha256=digest))  # hata beklenmez

    def test_mismatching_sha256_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / "model.pt"
            weights.write_bytes(b"degistirilmis-icerik")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                verify_weights_file(spec_for(weights, sha256="0" * 64))

    def test_without_expected_hash_only_existence_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / "model.pt"
            weights.write_bytes(b"herhangi bir icerik")

            verify_weights_file(spec_for(weights))  # hata beklenmez


if __name__ == "__main__":
    unittest.main()
