from __future__ import annotations

import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from roadvision.archive import ArchiveSchemaError
from roadvision.archive_fetcher import (
    ArchiveFetcher,
    ArchiveResult,
    create_default_archive_fetcher,
)

try:
    import psycopg
except ImportError:  # pragma: no cover - DB opsiyonel bağımlılığı
    psycopg = None


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self.conn = conn

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.split())
        self.conn.statements.append(
            (normalized, params, threading.current_thread().name)
        )
        if normalized.startswith("BEGIN TRANSACTION"):
            self.conn.transaction_sequence += 1
            self.conn.active_transaction = self.conn.transaction_sequence


class FakeConnection:
    def __init__(self, name: str = "conn") -> None:
        self.name = name
        self.read_only = False
        self.statements: list[tuple[str, object, str]] = []
        self.transaction_sequence = 0
        self.active_transaction: int | None = None
        self.rollbacks = 0
        self.closed = False
        self.close_threads: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1
        self.active_transaction = None

    def close(self) -> None:
        self.closed = True
        self.close_threads.append(threading.current_thread().name)


def await_results(
    fetcher: ArchiveFetcher,
    count: int,
    *,
    timeout: float = 2.0,
) -> list[ArchiveResult]:
    deadline = time.monotonic() + timeout
    results: list[ArchiveResult] = []
    while len(results) < count and time.monotonic() < deadline:
        results.extend(fetcher.poll())
        if len(results) < count:
            time.sleep(0.005)
    if len(results) != count:
        raise AssertionError(f"{count} sonuç bekleniyordu, {len(results)} geldi")
    return results


def wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not predicate():
        raise AssertionError("Koşul zaman aşımına uğradı")


