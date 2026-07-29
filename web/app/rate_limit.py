"""Giriş denemeleri için kayan pencereli oran sınırlayıcı (WEB_PLANI.md §8).

Süreç içi ve bilinçli olarak basittir: panel tek API kopyasıyla çalışır;
birden çok uvicorn worker'ında her worker kendi penceresini tutar (belge:
web/README.md). Saat, testlerde deterministik ilerletilebilsin diye
enjekte edilir — motor tarafındaki monotonic zaman disipliniyle aynı.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Hashable

# Anahtar sayısı bu sınırı aşarsa tablo sıfırlanır; saldırı altında bile
# bellek sınırsız büyümez (journal kuyruğu taşma disipliniyle aynı yaklaşım).
_MAX_TRACKED_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events en az 1 olmalı")
        if window_seconds <= 0:
            raise ValueError("window_seconds pozitif olmalı")
        self._max_events = max_events
        self._window = float(window_seconds)
        self._now = now_fn
        self._events: dict[Hashable, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: Hashable) -> float:
        """0.0 dönerse olay kabul edilip kaydedilir; pozitif değer,
        pencerede yer açılana kadar beklenecek saniyedir."""

        now = self._now()
        cutoff = now - self._window
        with self._lock:
            if len(self._events) > _MAX_TRACKED_KEYS:
                self._events.clear()
            bucket = self._events.get(key)
            if bucket is None:
                bucket = deque()
                self._events[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_events:
                return (bucket[0] + self._window) - now
            bucket.append(now)
            return 0.0
