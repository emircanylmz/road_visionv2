from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from roadvision.models.manager import ModelManager


class DevicePolicyTests(unittest.TestCase):
    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_mps_detection_model_is_created_on_cpu(self, adapter_class) -> None:
        adapter_class.return_value = Mock()
        manager = ModelManager(device="mps")

        manager.prepare_model("traffic_sign")

        args = adapter_class.call_args.args
        self.assertEqual(args[1], "cpu")
        self.assertEqual(manager.device_label, "mps + cpu(det)")
        manager.release_models()

    @patch("roadvision.models.manager.YoloModelAdapter")
    def test_pure_semantic_model_remains_on_mps(self, adapter_class) -> None:
        adapter_class.return_value = Mock()
        manager = ModelManager(device="mps")

        manager.prepare_model("roadline")

        args = adapter_class.call_args.args
        self.assertEqual(args[1], "mps")
        manager.release_models()


if __name__ == "__main__":
    unittest.main()
