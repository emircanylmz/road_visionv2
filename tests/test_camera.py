from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from roadvision.camera import Camera


class FakeCapture:
    opened_indexes = {0, 2}

    def __init__(self, index: int, _backend: int) -> None:
        self.index = index
        self.released = False

    def isOpened(self) -> bool:
        return self.index in self.opened_indexes and not self.released

    def read(self):
        if not self.isOpened():
            return False, None
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True

    def set(self, *_args) -> bool:
        return True


class CameraTests(unittest.TestCase):
    @patch("roadvision.camera.platform.system", return_value="Linux")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_camera_index_scan_returns_only_readable_cameras(self, _capture, _system) -> None:
        cameras = Camera.get_camera_index(max_index=4)
        self.assertEqual([camera.index for camera in cameras], [0, 2])
        self.assertEqual((cameras[0].width, cameras[0].height), (640, 480))

    @patch("roadvision.camera.platform.system", return_value="Darwin")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_macos_scan_stops_at_first_missing_contiguous_index(self, capture, _system) -> None:
        cameras = Camera.get_camera_indexes(max_index=8)
        self.assertEqual([camera.index for camera in cameras], [0])
        self.assertEqual(capture.call_count, 2)

    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_prepare_stream_and_release(self, _capture) -> None:
        camera = Camera()
        camera.prepare_camera(0)
        frame = next(camera.get_stream())
        self.assertEqual(frame.shape, (480, 640, 3))
        camera.release_camera()
        self.assertIsNone(camera.read_frame())


if __name__ == "__main__":
    unittest.main()
