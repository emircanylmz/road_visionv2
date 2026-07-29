from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from roadvision.models.manager import ModelManager


class DevicePolicyTests(unittest.TestCase):
    @patch("roadvision.models.manager.mps_detect_supported")
    def test_cpu_manager_does_not_probe_torchvision(self, supported) -> None:
        manager = ModelManager(device="cpu")

        supported.assert_not_called()
        self.assertEqual(manager.device_label, "cpu")
        manager.release_models()

    @patch("roadvision.models.manager.mps_detect_supported", return_value=False)
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_mps_detection_model_is_created_on_cpu_with_legacy_torchvision(
        self, adapter_class, _supported
    ) -> None:
        adapter_class.return_value = Mock()
        manager = ModelManager(device="mps")

        manager.prepare_model("traffic_sign")

        args = adapter_class.call_args.args
        self.assertEqual(args[1], "cpu")
        self.assertEqual(manager.device_label, "mps + cpu(det)")
        manager.release_models()

    @patch("roadvision.models.manager.mps_detect_supported", return_value=True)
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_mps_detection_model_stays_on_mps_with_native_nms(
        self, adapter_class, _supported
    ) -> None:
        adapter_class.return_value = Mock()
        manager = ModelManager(device="mps")

        manager.prepare_model("traffic_sign")

        args = adapter_class.call_args.args
        self.assertEqual(args[1], "mps")
        self.assertEqual(manager.device_label, "mps")
        manager.release_models()

    @patch("roadvision.models.manager.mps_detect_supported", return_value=False)
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_pure_semantic_model_remains_on_mps(
        self, adapter_class, _supported
    ) -> None:
        adapter_class.return_value = Mock()
        manager = ModelManager(device="mps")

        manager.prepare_model("roadline")

        args = adapter_class.call_args.args
        self.assertEqual(args[1], "mps")
        manager.release_models()


class CudaWarmupTests(unittest.TestCase):
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_cuda_adapter_receives_one_synthetic_warmup_predict(
        self,
        adapter_class,
    ) -> None:
        adapter = Mock()
        adapter_class.return_value = adapter
        statuses: list[str] = []
        manager = ModelManager(device="cuda:0", status_callback=statuses.append)

        manager.prepare_model("pothole")
        manager.prepare_model("pothole")

        adapter.predict.assert_called_once()
        frame = adapter.predict.call_args.args[0]
        self.assertEqual(frame.shape, (64, 64, 3))
        self.assertTrue(any("ısındır" in status for status in statuses))
        manager.release_models()

    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_failed_cuda_warmup_releases_partial_adapter(
        self,
        adapter_class,
    ) -> None:
        adapter = Mock()
        adapter.predict.side_effect = RuntimeError("warmup başarısız")
        adapter_class.return_value = adapter
        manager = ModelManager(device="cuda:0")

        with self.assertRaisesRegex(RuntimeError, "warmup başarısız"):
            manager.prepare_model("pothole")

        adapter.release_model.assert_called_once()
        manager.release_models()

    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_cpu_adapter_is_not_warmed_up(self, adapter_class) -> None:
        adapter = Mock()
        adapter_class.return_value = adapter
        manager = ModelManager(device="cpu")

        manager.prepare_model("pothole")

        adapter.predict.assert_not_called()
        manager.release_models()

    @patch("roadvision.models.manager.mps_detect_supported", return_value=False)
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_mps_compat_cpu_adapter_is_not_warmed_up(
        self,
        adapter_class,
        _supported,
    ) -> None:
        adapter = Mock()
        adapter_class.return_value = adapter
        manager = ModelManager(device="mps")

        manager.prepare_model("traffic_sign")

        adapter.predict.assert_not_called()
        manager.release_models()


if __name__ == "__main__":
    unittest.main()
