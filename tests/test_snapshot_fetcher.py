from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from roadvision.db import CaptureBundle, CaptureMedia
from roadvision.media import (
    SnapshotFetchResult,
    SnapshotFetcher,
    create_default_snapshot_fetcher,
)


def bundle(capture_id: str) -> CaptureBundle:
    return CaptureBundle(
        capture_id=capture_id,
        ts=datetime(2026, 7, 23, tzinfo=timezone.utc),
        source_name="test.jpg",
        source_kind="image",
        frame_sequence=1,
        is_reprocess=False,
        original=CaptureMedia(b"original", 10, 8),
        annotated=CaptureMedia(b"annotated", 10, 8),
        models=(("pothole", 1, None),),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.read_only = False
        self.rollbacks = 0
        self.closed = False

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def await_results(fetcher: SnapshotFetcher, count: int) -> list[SnapshotFetchResult]:
    deadline = time.monotonic() + 2.0
    results: list[SnapshotFetchResult] = []
    while len(results) < count and time.monotonic() < deadline:
        results.extend(fetcher.drain())
        if len(results) < count:
            time.sleep(0.01)
    if len(results) != count:
        raise AssertionError(f"{count} sonuç bekleniyordu, {len(results)} geldi")
    return results


class SnapshotFetcherTests(unittest.TestCase):
    first_id = "035de335-28d6-4c31-9d7d-54fc6ca076ff"
    second_id = "f19025a1-3189-432d-bc17-16b2ca7ba482"

    def make_fetcher(self, factory=None) -> SnapshotFetcher:
        fetcher = SnapshotFetcher(
            "postgresql://fake",
            connection_factory=factory or (lambda _dsn: FakeConnection()),
        )
        self.addCleanup(fetcher.close)
        return fetcher

    def test_request_is_async_and_result_is_delivered_by_drain(self) -> None:
        entered = threading.Event()
        allow = threading.Event()
        worker_names: list[str] = []

        def blocking_fetch(_conn, capture_id: str):
            worker_names.append(threading.current_thread().name)
            entered.set()
            if not allow.wait(1.0):
                raise TimeoutError("test worker serbest bırakılmadı")
            return bundle(capture_id)

        fetcher = self.make_fetcher()
        with patch("roadvision.media.fetch_capture", side_effect=blocking_fetch):
            generation = fetcher.request(self.first_id)
            self.assertTrue(entered.wait(1.0))
            self.assertEqual(fetcher.drain(), [])
            allow.set()
            result = await_results(fetcher, 1)[0]

        self.assertEqual(generation, 1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.generation, generation)
        self.assertEqual(result.bundle, bundle(self.first_id))
        self.assertEqual(worker_names, ["roadvision-snapshot-fetcher"])

    def test_two_fast_requests_are_generation_tagged_for_stale_filtering(self) -> None:
        fetcher = self.make_fetcher()
        with patch(
            "roadvision.media.fetch_capture",
            side_effect=lambda _conn, capture_id: bundle(capture_id),
        ):
            first_generation = fetcher.request(self.first_id)
            second_generation = fetcher.request(self.second_id)
            results = await_results(fetcher, 2)

        self.assertEqual((first_generation, second_generation), (1, 2))
        self.assertEqual(fetcher.latest_generation, 2)
        applied = [result for result in results if result.generation == fetcher.latest_generation]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].capture_id, self.second_id)

    def test_second_request_for_same_capture_uses_lru_cache(self) -> None:
        calls: list[str] = []
        fetcher = self.make_fetcher()

        def counted_fetch(_conn, capture_id: str):
            calls.append(capture_id)
            return bundle(capture_id)

        with patch("roadvision.media.fetch_capture", side_effect=counted_fetch):
            fetcher.request(self.first_id)
            await_results(fetcher, 1)
            fetcher.request(self.first_id)
            cached = await_results(fetcher, 1)[0]

        self.assertEqual(calls, [self.first_id])
        self.assertEqual(cached.status, "ok")
        self.assertEqual(cached.bundle, bundle(self.first_id))

    def test_not_found_is_not_cached_so_later_request_can_see_new_row(self) -> None:
        responses = [None, bundle(self.first_id)]
        fetcher = self.make_fetcher()
        with patch(
            "roadvision.media.fetch_capture",
            side_effect=lambda _conn, _capture_id: responses.pop(0),
        ):
            fetcher.request(self.first_id)
            missing = await_results(fetcher, 1)[0]
            fetcher.request(self.first_id)
            found = await_results(fetcher, 1)[0]

        self.assertEqual(missing.status, "not_found")
        self.assertEqual(found.status, "ok")

    def test_connection_failure_is_one_attempt_and_next_request_reconnects(self) -> None:
        connection = FakeConnection()
        attempts = [RuntimeError("DB kapalı"), connection]

        def factory(_dsn: str):
            outcome = attempts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        fetcher = self.make_fetcher(factory)
        with patch(
            "roadvision.media.fetch_capture",
            side_effect=lambda _conn, capture_id: bundle(capture_id),
        ):
            fetcher.request(self.first_id)
            failed = await_results(fetcher, 1)[0]
            fetcher.request(self.second_id)
            recovered = await_results(fetcher, 1)[0]

        self.assertEqual(failed.status, "error")
        self.assertIn("DB kapalı", failed.message)
        self.assertEqual(recovered.status, "ok")
        self.assertTrue(connection.read_only)

    def test_factory_is_disabled_without_dsn(self) -> None:
        self.assertIsNone(create_default_snapshot_fetcher(environ={}))
        self.assertIsNone(create_default_snapshot_fetcher(environ={"ROADVISION_DB_DSN": "  "}))


if __name__ == "__main__":
    unittest.main()
