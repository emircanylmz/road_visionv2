"""archivequery üreticisinin sözleşme testleri (DB'siz).

Masaüstü parite garantileri burada sabitlenir: FROM zinciri
``roadvision/archive.py._BASE_FROM_SQL`` ile aynı JOIN'leri içermeli, web'in
tek eklemesi ``webapp.detection_reviews`` LEFT JOIN'i olmalıdır.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.archivequery import (
    MAX_PAGE_SIZE,
    SCHEMA_CHECK_SQL,
    SCHEMA_VERSION_SQL,
    TYPE_TREE_SQL,
    ArchiveFilters,
    build_list_query,
)

_TS = datetime(2026, 7, 29, 15, 0, 0, tzinfo=timezone.utc)


class BaseContractTests(unittest.TestCase):
    def test_from_zinciri_masaustu_sozlesmesini_tasir(self):
        sql, _params = build_list_query(ArchiveFilters())
        for parca in (
            "FROM public.detected_objects AS o",
            "JOIN public.detection_events AS e",
            "ON e.id = o.event_id",
            "JOIN public.detection_types AS t",
            "ON t.type_id = o.type_id",
            "LEFT JOIN public.roadvision_model_catalog AS m",
            "LEFT JOIN public.media_captures AS mc",
            "ON mc.capture_id = e.capture_id",
            "LEFT JOIN webapp.detection_reviews AS r",
            "ON r.object_id = o.id",
        ):
            self.assertIn(parca, sql)

    def test_review_status_sqlde_turetilir(self):
        sql, _params = build_list_query(ArchiveFilters())
        self.assertIn("COALESCE(r.verdict, 'unreviewed') AS review_status", sql)
        self.assertIn("mc.original_media_id", sql)
        self.assertIn("mc.annotated_media_id", sql)

    def test_filtresiz_sorgu_limit_arti_bir(self):
        sql, params = build_list_query(ArchiveFilters(), limit=60)
        self.assertNotIn("WHERE", sql)
        self.assertIn("ORDER BY o.ts DESC, o.id DESC", sql)
        self.assertEqual(params, [61])

    def test_sema_kontrolu_ve_tur_agaci_sabitleri(self):
        for tablo in (
            "public.detected_objects",
            "public.detection_events",
            "public.detection_types",
            "public.roadvision_model_catalog",
            "public.media_captures",
            "public.media_blobs",
            "public.media_capture_models",
        ):
            self.assertIn(tablo, SCHEMA_CHECK_SQL)
        self.assertIn("column_name = 'type_id'", SCHEMA_CHECK_SQL)
        self.assertIn("MAX(version)", SCHEMA_VERSION_SQL)
        # Katalog dışı (yalnız çalışma zamanında görülmüş) modeller de ağaçta.
        self.assertIn("UNION ALL", TYPE_TREE_SQL)
        self.assertIn("NOT EXISTS", TYPE_TREE_SQL)
        self.assertIn("'unknown'::text", TYPE_TREE_SQL)
        self.assertIn("am.active DESC", TYPE_TREE_SQL)


class FilterTests(unittest.TestCase):
    def test_model_ve_tur_any_ile(self):
        filters = ArchiveFilters(model_ids=("pothole",), type_ids=(3, 5))
        sql, params = build_list_query(filters, limit=10)
        self.assertIn("o.model_id = ANY(%s)", sql)
        self.assertIn("o.type_id = ANY(%s)", sql)
        self.assertEqual(params, [["pothole"], [3, 5], 11])

    def test_yalniz_unreviewed_karar_satiri_yok_demektir(self):
        sql, params = build_list_query(
            ArchiveFilters(review_statuses=("unreviewed",)), limit=10
        )
        self.assertIn("(r.object_id IS NULL)", sql)
        self.assertNotIn("r.verdict = ANY", sql)
        self.assertEqual(params, [11])

    def test_karma_dogrulama_durumu_or_ile(self):
        sql, params = build_list_query(
            ArchiveFilters(review_statuses=("unreviewed", "wrong", "correct")),
            limit=10,
        )
        self.assertIn("(r.object_id IS NULL OR r.verdict = ANY(%s))", sql)
        self.assertEqual(params, [["wrong", "correct"], 11])

    def test_gecersiz_dogrulama_durumu_reddedilir(self):
        with self.assertRaises(ValueError):
            build_list_query(
                ArchiveFilters(review_statuses=("belirsiz",))
            )

    def test_capture_guven_ve_goruntu_filtreleri(self):
        filters = ArchiveFilters(
            capture_id="0b6f8f5e-8f61-4f3f-9d55-1a2b3c4d5e6f",
            min_confidence=0.5,
            only_with_image=True,
            run_id=4,
        )
        sql, params = build_list_query(filters, limit=10)
        self.assertIn("e.capture_id = %s::uuid", sql)
        self.assertIn("o.confidence >= %s", sql)
        self.assertIn("mc.capture_id IS NOT NULL", sql)
        self.assertIn("o.run_id = %s", sql)
        self.assertEqual(
            params,
            [4, "0b6f8f5e-8f61-4f3f-9d55-1a2b3c4d5e6f", 0.5, 11],
        )

    def test_zaman_araligi_ve_imlec(self):
        ts_to = datetime(2026, 7, 30, tzinfo=timezone.utc)
        sql, params = build_list_query(
            ArchiveFilters(ts_from=_TS, ts_to=ts_to),
            cursor=(_TS, 99),
            order="desc",
            limit=10,
        )
        self.assertIn("o.ts >= %s", sql)
        self.assertIn("o.ts < %s", sql)
        self.assertIn("(o.ts, o.id) < (%s, %s)", sql)
        self.assertEqual(params, [_TS, ts_to, _TS, 99, 11])

    def test_asc_imlec_buyuktur(self):
        sql, _params = build_list_query(
            ArchiveFilters(), cursor=(_TS, 99), order="asc"
        )
        self.assertIn("(o.ts, o.id) > (%s, %s)", sql)
        self.assertIn("ORDER BY o.ts ASC, o.id ASC", sql)

    def test_gecersiz_siralama_ve_limit_sinirlari(self):
        with self.assertRaises(ValueError):
            build_list_query(ArchiveFilters(), order="rastgele")
        _sql, alt = build_list_query(ArchiveFilters(), limit=0)
        self.assertEqual(alt[-1], 2)  # clamp(0)=1, +1
        _sql, ust = build_list_query(ArchiveFilters(), limit=10_000)
        self.assertEqual(ust[-1], MAX_PAGE_SIZE + 1)


if __name__ == "__main__":
    unittest.main()
