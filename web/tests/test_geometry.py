"""geometry yardımcılarının sözleşme testleri; §4.6 ±1 px kabulü burada sabitlenir."""

from __future__ import annotations

import unittest

from app.geometry import clamp_bbox, scale_bbox, validate_bbox


class ValidateBboxTests(unittest.TestCase):
    def test_gecerli_kutu_normalize_doner(self):
        self.assertEqual(
            validate_bbox([10, 20, 110, 220], 1280, 720),
            (10.0, 20.0, 110.0, 220.0),
        )

    def test_ters_ve_bicimsiz_kutular(self):
        for bozuk in ([1, 2, 3], [100, 20, 10, 220], [10, 220, 110, 20],
                      [10, 20, 10, 220], ["a", 2, 3, 4],
                      [float("nan"), 2, 3, 4], [float("inf"), 2, 3, 4]):
            with self.subTest(bbox=bozuk):
                with self.assertRaises(ValueError):
                    validate_bbox(bozuk, 1280, 720)

    def test_kare_disi_reddedilir_tolerans_iceri_oturur(self):
        with self.assertRaises(ValueError):
            validate_bbox([0, 0, 1282.5, 100], 1280, 720)
        # ±1 px toleransındaki taşma kabul edilir ve kareye oturtulur.
        x1, y1, x2, y2 = validate_bbox([-0.5, 0, 1280.9, 720.4], 1280, 720)
        self.assertEqual((x1, y1, x2, y2), (0.0, 0.0, 1280.0, 720.0))

    def test_gecersiz_kare_boyutu(self):
        with self.assertRaises(ValueError):
            validate_bbox([0, 0, 10, 10], 0, 720)


class ScaleBboxTests(unittest.TestCase):
    def test_bilincli_kucultulmus_goruntuyle_gidis_donus_1px_icinde(self):
        # §4.6 kabulü: 1920×1080 kare, MEDIA_MAX_EDGE benzeri 1280×720
        # gösterim; ekran koordinatına in, geri dön.
        frame, display = (1920, 1080), (1280, 720)
        original = (101.3, 57.7, 1523.9, 998.2)
        display_box = scale_bbox(original, frame, display)
        round_trip = scale_bbox(display_box, display, frame)
        for once, sonra in zip(original, round_trip):
            self.assertLessEqual(abs(once - sonra), 1.0)

    def test_orantisiz_olcek_ve_tamsayi_yuvarlama_1px_icinde(self):
        frame, display = (1024, 1024), (333, 333)
        original = (10.0, 10.0, 1000.0, 1000.0)
        display_box = tuple(
            round(value) for value in scale_bbox(original, frame, display)
        )
        round_trip = scale_bbox(display_box, display, frame)
        for once, sonra in zip(original, round_trip):
            self.assertLessEqual(abs(once - sonra), 2.0)  # yuvarlama dahil
        # Yuvarlamasız saf gidiş-dönüş ±1 px sözleşmesi:
        pure = scale_bbox(scale_bbox(original, frame, display), display, frame)
        for once, sonra in zip(original, pure):
            self.assertLessEqual(abs(once - sonra), 1.0)

    def test_gecersiz_boyut(self):
        with self.assertRaises(ValueError):
            scale_bbox((0, 0, 1, 1), (0, 10), (10, 10))


class ClampTests(unittest.TestCase):
    def test_clamp_kare_icine_oturtur(self):
        self.assertEqual(
            clamp_bbox((-5.0, -1.0, 1290.0, 725.0), 1280, 720),
            (0.0, 0.0, 1280.0, 720.0),
        )


if __name__ == "__main__":
    unittest.main()
