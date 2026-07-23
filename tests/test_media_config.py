from __future__ import annotations

import unittest

from roadvision.config import MediaConfig
from roadvision.media import MediaRecorder, NullRecorder, create_default_recorder


class MediaConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_complete(self) -> None:
        config = MediaConfig.from_env({})
        self.assertEqual(config.backend, "db")
        self.assertEqual(config.jpeg_quality, 80)
        self.assertEqual(config.max_edge, 1280)
        self.assertEqual(config.queue_max_mb, 256)
        self.assertEqual(config.retention_days, 30)
        self.assertEqual(config.max_total_mb, 2048)

    def test_environment_overrides_are_parsed(self) -> None:
        config = MediaConfig.from_env(
            {
                "ROADVISION_MEDIA": "OFF",
                "ROADVISION_MEDIA_JPEG_QUALITY": "72",
                "ROADVISION_MEDIA_MIN_INTERVAL_S": "0.5",
                "ROADVISION_MEDIA_QUEUE_SIZE": "3",
            }
        )
        self.assertEqual(config.backend, "off")
        self.assertEqual(config.jpeg_quality, 72)
        self.assertEqual(config.min_interval_s, 0.5)
        self.assertEqual(config.queue_size, 3)

    def test_invalid_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ROADVISION_MEDIA"):
            MediaConfig.from_env({"ROADVISION_MEDIA": "filesystem"})
        with self.assertRaisesRegex(ValueError, "JPEG_QUALITY"):
            MediaConfig.from_env({"ROADVISION_MEDIA_JPEG_QUALITY": "101"})
        with self.assertRaisesRegex(ValueError, "QUEUE_SIZE"):
            MediaConfig.from_env({"ROADVISION_MEDIA_QUEUE_SIZE": "x"})

    def test_factory_is_null_without_dsn_or_when_off(self) -> None:
        self.assertIsInstance(create_default_recorder(environ={}), NullRecorder)
        self.assertIsInstance(
            create_default_recorder(
                environ={
                    "ROADVISION_MEDIA": "off",
                    "ROADVISION_DB_DSN": "postgresql://unused",
                }
            ),
            NullRecorder,
        )

    def test_factory_builds_recorder_when_db_is_configured(self) -> None:
        recorder = create_default_recorder(
            environ={
                "ROADVISION_MEDIA": "db",
                "ROADVISION_DB_DSN": "postgresql://not-connected-yet",
            }
        )
        self.assertIsInstance(recorder, MediaRecorder)
        # Factory tembel bağlantı kurar; hazırlanmadığı için ağ erişimi olmaz.
        self.assertTrue(recorder.release_recorder(timeout=0.0))


if __name__ == "__main__":
    unittest.main()
