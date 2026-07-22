from __future__ import annotations

import unittest

import numpy as np
import torch
from ultralytics.engine.results import Results

from roadvision.config import MODEL_SPECS
from roadvision.models.yolo import YoloModelAdapter


class AnnotationTests(unittest.TestCase):
    def test_detection_at_image_edge_uses_safe_ascii_label_path(self) -> None:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        spec = next(spec for spec in MODEL_SPECS if spec.id == "traffic_sign")
        result = Results(
            orig_img=frame,
            path="edge.jpg",
            names={0: "soladonulmez"},
            boxes=torch.tensor([[120.0, 4.0, 159.0, 116.0, 0.95, 0.0]]),
        )
        adapter = YoloModelAdapter(spec, "cpu", 0.25)

        annotated, count = adapter.annotate(frame.copy(), result)

        self.assertEqual(annotated.shape, frame.shape)
        self.assertEqual(count, 1)

    def test_semantic_background_is_not_colorized(self) -> None:
        frame = np.full((80, 100, 3), 100, dtype=np.uint8)
        mask = torch.zeros((80, 100), dtype=torch.uint8)
        mask[30:40, 15:85] = 1
        spec = next(spec for spec in MODEL_SPECS if spec.id == "roadline")
        result = Results(
            orig_img=frame,
            path="road.jpg",
            names={0: "road_line"},
            semantic_mask=mask,
        )
        adapter = YoloModelAdapter(spec, "cpu", 0.25)

        annotated, count = adapter.annotate(frame.copy(), result)

        self.assertEqual(count, 1)
        self.assertTrue(np.array_equal(annotated[0, 0], frame[0, 0]))
        self.assertFalse(np.array_equal(annotated[35, 50], frame[35, 50]))


if __name__ == "__main__":
    unittest.main()
