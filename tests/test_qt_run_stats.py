"""Qt Çalışma Özeti akümülatörünün (RunStats) saf birim testleri."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from roadvision.qt.run_stats import BUCKET_SECONDS, MAX_BUCKETS, RunStats


def _stat(model_id="pothole", count=2, elapsed=9.0, classes=("pothole", "manhole")):
    objects = tuple(
        SimpleNamespace(class_name=name, confidence=0.5 + index * 0.1)
        for index, name in enumerate(classes[:count])
    )
    return SimpleNamespace(
        model_id=model_id,
        display_name="Çukur",
        object_count=count,
        elapsed_ms=elapsed,
        objects=objects,
    )


class RunStatsTests(unittest.TestCase):
    def test_reset_clears_previous_run(self) -> None:
        stats = RunStats()
        stats.reset(run_id=1, source_name="a", device="cpu", profile_label="Kalite")
        stats.note_frame((_stat(),), inference_fps=20.0, total_ms=50.0, ts=100.0)
        stats.note_capture("cap-1")
        stats.reset(run_id=2, source_name="b", device="cpu", profile_label="Hızlı")
        self.assertEqual(stats.frames, 0)
        self.assertEqual(stats.total_objects, 0)
        self.assertEqual(stats.captures, set())
        self.assertEqual(stats.info.run_id, 2)

    def test_note_frame_aggregates_models_and_types(self) -> None:
        stats = RunStats()
        stats.reset(run_id=1, source_name="a", device="cpu", profile_label="Kalite")
        stats.note_frame((_stat(count=2),), inference_fps=20.0, total_ms=50.0, ts=100.0)
        stats.note_frame((_stat(count=1),), inference_fps=10.0, total_ms=80.0, ts=101.0)
        self.assertEqual(stats.frames, 2)
        self.assertEqual(stats.total_objects, 3)
        self.assertEqual(stats.fps_min, 10.0)
        self.assertEqual(stats.fps_max, 20.0)
        aggregate = stats.models["pothole"]
        self.assertEqual(aggregate.last_count, 1)
        self.assertEqual(aggregate.object_count, 3)
        pothole_type = stats.types[("pothole", "pothole")]
        self.assertEqual(pothole_type.count, 2)
        self.assertAlmostEqual(pothole_type.mean_confidence or 0.0, 0.5)

    def test_bucket_series_is_bounded(self) -> None:
        stats = RunStats()
        stats.reset(run_id=1, source_name="a", device="cpu", profile_label="Kalite")
        for index in range(MAX_BUCKETS + 4):
            stats.note_frame(
                (_stat(count=1),),
                inference_fps=20.0,
                total_ms=50.0,
                ts=1000.0 + index * BUCKET_SECONDS,
            )
        series = stats.bucket_series()
        self.assertEqual(len(series), MAX_BUCKETS)
        self.assertTrue(all(bucket["pothole"] == 1 for _label, bucket in series))

    def test_breakdown_includes_untyped_models(self) -> None:
        stats = RunStats()
        stats.reset(run_id=1, source_name="a", device="cpu", profile_label="Kalite")
        semantic = SimpleNamespace(
            model_id="roadline",
            display_name="Yol Çizgisi",
            object_count=4,
            elapsed_ms=21.0,
            objects=(),
        )
        stats.note_frame(
            (_stat(count=1), semantic), inference_fps=20.0, total_ms=50.0, ts=100.0
        )
        rows = stats.breakdown_rows()
        names = {(row.model_id, row.class_name) for row in rows}
        self.assertIn(("pothole", "pothole"), names)
        self.assertIn(("roadline", "Yol Çizgisi"), names)

    def test_finish_freezes_duration(self) -> None:
        stats = RunStats()
        stats.reset(
            run_id=1,
            source_name="a",
            device="cpu",
            profile_label="Kalite",
            started_at=100.0,
        )
        stats.finish(ts=160.0)
        self.assertEqual(stats.duration_seconds, 60.0)
        stats.finish(ts=999.0)  # ikinci çağrı bitişi değiştirmez
        self.assertEqual(stats.duration_seconds, 60.0)


if __name__ == "__main__":
    unittest.main()
