"""logquery sorgu üreticisi ve imleç kodekinin sözleşme testleri (DB'siz)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.logquery import (
    MAX_PAGE_SIZE,
    LogFilters,
    build_list_query,
    decode_cursor,
    encode_cursor,
)

_TS = datetime(2026, 7, 29, 12, 30, 45, 123456, tzinfo=timezone.utc)


class CursorCodecTests(unittest.TestCase):
    def test_gidis_donus_zaman_dilimini_korur(self):
        ts, record_id = decode_cursor(encode_cursor(_TS, 42))
        self.assertEqual(ts, _TS)
        self.assertEqual(ts.tzinfo, timezone.utc)
        self.assertEqual(record_id, 42)

    def test_bozuk_imlecler_value_error(self):
        for bozuk in ("", "!!!", "abc", encode_cursor(_TS, 1)[:-6] + "AAAAAA"):
            with self.subTest(cursor=bozuk):
                with self.assertRaises(ValueError):
                    decode_cursor(bozuk)

    def test_naive_zaman_ve_gecersiz_id_reddedilir(self):
        for ts, record_id in (
            (datetime(2026, 7, 29, 12, 30), 1),
            (_TS, 0),
            (_TS, -1),
        ):
            with self.subTest(ts=ts, record_id=record_id):
                with self.assertRaises(ValueError):
                    decode_cursor(encode_cursor(ts, record_id))


class BuildListQueryTests(unittest.TestCase):
    def test_filtresiz_sorgu(self):
        sql, params = build_list_query(LogFilters())
        self.assertNotIn("WHERE", sql)
        self.assertIn("FROM public.log_records", sql)
        self.assertIn("ORDER BY ts DESC, id DESC", sql)
        self.assertIn("has_payload", sql)
        self.assertEqual(params, [101])

    def test_seviye_ve_kategori_any_ile(self):
        filters = LogFilters(levels=("error", "warning"), categories=("app",))
        sql, params = build_list_query(filters, limit=50)
        self.assertIn("level = ANY(%s)", sql)
        self.assertIn("category = ANY(%s)", sql)
        self.assertEqual(params, [["error", "warning"], ["app"], 51])

    def test_model_run_ve_zaman_araligi(self):
        ts_to = datetime(2026, 7, 30, tzinfo=timezone.utc)
        filters = LogFilters(
            model_ids=("pothole",), run_id=7, ts_from=_TS, ts_to=ts_to
        )
        sql, params = build_list_query(filters)
        self.assertIn("model_id = ANY(%s)", sql)
        self.assertIn("run_id = %s", sql)
        self.assertIn("ts >= %s", sql)
        self.assertIn("ts < %s", sql)
        self.assertEqual(params, [["pothole"], 7, _TS, ts_to, 101])
        # Koşullar AND ile ve üretim sırasıyla bağlanır (WHERE bölümünde;
        # kolon adları SELECT listesinde de geçtiğinden arama oradan başlar).
        where = sql[sql.index("WHERE") :]
        self.assertLess(where.index("model_id"), where.index("run_id"))
        self.assertLess(where.index("run_id"), where.index("ts >="))

    def test_desc_imlec_kucuktur_karsilastirir(self):
        sql, params = build_list_query(
            LogFilters(), cursor=(_TS, 42), order="desc"
        )
        self.assertIn("(ts, id) < (%s, %s)", sql)
        self.assertEqual(params[:2], [_TS, 42])

    def test_asc_imlec_buyuktur_ve_asc_siralar(self):
        sql, _params = build_list_query(
            LogFilters(), cursor=(_TS, 42), order="asc"
        )
        self.assertIn("(ts, id) > (%s, %s)", sql)
        self.assertIn("ORDER BY ts ASC, id ASC", sql)

    def test_gecersiz_siralama_reddedilir(self):
        with self.assertRaises(ValueError):
            build_list_query(LogFilters(), order="rastgele")

    def test_limit_sinirlari(self):
        _sql, alt = build_list_query(LogFilters(), limit=0)
        self.assertEqual(alt[-1], 2)
        _sql, ust = build_list_query(LogFilters(), limit=10_000)
        self.assertEqual(ust[-1], MAX_PAGE_SIZE + 1)


if __name__ == "__main__":
    unittest.main()
