#!/usr/bin/env python3
"""Faz 4 kabul kontrolü (WEB_PLANI.md §9): doğrulama akışı ve dataset katmanı.

Doğrulananlar:

1. ``POST /api/reviews`` tek transaction'da üçlüyü yazar: karar +
   copy-on-verify medya + karar × model bölümüne düşen dataset örneği.
2. Çifte karar 409 ``already_reviewed``; CSRF başlıksız yazım 403.
3. ``corrected`` kuralları: çapraz-model sınıf 400 ``unknown_class``;
   semantic modelde 400 ``semantic_no_correction`` (roadline tespiti varsa).
4. Ölçek gidiş-dönüşü: arayüz formülüyle (görüntü→kare oranı) gönderilen
   düzeltilmiş kutu, DB'deki ``final_bbox``ta ±1 piksel içinde durur (§4.6).
5. ``PATCH`` karar değişikliği örneği yeni bölüme taşır (wrong→positive)
   ve ``admin_audit``e ``change_review`` yazar.
6. ``/api/reviews/bulk`` kısmi başarı raporu döndürür.

``--seed`` masaüstü SAHİP DSN'i ile yalnız bu kabul çalışmasında kullanılan
``faz4-seed`` damgalı fikstürü ekler (2 blob, 1 capture, 4 tespit).
``--cleanup-seed`` fikstürü ve webapp'teki bağlı karar/örnek satırlarını
siler (webapp silmeleri için ``ROADVISION_WEB_DSN`` gerekir).

Gerekli ortam: ``ROADVISION_WEB_ADMIN_EMAIL`` / ``ROADVISION_WEB_ADMIN_PASSWORD``
(PATCH adımı yönetici olmayan sahiple de geçer; audit için admin önerilir).
İsteğe bağlı: ``ROADVISION_WEB_URL``, ``ROADVISION_WEB_DSN``,
``ROADVISION_DB_DSN``. Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = os.environ.get("ROADVISION_WEB_URL", "http://127.0.0.1:8800").rstrip("/")

_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR"
    "ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QA"
    "FAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN"
    "//2Q=="
)

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

    def csrf(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "rv_csrf":
                return cookie.value
        return None

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        with_csrf: bool = True,
    ) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if with_csrf and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
            token = self.csrf()
            if token:
                req.add_header("X-RoadVision-CSRF", token)
        try:
            with self.opener.open(req, timeout=30) as resp:
                status = resp.status
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return exc.code, {"raw": payload.decode("utf-8", "replace")}
        try:
            return status, json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return status, {"raw": payload.decode("utf-8", "replace")}


def _seed(dsn: str) -> list[int]:
    import psycopg

    print("  … faz4-seed fikstürü ekleniyor")
    orig = _TINY_JPEG
    anno = _TINY_JPEG + b"\x01"
    capture_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as conn, conn.transaction():
        type_rows = conn.execute(
            """
            SELECT t.type_id, t.model_id, t.class_name
            FROM detection_types AS t
            LEFT JOIN roadvision_model_catalog AS m
                ON m.model_id = t.model_id
            WHERE COALESCE(m.task, 'detect') = 'detect'
            ORDER BY t.type_id
            LIMIT 4
            """
        ).fetchall()
        if not type_rows:
            raise SystemExit("detect görevli tür bulunamadı; şema v3 gerekli.")
        # Bulk kabulü bir hata + bir başarı öğesi gerektirir. Küçük test
        # sözlüklerinde dört ayrı tür yoksa mevcut detect türlerini döndür.
        type_rows = [
            type_rows[index % len(type_rows)]
            for index in range(max(4, len(type_rows)))
        ][:4]
        media_ids = []
        for blob in (orig, anno):
            sha = hashlib.sha256(blob).hexdigest()
            row = conn.execute(
                """
                INSERT INTO media_blobs
                    (sha256, mime, width, height, byte_size, data)
                VALUES (%s, 'image/jpeg', 1280, 720, %s, %s)
                ON CONFLICT (sha256) DO UPDATE SET width = 1280, height = 720
                RETURNING id
                """,
                (sha, len(blob), blob),
            ).fetchone()
            media_ids.append(row[0])
        conn.execute(
            """
            INSERT INTO media_captures
                (capture_id, ts, run_id, source_name, source_kind,
                 frame_sequence, is_reprocess,
                 original_media_id, annotated_media_id)
            VALUES (%s::uuid, now(), 998, 'faz4-seed', 'photo', 1, FALSE,
                    %s, %s)
            """,
            (capture_id, media_ids[0], media_ids[1]),
        )
        object_ids = []
        for index, (type_id, model_id, class_name) in enumerate(type_rows):
            event = conn.execute(
                """
                INSERT INTO detection_events
                    (ts, run_id, model_id, object_count, capture_id,
                     ingest_key)
                VALUES (now(), 998, %s, 1, %s::uuid, %s)
                ON CONFLICT (ingest_key) DO NOTHING
                RETURNING id
                """,
                (model_id, capture_id, f"faz4-seed:{index}"),
            ).fetchone()
            if event is None:
                continue
            object_row = conn.execute(
                """
                INSERT INTO detected_objects
                    (event_id, ts, run_id, model_id, class_name,
                     confidence, bbox, area_ratio, type_id)
                VALUES (%s, now(), 998, %s, %s, 0.85,
                        ARRAY[100.0, 80.0, 400.0, 360.0]::real[], 0.09, %s)
                RETURNING id
                """,
                (event[0], model_id, class_name, type_id),
            ).fetchone()
            object_ids.append(object_row[0])
    _ok("fikstür eklendi")
    return object_ids


def _cleanup(owner_dsn: str, web_dsn: str | None) -> None:
    import psycopg

    with psycopg.connect(owner_dsn) as conn:
        object_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT o.id FROM detected_objects AS o
                JOIN detection_events AS e ON e.id = o.event_id
                WHERE e.ingest_key LIKE 'faz4-seed:%'
                """
            ).fetchall()
        ]
    if web_dsn and object_ids:
        with psycopg.connect(web_dsn) as conn, conn.transaction():
            conn.execute(
                "DELETE FROM webapp.admin_audit "
                "WHERE action = 'change_review' AND target = ANY(%s)",
                ([f"object:{object_id}" for object_id in object_ids],),
            )
            conn.execute(
                "DELETE FROM webapp.dataset_samples WHERE object_id = ANY(%s)",
                (object_ids,),
            )
            conn.execute(
                "DELETE FROM webapp.detection_reviews WHERE object_id = ANY(%s)",
                (object_ids,),
            )
            seed_shas = [
                hashlib.sha256(blob).hexdigest()
                for blob in (_TINY_JPEG, _TINY_JPEG + b"\x01")
            ]
            conn.execute(
                "DELETE FROM webapp.dataset_media AS dm "
                "WHERE dm.sha256 = ANY(%s) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM webapp.dataset_samples AS ds "
                "WHERE ds.original_sha = dm.sha256 "
                "OR ds.annotated_sha = dm.sha256)",
                (seed_shas,),
            )
        print(f"webapp: {len(object_ids)} tespitin karar/örnek satırları silindi.")
    elif object_ids:
        print("UYARI: ROADVISION_WEB_DSN verilmedi; webapp satırları kaldı.")
    with psycopg.connect(owner_dsn) as conn, conn.transaction():
        cur = conn.execute(
            "DELETE FROM detection_events WHERE ingest_key LIKE 'faz4-seed:%'"
        )
        conn.execute(
            "DELETE FROM media_captures WHERE source_name = 'faz4-seed'"
        )
        for blob in (_TINY_JPEG, _TINY_JPEG + b"\x01"):
            conn.execute(
                "DELETE FROM media_blobs WHERE sha256 = %s "
                "AND NOT EXISTS (SELECT 1 FROM media_captures mc "
                "WHERE mc.original_media_id = media_blobs.id "
                "   OR mc.annotated_media_id = media_blobs.id)",
                (hashlib.sha256(blob).hexdigest(),),
            )
        print(f"public: {cur.rowcount} fikstür eventi silindi.")


