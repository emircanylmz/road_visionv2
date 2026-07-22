from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from roadvision.sources import ImageSource


class ImageSourceTests(unittest.TestCase):
    def test_image_source_emits_one_independent_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "örnek.png"
            encoded, data = cv2.imencode(".png", np.full((10, 12, 3), 127, dtype=np.uint8))
            self.assertTrue(encoded)
            path.write_bytes(data.tobytes())

            source = ImageSource(path)
            source.prepare_source()
            frames = list(source.get_stream(threading.Event()))
            source.release_source()

            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].shape, (10, 12, 3))

    def test_phone_exif_orientation_is_applied_to_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phone.jpg"
            image = Image.new("RGB", (20, 10), (200, 30, 10))
            exif = image.getexif()
            exif[274] = 6  # 90° clockwise
            image.save(path, exif=exif)

            source = ImageSource(path)
            source.prepare_source()
            frame = next(source.get_stream(threading.Event()))
            source.release_source()

            self.assertEqual(frame.shape[:2], (20, 10))


if __name__ == "__main__":
    unittest.main()
