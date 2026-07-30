"""Giriş/kayıt denemeleri için kayan pencereli oran sınırlayıcı (WEB_PLANI.md §8).

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

# Bellek emniyeti: izlenen anahtar sayısı bu sınıra dayandığında penceresi
# tamamen boşalmış kovalar kayıpsız ayıklanır. Kapasite hâlâ doluysa yeni
# anahtar geçici olarak reddedilir; aktif bir anahtar asla silinmez. Eski
# "tabloyu sıfırla" davranışı,
# benzersiz anahtar seliyle HEDEF anahtarın penceresini de sıfırlatarak
# oran sınırını atlatmaya izin verdiğinden kaldırıldı.
_MAX_TRACKED_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        now_fn: Callable[[], float] = time.monotonic,
        max_tracked_keys: int = _MAX_TRACKED_KEYS,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events en az 1 olmalı")
        if window_seconds <= 0:
            raise ValueError("window_seconds pozitif olmalı")
        if max_tracked_keys < 1:
            raise ValueError("max_tracked_keys en az 1 olmalı")
        self._max_events = max_events
        self._window = float(window_seconds)
        self._now = now_fn
        self._max_tracked_keys = max_tracked_keys
        self._events: dict[Hashable, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def tracked_keys(self) -> int:
        """İzlenen anahtar sayısı (test/teşhis)."""

        with self._lock:
            return len(self._events)

    def check(self, key: Hashable) -> float:
        """0.0 dönerse olay kabul edilip kaydedilir; pozitif değer,
        pencerede yer açılana kadar beklenecek saniyedir."""

        now = self._now()
        cutoff = now - self._window
        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                # Tablo yalnız yeni anahtarla büyür; ayıklama da yalnız o
                # anda gerekir. Var olan anahtarın kontrolü hiç bedel ödemez.
                if len(self._events) >= self._max_tracked_keys:
                    self._evict_locked(cutoff)
                if len(self._events) >= self._max_tracked_keys:
                    # Aktif kovayı silmek rate-limit'i fail-open yapar.
                    # En erken tamamen boşalacak kovanın süresini dönerek
                    # kapasite baskısında yeni anahtarları fail-closed tut.
                    return min(
                        active[-1] + self._window - now
                        for active in self._events.values()
                    )
                bucket = deque()
                self._events[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_events:
                return (bucket[0] + self._window) - now
            bucket.append(now)
            return 0.0

    def _evict_locked(self, cutoff: float) -> None:
        """Kapasite dolduğunda yer açar; kilit tutulurken çağrılır.

        1. adım kayıpsızdır: her kovadan pencere dışı olaylar düşülür,
        boşalan kovalar silinir (uzun çalışma sürelerinde biriken bayat
        anahtarların olağan yolu budur; hiçbir aktif pencereye dokunmaz).

        Kapasite temizliğe rağmen dolu kalırsa çağıran yeni anahtarı
        reddeder. Böylece saldırgan aktif hedef penceresini benzersiz anahtar
        seliyle veya tüm sel anahtarlarını doldurarak sıfırlatamaz.
        """

        for key in list(self._events):
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._events[key]
        if len(self._events) < self._max_tracked_keys:
            return
