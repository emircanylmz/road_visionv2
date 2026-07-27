from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from roadvision.archive import (
    ARCHIVE_SCHEMA_VERSION,
    ArchiveSchemaError,
    DetectionFilter,
    DetectionPage,
    PageCursor,
    SortColumn,
    SortSpec,
    build_where,
    check_archive_capability,
    check_archive_schema,
    fetch_detections,
    fetch_type_counts,
    fetch_type_tree,
)


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


class QueryCursor:
    def __init__(self, conn: "QueryConnection") -> None:
        self.conn = conn
        self.rows: list[tuple] = []
        self.one = None

    def __enter__(self) -> "QueryCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = normalize_sql(sql)
        self.conn.statements.append((normalized, params))
        self.rows = []
        self.one = None
        if "WITH archive_models AS" in normalized:
            self.rows = list(self.conn.tree_rows)
        elif "count(*)::bigint AS detection_count" in normalized:
            self.rows = list(self.conn.count_rows)
        elif "AS model_display_name" in normalized:
            self.rows = list(self.conn.detection_rows)
        elif "to_regclass('schema_info')" in normalized:
            self.one = (self.conn.has_schema_info,)
        elif "MAX(version)" in normalized:
            self.one = (self.conn.schema_version,)
        elif "to_regclass('detected_objects')" in normalized:
            self.one = tuple(self.conn.capabilities)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.rows)


class QueryConnection:
    def __init__(
        self,
        *,
        tree_rows=(),
        detection_rows=(),
        count_rows=(),
        schema_version: int = ARCHIVE_SCHEMA_VERSION,
        has_schema_info: bool = True,
        capabilities=(True, True, True, True, True),
    ) -> None:
        self.tree_rows = tree_rows
        self.detection_rows = detection_rows
        self.count_rows = count_rows
        self.schema_version = schema_version
        self.has_schema_info = has_schema_info
        self.capabilities = capabilities
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> QueryCursor:
        return QueryCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def detection_tuple(
    row_id: int,
    *,
    ts: datetime | None = None,
    confidence: float | None = 0.91,
    area_ratio: float | None = None,
    model_id: str = "pothole",
    model_display_name: str = "Çukur ve Rögar",
    class_name: str = "pothole",
    display_name: str = "Çukur",
    bbox=(1.0, 2.0, 3.0, 4.0),
    capture_id=None,
    is_catalogued: bool = True,
):
    return (
        row_id,
        ts or datetime(2026, 7, 24, 8, 30, tzinfo=timezone.utc),
        7,
        model_id,
        model_display_name,
        class_name,
        display_name,
        confidence,
        area_ratio,
        bbox,
        capture_id,
        is_catalogued,
    )


class DetectionFilterTests(unittest.TestCase):
    def test_normalizes_type_ids_and_requires_aware_ordered_bounds(self) -> None:
        start = datetime(2026, 7, 23, tzinfo=timezone.utc)
        flt = DetectionFilter(type_ids={3, 1}, ts_from=start, ts_to=start + timedelta(hours=1))
        self.assertEqual(flt.type_ids, frozenset({1, 3}))

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            DetectionFilter(ts_from=datetime(2026, 7, 23))
        with self.assertRaisesRegex(ValueError, "küçük"):
            DetectionFilter(ts_from=start, ts_to=start)

    def test_validates_confidence_type_ids_and_run(self) -> None:
        with self.assertRaises(ValueError):
            DetectionFilter(type_ids=frozenset({0}))
        with self.assertRaises(ValueError):
            DetectionFilter(min_confidence=1.01)
        with self.assertRaises(ValueError):
            DetectionFilter(run_id=-1)

    def test_build_where_uses_dynamic_filters_and_integer_array_list(self) -> None:
        start = datetime(2026, 7, 23, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        flt = DetectionFilter(
            type_ids=frozenset({8, 2}),
            ts_from=start,
            ts_to=end,
            min_confidence=0.35,
            run_id=4,
            only_with_image=True,
        )

        sql, params = build_where(flt)
        normalized = normalize_sql(sql)

        self.assertIn("o.type_id = ANY(%(type_ids)s::integer[])", normalized)
        self.assertIn("o.ts >= %(ts_from)s::timestamptz", normalized)
        self.assertIn("o.ts < %(ts_to)s::timestamptz", normalized)
        self.assertIn("o.confidence >= %(min_confidence)s::real", normalized)
        self.assertIn("o.run_id = %(run_id)s::integer", normalized)
        self.assertIn("mc.capture_id IS NOT NULL", normalized)
        self.assertEqual(params["type_ids"], [2, 8])
        self.assertIsInstance(params["type_ids"], list)

    def test_all_time_and_optional_filters_add_no_fake_bounds(self) -> None:
        sql, params = build_where(
            DetectionFilter(type_ids=frozenset({1})),
        )
        self.assertNotIn("ts_from", sql)
        self.assertNotIn("ts_to", sql)
        self.assertNotIn("min_confidence", sql)
        self.assertNotIn("run_id", sql)
        self.assertEqual(params, {"type_ids": [1]})

    def test_empty_type_selection_is_false_for_page_but_ignored_for_facets(self) -> None:
        flt = DetectionFilter()
        page_sql, _ = build_where(flt)
        facet_sql, facet_params = build_where(flt, include_type_ids=False)
        self.assertEqual(page_sql, "WHERE FALSE")
        self.assertEqual(facet_sql, "")
        self.assertEqual(facet_params, {})


class CapabilityTests(unittest.TestCase):
    def test_accepts_complete_v3_without_managing_transaction(self) -> None:
        conn = QueryConnection()
        self.assertEqual(check_archive_capability(conn), 3)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 0))
        self.assertEqual(len(conn.statements), 3)

    def test_fetcher_facing_schema_alias_uses_same_contract(self) -> None:
        conn = QueryConnection()
        self.assertEqual(check_archive_schema(conn), ARCHIVE_SCHEMA_VERSION)

    def test_rejects_missing_or_old_schema(self) -> None:
        with self.assertRaisesRegex(ArchiveSchemaError, "schema_info"):
            check_archive_capability(QueryConnection(has_schema_info=False))
        with self.assertRaisesRegex(ArchiveSchemaError, "bulunan sürüm: 2"):
            check_archive_capability(QueryConnection(schema_version=2))
        with self.assertRaisesRegex(ArchiveSchemaError, "tabloları eksik"):
            check_archive_capability(
                QueryConnection(capabilities=(True, True, False, True, True))
            )


