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


if __name__ == "__main__":
    unittest.main()