class ArchiveFetcherTests(unittest.TestCase):
    def make_fetcher(self, factory=None) -> ArchiveFetcher:
        fetcher = ArchiveFetcher(
            "postgresql://fake",
            connection_factory=factory or (lambda _dsn: FakeConnection()),
        )
        self.addCleanup(fetcher.close, 1.0)
        return fetcher

    def patched_queries(
        self,
        *,
        schema=None,
        tree=None,
        page=None,
        counts=None,
    ) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch(
                "roadvision.archive_fetcher.check_archive_schema",
                side_effect=schema or (lambda _conn: None),
            )
        )
        stack.enter_context(
            patch(
                "roadvision.archive_fetcher.fetch_type_tree",
                side_effect=tree or (lambda _conn: ("tree",)),
            )
        )
        stack.enter_context(
            patch(
                "roadvision.archive_fetcher.fetch_detections",
                side_effect=page or (
                    lambda _conn, flt, _sort, _cursor, _page_size: f"page:{flt}"
                ),
            )
        )
        stack.enter_context(
            patch(
                "roadvision.archive_fetcher.fetch_type_counts",
                side_effect=counts or (lambda _conn, flt: (f"count:{flt}",)),
            )
        )
        self.addCleanup(stack.close)
        return stack

    def test_tree_and_refresh_have_independent_generations_and_one_snapshot(self) -> None:
        conn = FakeConnection()
        seen: list[tuple[str, int | None, str]] = []
        page_value = object()
        count_value = object()

        def schema(current) -> None:
            seen.append(("schema", current.active_transaction, threading.current_thread().name))

        def tree(current):
            seen.append(("tree", current.active_transaction, threading.current_thread().name))
            return ("type-tree",)

        def page(current, _flt, _sort, _cursor, page_size):
            self.assertEqual(page_size, 25)
            seen.append(("page", current.active_transaction, threading.current_thread().name))
            return page_value

        def counts(current, _flt):
            seen.append(("counts", current.active_transaction, threading.current_thread().name))
            return (count_value,)

        self.patched_queries(schema=schema, tree=tree, page=page, counts=counts)
        fetcher = self.make_fetcher(lambda _dsn: conn)

        tree_generation = fetcher.request_tree()
        refresh_generation = fetcher.request_refresh("filter", "sort", None, 25)
        results = await_results(fetcher, 2)

        self.assertEqual((tree_generation, refresh_generation), (1, 1))
        self.assertEqual(fetcher.latest_tree_generation, 1)
        self.assertEqual(fetcher.latest_refresh_generation, 1)
        by_kind = {result.kind: result for result in results}
        self.assertEqual(by_kind["tree"].tree, ("type-tree",))
        self.assertIs(by_kind["refresh"].page, page_value)
        self.assertEqual(by_kind["refresh"].counts, (count_value,))
        page_tx = next(tx for kind, tx, _thread in seen if kind == "page")
        counts_tx = next(tx for kind, tx, _thread in seen if kind == "counts")
        self.assertEqual(page_tx, counts_tx)
        self.assertNotEqual(
            next(tx for kind, tx, _thread in seen if kind == "tree"),
            page_tx,
        )
        self.assertTrue(
            all(thread == "roadvision-archive-fetcher" for _, _, thread in seen)
        )
        self.assertTrue(conn.read_only)
        begins = [sql for sql, _, _ in conn.statements if sql.startswith("BEGIN")]
        timeouts = [
            sql for sql, _, _ in conn.statements if sql.startswith("SET LOCAL")
        ]
        self.assertEqual(len(begins), 3)  # schema doğrulaması + tree + refresh
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(all("REPEATABLE READ READ ONLY" in sql for sql in begins))
        self.assertTrue(all("statement_timeout = 5000" in sql for sql in timeouts))
        self.assertEqual(conn.rollbacks, 3)

    def test_refresh_can_skip_counts(self) -> None:
        counts_called = threading.Event()
        self.patched_queries(counts=lambda *_args: counts_called.set())
        fetcher = self.make_fetcher()

        generation = fetcher.request_refresh(
            "filter",
            "sort",
            None,
            50,
            include_counts=False,
        )
        result = await_results(fetcher, 1)[0]

        self.assertEqual((result.kind, result.generation), ("refresh", generation))
        self.assertEqual(result.page, "page:filter")
        self.assertIsNone(result.counts)
        self.assertFalse(counts_called.is_set())

    def test_pending_refreshes_are_coalesced_and_stale_count_is_skipped(self) -> None:
        entered = threading.Event()
        allow_first = threading.Event()
        page_calls: list[str] = []
        count_calls: list[str] = []

        def page(_conn, flt, _sort, _cursor, _page_size):
            page_calls.append(flt)
            if flt == "first":
                entered.set()
                if not allow_first.wait(1.0):
                    raise TimeoutError("ilk sayfa serbest bırakılmadı")
            return f"page:{flt}"

        def counts(_conn, flt):
            count_calls.append(flt)
            return (f"count:{flt}",)

        self.patched_queries(page=page, counts=counts)
        fetcher = self.make_fetcher()

        first = fetcher.request_refresh("first", "sort", None, 25)
        self.assertTrue(entered.wait(1.0))
        second = fetcher.request_refresh("second", "sort", None, 25)
        third = fetcher.request_refresh("third", "sort", None, 25)
        allow_first.set()
        result = await_results(fetcher, 1)[0]

        self.assertEqual((first, second, third), (1, 2, 3))
        self.assertEqual(page_calls, ["first", "third"])
        self.assertEqual(count_calls, ["third"])
        self.assertEqual(result.generation, third)
        self.assertEqual(result.page, "page:third")
        self.assertEqual(fetcher.poll(), [])

    def test_tree_request_does_not_invalidate_pending_refresh(self) -> None:
        tree_entered = threading.Event()
        allow_tree = threading.Event()

        def tree(_conn):
            tree_entered.set()
            if not allow_tree.wait(1.0):
                raise TimeoutError("tree serbest bırakılmadı")
            return ("tree",)

        self.patched_queries(tree=tree)
        fetcher = self.make_fetcher()

        tree_generation = fetcher.request_tree()
        self.assertTrue(tree_entered.wait(1.0))
        refresh_generation = fetcher.request_refresh("flt", "sort", None, 25)
        allow_tree.set()
        results = await_results(fetcher, 2)

        self.assertEqual((tree_generation, refresh_generation), (1, 1))
        self.assertEqual({result.kind for result in results}, {"tree", "refresh"})

    def test_pending_tree_requests_are_coalesced(self) -> None:
        entered = threading.Event()
        allow_first = threading.Event()
        calls = 0

        def tree(_conn):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                if not allow_first.wait(1.0):
                    raise TimeoutError("ilk tree serbest bırakılmadı")
            return (f"tree:{calls}",)

        self.patched_queries(tree=tree)
        fetcher = self.make_fetcher()

        fetcher.request_tree()
        self.assertTrue(entered.wait(1.0))
        fetcher.request_tree()
        latest = fetcher.request_tree()
        allow_first.set()
        result = await_results(fetcher, 1)[0]

        self.assertEqual(calls, 2)
        self.assertEqual(result.generation, latest)
        self.assertEqual(result.tree, ("tree:2",))

    def test_unpolled_results_are_bounded_and_newest_kind_replaces_old(self) -> None:
        self.patched_queries()
        fetcher = self.make_fetcher()

        fetcher.request_tree()
        wait_until(lambda: len(fetcher._results) == 1)  # type: ignore[attr-defined]
        latest_tree = fetcher.request_tree()
        wait_until(
            lambda: (
                "tree" in fetcher._results  # type: ignore[attr-defined]
                and fetcher._results["tree"].generation == latest_tree  # type: ignore[attr-defined]
            )
        )
        latest_refresh = fetcher.request_refresh("flt", "sort", None, 25)
        wait_until(lambda: len(fetcher._results) == 2)  # type: ignore[attr-defined]

        results = fetcher.poll()

        self.assertEqual(len(results), 2)
        by_kind = {result.kind: result.generation for result in results}
        self.assertEqual(
            by_kind,
            {"tree": latest_tree, "refresh": latest_refresh},
        )

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_operational_connection_error_reconnects_once_with_same_revision(self) -> None:
        connections = [FakeConnection("first"), FakeConnection("second")]
        factory_calls: list[str] = []
        responses = [psycopg.OperationalError("socket closed"), "recovered"]

        def factory(dsn: str):
            factory_calls.append(dsn)
            return connections[len(factory_calls) - 1]

        def page(*_args):
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        self.patched_queries(page=page)
        fetcher = self.make_fetcher(factory)

        generation = fetcher.request_refresh(
            "flt",
            "sort",
            None,
            25,
            include_counts=False,
        )
        result = await_results(fetcher, 1)[0]

        self.assertEqual(result.generation, generation)
        self.assertEqual(result.page, "recovered")
        self.assertEqual(len(factory_calls), 2)
        self.assertTrue(connections[0].closed)
        self.assertEqual(connections[0].close_threads, ["roadvision-archive-fetcher"])

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_connection_factory_failure_is_retried_once(self) -> None:
        conn = FakeConnection("reconnected")
        outcomes = [psycopg.OperationalError("connect failed"), conn]
        factory_calls = 0

        def factory(_dsn: str):
            nonlocal factory_calls
            outcome = outcomes[factory_calls]
            factory_calls += 1
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        self.patched_queries()
        fetcher = self.make_fetcher(factory)

        generation = fetcher.request_tree()
        result = await_results(fetcher, 1)[0]

        self.assertEqual((result.kind, result.generation), ("tree", generation))
        self.assertEqual(result.tree, ("tree",))
        self.assertEqual(factory_calls, 2)

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_second_operational_failure_returns_error_without_third_attempt(self) -> None:
        connections = [FakeConnection("first"), FakeConnection("second")]
        factory_calls = 0
        page_calls = 0

        def factory(_dsn: str):
            nonlocal factory_calls
            connection = connections[factory_calls]
            factory_calls += 1
            return connection

        def page(*_args):
            nonlocal page_calls
            page_calls += 1
            raise psycopg.OperationalError(f"connection lost {page_calls}")

        self.patched_queries(page=page)
        fetcher = self.make_fetcher(factory)

        generation = fetcher.request_refresh(
            "flt",
            "sort",
            None,
            25,
            include_counts=False,
        )
        result = await_results(fetcher, 1)[0]

        self.assertEqual(result.generation, generation)
        self.assertIn("connection lost 2", result.error)
        self.assertEqual((factory_calls, page_calls), (2, 2))
        self.assertTrue(all(conn.closed for conn in connections))

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_programming_and_statement_timeout_errors_are_not_retried(self) -> None:
        for error in (
            psycopg.ProgrammingError("bad SQL"),
            psycopg.errors.QueryCanceled("statement timeout"),
        ):
            with self.subTest(error=type(error).__name__):
                conn = FakeConnection()
                factory_calls = 0

                def factory(_dsn: str):
                    nonlocal factory_calls
                    factory_calls += 1
                    return conn

                self.patched_queries(
                    page=lambda *_args, current_error=error: (_ for _ in ()).throw(
                        current_error
                    )
                )
                fetcher = self.make_fetcher(factory)
                fetcher.request_refresh(
                    "flt",
                    "sort",
                    None,
                    25,
                    include_counts=False,
                )
                result = await_results(fetcher, 1)[0]

                self.assertTrue(result.error)
                self.assertEqual(factory_calls, 1)
                self.assertTrue(conn.closed)
                self.assertTrue(fetcher.close(1.0))

    def test_schema_error_is_reported_without_query_or_retry(self) -> None:
        conn = FakeConnection()
        factory_calls = 0
        page_called = threading.Event()

        def factory(_dsn: str):
            nonlocal factory_calls
            factory_calls += 1
            return conn

        def schema(_conn):
            raise ArchiveSchemaError("schema v3 gerekli")

        self.patched_queries(
            schema=schema,
            page=lambda *_args: page_called.set(),
        )
        fetcher = self.make_fetcher(factory)

        fetcher.request_refresh("flt", "sort", None, 25)
        result = await_results(fetcher, 1)[0]

        self.assertIn("schema v3", result.error)
        self.assertEqual(factory_calls, 1)
        self.assertFalse(page_called.is_set())
        self.assertTrue(conn.closed)

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_stale_operational_failure_is_not_retried_for_old_revision(self) -> None:
        entered = threading.Event()
        allow_failure = threading.Event()
        calls: list[str] = []
        connections = [FakeConnection("first"), FakeConnection("second")]
        factory_calls = 0

        def factory(_dsn: str):
            nonlocal factory_calls
            connection = connections[factory_calls]
            factory_calls += 1
            return connection

        def page(_conn, flt, _sort, _cursor, _page_size):
            calls.append(flt)
            if flt == "old":
                entered.set()
                if not allow_failure.wait(1.0):
                    raise TimeoutError("hata serbest bırakılmadı")
                raise psycopg.OperationalError("connection lost")
            return "new-page"

        self.patched_queries(page=page)
        fetcher = self.make_fetcher(factory)

        fetcher.request_refresh("old", "sort", None, 25, include_counts=False)
        self.assertTrue(entered.wait(1.0))
        new_generation = fetcher.request_refresh(
            "new",
            "sort",
            None,
            25,
            include_counts=False,
        )
        allow_failure.set()
        result = await_results(fetcher, 1)[0]

        self.assertEqual(calls, ["old", "new"])
        self.assertEqual(factory_calls, 2)
        self.assertEqual(result.generation, new_generation)
        self.assertEqual(result.page, "new-page")

    @unittest.skipIf(psycopg is None, "psycopg opsiyonel bağımlılığı kurulu değil")
    def test_closing_operational_failure_is_not_retried(self) -> None:
        entered = threading.Event()
        allow_failure = threading.Event()
        factory_calls = 0

        def factory(_dsn: str):
            nonlocal factory_calls
            factory_calls += 1
            return FakeConnection()

        def page(*_args):
            entered.set()
            if not allow_failure.wait(1.0):
                raise TimeoutError("hata serbest bırakılmadı")
            raise psycopg.OperationalError("connection lost during close")

        self.patched_queries(page=page)
        fetcher = self.make_fetcher(factory)
        fetcher.request_refresh("flt", "sort", None, 25, include_counts=False)
        self.assertTrue(entered.wait(1.0))

        self.assertFalse(fetcher.close(0.0))
        allow_failure.set()
        self.assertTrue(fetcher.close(1.0))

        self.assertEqual(factory_calls, 1)
        self.assertEqual(fetcher.poll(), [])

    def test_close_timeout_does_not_close_worker_owned_connection_from_ui(self) -> None:
        entered = threading.Event()
        allow = threading.Event()
        conn = FakeConnection()

        def page(*_args):
            entered.set()
            if not allow.wait(1.0):
                raise TimeoutError("worker serbest bırakılmadı")
            return "late-page"

        self.patched_queries(page=page)
        fetcher = self.make_fetcher(lambda _dsn: conn)
        fetcher.request_refresh("flt", "sort", None, 25, include_counts=False)
        self.assertTrue(entered.wait(1.0))

        self.assertFalse(fetcher.close(0.0))
        self.assertFalse(conn.closed)
        allow.set()
        self.assertTrue(fetcher.close(1.0))

        self.assertTrue(conn.closed)
        self.assertEqual(conn.close_threads, ["roadvision-archive-fetcher"])
        self.assertEqual(fetcher.poll(), [])
        self.assertTrue(fetcher.close(0.0))
        with self.assertRaises(RuntimeError):
            fetcher.request_tree()

    def test_request_close_race_never_blocks_or_leaves_a_worker(self) -> None:
        self.patched_queries()

        for _ in range(20):
            fetcher = self.make_fetcher()
            barrier = threading.Barrier(3)
            outcomes: list[str] = []

            def requester() -> None:
                barrier.wait()
                try:
                    fetcher.request_tree()
                    outcomes.append("accepted")
                except RuntimeError:
                    outcomes.append("closed")

            def closer() -> None:
                barrier.wait()
                fetcher.close(1.0)

            request_thread = threading.Thread(target=requester)
            close_thread = threading.Thread(target=closer)
            request_thread.start()
            close_thread.start()
            barrier.wait()
            request_thread.join(1.0)
            close_thread.join(1.0)

            self.assertFalse(request_thread.is_alive())
            self.assertFalse(close_thread.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertTrue(fetcher.close(1.0))

    def test_factory_is_disabled_without_dsn_and_validates_page_size(self) -> None:
        self.assertIsNone(create_default_archive_fetcher(environ={}))
        self.assertIsNone(
            create_default_archive_fetcher(environ={"ROADVISION_DB_DSN": "  "})
        )
        fetcher = self.make_fetcher()
        for invalid_size in (10, 25.0, True):
            with self.subTest(page_size=invalid_size), self.assertRaises(ValueError):
                fetcher.request_refresh("flt", "sort", None, invalid_size)


if __name__ == "__main__":
    unittest.main()