def _sample_row(web_dsn: str, object_id: int):
    import psycopg

    with psycopg.connect(web_dsn) as conn:
        return conn.execute(
            """
            SELECT verdict, tableoid::regclass::text, final_class_name,
                   final_bbox, original_sha
            FROM webapp.dataset_samples WHERE object_id = %s
            """,
            (object_id,),
        ).fetchone()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--cleanup-seed", action="store_true")
    args = parser.parse_args()

    owner_dsn = os.environ.get("ROADVISION_DB_DSN")
    web_dsn = os.environ.get("ROADVISION_WEB_DSN")
    if args.cleanup_seed:
        if not owner_dsn:
            print("Temizlik için ROADVISION_DB_DSN gerekli.")
            return 1
        _cleanup(owner_dsn, web_dsn)
        return 0

    admin_email = os.environ.get("ROADVISION_WEB_ADMIN_EMAIL")
    admin_password = os.environ.get("ROADVISION_WEB_ADMIN_PASSWORD")
    if not admin_email or not admin_password:
        print("ROADVISION_WEB_ADMIN_EMAIL ve ROADVISION_WEB_ADMIN_PASSWORD gerekli.")
        return 1

    print(f"RoadVision Web — Faz 4 kabul doğrulaması ({BASE_URL})")
    client = Client(BASE_URL)
    status, body = client.request(
        "POST", "/api/auth/login",
        {"email": admin_email, "password": admin_password},
    )
    if status != 200:
        _fail(f"giriş başarısız: {status} {body}")
        print("SONUÇ: FAIL")
        return 1
    _ok("giriş yapıldı")

    def logout() -> None:
        try:
            client.request("POST", "/api/auth/logout", {})
        except Exception:
            pass

    # Erken FAIL dönüşlerinde de kabul oturumu bırakma.
    atexit.register(logout)

    def queue(params: dict | None = None) -> list[dict]:
        qs = urllib.parse.urlencode(params or {"limit": 50}, doseq=True)
        status, body = client.request("GET", "/api/verify/queue?" + qs)
        if status != 200:
            _fail(f"kuyruk {status} döndü: {body}")
            return []
        return body.get("records", [])

    if args.seed:
        if not owner_dsn:
            _fail("--seed için ROADVISION_DB_DSN gerekli")
            print("SONUÇ: FAIL")
            return 1
        # Önce yarım kalmış eski fikstürü temizle; ardından yalnız bu çalışmada
        # üretilen kimlikleri seç. Gerçek kullanıcı tespitlerine asla karar yazma.
        _cleanup(owner_dsn, web_dsn)
        seeded_ids = set(_seed(owner_dsn))
        records = [
            record
            for record in queue({"limit": 100, "run_id": 998, "order": "desc"})
            if record["id"] in seeded_ids
        ]
    else:
        records = queue()
    if len(records) < 4:
        _fail("kuyrukta en az 4 karar bekleyen tespit gerekli "
              "(--seed ile ROADVISION_DB_DSN verin)")
        print("SONUÇ: FAIL")
        return 1
    _ok(f"kuyruk: {len(records)} karar bekleyen (en eski önce)")

    # Tür ağacından çapraz-model sınıf adı bul.
    status, body = client.request("GET", "/api/archive/types")
    tree = body.get("models", []) if status == 200 else []

    q0, q1, q2 = records[0], records[1], records[2]

    # CSRF olmadan yazım reddedilir.
    status, body = client.request(
        "POST", "/api/reviews",
        {"object_id": q0["id"], "verdict": "correct"},
        with_csrf=False,
    )
    if status == 403:
        _ok("CSRF başlıksız karar 403 ile reddedildi")
    else:
        _fail(f"CSRF'siz istek {status} döndü")

    # 1) correct → üçlü yazım.
    status, body = client.request(
        "POST", "/api/reviews", {"object_id": q0["id"], "verdict": "correct"}
    )
    if status == 201 or (status == 200 and "review" in body):
        _ok(f"correct kararı yazıldı (object {q0['id']})")
    else:
        _fail(f"correct kararı {status} döndü: {body}")
    if web_dsn:
        row = _sample_row(web_dsn, q0["id"])
        if row and row[0] == "correct" and "ds_positive_" in row[1]:
            _ok(f"örnek doğru bölümde: {row[1]}")
        else:
            _fail(f"dataset örneği bulunamadı/yanlış bölümde: {row}")

    # 2) çifte karar 409.
    status, body = client.request(
        "POST", "/api/reviews", {"object_id": q0["id"], "verdict": "wrong"}
    )
    if status == 409 and body.get("error", {}).get("code") == "already_reviewed":
        _ok("çifte karar 409 already_reviewed")
    else:
        _fail(f"çifte karar {status} döndü: {body}")

    # 3) çapraz-model sınıf reddi.
    cross_class = None
    for model in tree:
        if model["model_id"] != q1["model_id"]:
            kendi = {t["class_name"] for m in tree
                     if m["model_id"] == q1["model_id"] for t in m["types"]}
            for t in model["types"]:
                if t["class_name"] not in kendi:
                    cross_class = t["class_name"]
                    break
        if cross_class:
            break
    if cross_class:
        status, body = client.request(
            "POST", "/api/reviews",
            {"object_id": q1["id"], "verdict": "corrected",
             "corrected_class": cross_class},
        )
        if status == 400 and body.get("error", {}).get("code") == "unknown_class":
            _ok(f"çapraz-model sınıf ('{cross_class}') 400 unknown_class")
        else:
            _fail(f"çapraz-model düzeltme {status} döndü: {body}")
    else:
        _warn("çapraz-model sınıf adayı bulunamadı; kontrol atlandı")

    # 4) ölçek gidiş-dönüşü + corrected yazımı (§4.6).
    frame_w = frame_h = None
    if q1.get("capture_id"):
        status, body = client.request(
            "GET", "/api/captures/" + q1["capture_id"]
        )
        if status == 200:
            original = body["capture"]["original"]
            frame_w, frame_h = original["width"], original["height"]
    ayni_model = [
        t["class_name"] for m in tree if m["model_id"] == q1["model_id"]
        for t in m["types"] if t["class_name"] != q1["class_name"]
    ]
    corrected_payload: dict = {"object_id": q1["id"], "verdict": "corrected"}
    hedef_kutu = None
    if frame_w and frame_h:
        display_w, display_h = 640, max(1, round(640 * frame_h / frame_w))
        display_box = (50.0, 40.0, 300.0, 200.0)
        sx, sy = frame_w / display_w, frame_h / display_h
        hedef_kutu = [display_box[0] * sx, display_box[1] * sy,
                      display_box[2] * sx, display_box[3] * sy]
        corrected_payload["corrected_bbox"] = hedef_kutu
    if ayni_model:
        corrected_payload["corrected_class"] = ayni_model[0]
    if "corrected_bbox" not in corrected_payload and not ayni_model:
        _warn("düzeltme adayı üretilemedi; corrected kontrolü atlandı")
    else:
        status, body = client.request("POST", "/api/reviews", corrected_payload)
        if status in (200, 201) and body.get("review", {}).get("verdict") == "corrected":
            _ok(f"corrected kararı yazıldı (object {q1['id']})")
        else:
            _fail(f"corrected kararı {status} döndü: {body}")
        if web_dsn and hedef_kutu:
            row = _sample_row(web_dsn, q1["id"])
            if row and row[3] and all(
                abs(float(a) - b) <= 1.0 for a, b in zip(row[3], hedef_kutu)
            ):
                _ok("ölçek gidiş-dönüşü ±1 px içinde (final_bbox)")
            else:
                _fail(f"final_bbox sapması >1 px: {row and row[3]} vs {hedef_kutu}")

    # 5) wrong → PATCH ile correct: bölüm taşınması + audit.
    status, body = client.request(
        "POST", "/api/reviews", {"object_id": q2["id"], "verdict": "wrong"}
    )
    if status in (200, 201):
        _ok(f"wrong kararı yazıldı (object {q2['id']})")
    else:
        _fail(f"wrong kararı {status} döndü: {body}")
    if web_dsn:
        row = _sample_row(web_dsn, q2["id"])
        if row and "ds_wrong_" in row[1]:
            _ok(f"örnek wrong bölümünde: {row[1]}")
        else:
            _fail(f"wrong örneği yanlış bölümde: {row}")
    status, body = client.request(
        "PATCH", f"/api/reviews/{q2['id']}", {"verdict": "correct"}
    )
    if status == 200 and body.get("previous_verdict") == "wrong":
        _ok("PATCH karar değişikliği kabul edildi (wrong → correct)")
    else:
        _fail(f"PATCH {status} döndü: {body}")
    if web_dsn:
        import psycopg

        row = _sample_row(web_dsn, q2["id"])
        if row and row[0] == "correct" and "ds_positive_" in row[1]:
            _ok(f"örnek yeni bölüme taşındı: {row[1]}")
        else:
            _fail(f"bölüm taşınması doğrulanamadı: {row}")
        with psycopg.connect(web_dsn) as conn:
            audit = conn.execute(
                "SELECT count(*) FROM webapp.admin_audit "
                "WHERE action = 'change_review' AND target = %s",
                (f"object:{q2['id']}",),
            ).fetchone()[0]
        if audit >= 1:
            _ok("karar değişikliği admin_audit'e yazıldı")
        else:
            _fail("change_review audit kaydı bulunamadı")

    # 6) bulk kısmi başarı.
    status, body = client.request(
        "POST", "/api/reviews/bulk",
        {"items": [
            {"object_id": q0["id"], "verdict": "correct"},  # already_reviewed
        ] + ([{"object_id": records[3]["id"], "verdict": "correct"}]
             if len(records) > 3 else [])},
    )
    if status == 200 and body.get("error_count", 0) >= 1:
        _ok(f"bulk kısmi rapor: ok={body.get('ok_count')} "
            f"error={body.get('error_count')}")
    else:
        _fail(f"bulk yanıtı beklenen biçimde değil: {status} {body}")

    # 7) semantic model reddi (roadline tespiti varsa).
    sem = queue({"limit": 50, "model_id": "roadline"})
    if sem:
        status, body = client.request(
            "POST", "/api/reviews",
            {"object_id": sem[0]["id"], "verdict": "corrected",
             "corrected_class": sem[0]["class_name"]},
        )
        if status == 400 and body.get("error", {}).get("code") in (
            "semantic_no_correction", "no_change"
        ):
            _ok("semantic modelde corrected reddedildi")
        else:
            _fail(f"semantic corrected {status} döndü: {body}")
    else:
        _warn("roadline tespiti yok; semantic reddi kontrolü atlandı")

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
