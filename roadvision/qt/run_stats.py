"""Çalışma Özeti sayfasını besleyen saf istatistik akümülatörü.

Tk-siz ve Qt-siz; motorun ``frame`` olaylarındaki ``ModelRunStat``
demetlerinden beslenir, testlenebilirdir. Bir çalışma başladığında
``reset`` çağrılır; sayfa görünür olduğunda anlık görüntü okunur.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

BUCKET_SECONDS = 30
MAX_BUCKETS = 12


@dataclass
class TypeAggregate:
    model_id: str
    class_name: str
    count: int = 0
    confidence_sum: float = 0.0
    confidence_n: int = 0

    @property
    def mean_confidence(self) -> float | None:
        if self.confidence_n == 0:
            return None
        return self.confidence_sum / self.confidence_n


@dataclass
class ModelAggregate:
    model_id: str
    display_name: str
    object_count: int = 0
    last_count: int = 0
    last_elapsed_ms: float = 0.0
    frames: int = 0


@dataclass
class RunInfo:
    run_id: int | None = None
    source_name: str = "—"
    device: str = "—"
    profile_label: str = "—"
    started_at: float | None = None
    ended_at: float | None = None


@dataclass
class RunStats:
    info: RunInfo = field(default_factory=RunInfo)
    frames: int = 0
    fps_last: float = 0.0
    fps_min: float | None = None
    fps_max: float | None = None
    fps_sum: float = 0.0
    total_ms_last: float = 0.0
    models: dict[str, ModelAggregate] = field(default_factory=dict)
    types: dict[tuple[str, str], TypeAggregate] = field(default_factory=dict)
    buckets: dict[int, Counter] = field(default_factory=dict)
    captures: set[str] = field(default_factory=set)

    def reset(
        self,
        *,
        run_id: int | None,
        source_name: str,
        device: str,
        profile_label: str,
        started_at: float | None = None,
    ) -> None:
        self.info = RunInfo(
            run_id=run_id,
            source_name=source_name,
            device=device,
            profile_label=profile_label,
            started_at=started_at if started_at is not None else time.time(),
        )
        self.frames = 0
        self.fps_last = 0.0
        self.fps_min = None
        self.fps_max = None
        self.fps_sum = 0.0
        self.total_ms_last = 0.0
        self.models.clear()
        self.types.clear()
        self.buckets.clear()
        self.captures.clear()

    def note_frame(
        self,
        stats,
        *,
        inference_fps: float,
        total_ms: float,
        ts: float | None = None,
    ) -> None:
        now = ts if ts is not None else time.time()
        self.frames += 1
        self.fps_last = inference_fps
        self.total_ms_last = total_ms
        if inference_fps > 0:
            self.fps_sum += inference_fps
            self.fps_min = (
                inference_fps
                if self.fps_min is None
                else min(self.fps_min, inference_fps)
            )
            self.fps_max = (
                inference_fps
                if self.fps_max is None
                else max(self.fps_max, inference_fps)
            )
        bucket_key = int(now // BUCKET_SECONDS)
        bucket = self.buckets.get(bucket_key)
        if bucket is None:
            bucket = Counter()
            self.buckets[bucket_key] = bucket
            if len(self.buckets) > MAX_BUCKETS:
                for stale in sorted(self.buckets)[: len(self.buckets) - MAX_BUCKETS]:
                    del self.buckets[stale]
        for stat in stats:
            aggregate = self.models.get(stat.model_id)
            if aggregate is None:
                aggregate = ModelAggregate(stat.model_id, stat.display_name)
                self.models[stat.model_id] = aggregate
            aggregate.frames += 1
            aggregate.last_count = stat.object_count
            aggregate.last_elapsed_ms = stat.elapsed_ms
            aggregate.object_count += stat.object_count
            bucket[stat.model_id] += stat.object_count
            for detected in getattr(stat, "objects", ()) or ():
                key = (stat.model_id, detected.class_name)
                type_aggregate = self.types.get(key)
                if type_aggregate is None:
                    type_aggregate = TypeAggregate(stat.model_id, detected.class_name)
                    self.types[key] = type_aggregate
                type_aggregate.count += 1
                confidence = getattr(detected, "confidence", None)
                if confidence is not None:
                    type_aggregate.confidence_sum += float(confidence)
                    type_aggregate.confidence_n += 1

    def note_capture(self, capture_id: str) -> None:
        if capture_id:
            self.captures.add(str(capture_id))

    def finish(self, ts: float | None = None) -> None:
        if self.info.started_at is not None and self.info.ended_at is None:
            self.info.ended_at = ts if ts is not None else time.time()

    # ── türetilmiş görünümler ───────────────────────────────────────────────

    @property
    def total_objects(self) -> int:
        return sum(aggregate.object_count for aggregate in self.models.values())

    @property
    def duration_seconds(self) -> float:
        if self.info.started_at is None:
            return 0.0
        end = self.info.ended_at if self.info.ended_at is not None else time.time()
        return max(0.0, end - self.info.started_at)

    @property
    def mean_fps(self) -> float:
        return self.fps_sum / self.frames if self.frames else 0.0

    def breakdown_rows(self) -> list[TypeAggregate]:
        """Tür bazında satırlar; tür ayrımı olmayan modeller model satırı olur."""

        rows = sorted(
            self.types.values(), key=lambda item: item.count, reverse=True
        )
        typed_models = {aggregate.model_id for aggregate in rows}
        for model_id, aggregate in self.models.items():
            if model_id not in typed_models and aggregate.object_count > 0:
                rows.append(
                    TypeAggregate(
                        model_id=model_id,
                        class_name=aggregate.display_name,
                        count=aggregate.object_count,
                    )
                )
        return rows

    def bucket_series(self) -> list[tuple[str, dict[str, int]]]:
        series: list[tuple[str, dict[str, int]]] = []
        for key in sorted(self.buckets):
            stamp = time.strftime("%H:%M", time.localtime(key * BUCKET_SECONDS))
            series.append((stamp, dict(self.buckets[key])))
        return series
