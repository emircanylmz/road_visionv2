"""exportbuild saf modülünün sözleşme testleri (§9 Faz 5 kabulü)."""

from __future__ import annotations

import io
import json
import unittest
import zipfile

from app.exportbuild import (
    ExportSample,
    assemble_zip,
    build_class_map,
    build_yolo_entries,
    yolo_line,
)


def _sample(**overrides):
    defaults = dict(
        object_id=1, verdict="correct", final_type_id=7,
        final_bbox=(100.0, 80.0, 400.0, 360.0),
        frame_w=1280, frame_h=720, original_sha="a" * 64,
    )
    defaults.update(overrides)
    return ExportSample(**defaults)


class ClassMapTests(unittest.TestCase):
    def test_class_index_sirasi_ve_katalog_disi_sona(self):
        names, mapping = build_class_map(
            [(30, None, "yeni_sinif"), (11, 1, "rogar"), (10, 0, "cukur")]
        )
        self.assertEqual(names, ["cukur", "rogar", "yeni_sinif"])
        self.assertEqual(mapping, {10: 0, 11: 1, 30: 2})

    def test_ayni_indekste_ada_gore_deterministik(self):
        names_a, _ = build_class_map([(2, None, "b"), (1, None, "a")])
        names_b, _ = build_class_map([(1, None, "a"), (2, None, "b")])
        self.assertEqual(names_a, names_b)


class YoloLineTests(unittest.TestCase):
    def test_normalize_kordinat_ve_gidis_donus(self):
        line = yolo_line((100.0, 80.0, 400.0, 360.0), 1280, 720, 3)
        idx, cx, cy, w, h = line.split()
        self.assertEqual(idx, "3")
        # Geri hesap: kutu ±1 px içinde çıkmalı (§4.6 zinciri).
        cx, cy, w, h = float(cx), float(cy), float(w), float(h)
        x1 = (cx - w / 2) * 1280
        x2 = (cx + w / 2) * 1280
        y1 = (cy - h / 2) * 720
        y2 = (cy + h / 2) * 720
        for beklenen, bulunan in zip((100, 80, 400, 360), (x1, y1, x2, y2)):
            self.assertLessEqual(abs(beklenen - bulunan), 1.0)

    def test_kenetleme_0_1(self):
        line = yolo_line((-10.0, -10.0, 1290.0, 730.0), 1280, 720, 0)
        values = [float(part) for part in line.split()[1:]]
        self.assertTrue(all(0.0 <= value <= 1.0 for value in values))

    def test_gecersiz_kare(self):
        with self.assertRaises(ValueError):
            yolo_line((0, 0, 1, 1), 0, 720, 0)


class EntriesTests(unittest.TestCase):
    def test_ayni_kare_tek_goruntu_cok_satir(self):
        labels, counters = build_yolo_entries(
            [_sample(object_id=1), _sample(object_id=2, final_type_id=8)],
            {7: 0, 8: 1},
            "positive",
        )
        self.assertEqual(counters["sample_count"], 2)
        self.assertEqual(counters["image_count"], 1)
        self.assertEqual(len(labels["a" * 64]), 2)
        self.assertTrue(labels["a" * 64][0].startswith("0 "))
        self.assertTrue(labels["a" * 64][1].startswith("1 "))

    def test_atlananlar_sayilir(self):
        labels, counters = build_yolo_entries(
            [
                _sample(original_sha=None),
                _sample(final_bbox=None),
                _sample(final_type_id=99),
            ],
            {7: 0},
            "positive",
        )
        self.assertEqual(labels, {})
        self.assertEqual(counters["skipped_no_image"], 1)
        self.assertEqual(counters["skipped_no_bbox"], 1)
        self.assertEqual(counters["skipped_unknown_type"], 1)
        self.assertEqual(counters["sample_count"], 0)

    def test_wrong_kapsami_bos_etiket_uretir(self):
        labels, counters = build_yolo_entries(
            [_sample(verdict="wrong", final_bbox=None, frame_w=None,
                     frame_h=None)],
            {7: 0},
            "wrong",
        )
        self.assertEqual(labels, {"a" * 64: []})
        self.assertEqual(counters["sample_count"], 1)

    def test_gecersiz_kapsam(self):
        with self.assertRaises(ValueError):
            build_yolo_entries([], {}, "hepsi")


class ZipTests(unittest.TestCase):
    def test_zip_yolo_duzeni_ve_manifest(self):
        sha = "b" * 64
        payload = assemble_zip(
            model_id="pothole",
            verdict_scope="positive",
            names=["cukur", "rogar"],
            labels={sha: ["0 0.5 0.5 0.1 0.1"]},
            images={sha: b"\xff\xd8jpegbytes"},
            counters={"sample_count": 1, "image_count": 1,
                      "skipped_no_image": 0, "skipped_no_bbox": 0},
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            self.assertEqual(
                names,
                {"data.yaml", "manifest.json",
                 f"labels/{sha}.txt", f"images/{sha}.jpg"},
            )
            yaml_text = archive.read("data.yaml").decode()
            self.assertIn("nc: 2", yaml_text)
            self.assertIn("0: cukur", yaml_text)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["model_id"], "pothole")
            self.assertEqual(
                archive.read(f"labels/{sha}.txt").decode(),
                "0 0.5 0.5 0.1 0.1\n",
            )
            self.assertEqual(
                archive.getinfo(f"images/{sha}.jpg").compress_type,
                zipfile.ZIP_STORED,
            )

    def test_bos_etiket_dosyasi_bos_yazilir(self):
        sha = "c" * 64
        payload = assemble_zip(
            model_id="pothole", verdict_scope="wrong", names=["cukur"],
            labels={sha: []}, images={sha: b"x"},
            counters={"sample_count": 1, "image_count": 1,
                      "skipped_no_image": 0, "skipped_no_bbox": 0},
        )
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read(f"labels/{sha}.txt"), b"")


if __name__ == "__main__":
    unittest.main()
