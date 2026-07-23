from __future__ import annotations

import time
import unittest

import numpy as np

from roadvision.logbook import DetectionSuppressor, EventJournal, LogCategory, LogRecord, LogSink
from roadvision.models.detections import DetectedObject, extract_objects


class FakeBoxes:
    def __init__(self, cls, conf, xyxy) -> None:
        self.cls = np.asarray(cls)
        self.conf = np.asarray(conf)
        self.xyxy = np.asarray(xyxy)

    def __len__(self) -> int:
        return len(self.cls)


class FakeResult:
    def __init__(self, boxes=None, names=None, semantic_mask=None) -> None:
        self.boxes = boxes
        self.names = names or {}
        if semantic_mask is not None:
            self.semantic_mask = semantic_mask


class FakeMask:
    def __init__(self, data) -> None:
        self.data = np.asarray(data)


class MemorySink(LogSink):
    def __init__(self) -> None:
        self.records: list[LogRecord] = []

    def prepare_sink(self) -> None: ...

    def write_record(self, record: LogRecord) -> None:
        self.records.append(record)

    def release_sink(self) -> None: ...


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ExtractObjectsTests(unittest.TestCase):
    def test_boxes_yield_class_confidence_and_bbox(self) -> None:
        result = FakeResult(
            boxes=FakeBoxes(
                cls=[0, 1],
                conf=[0.91, 0.42],
                xyxy=[[10.0, 20.0, 110.0, 220.0], [5.0, 5.0, 50.0, 50.0]],
            ),
            names={0: "pothole", 1: "manhole"},
        )
        objects = extract_objects(result, "pothole_model")
        self.assertEqual(len(objects), 2)
        self.assertEqual(objects[0].class_name, "pothole")
        self.assertAlmostEqual(objects[0].confidence, 0.91)
        self.assertEqual(objects[0].bbox, (10.0, 20.0, 110.0, 220.0))
        self.assertEqual(objects[1].class_name, "manhole")

    def test_unknown_class_id_falls_back_to_numeric_name(self) -> None:
        result = FakeResult(boxes=FakeBoxes([7], [0.5], [[0, 0, 1, 1]]), names={})
        objects = extract_objects(result, "m")
        self.assertEqual(objects[0].class_name, "7")

    def test_empty_boxes_yield_nothing(self) -> None:
        result = FakeResult(boxes=FakeBoxes([], [], np.zeros((0, 4))))
        self.assertEqual(extract_objects(result, "m"), ())

    def test_semantic_mask_yields_single_object_with_area_ratio(self) -> None:
        mask = FakeMask([[0, 1], [1, 1]])
        objects = extract_objects(FakeResult(semantic_mask=mask), "roadline")
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].class_name, "roadline")
        self.assertIsNone(objects[0].confidence)
        self.assertAlmostEqual(objects[0].area_ratio, 0.75)

    def test_empty_semantic_mask_yields_nothing(self) -> None:
        objects = extract_objects(FakeResult(semantic_mask=FakeMask([[0, 0]])), "roadline")
        self.assertEqual(objects, ())

    def test_malformed_result_never_raises(self) -> None:
        class Broken:
            @property
            def boxes(self):
                raise RuntimeError("bozuk")

        self.assertEqual(extract_objects(Broken(), "m"), ())

    def test_to_payload_rounds_values(self) -> None:
        obj = DetectedObject("pothole", 0.91234, (1.05, 2.0, 3.0, 4.0))
        payload = obj.to_payload()
        self.assertEqual(payload["class"], "pothole")
        self.assertEqual(payload["confidence"], 0.9123)
        self.assertEqual(payload["bbox"], [1.1, 2.0, 3.0, 4.0])


class ClassSignatureTests(unittest.TestCase):
    def make_journal(self) -> tuple[EventJournal, MemorySink]:
        memory = MemorySink()
        journal = EventJournal(sinks=[memory], suppressor=DetectionSuppressor(heartbeat_seconds=0))
        journal.prepare_journal()
        self.addCleanup(journal.release_journal)
        return journal, memory

    def detections(self, memory: MemorySink) -> list[LogRecord]:
        return [r for r in memory.records if r.category == LogCategory.DETECTION]

    def test_composition_change_with_same_count_is_logged(self) -> None:
        journal, memory = self.make_journal()
        two_potholes = [DetectedObject("pothole", 0.9), DetectedObject("pothole", 0.8)]
        mixed = [DetectedObject("pothole", 0.9), DetectedObject("manhole", 0.7)]
        journal.detection(1, "m", "M", 2, 10.0, objects=two_potholes)
        journal.detection(1, "m", "M", 2, 10.0, objects=two_potholes)
        journal.detection(1, "m", "M", 2, 10.0, objects=mixed)  # sayı aynı, tür farklı
        self.assertTrue(wait_until(lambda: len(self.detections(memory)) == 2))
        first, second = self.detections(memory)
        self.assertEqual(first.payload["signature"], (("pothole", 2),))
        self.assertEqual(second.payload["signature"], (("manhole", 1), ("pothole", 1)))

    def test_objects_are_serialized_into_payload(self) -> None:
        journal, memory = self.make_journal()
        journal.detection(
            1, "m", "M", 1, 10.0,
            objects=[DetectedObject("pothole", 0.9, (1.0, 2.0, 3.0, 4.0))],
        )
        self.assertTrue(wait_until(lambda: len(self.detections(memory)) == 1))
        objects = self.detections(memory)[0].payload["objects"]
        self.assertEqual(objects[0]["class"], "pothole")
        self.assertEqual(objects[0]["confidence"], 0.9)

    def test_without_objects_count_signature_is_preserved(self) -> None:
        journal, memory = self.make_journal()
        journal.detection(1, "m", "M", 3, 10.0)
        self.assertTrue(wait_until(lambda: len(self.detections(memory)) == 1))
        self.assertEqual(self.detections(memory)[0].payload["signature"], 3)
        self.assertNotIn("objects", self.detections(memory)[0].payload)

    def test_explicit_signature_overrides_objects(self) -> None:
        journal, memory = self.make_journal()
        journal.detection(1, "m", "M", 1, 10.0, signature="özel", objects=[DetectedObject("x", 0.5)])
        self.assertTrue(wait_until(lambda: len(self.detections(memory)) == 1))
        self.assertEqual(self.detections(memory)[0].payload["signature"], "özel")

    def test_capture_id_forces_event_when_journal_signature_is_unchanged(self) -> None:
        journal, memory = self.make_journal()
        objects = [DetectedObject("pothole", 0.9, (1.0, 2.0, 3.0, 4.0))]
        journal.detection(
            1,
            "m",
            "M",
            1,
            10.0,
            capture_id="11111111-1111-4111-8111-111111111111",
            objects=objects,
        )
        journal.detection(
            1,
            "m",
            "M",
            1,
            10.0,
            capture_id="22222222-2222-4222-8222-222222222222",
            objects=objects,
        )

        self.assertTrue(wait_until(lambda: len(self.detections(memory)) == 2))
        first, second = self.detections(memory)
        self.assertEqual(first.payload["dedup"], "changed")
        self.assertEqual(second.payload["dedup"], "capture")
        self.assertEqual(
            second.payload["capture_id"],
            "22222222-2222-4222-8222-222222222222",
        )


if __name__ == "__main__":
    unittest.main()
