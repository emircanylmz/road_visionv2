from __future__ import annotations

import unittest
from unittest.mock import Mock

import numpy as np

from roadvision.config import MODEL_SPECS
from roadvision.models.yolo import YoloModelAdapter


class MpsFallbackTests(unittest.TestCase):
    def test_nms_mps_error_retries_on_cpu(self) -> None:
        adapter = YoloModelAdapter(MODEL_SPECS[2], "mps", 0.35)
        expected = object()
        model = Mock()
        model.predict.side_effect = [
            RuntimeError("The operator 'torchvision::nms' is not currently implemented for the MPS device"),
            [expected],
        ]
        adapter._model = model

        result = adapter.predict(np.zeros((32, 32, 3), dtype=np.uint8))

        self.assertIs(result, expected)
        self.assertEqual(adapter.device, "cpu")
        self.assertEqual(model.predict.call_count, 2)
        self.assertEqual(model.predict.call_args.kwargs["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
