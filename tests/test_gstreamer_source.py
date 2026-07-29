from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from roadvision.sources import (
    CsiCameraInfo,
    GStreamerCameraSource,
    SourceFactory,
    build_nvargus_pipeline,
    configured_csi_cameras,
    gstreamer_available,
)


class FakeGstCapture:
    """cv2.VideoCapture'ı taklit eder; açılış ve read davranışı ayarlanabilir."""

    opens = True
    frames_before_eof = 3
    last_init: tuple | None = None

    def __init__(self, pipeline: str, backend: int) -> None:
        FakeGstCapture.last_init = (pipeline, backend)
        self._opened = FakeGstCapture.opens
        self._remaining = FakeGstCapture.frames_before_eof
        self.released = False

    def isOpened(self) -> bool:
        return self._opened and not self.released

    def read(self):
        if not self.isOpened() or self._remaining <= 0:
            return False, None
        self._remaining -= 1
        return True, np.zeros((720, 1280, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


class BuildNvargusPipelineTests(unittest.TestCase):
    def test_pipeline_contains_sensor_and_latest_frame_sink(self) -> None:
        pipeline = build_nvargus_pipeline(
            sensor_id=1,
            width=1920,
            height=1080,
            fps=60,
            flip_method=2,
        )

        self.assertIn("nvarguscamerasrc sensor-id=1", pipeline)
        self.assertIn("width=1920", pipeline)
        self.assertIn("height=1080", pipeline)
        self.assertIn("framerate=60/1", pipeline)
        self.assertIn("flip-method=2", pipeline)
        self.assertIn("format=BGR ", pipeline)
        self.assertIn("appsink drop=1 max-buffers=1", pipeline)

    def test_invalid_camera_parameters_are_rejected(self) -> None:
        invalid_options = (
            {"sensor_id": -1},
            {"width": 0},
            {"height": 0},
            {"fps": 0},
            {"flip_method": 8},
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    build_nvargus_pipeline(**options)


class ConfiguredCsiCameraTests(unittest.TestCase):
    def test_empty_configuration_adds_no_camera(self) -> None:
        self.assertEqual(configured_csi_cameras(environ={}), ())

    def test_sensor_list_is_normalized_and_deduplicated(self) -> None:
        cameras = configured_csi_cameras(
            width=1920,
            height=1080,
            fps=60,
            environ={
                "ROADVISION_CSI_SENSORS": "0, 1,0",
                "ROADVISION_CSI_FLIP_METHOD": "2",
            },
        )

        self.assertEqual([camera.sensor_id for camera in cameras], [0, 1])
        self.assertTrue(all(isinstance(camera, CsiCameraInfo) for camera in cameras))
        self.assertEqual(
            (cameras[0].width, cameras[0].height, cameras[0].fps),
            (1920, 1080, 60),
        )
        self.assertEqual(cameras[0].flip_method, 2)
        self.assertIn("CSI Kamera 0", str(cameras[0]))

    def test_invalid_sensor_or_flip_configuration_is_rejected(self) -> None:
        invalid_environments = (
            {"ROADVISION_CSI_SENSORS": "-1"},
            {"ROADVISION_CSI_SENSORS": "0,"},
            {"ROADVISION_CSI_SENSORS": "kamera"},
            {
                "ROADVISION_CSI_SENSORS": "0",
                "ROADVISION_CSI_FLIP_METHOD": "8",
            },
            {
                "ROADVISION_CSI_SENSORS": "0",
                "ROADVISION_CSI_FLIP_METHOD": "yan",
            },
        )
        for environ in invalid_environments:
            with self.subTest(environ=environ):
                with self.assertRaises(ValueError):
                    configured_csi_cameras(environ=environ)


class GStreamerAvailabilityTests(unittest.TestCase):
    @patch(
        "roadvision.sources.cv2.getBuildInformation",
        return_value="  GStreamer:                   YES (1.20.3)",
    )
    def test_yes_line_reports_available(self, _info) -> None:
        self.assertTrue(gstreamer_available())

    @patch(
        "roadvision.sources.cv2.getBuildInformation",
        return_value="  GStreamer:                   NO",
    )
    def test_no_line_reports_unavailable(self, _info) -> None:
        self.assertFalse(gstreamer_available())

    @patch(
        "roadvision.sources.cv2.getBuildInformation",
        side_effect=RuntimeError("boom"),
    )
    def test_build_info_failure_is_conservative(self, _info) -> None:
        self.assertFalse(gstreamer_available())


class GStreamerCameraSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeGstCapture.opens = True
        FakeGstCapture.frames_before_eof = 3
        FakeGstCapture.last_init = None

    def test_empty_pipeline_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "boş olamaz"):
            GStreamerCameraSource("   ")

    @patch("roadvision.sources.cv2.VideoCapture", side_effect=FakeGstCapture)
    def test_prepare_opens_with_gstreamer_backend(self, _capture) -> None:
        import roadvision.sources as sources_module

        source = SourceFactory.create_csi_camera(sensor_id=0)
        source.prepare_source()

        pipeline, backend = FakeGstCapture.last_init
        self.assertIn("nvarguscamerasrc sensor-id=0", pipeline)
        self.assertEqual(backend, sources_module.cv2.CAP_GSTREAMER)
        self.assertEqual(source.display_name, "CSI Kamera 0")
        source.release_source()

    @patch("roadvision.sources.gstreamer_available", return_value=False)
    @patch("roadvision.sources.cv2.VideoCapture", side_effect=FakeGstCapture)
    def test_open_failure_without_gstreamer_build_gives_install_hint(
        self,
        _capture,
        _available,
    ) -> None:
        FakeGstCapture.opens = False
        source = GStreamerCameraSource("nvarguscamerasrc ! fakesink")

        with self.assertRaisesRegex(RuntimeError, "system-site-packages"):
            source.prepare_source()

    @patch("roadvision.sources.gstreamer_available", return_value=True)
    @patch("roadvision.sources.cv2.VideoCapture", side_effect=FakeGstCapture)
    def test_open_failure_with_gstreamer_build_reports_pipeline(
        self,
        _capture,
        _available,
    ) -> None:
        FakeGstCapture.opens = False
        source = GStreamerCameraSource("nvarguscamerasrc ! fakesink")

        with self.assertRaisesRegex(RuntimeError, "pipeline açılamadı"):
            source.prepare_source()

    @patch("roadvision.sources.cv2.VideoCapture", side_effect=FakeGstCapture)
    def test_stream_yields_until_eof_and_respects_stop_event(self, _capture) -> None:
        source = GStreamerCameraSource("pipeline", display_name="Test CSI")
        source.prepare_source()
        stop_event = threading.Event()

        frames = list(source.get_stream(stop_event))
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0].shape, (720, 1280, 3))

        source.prepare_source()
        stop_event.set()
        self.assertEqual(list(source.get_stream(stop_event)), [])
        source.release_source()

    @patch("roadvision.sources.cv2.VideoCapture", side_effect=FakeGstCapture)
    def test_release_is_idempotent_and_stops_stream(self, _capture) -> None:
        source = GStreamerCameraSource("pipeline")
        source.prepare_source()
        source.release_source()
        source.release_source()

        self.assertEqual(list(source.get_stream(threading.Event())), [])


class QtCsiSourceWiringTests(unittest.TestCase):
    @patch("roadvision.sources.SourceFactory.create_csi_camera")
    def test_qt_camera_selection_uses_configured_csi_source(
        self,
        create_csi,
    ) -> None:
        from roadvision.qt.main_window import RoadVisionQtApp

        live = SimpleNamespace(
            source_kind=lambda: "camera",
            camera_index=lambda: 0,
        )
        window = SimpleNamespace(
            live=live,
            _camera_infos=[
                CsiCameraInfo(
                    sensor_id=2,
                    width=1280,
                    height=720,
                    fps=30,
                    flip_method=1,
                )
            ],
        )
        expected = object()
        create_csi.return_value = expected

        result = RoadVisionQtApp._create_source(window)

        self.assertIs(result, expected)
        create_csi.assert_called_once_with(
            sensor_id=2,
            width=1280,
            height=720,
            fps=30,
            flip_method=1,
        )


if __name__ == "__main__":
    unittest.main()
