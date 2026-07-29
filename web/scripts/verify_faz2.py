#!/usr/bin/env python3
"""Faz 2 kabul kontrolü (WEB_PLANI.md §9): log ucu performansı ve doğruluğu.

Doğrulananlar:

1. ``/api/logs`` keyset sayfaları en az 100k kayıt üzerinde gezilir;
   p95 gecikme eşiğin (varsayılan 100 ms) altındadır.
2. Sayfalar arasında id tekrarı/atlaması yoktur; ``ts`` sıralaması
   monotondur; seviye filtresi yalnız istenen seviyeleri döndürür.
3. ``has_payload`` işaretli bir kaydın ayrıntısı payload gövdesiyle gelir;
   ``/api/meta/models`` 200 döner.
4. (İsteğe bağlı) nginx üzerinden SPA kökü ve ``/api`` proxy'si yanıt verir.

Tablo 100k kaydın altındaysa ``--seed`` bayrağı, masaüstü SAHİP DSN'i
(``ROADVISION_DB_DSN``) ile ``ingest_key='faz2-seed:N'`` damgalı sentetik
kayıtlar ekler; ``--cleanup-seed`` aynı damgayla siler. Web rolü salt-okunur
olduğundan seed web DSN'iyle YAPILAMAZ — bu bilinçli bir tasarımdır.

Gerekli ortam: ``ROADVISION_WEB_ADMIN_EMAIL`` / ``ROADVISION_WEB_ADMIN_PASSWORD``
(onaylı herhangi bir hesap yeterlidir). İsteğe bağlı: ``ROADVISION_WEB_URL``
(varsayılan http://127.0.0.1:8800), ``ROADVISION_WEB_HTTP_URL`` (nginx,
ör. http://127.0.0.1:8080). Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("ROADVISION_WEB_URL", "http://127.0.0.1:8800").rstrip("/")
SEED_TARGET = 100_000

_FAILED = False


def _ok(message: str) -> None:
    print(f"  [OK]   {message}")


def _warn(message: str) -> None:
    print(f"  [UYARI] {message}")


def _fail(message: str) -> None:
    global _FAILED
    _FAILED = True
    print(f"  [HATA] {message}")


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict, float]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        started = time.perf_counter()
        try:
            with self.opener.open(req, timeout=30) as resp:
                payload = resp.read()
                elapsed = (time.perf_counter() - started) * 1000.0
                try:
                    return resp.status, json.loads(payload) if payload else {}, elapsed
                except json.JSONDecodeError:
                    # nginx SPA kökü gibi JSON olmayan yanıtlar ham taşınır.
                    return (
                        resp.status,
                        {"raw": payload.decode("utf-8", "replace")},
                        elapsed,
                    )
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            elapsed = (time.perf_counter() - started) * 1000.0
            try:
                return exc.code, json.loads(payload) if payload else {}, elapsed
            except json.JSONDecodeError:
                return exc.code, {"raw": payload.decode("utf-8", "replace")}, elapsed
        except urllib.error.URLError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            return 0, {"error": str(exc.reason)}, elapsed

    def csrf(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "rv_csrf":
                return cookie.value
        return None


def _seed(dsn: str, missing: int) -> None:
    import psycopg

    print(f"  … {missing} sentetik kayıt ekleniyor (faz2-seed damgalı)")
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute(
            """
            INSERT INTO public.log_records
                (ts, level, category, message, run_id, model_id, payload,
                 ingest_key)
            SELECT
                now() - (g * interval '2 seconds'),
                (ARRAY['debug','info','info','info','warning','error'])
                    [1 + g %% 6],
                CASE WHEN g %% 3 = 0 THEN 'app' ELSE 'detection' END,
                'faz2 sentetik kayıt #' || g,
                1 + g %% 40,
                CASE g %% 5
                    WHEN 0 THEN NULL
                    WHEN 1 THEN 'roadline'
                    WHEN 2 THEN 'traffic_sign'
                    WHEN 3 THEN 'pothole'
                    ELSE 'marking_damage'
                END,
                CASE WHEN g %% 4 = 0
                     THEN jsonb_build_object('seed', g, 'note', 'faz2')
                     ELSE '{}'::jsonb END,
                'faz2-seed:' || g
            FROM generate_series(1, %s) AS g
            ON CONFLICT (ingest_key) DO NOTHING
            """,
            (missing,),
        )


def _walk_pages(
    client: Client, label: str, query: dict, pages: int, threshold_ms: float
) -> None:
    timings: list[float] = []
    seen_ids: set[int] = set()
    previous_key: tuple[str, int] | None = None
    cursor: str | None = None
    fetched_pages = 0
    for _ in range(pages):
        params = dict(query)
        if cursor:
            params["cursor"] = cursor
        path = "/api/logs?" + urllib.parse.urlencode(params, doseq=True)
        status, body, elapsed = client.request("GET", path)
        if status != 200:
            _fail(f"{label}: sayfa isteği {status} döndü: {body}")
            return
        timings.append(elapsed)
        records = body.get("records", [])
        for record in records:
            record_id = record["id"]
            if record_id in seen_ids:
                _fail(f"{label}: {record_id} kimliği iki sayfada tekrarlandı")
                return
            seen_ids.add(record_id)
            key = (record["ts"], record_id)
            if previous_key is not None and key >= previous_key:
                _fail(f"{label}: (ts,id) sıralaması bozuldu: {key}")
                return
            previous_key = key
            levels = query.get("level")
            if levels and record["level"] not in levels:
                _fail(f"{label}: filtre dışı seviye döndü: {record['level']}")
                return
        fetched_pages += 1
        cursor = body.get("next_cursor")
        if not cursor:
            break
    p50 = statistics.median(timings)
    p95 = sorted(timings)[max(0, math.ceil(len(timings) * 0.95) - 1)]
    summary = (
        f"{label}: {fetched_pages} sayfa / {len(seen_ids)} kayıt, "
        f"p50={p50:.1f} ms, p95={p95:.1f} ms (eşik {threshold_ms:.0f} ms)"
    )
    if p95 < threshold_ms:
        _ok(summary)
    else:
        _fail(summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--threshold-ms", type=float, default=100.0)
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Kayıt sayısı 100k altındaysa ROADVISION_DB_DSN ile tamamla",
    )
    parser.add_argument(
        "--cleanup-seed",
        action="store_true",
        help="faz2-seed damgalı sentetik kayıtları sil ve çık",
    )
    args = parser.parse_args()
    if args.pages < 1:
        parser.error("--pages en az 1 olmalı")
    if not 1 <= args.limit <= 500:
        parser.error("--limit 1-500 aralığında olmalı")
    if args.threshold_ms <= 0:
        parser.error("--threshold-ms pozitif olmalı")

    owner_dsn = os.environ.get("ROADVISION_DB_DSN")
    if args.cleanup_seed:
        if not owner_dsn:
            print("Temizlik için ROADVISION_DB_DSN gerekli.")
            return 1
        import psycopg

        with psycopg.connect(owner_dsn) as conn, conn.transaction():
            cur = conn.execute(
                "DELETE FROM public.log_records "
                "WHERE ingest_key LIKE 'faz2-seed:%'"
            )
            print(f"{cur.rowcount} sentetik kayıt silindi.")
        return 0

    admin_email = os.environ.get("ROADVISION_WEB_ADMIN_EMAIL")
    admin_password = os.environ.get("ROADVISION_WEB_ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        print(
            "ROADVISION_WEB_ADMIN_EMAIL ve ROADVISION_WEB_ADMIN_PASSWORD "
            "tanımlanmalı (onaylı herhangi bir hesap yeterlidir)."
        )
        return 1

    print(f"RoadVision Web — Faz 2 kabul doğrulaması ({BASE_URL})")
    client = Client(BASE_URL)
    status, body, _elapsed = client.request(
        "POST",
        "/api/auth/login",
        {"email": admin_email, "password": admin_password},
    )
    if status != 200:
        _fail(f"giriş başarısız: {status} {body}")
        print("SONUÇ: FAIL")
        return 1
    _ok("giriş yapıldı")

    web_dsn = os.environ.get("ROADVISION_WEB_DSN")
    total: int | None = None
    if web_dsn:
        import psycopg

        with psycopg.connect(web_dsn) as conn:
            total = conn.execute(
                "SELECT count(*) FROM public.log_records"
            ).fetchone()[0]
        _ok(f"log_records satır sayısı: {total}")
        if total < SEED_TARGET:
            if args.seed and owner_dsn:
                try:
                    _seed(owner_dsn, SEED_TARGET - total)
                    with psycopg.connect(web_dsn) as conn:
                        total = conn.execute(
                            "SELECT count(*) FROM public.log_records"
                        ).fetchone()[0]
                    if total >= SEED_TARGET:
                        _ok(f"seed sonrası satır sayısı: {total}")
                    else:
                        _fail(
                            f"seed sonrası satır sayısı hedefin altında: {total}"
                        )
                except Exception as exc:
                    _fail(f"sentetik kayıt eklenemedi: {exc}")
            else:
                _warn(
                    f"kayıt sayısı {SEED_TARGET} altında; 100k kabulü için "
                    "--seed ile ROADVISION_DB_DSN verin"
                )
    else:
        _warn("ROADVISION_WEB_DSN verilmedi; satır sayısı doğrulanamadı")

    _walk_pages(
        client,
        "filtresiz sayfalama",
        {"limit": args.limit},
        args.pages,
        args.threshold_ms,
    )
    _walk_pages(
        client,
        "seviye filtresi (warning+error)",
        {"limit": args.limit, "level": ["warning", "error"]},
        args.pages,
        args.threshold_ms,
    )

    status, body, _elapsed = client.request("GET", "/api/meta/models")
    if status == 200:
        _ok(f"/api/meta/models → {len(body.get('models', []))} model")
    else:
        _fail(f"/api/meta/models {status} döndü")

    status, body, _elapsed = client.request(
        "GET", "/api/logs?limit=200"
    )
    detay_id = next(
        (r["id"] for r in body.get("records", []) if r.get("has_payload")),
        None,
    )
    if detay_id is None:
        _warn("has_payload işaretli kayıt bulunamadı; ayrıntı testi atlandı")
    else:
        status, body, _elapsed = client.request("GET", f"/api/logs/{detay_id}")
        payload = body.get("record", {}).get("payload")
        if status == 200 and isinstance(payload, dict) and payload:
            _ok(f"ayrıntı ucu payload döndürdü (id={detay_id})")
        else:
            _fail(f"ayrıntı ucu beklenen payload'ı vermedi: {status} {body}")

    frontend_url = os.environ.get("ROADVISION_WEB_HTTP_URL")
    if frontend_url:
        fe = Client(frontend_url)
        status, body, _elapsed = fe.request("GET", "/")
        raw = body.get("raw", "") if isinstance(body, dict) else ""
        if status == 200 and 'id="root"' in raw:
            _ok("nginx SPA kökü sunuyor")
        else:
            _fail(f"nginx SPA kökü doğrulanamadı: {status}")
        status, body, _elapsed = fe.request("GET", "/api/auth/me")
        if status in (200, 401):
            _ok("nginx /api proxy'si API'ye ulaşıyor")
        else:
            _fail(f"nginx /api proxy'si beklenmedik yanıt verdi: {status}")

    csrf = client.csrf()
    if csrf:
        status, body, _elapsed = client.request(
            "POST",
            "/api/auth/logout",
            headers={"X-RoadVision-CSRF": csrf},
        )
        if status == 200:
            _ok("kabul testi yönetici oturumunu kapattı")
        else:
            _fail(f"kabul testi oturumu kapatılamadı: {status} {body}")
    else:
        _fail("giriş sonrası CSRF çerezi bulunamadı")

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
