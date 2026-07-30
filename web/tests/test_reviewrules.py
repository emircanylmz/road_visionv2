"""reviewrules saf kural modülünün sözleşme testleri (DB'siz)."""

from __future__ import annotations

import unittest

from app.reviewrules import (
    DetectionContext,
    ReviewRuleError,
    normalize_review,
)

_TYPES = {"cukur": 7, "rogar": 8}


def _ctx(**overrides):
    defaults = dict(
        object_id=1,
        model_id="pothole",
        model_task="detect",
        type_id=7,
        class_name="cukur",
        bbox=(10.0, 20.0, 110.0, 220.0),
        frame_w=1280,
        frame_h=720,
    )
    defaults.update(overrides)
    return DetectionContext(**defaults)


class PlainVerdictTests(unittest.TestCase):
    def test_correct_ozgun_etiketi_dondurur(self):
        n = normalize_review(
            _ctx(), verdict="correct", corrected_bbox=None,
            corrected_class=None, model_types=_TYPES,
        )
        self.assertEqual(
            (n.verdict, n.final_type_id, n.final_class_name, n.final_bbox),
            ("correct", 7, "cukur", (10.0, 20.0, 110.0, 220.0)),
        )
        self.assertIsNone(n.corrected_type_id)

    def test_wrong_da_ozgun_etiket_dondurulur(self):
        n = normalize_review(
            _ctx(), verdict="wrong", corrected_bbox=None,
            corrected_class=None, model_types=_TYPES,
        )
        self.assertEqual((n.verdict, n.final_class_name), ("wrong", "cukur"))

    def test_correct_duzeltme_tasiyamaz(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="correct",
                corrected_bbox=[1, 2, 3, 4], corrected_class=None,
                model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "unexpected_correction")

    def test_gecersiz_karar(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="belki", corrected_bbox=None,
                corrected_class=None, model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "invalid_verdict")


class CorrectedTests(unittest.TestCase):
    def test_semantic_model_duzeltme_alamaz(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(model_id="roadline", model_task="segment"),
                verdict="corrected", corrected_bbox=None,
                corrected_class="cukur", model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "semantic_no_correction")

    def test_katalog_disi_model_de_detect_degilse_reddedilir(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(model_task="unknown"),
                verdict="corrected", corrected_bbox=None,
                corrected_class="rogar", model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "semantic_no_correction")

    def test_duzeltmesiz_corrected_reddedilir(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="corrected", corrected_bbox=None,
                corrected_class=None, model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "correction_required")

    def test_capraz_model_sinifi_sozlukte_yoktur(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="corrected", corrected_bbox=None,
                corrected_class="dur_tabelasi", model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "unknown_class")

    def test_ayni_sinif_kutusuz_degisiklik_yok(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="corrected", corrected_bbox=None,
                corrected_class="cukur", model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "no_change")

    def test_sinif_duzeltmesi_final_etiketi_gunceller(self):
        n = normalize_review(
            _ctx(), verdict="corrected", corrected_bbox=None,
            corrected_class="rogar", model_types=_TYPES,
        )
        self.assertEqual(
            (n.corrected_type_id, n.final_type_id, n.final_class_name),
            (8, 8, "rogar"),
        )
        # Kutu düzeltilmedi: final kutu özgün kutudur.
        self.assertEqual(n.final_bbox, (10.0, 20.0, 110.0, 220.0))
        self.assertIsNone(n.corrected_bbox)

    def test_kutu_duzeltmesi_dogrulanir_ve_final_olur(self):
        n = normalize_review(
            _ctx(), verdict="corrected",
            corrected_bbox=[15.0, 25.0, 120.0, 230.0],
            corrected_class=None, model_types=_TYPES,
        )
        self.assertEqual(n.final_bbox, (15.0, 25.0, 120.0, 230.0))
        self.assertEqual(n.final_type_id, 7)  # sınıf değişmedi

    def test_kare_disi_kutu_invalid_bbox(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(), verdict="corrected",
                corrected_bbox=[0, 0, 5000, 100],
                corrected_class=None, model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "invalid_bbox")

    def test_kare_boyutu_yoksa_kutu_duzeltmesi_reddedilir(self):
        with self.assertRaises(ReviewRuleError) as caught:
            normalize_review(
                _ctx(frame_w=None, frame_h=None),
                verdict="corrected",
                corrected_bbox=[1, 2, 30, 40],
                corrected_class=None, model_types=_TYPES,
            )
        self.assertEqual(caught.exception.code, "frame_unavailable")

    def test_kare_boyutu_yokken_sinif_duzeltmesi_serbesttir(self):
        n = normalize_review(
            _ctx(frame_w=None, frame_h=None, bbox=None),
            verdict="corrected", corrected_bbox=None,
            corrected_class="rogar", model_types=_TYPES,
        )
        self.assertEqual(n.final_class_name, "rogar")
        self.assertIsNone(n.final_bbox)


if __name__ == "__main__":
    unittest.main()
