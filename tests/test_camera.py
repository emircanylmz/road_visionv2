from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from roadvision.camera import Camera


class FakeCapture:
    opened_indexes = {0, 2}
    instances: list["FakeCapture"] = []

    def __init__(self, index: int, _backend: int) -> None:
        self.index = index
        self.released = False
        self.set_calls: list[tuple] = []
        FakeCapture.instances.append(self)

    def isOpened(self) -> bool:
        return self.index in self.opened_indexes and not self.released

    def read(self):
        if not self.isOpened():
            return False, None
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True

    def set(self, *args) -> bool:
        self.set_calls.append(args)
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


class CameraFourccTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeCapture.instances = []

    def _prepare(self):
        camera = Camera()
        camera.prepare_camera(0, width=1280, height=720, fps=30)
        return FakeCapture.instances[-1]

    @patch.dict("roadvision.camera.os.environ", {}, clear=False)
    @patch("roadvision.camera.platform.system", return_value="Linux")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_linux_requests_mjpg_before_resolution(self, _capture, _system) -> None:
        import roadvision.camera as camera_module

        camera_module.os.environ.pop("ROADVISION_CAMERA_FOURCC", None)
        capture = self._prepare()

        expected = camera_module.cv2.VideoWriter_fourcc(*"MJPG")
        self.assertEqual(
            capture.set_calls[0],
            (camera_module.cv2.CAP_PROP_FOURCC, expected),
        )
        properties = [call[0] for call in capture.set_calls]
        self.assertLess(
            properties.index(camera_module.cv2.CAP_PROP_FOURCC),
            properties.index(camera_module.cv2.CAP_PROP_FRAME_WIDTH),
        )

    @patch.dict(
        "roadvision.camera.os.environ",
        {"ROADVISION_CAMERA_FOURCC": "YUYV"},
        clear=False,
    )
    @patch("roadvision.camera.platform.system", return_value="Darwin")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_macos_keeps_backend_default_format(self, _capture, _system) -> None:
        import roadvision.camera as camera_module

        capture = self._prepare()

        properties = [call[0] for call in capture.set_calls]
        self.assertNotIn(camera_module.cv2.CAP_PROP_FOURCC, properties)

    @patch.dict(
        "roadvision.camera.os.environ",
        {"ROADVISION_CAMERA_FOURCC": ""},
        clear=False,
    )
    @patch("roadvision.camera.platform.system", return_value="Linux")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_empty_env_disables_fourcc_request(self, _capture, _system) -> None:
        import roadvision.camera as camera_module

        capture = self._prepare()

        properties = [call[0] for call in capture.set_calls]
        self.assertNotIn(camera_module.cv2.CAP_PROP_FOURCC, properties)

    @patch.dict(
        "roadvision.camera.os.environ",
        {"ROADVISION_CAMERA_FOURCC": "yuyv"},
        clear=False,
    )
    @patch("roadvision.camera.platform.system", return_value="Linux")
    @patch("roadvision.camera.cv2.VideoCapture", side_effect=FakeCapture)
    def test_env_override_is_normalized_and_used(self, _capture, _system) -> None:
        import roadvision.camera as camera_module

        capture = self._prepare()

        expected = camera_module.cv2.VideoWriter_fourcc(*"YUYV")
        self.assertEqual(
            capture.set_calls[0],
            (camera_module.cv2.CAP_PROP_FOURCC, expected),
        )


if __name__ == "__main__":
    unittest.main()
