#!/usr/bin/env python3
"""Faz 3 kabul kontrolü (WEB_PLANI.md §9): tespit arşivi ve medya uçları.

Doğrulananlar:

1. ``/api/archive/types`` model → tür ağacını ve tür × doğrulama durumu
   sayımlarını döndürür (şema v3 yoksa 409 ``archive_unavailable``).
2. ``/api/archive/detections`` keyset sayfaları tekrar/atlama üretmez;
   ``review_status`` alanı geçerlidir; tür filtresi yalnız o türü döndürür.
3. Masaüstü paritesi: aynı filtre için API'nin gezdiği satır sayısı,
   web DSN'iyle çalıştırılan doğrudan SQL sayımına eşittir.
4. ``/api/media/{id}`` ``ETag: "sha256"`` döndürür; ``If-None-Match``
   eşleşmesinde gövdesiz 304 döner. ``/api/captures/{id}`` orijinal +
   işaretli blob bilgisini verir.

Arşivde hiç tespit yoksa ``--seed``, masaüstü SAHİP DSN'i
(``ROADVISION_DB_DSN``) ile küçük bir fikstür ekler: 2 medya blobu,
1 capture, model başına 1 event ve mevcut ``detection_types``
sözlüğünden 3 tespit. Fikstür ``faz3-seed`` damgalıdır; ``--cleanup-seed``
siler (detected_objects, event CASCADE'iyle temizlenir). Web rolü
salt-okunur olduğundan seed web DSN'iyle YAPILAMAZ — bilinçli tasarım.

Gerekli ortam: ``ROADVISION_WEB_ADMIN_EMAIL`` / ``ROADVISION_WEB_ADMIN_PASSWORD``
(onaylı herhangi bir hesap). İsteğe bağlı: ``ROADVISION_WEB_URL``
(varsayılan http://127.0.0.1:8800), ``ROADVISION_WEB_DSN`` (parite sayımı).
Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = os.environ.get(
    "ROADVISION_WEB_URL", "http://127.0.0.1:8800"
).rstrip("/")

# 1x1 piksel geçerli JPEG (fikstür görüntüsü).
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR"
    "ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QA"
    "FAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN"
    "//2Q=="
)
# Fikstür blobları gerçek bir 1×1 JPEG'in sonuna yalnız bu teste özgü
# işaret ekler. JPEG çözücüleri EOI sonrasını yok sayar; özgün özetler,
# temizlikte kullanıcıya ait aynı küçük görselin yanlışlıkla silinmesini
# önler.
_SEED_ORIGINAL = _TINY_JPEG + b"\nroadvision:faz3-seed:original"
_SEED_ANNOTATED = _TINY_JPEG + b"\nroadvision:faz3-seed:annotated"

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

    def raw(
        self, method: str, path: str, body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=30) as resp:
                return (
                    resp.status,
                    {key.lower(): value for key, value in resp.headers.items()},
                    resp.read(),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()},
                exc.read(),
            )
        except urllib.error.URLError as exc:
            payload = json.dumps(
                {"error": {"code": "network_error", "message": str(exc.reason)}}
            ).encode("utf-8")
            return 0, {}, payload

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        status, _headers, payload = self.raw(method, path, body, headers)
        try:
            return status, json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return status, {"raw": payload.decode("utf-8", "replace")}

    def csrf(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "rv_csrf":
                return cookie.value
        return None


def _seed(dsn: str) -> None:
    import psycopg

    print("  … faz3-seed fikstürü ekleniyor (2 blob, 1 capture, 3 tespit)")
    orig = _SEED_ORIGINAL
    anno = _SEED_ANNOTATED
    capture_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as conn, conn.transaction():
        type_rows = conn.execute(
            "SELECT type_id, model_id, class_name FROM public.detection_types "
            "ORDER BY type_id LIMIT 3"
        ).fetchall()
        if not type_rows:
            raise RuntimeError(
                "detection_types boş; masaüstü şema v3 migration'ı "
                "çalışmamış görünüyor."
            )
        media_ids = []
        for blob in (orig, anno):
            sha = hashlib.sha256(blob).hexdigest()
            row = conn.execute(
                """
                INSERT INTO public.media_blobs
                    (sha256, mime, width, height, byte_size, data)
                VALUES (%s, 'image/jpeg', 1, 1, %s, %s)
                ON CONFLICT (sha256) DO UPDATE SET byte_size = EXCLUDED.byte_size
                RETURNING id
                """,
                (sha, len(blob), blob),
            ).fetchone()
            media_ids.append(row[0])
        conn.execute(
            """
            INSERT INTO public.media_captures
                (capture_id, ts, run_id, source_name, source_kind,
                 frame_sequence, is_reprocess,
                 original_media_id, annotated_media_id)
            VALUES (%s::uuid, now(), 999, 'faz3-seed', 'photo', 1, FALSE,
                    %s, %s)
            """,
            (capture_id, media_ids[0], media_ids[1]),
        )
        model_counts: dict[str, int] = {}
        for _type_id, model_id, _class_name in type_rows:
            model_counts[model_id] = model_counts.get(model_id, 0) + 1
        for model_id, object_count in model_counts.items():
            conn.execute(
                """
                INSERT INTO public.media_capture_models
                    (capture_id, model_id, object_count)
                VALUES (%s::uuid, %s, %s)
                """,
                (capture_id, model_id, object_count),
            )
        for index, (type_id, model_id, class_name) in enumerate(type_rows):
            event_id = conn.execute(
                """
                INSERT INTO public.detection_events
                    (ts, run_id, model_id, object_count, capture_id,
                     ingest_key)
                VALUES (now(), 999, %s, 1, %s::uuid, %s)
                ON CONFLICT (ingest_key) DO NOTHING
                RETURNING id
                """,
                (model_id, capture_id, f"faz3-seed:{index}"),
            ).fetchone()
            if event_id is None:
                continue
            conn.execute(
                """
                INSERT INTO public.detected_objects
                    (event_id, ts, run_id, model_id, class_name,
                     confidence, bbox, area_ratio, type_id)
                VALUES (%s, now(), 999, %s, %s, 0.9,
                        ARRAY[10.0, 20.0, 110.0, 220.0]::real[], 0.05, %s)
                """,
                (event_id[0], model_id, class_name, type_id),
            )
    _ok(f"fikstür eklendi (capture={capture_id})")


def _cleanup(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn, conn.transaction():
        # detected_objects, detection_events CASCADE'iyle silinir.
        cur = conn.execute(
            "DELETE FROM public.detection_events "
            "WHERE ingest_key LIKE 'faz3-seed:%'"
        )
        events = cur.rowcount
        conn.execute(
            "DELETE FROM public.media_captures "
            "WHERE source_name = 'faz3-seed'"
        )
        for blob in (_SEED_ORIGINAL, _SEED_ANNOTATED):
            conn.execute(
                "DELETE FROM public.media_blobs AS mb WHERE mb.sha256 = %s "
                "AND NOT EXISTS (SELECT 1 FROM public.media_captures mc "
                "WHERE mc.original_media_id = mb.id "
                "   OR mc.annotated_media_id = mb.id)",
                (hashlib.sha256(blob).hexdigest(),),
            )
        print(f"{events} fikstür eventi ve bağlı kayıtlar silindi.")


def _walk(client: Client, query: dict, max_pages: int = 200) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    previous_key = None
    cursor = None
    for _ in range(max_pages):
        params = dict(query)
        if cursor:
            params["cursor"] = cursor
        status, body = client.request(
            "GET", "/api/archive/detections?" + urllib.parse.urlencode(
                params, doseq=True
            )
        )
        if status != 200:
            _fail(f"arşiv listesi {status} döndü: {body}")
            return rows
        for record in body.get("records", []):
            if record["id"] in seen:
                _fail(f"{record['id']} kimliği iki sayfada tekrarlandı")
                return rows
            seen.add(record["id"])
            key = (record["ts"], record["id"])
            if previous_key is not None and key >= previous_key:
                _fail(f"(ts,id) sıralaması bozuldu: {key}")
                return rows
            previous_key = key
            if record["review_status"] not in (
                "unreviewed", "correct", "corrected", "wrong"
            ):
                _fail(f"geçersiz review_status: {record['review_status']}")
                return rows
            rows.append(record)
        cursor = body.get("next_cursor")
        if not cursor:
            break
    else:
        _fail(
            f"arşiv gezintisi {max_pages} sayfada tamamlanmadı; "
            "parite sonucu eksik kalır"
        )
    return rows


def _check_filter(
    client: Client,
    *,
    label: str,
    query: dict,
    expected: list[dict],
    limit: int,
) -> None:
    actual = _walk(client, {"limit": limit, **query})
    actual_ids = [record["id"] for record in actual]
    expected_ids = [record["id"] for record in expected]
    if actual_ids == expected_ids:
        _ok(f"{label} filtresi: {len(actual_ids)} satır")
    else:
        _fail(
            f"{label} filtresi parite dışı: "
            f"beklenen {len(expected_ids)}, API {len(actual_ids)}"
        )


def _archive_total(body: dict) -> tuple[int, int]:
    models = body.get("models", [])
    total = sum(
        type_info["counts"]["total"]
        for model in models
        for type_info in model.get("types", [])
    )
    return len(models), total


def _run_acceptance(
    client: Client,
    *,
    limit: int,
    seed: bool,
    owner_dsn: str | None,
) -> None:
    status, body = client.request("GET", "/api/archive/types")
    if status == 409:
        _fail("arşiv şeması yok: masaüstü en az bir kez şema v3 "
              "migration'ını çalıştırmalı (409 archive_unavailable)")
        return
    if status != 200:
        _fail(f"/api/archive/types {status} döndü: {body}")
        return
    model_count, toplam = _archive_total(body)
    _ok(f"tür ağacı: {model_count} model, toplam {toplam} tespit")

    if toplam == 0:
        if seed and owner_dsn:
            try:
                _seed(owner_dsn)
            except Exception as exc:
                _fail(f"fikstür eklenemedi: {exc}")
                return
            status, body = client.request("GET", "/api/archive/types")
            if status != 200:
                _fail(f"seed sonrası tür ağacı {status} döndü: {body}")
                return
            model_count, toplam = _archive_total(body)
            if toplam == 0:
                _fail("fikstür eklendi ancak tür ağacı hâlâ boş")
                return
            _ok(f"seed sonrası tür ağacı: {model_count} model, {toplam} tespit")
        elif seed:
            _fail("--seed için ROADVISION_DB_DSN gerekli")
            return
        else:
            _warn("arşivde tespit yok; tam kabul için --seed ile "
                  "ROADVISION_DB_DSN verin veya masaüstünde tespit üretin")

    rows = _walk(client, {"limit": limit})
    _ok(f"filtresiz gezinti: {len(rows)} satır, tekrar/atlama yok")
    if not rows:
        _fail("arşivde satır bulunamadı; kabul tamamlanamadı")
        return

    ref_model = rows[0]["model_id"]
    _check_filter(
        client,
        label="model",
        query={"model_id": ref_model},
        expected=[row for row in rows if row["model_id"] == ref_model],
        limit=limit,
    )
    ref_run = next(
        (row["run_id"] for row in rows if row["run_id"] is not None), None
    )
    if ref_run is not None:
        _check_filter(
            client,
            label="run",
            query={"run_id": ref_run},
            expected=[row for row in rows if row["run_id"] == ref_run],
            limit=limit,
        )
    _check_filter(
        client,
        label="en az güven",
        query={"min_confidence": 0.5},
        expected=[
            row
            for row in rows
            if row["confidence"] is not None and row["confidence"] >= 0.5
        ],
        limit=limit,
    )
    _check_filter(
        client,
        label="yalnız görüntülü",
        query={"only_with_image": "true"},
        expected=[row for row in rows if row["capture_id"] is not None],
        limit=limit,
    )
    cutoff = rows[len(rows) // 2]["ts"]
    _check_filter(
        client,
        label="başlangıç zamanı",
        query={"ts_from": cutoff},
        expected=[row for row in rows if row["ts"] >= cutoff],
        limit=limit,
    )
    _check_filter(
        client,
        label="bitiş zamanı",
        query={"ts_to": cutoff},
        expected=[row for row in rows if row["ts"] < cutoff],
        limit=limit,
    )

    # Tür filtresi doğruluğu + masaüstü parite sayımı.
    ref_type = rows[0]["type_id"]
    typed = _walk(client, {"limit": limit, "type_id": ref_type})
    if all(r["type_id"] == ref_type for r in typed) and typed:
        _ok(f"tür filtresi doğru (type_id={ref_type}, {len(typed)} satır)")
    else:
        _fail(f"tür filtresi yanlış satır döndürdü (type_id={ref_type})")

    web_dsn = os.environ.get("ROADVISION_WEB_DSN")
    if web_dsn:
        import psycopg

        try:
            with psycopg.connect(web_dsn) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM public.detected_objects "
                    "WHERE type_id = %s", (ref_type,),
                ).fetchone()[0]
            if count == len(typed):
                _ok(f"masaüstü paritesi: SQL sayımı {count} == API {len(typed)}")
            else:
                _fail(f"parite bozuk: SQL {count} != API {len(typed)}")
        except Exception as exc:
            _fail(f"masaüstü parite sayımı çalışmadı: {exc}")
    else:
        _warn("ROADVISION_WEB_DSN verilmedi; parite sayımı atlandı")

    # review_status=unreviewed süzgeci geçerli alanlarla dönmeli.
    unreviewed = _walk(
        client, {"limit": limit, "review_status": "unreviewed"}
    )
    if all(r["review_status"] == "unreviewed" for r in unreviewed):
        _ok(f"review_status=unreviewed filtresi tutarlı "
            f"({len(unreviewed)} satır)")
    else:
        _fail("review_status filtresi dışı satır döndü")

    goruntulu = next((r for r in rows if r.get("annotated_media_id")), None)
    if goruntulu is None:
        _warn("görüntülü tespit yok; medya/capture kontrolleri atlandı")
    else:
        _check_filter(
            client,
            label="capture",
            query={"capture_id": goruntulu["capture_id"]},
            expected=[
                row
                for row in rows
                if row["capture_id"] == goruntulu["capture_id"]
            ],
            limit=limit,
        )
        media_id = goruntulu["annotated_media_id"]
        status, headers, payload = client.raw("GET", f"/api/media/{media_id}")
        etag = headers.get("etag")
        if status == 200 and etag and headers.get(
            "content-type", ""
        ).startswith("image/") and payload and headers.get(
            "cache-control"
        ) == "private, no-cache":
            _ok(f"medya {media_id}: 200, {len(payload)} bayt, ETag={etag[:18]}…")
        else:
            _fail(f"medya yanıtı beklenen biçimde değil: {status} {dict(headers)}")
        status, headers, payload = client.raw(
            "GET", f"/api/media/{media_id}",
            headers={"If-None-Match": etag or '"x"'},
        )
        if status == 304 and not payload:
            _ok("If-None-Match eşleşmesinde gövdesiz 304 döndü")
        else:
            _fail(f"304 beklenirken {status} ({len(payload)} bayt) döndü")

        anonymous = Client(BASE_URL)
        status, _headers, _payload = anonymous.raw(
            "GET", f"/api/media/{media_id}"
        )
        if status == 401:
            _ok("medya ucu oturumsuz isteği 401 ile reddetti")
        else:
            _fail(f"medya ucu oturumsuz istekte {status} döndürdü")

        status, body = client.request(
            "GET", f"/api/captures/{goruntulu['capture_id']}"
        )
        capture = body.get("capture", {})
        if (
            status == 200
            and capture.get("original", {}).get("media_id")
            and capture.get("annotated", {}).get("media_id") == media_id
            and capture.get("models")
        ):
            _ok("capture ayrıntısı orijinal + işaretli blob bilgisini verdi")
        else:
            _fail(f"capture ayrıntısı hatalı: {status} {body}")


def _logout(client: Client) -> None:
    csrf = client.csrf()
    if not csrf:
        _warn("kabul oturumu için CSRF çerezi bulunamadı")
        return
    status, body = client.request(
        "POST",
        "/api/auth/logout",
        headers={"X-RoadVision-CSRF": csrf},
    )
    if status == 200:
        _ok("kabul testi yönetici oturumunu kapattı")
    else:
        _fail(f"kabul oturumu kapatılamadı: {status} {body}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", action="store_true",
                        help="Arşiv boşsa ROADVISION_DB_DSN ile fikstür ekle")
    parser.add_argument("--cleanup-seed", action="store_true",
                        help="faz3-seed fikstürünü sil ve çık")
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error("--limit 1-200 aralığında olmalı")

    owner_dsn = os.environ.get("ROADVISION_DB_DSN")
    if args.cleanup_seed:
        if not owner_dsn:
            print("Temizlik için ROADVISION_DB_DSN gerekli.")
            return 1
        try:
            _cleanup(owner_dsn)
        except Exception as exc:
            print(f"Fikstür temizlenemedi: {exc}")
            return 1
        return 0

    admin_email = os.environ.get("ROADVISION_WEB_ADMIN_EMAIL")
    admin_password = os.environ.get("ROADVISION_WEB_ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        print("ROADVISION_WEB_ADMIN_EMAIL ve ROADVISION_WEB_ADMIN_PASSWORD "
              "tanımlanmalı.")
        return 1

    print(f"RoadVision Web — Faz 3 kabul doğrulaması ({BASE_URL})")
    client = Client(BASE_URL)
    status, body = client.request(
        "POST", "/api/auth/login",
        {"email": admin_email, "password": admin_password},
    )
    if status != 200:
        _fail(f"giriş başarısız: {status} {body}")
    else:
        _ok("giriş yapıldı")
        try:
            _run_acceptance(
                client,
                limit=args.limit,
                seed=args.seed,
                owner_dsn=owner_dsn,
            )
        except Exception as exc:
            _fail(f"beklenmeyen kabul hatası: {exc}")
        finally:
            _logout(client)

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