class TypeTreeTests(unittest.TestCase):
    def test_groups_catalogued_and_unknown_models_in_query_order(self) -> None:
        conn = QueryConnection(
            tree_rows=(
                ("pothole", "Çukur ve Rögar", "detect", True, 2, "manhole_cover", "Rögar", True),
                ("pothole", "Çukur ve Rögar", "detect", True, 1, "pothole", "Çukur", True),
                ("legacy", "legacy", "unknown", False, 99, "old", "old", False),
            )
        )

        models = fetch_type_tree(conn)

        self.assertEqual([model.model_id for model in models], ["pothole", "legacy"])
        self.assertEqual([item.type_id for item in models[0].types], [2, 1])
        self.assertFalse(models[1].active)
        self.assertEqual(models[1].display_name, "legacy")
        self.assertFalse(models[1].types[0].is_catalogued)
        sql = conn.statements[0][0]
        self.assertIn("UNION ALL", sql)
        self.assertIn("WHERE NOT EXISTS", sql)
        self.assertIn("am.active DESC", sql)
        self.assertIn("t.class_index NULLS LAST", sql)


class FetchDetectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flt = DetectionFilter(type_ids=frozenset({1, 2}))

    def test_empty_type_selection_short_circuits_without_sql(self) -> None:
        conn = QueryConnection()
        page = fetch_detections(
            conn,
            DetectionFilter(),
            SortSpec(),
            None,
            25,
        )
        self.assertEqual(page, DetectionPage((), None, False))
        self.assertEqual(conn.statements, [])

    def test_default_page_uses_real_media_join_and_page_size_plus_one(self) -> None:
        capture_id = uuid.UUID("035de335-28d6-4c31-9d7d-54fc6ca076ff")
        conn = QueryConnection(
            detection_rows=tuple(
                detection_tuple(
                    index,
                    capture_id=capture_id if index == 30 else None,
                )
                for index in range(30, 4, -1)
            )
        )

        page = fetch_detections(conn, self.flt, SortSpec(), None, 25)

        self.assertTrue(page.has_more)
        self.assertEqual(len(page.rows), 25)
        self.assertEqual(page.rows[0].capture_id, str(capture_id))
        self.assertEqual(page.rows[0].bbox, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(page.next_cursor.last_id, page.rows[-1].id)
        self.assertEqual(page.next_cursor.last_value, page.rows[-1].ts)
        sql, params = conn.statements[0]
        self.assertIn("LEFT JOIN media_captures AS mc", sql)
        self.assertIn("mc.capture_id", sql)
        self.assertNotIn("e.capture_id AS", sql)
        self.assertIn("ORDER BY o.ts DESC, o.id DESC", sql)
        self.assertEqual(params["page_limit"], 26)

    def test_no_extra_row_means_no_next_cursor(self) -> None:
        conn = QueryConnection(detection_rows=(detection_tuple(2), detection_tuple(1)))
        page = fetch_detections(conn, self.flt, SortSpec(), None, 25)
        self.assertFalse(page.has_more)
        self.assertIsNone(page.next_cursor)

    def test_rejects_non_contract_page_size(self) -> None:
        for invalid in (10, 25.0, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "25, 50, 100"):
                    fetch_detections(
                        QueryConnection(),
                        self.flt,
                        SortSpec(),
                        None,
                        invalid,  # type: ignore[arg-type]
                    )

    def test_all_non_nullable_sorts_use_allowlisted_directional_keyset(self) -> None:
        cases = (
            (SortColumn.TS, "o.ts", "timestamptz"),
            (SortColumn.MODEL, "COALESCE(m.display_name, o.model_id)", "text"),
            (SortColumn.CLASS, "t.display_name", "text"),
        )
        values = {
            SortColumn.TS: datetime(2026, 7, 24, tzinfo=timezone.utc),
            SortColumn.MODEL: "Model",
            SortColumn.CLASS: "Tür",
        }
        for column, expression, cast in cases:
            for descending, operator, direction in (
                (True, "<", "DESC"),
                (False, ">", "ASC"),
            ):
                with self.subTest(column=column, descending=descending):
                    conn = QueryConnection()
                    fetch_detections(
                        conn,
                        self.flt,
                        SortSpec(column, descending),
                        PageCursor(values[column], 44),
                        25,
                    )
                    sql = conn.statements[0][0]
                    self.assertIn(
                        f"{expression} {operator} %(cursor_value)s::{cast}",
                        sql,
                    )
                    self.assertIn(f"o.id {operator} %(cursor_id)s::bigint", sql)
                    self.assertIn(
                        f"ORDER BY {expression} {direction}, o.id {direction}",
                        sql,
                    )

    def test_nullable_non_null_cursor_reaches_null_section_in_both_directions(self) -> None:
        for column, expression in (
            (SortColumn.CONFIDENCE, "o.confidence"),
            (SortColumn.AREA_RATIO, "o.area_ratio"),
        ):
            for descending, operator, direction in (
                (True, "<", "DESC"),
                (False, ">", "ASC"),
            ):
                with self.subTest(column=column, descending=descending):
                    conn = QueryConnection()
                    fetch_detections(
                        conn,
                        self.flt,
                        SortSpec(column, descending),
                        PageCursor(0.5, 22),
                        25,
                    )
                    sql = conn.statements[0][0]
                    self.assertIn(f"{expression} {operator} %(cursor_value)s::real", sql)
                    self.assertIn(f"OR {expression} IS NULL", sql)
                    self.assertIn(
                        f"ORDER BY {expression} {direction} NULLS LAST, o.id {direction}",
                        sql,
                    )

    def test_nullable_null_cursor_stays_in_null_section(self) -> None:
        conn = QueryConnection()
        fetch_detections(
            conn,
            self.flt,
            SortSpec(SortColumn.CONFIDENCE, True),
            PageCursor(None, 22, last_is_null=True),
            25,
        )
        sql, params = conn.statements[0]
        self.assertIn(
            "o.confidence IS NULL AND o.id < %(cursor_id)s::bigint",
            sql,
        )
        self.assertNotIn("cursor_value", params)

    def test_non_nullable_sort_rejects_null_cursor(self) -> None:
        with self.assertRaisesRegex(ValueError, "NULL imleci"):
            fetch_detections(
                QueryConnection(),
                self.flt,
                SortSpec(SortColumn.TS),
                PageCursor(None, 1, last_is_null=True),
                25,
            )

    def test_next_cursor_uses_raw_nullable_sort_value(self) -> None:
        rows = tuple(
            detection_tuple(index, confidence=None)
            for index in range(30, 4, -1)
        )
        page = fetch_detections(
            QueryConnection(detection_rows=rows),
            self.flt,
            SortSpec(SortColumn.CONFIDENCE),
            None,
            25,
        )
        self.assertTrue(page.next_cursor.last_is_null)
        self.assertIsNone(page.next_cursor.last_value)


class FetchTypeCountsTests(unittest.TestCase):
    def test_excludes_type_self_filter_but_shares_other_filters_and_joins(self) -> None:
        start = datetime(2026, 7, 23, tzinfo=timezone.utc)
        flt = DetectionFilter(
            type_ids=frozenset({99}),
            ts_from=start,
            min_confidence=0.4,
            run_id=8,
            only_with_image=True,
        )
        conn = QueryConnection(count_rows=((1, 7), (2, 0)))

        counts = fetch_type_counts(conn, flt)

        self.assertEqual([(item.type_id, item.count) for item in counts], [(1, 7), (2, 0)])
        sql, params = conn.statements[0]
        self.assertNotIn("type_ids", sql)
        self.assertNotIn("type_ids", params)
        self.assertIn("JOIN detection_events AS e", sql)
        self.assertIn("JOIN detection_types AS t", sql)
        self.assertIn("LEFT JOIN media_captures AS mc", sql)
        self.assertIn("o.ts >= %(ts_from)s::timestamptz", sql)
        self.assertIn("o.confidence >= %(min_confidence)s::real", sql)
        self.assertIn("o.run_id = %(run_id)s::integer", sql)
        self.assertIn("mc.capture_id IS NOT NULL", sql)
        self.assertNotIn("LIMIT", sql)
        self.assertNotIn("cursor_", sql)

    def test_query_functions_never_commit_or_rollback(self) -> None:
        conn = QueryConnection(
            tree_rows=(),
            detection_rows=(detection_tuple(1),),
            count_rows=((1, 1),),
        )
        fetch_type_tree(conn)
        fetch_detections(
            conn,
            DetectionFilter(type_ids=frozenset({1})),
            SortSpec(),
            None,
            25,
        )
        fetch_type_counts(conn, DetectionFilter(type_ids=frozenset({1})))
        self.assertEqual((conn.commits, conn.rollbacks), (0, 0))


if __name__ == "__main__":
    unittest.main()
