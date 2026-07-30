"""webapp migration runner'ının sözleşme testleri.

Masaüstü test paketindeki fake yaklaşımıyla aynıdır: gerçek PostgreSQL veya
psycopg gerekmez; kilit sırası, sürüm kapısı ve sıra-atlama koruması saf
Python fake bağlantısıyla doğrulanır. Bu dosya torch/psycopg kurulu olmayan
ortamlarda da çalışır (bkz. WEB_PLANI.md §9 Faz 0 kabulü).
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path

from app.migrations import (
    CURRENT_VERSION,
    MIGRATIONS,
    SCHEMA_MISSING_HINT,
    WEBAPP_ADVISORY_LOCK,
    ensure_webapp_schema,
)


class FakeCursor:
    def __init__(self, fetch_results):
        self.executed: list[tuple[str, tuple | None]] = []
        self._fetch_results = list(fetch_results)

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        if not self._fetch_results:
            raise AssertionError("beklenmeyen fetchone çağrısı")
        return self._fetch_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """psycopg bağlantısının runner'ın kullandığı alt kümesi."""

    def __init__(self, fetch_results):
        self.cursor_obj = FakeCursor(fetch_results)
        self.tx_events: list[str] = []

    @contextmanager
    def transaction(self):
        self.tx_events.append("begin")
        try:
            yield self
        except BaseException:
            self.tx_events.append("rollback")
            raise
        self.tx_events.append("commit")

    def cursor(self):
        return self.cursor_obj


def _sql_list(conn: FakeConnection) -> list[str]:
    return [sql for sql, _ in conn.cursor_obj.executed]


class EnsureWebappSchemaTests(unittest.TestCase):
    def test_migration_listesi_v1_ile_baslar_ve_ardisiktir(self):
        versions = [version for version, _sql in MIGRATIONS]
        self.assertEqual(versions, list(range(1, len(versions) + 1)))
        self.assertEqual(CURRENT_VERSION, versions[-1])

    def test_v1_kimlik_tablolarini_icerir(self):
        sql = dict(MIGRATIONS)[1]
        for parca in (
            "CREATE TABLE webapp.users",
            "CREATE TABLE webapp.sessions",
            "CREATE TABLE webapp.admin_audit",
            "csrf_token",
            "last_seen_at",
            "users_email_lower_uq",
            "'pending', 'approved', 'rejected', 'disabled'",
        ):
            self.assertIn(parca, sql)

    def test_v2_dogrulama_tablosunu_icerir(self):
        sql = dict(MIGRATIONS)[2]
        for parca in (
            "CREATE TABLE webapp.detection_reviews",
            "'correct', 'corrected', 'wrong'",
            "array_length(corrected_bbox, 1) = 4",
            "CONSTRAINT corrected_payload CHECK",
            "REFERENCES webapp.users(user_id)",
            "detection_reviews_reviewed_idx",
        ):
            self.assertIn(parca, sql)
        # FK'sız işaret: retention bağımsızlığı (§2/2) — public'e REFERENCES yok.
        self.assertNotIn("REFERENCES public.", sql)

    def test_v3_dataset_bolumlerini_icerir(self):
        sql = dict(MIGRATIONS)[3]
        for parca in (
            "CREATE TABLE webapp.dataset_media",
            "CREATE TABLE webapp.dataset_samples",
            "PARTITION BY LIST (verdict)",
            "PARTITION BY LIST (model_id)",
            "PRIMARY KEY (verdict, model_id, sample_id)",
            "FOR VALUES IN ('correct', 'corrected')",
            "FOR VALUES IN ('wrong')",
            "detection_reviews_corrected_type_trg",
        ):
            self.assertIn(parca, sql)
        # 2 karar grubu × 4 model = 8 yaprak (§4.5).
        for model in ("roadline", "traffic_sign", "pothole", "marking_damage"):
            self.assertIn(f"webapp.ds_positive_{model}", sql)
            self.assertIn(f"webapp.ds_wrong_{model}", sql)
        self.assertNotIn("REFERENCES public.", sql)

    def test_kilit_ilk_komut_ve_dogru_sabitle_alinir(self):
        conn = FakeConnection(fetch_results=[(1,), (0,)])
        ensure_webapp_schema(conn, migrations=())
        first_sql, first_params = conn.cursor_obj.executed[0]
        self.assertIn("pg_advisory_xact_lock", first_sql)
        self.assertEqual(first_params, (WEBAPP_ADVISORY_LOCK,))
        # Masaüstü (1385428466) ve bootstrap (1385428468) sabitleriyle ayrık.
        self.assertNotIn(WEBAPP_ADVISORY_LOCK, (1385428466, 1385428468))
        self.assertEqual(conn.tx_events, ["begin", "commit"])

    def test_webapp_semasi_yoksa_bootstrap_yonlendirmesi(self):
        conn = FakeConnection(fetch_results=[None])
        with self.assertRaises(RuntimeError) as ctx:
            ensure_webapp_schema(conn, migrations=())
        self.assertEqual(str(ctx.exception), SCHEMA_MISSING_HINT)
        self.assertIn("bootstrap_db.sh", SCHEMA_MISSING_HINT)
        self.assertEqual(conn.tx_events, ["begin", "rollback"])

    def test_bekleyenler_sirayla_uygulanir_ve_surum_yazilir(self):
        migrations = (
            (1, "CREATE TABLE webapp.a (id integer)"),
            (2, "CREATE TABLE webapp.b (id integer)"),
        )
        conn = FakeConnection(fetch_results=[(1,), (0,)])
        self.assertEqual(ensure_webapp_schema(conn, migrations), 2)
        sqls = _sql_list(conn)
        insert_sql = "INSERT INTO webapp.schema_info (version) VALUES (%s)"
        # DDL'den hemen sonra ilgili sürüm satırı yazılmalı.
        idx_a = sqls.index(migrations[0][1])
        idx_b = sqls.index(migrations[1][1])
        self.assertLess(idx_a, idx_b)
        self.assertEqual(sqls[idx_a + 1], insert_sql)
        self.assertEqual(sqls[idx_b + 1], insert_sql)
        versions = [
            params[0]
            for sql, params in conn.cursor_obj.executed
            if sql == insert_sql
        ]
        self.assertEqual(versions, [1, 2])

    def test_uygulanmis_surumler_atlanir(self):
        migrations = ((1, "CREATE TABLE webapp.a (id integer)"),)
        conn = FakeConnection(fetch_results=[(1,), (1,)])
        self.assertEqual(ensure_webapp_schema(conn, migrations), 1)
        self.assertNotIn(migrations[0][1], _sql_list(conn))

    def test_sira_atlamasi_calismayi_durdurur(self):
        migrations = ((2, "CREATE TABLE webapp.b (id integer)"),)
        conn = FakeConnection(fetch_results=[(1,), (0,)])
        with self.assertRaises(RuntimeError) as ctx:
            ensure_webapp_schema(conn, migrations)
        self.assertIn("sırası bozuk", str(ctx.exception))
        self.assertNotIn(migrations[0][1], _sql_list(conn))
        self.assertEqual(conn.tx_events, ["begin", "rollback"])


class BootstrapSqlSecurityTests(unittest.TestCase):
    def test_web_password_assignment_does_not_print_query_result(self):
        bootstrap_sql = (
            Path(__file__).resolve().parents[1] / "db" / "bootstrap.sql"
        ).read_text(encoding="utf-8")

        self.assertIn(
            ") AS _roadvision_web_password \\gset",
            bootstrap_sql,
        )


if __name__ == "__main__":
    unittest.main()
