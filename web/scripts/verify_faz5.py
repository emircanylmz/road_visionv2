#!/usr/bin/env python3
"""Faz 5 kabul kontrolü (WEB_PLANI.md §9): dataset kırılımı, export ve istatistik.

Doğrulananlar:

1. ``/api/datasets/summary`` model × tür × karar kırılımını verir.
2. ``POST /api/datasets/export`` 202 ile iş açar; iş arka planda ``done``
   olur ve indirilen zip YOLO düzenindedir: ``data.yaml`` sözlüğü,
   ``manifest.json`` sayımları, kare başına görüntü + etiket.
3. **Kabul (§9 Faz 5):** etiketler ``final_*`` değerlerden üretilir ve
   normalize koordinatlar DB'deki ``final_bbox / frame`` bölümüyle 1e-4
   içinde eşleşir; ``wrong`` kapsamı ayrı seçilebilir ve boş etiketli
   (hard-negative/background) görüntüler üretir.
4. Aynı model + kapsam için ikinci istek işi bitmeden 409
   ``export_in_progress`` döner; ``download`` işi bitmeden 409
   ``export_not_ready``.
5. ``/api/stats/overview`` panel kartı alanlarını döndürür.

Örnek yoksa ``--seed``, ``faz5-seed`` fikstürünü ekler ve API üzerinden
karar vererek (1 correct + 1 wrong) dataset örneği üretir.
``--cleanup-seed`` fikstürü, kararları ve fikstürden doğan export işlerini
geri alır. Gerekli ortam: ``ROADVISION_WEB_ADMIN_EMAIL`` /
``ROADVISION_WEB_ADMIN_PASSWORD``; DB kontrolleri için
``ROADVISION_WEB_DSN``; seed için ``ROADVISION_DB_DSN``.
Çıkış kodu 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import atexit
import argparse
import base64
import hashlib
import http.cookiejar
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

BASE_URL = os.environ.get("ROADVISION_WEB_URL", "http://127.0.0.1:8800").rstrip("/")

_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHR"
    "ofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QA"
    "FAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN"
    "//2Q=="
)

_FAILED = False
_EXPORT_MARK_ACTION = "verify_faz5_export"


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

    def raw(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
            token = self.csrf()
            if token:
                req.add_header("X-RoadVision-CSRF", token)
        try:
            with self.opener.open(req, timeout=60) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def request(self, method: str, path: str, body: dict | None = None):
        status, _headers, payload = self.raw(method, path, body)
        try:
            return status, json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return status, {"raw": payload.decode("utf-8", "replace")}


def _logout(client: Client) -> None:
    """Kabul yarıda kalsa da yalnız bu istemcinin oturumunu kapat."""

    try:
        client.request("POST", "/api/auth/logout")
    except Exception:
        pass


def _mark_export_job(dsn: str | None, job_id: int) -> None:
    """Kabul export'unu kesin kimliğiyle işaretle; temizlik kapsamını daralt."""

    if not dsn:
        return
    import psycopg

    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute(
            """
            INSERT INTO webapp.admin_audit (actor_id, action, target)
            SELECT requested_by, %s, %s
            FROM webapp.export_jobs
            WHERE job_id = %s
            """,
            (_EXPORT_MARK_ACTION, f"job:{job_id}", job_id),
        )


def _seed_fixture(dsn: str) -> None:
    import psycopg

    print("  … faz5-seed fikstürü ekleniyor")
    orig = _TINY_JPEG + b"\x05"
    anno = _TINY_JPEG + b"\x06"
    capture_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as conn, conn.transaction():
        type_rows = conn.execute(
            """
            SELECT t.type_id, t.model_id, t.class_name
            FROM detection_types AS t
            LEFT JOIN roadvision_model_catalog AS m
                ON m.model_id = t.model_id
            WHERE COALESCE(m.task, 'detect') = 'detect'
              AND t.model_id IN ('roadline', 'traffic_sign', 'pothole',
                                 'marking_damage')
            ORDER BY t.type_id
            LIMIT 2
            """
        ).fetchall()
        if not type_rows:
            raise SystemExit("detect görevli tür yok; şema v3 gerekli.")
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
            VALUES (%s::uuid, now(), 997, 'faz5-seed', 'photo', 1, FALSE,
                    %s, %s)
            """,
            (capture_id, media_ids[0], media_ids[1]),
        )
        for index in range(2):
            type_id, model_id, class_name = type_rows[index % len(type_rows)]
            event = conn.execute(
                """
                INSERT INTO detection_events
                    (ts, run_id, model_id, object_count, capture_id,
                     ingest_key)
                VALUES (now(), 997, %s, 1, %s::uuid, %s)
                ON CONFLICT (ingest_key) DO NOTHING
                RETURNING id
                """,
                (model_id, capture_id, f"faz5-seed:{index}"),
            ).fetchone()
            if event is None:
                continue
            conn.execute(
                """
                INSERT INTO detected_objects
                    (event_id, ts, run_id, model_id, class_name,
                     confidence, bbox, area_ratio, type_id)
                VALUES (%s, now(), 997, %s, %s, 0.8,
                        ARRAY[64.0, 36.0, 640.0, 360.0]::real[], 0.2, %s)
                """,
                (event[0], model_id, class_name, type_id),
            )
    _ok("fikstür eklendi")


def _seed_object_ids(dsn: str) -> list[int]:
    return [object_id for object_id, _model_id in _seed_objects(dsn)]


def _seed_objects(dsn: str) -> list[tuple[int, str]]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        return [
            (row[0], row[1])
            for row in conn.execute(
                """
                SELECT o.id, o.model_id FROM detected_objects AS o
                JOIN detection_events AS e ON e.id = o.event_id
                WHERE e.ingest_key LIKE 'faz5-seed:%'
                ORDER BY o.id
                """
            ).fetchall()
        ]


def _cleanup(owner_dsn: str, web_dsn: str | None) -> None:
    import psycopg

    object_ids = _seed_object_ids(owner_dsn)
    if web_dsn:
        with psycopg.connect(web_dsn) as conn, conn.transaction():
            job_ids = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT split_part(target, ':', 2)::bigint
                    FROM webapp.admin_audit
                    WHERE action = %s AND target ~ '^job:[0-9]+$'
                    """,
                    (_EXPORT_MARK_ACTION,),
                ).fetchall()
            ]
            if job_ids:
                conn.execute(
                    "DELETE FROM webapp.export_jobs WHERE job_id = ANY(%s)",
                    (job_ids,),
                )
            conn.execute(
                "DELETE FROM webapp.admin_audit WHERE action = %s",
                (_EXPORT_MARK_ACTION,),
            )
            if object_ids:
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
                for blob in (_TINY_JPEG + b"\x05", _TINY_JPEG + b"\x06")
            ]
            conn.execute(
                """
                DELETE FROM webapp.dataset_media AS m
                WHERE m.sha256 = ANY(%s)
                  AND NOT EXISTS (
                      SELECT 1 FROM webapp.dataset_samples AS s
                      WHERE s.original_sha = m.sha256
                         OR s.annotated_sha = m.sha256
                  )
                """,
                (seed_shas,),
            )
        print(
            "webapp: yalnız işaretli kabul exportları ve fikstür satırları silindi."
        )
    with psycopg.connect(owner_dsn) as conn, conn.transaction():
        cur = conn.execute(
            "DELETE FROM detection_events WHERE ingest_key LIKE 'faz5-seed:%'"
        )
        conn.execute(
            "DELETE FROM media_captures WHERE source_name = 'faz5-seed'"
        )
        for blob in (_TINY_JPEG + b"\x05", _TINY_JPEG + b"\x06"):
            conn.execute(
                "DELETE FROM media_blobs WHERE sha256 = %s "
                "AND NOT EXISTS (SELECT 1 FROM media_captures mc "
                "WHERE mc.original_media_id = media_blobs.id "
                "   OR mc.annotated_media_id = media_blobs.id)",
                (hashlib.sha256(blob).hexdigest(),),
            )
        print(f"public: {cur.rowcount} fikstür eventi silindi.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--cleanup-seed", action="store_true")
    parser.add_argument("--timeout", type=int, default=90,
                        help="export işinin bitmesi için saniye")
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

    print(f"RoadVision Web — Faz 5 kabul doğrulaması ({BASE_URL})")
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
    atexit.register(_logout, client)

    # --seed her zaman izole fikstürle çalışır; mevcut kullanıcı dataset'ini
    # kabul girdisi veya temizlik hedefi yapmaz.
    seed_positive_model: str | None = None
    seed_wrong_model: str | None = None
    if args.seed:
        if not owner_dsn or not web_dsn:
            _fail("--seed için ROADVISION_DB_DSN ve ROADVISION_WEB_DSN gerekli")
            print("SONUÇ: FAIL")
            return 1
        _cleanup(owner_dsn, web_dsn)
        _seed_fixture(owner_dsn)
        seed_objects = _seed_objects(owner_dsn)
        for (object_id, _model_id), verdict in zip(
            seed_objects, ("correct", "wrong")
        ):
            status, body = client.request(
                "POST", "/api/reviews",
                {"object_id": object_id, "verdict": verdict},
            )
            if status not in (200, 201):
                _fail(f"seed kararı {status} döndü: {body}")
        if len(seed_objects) >= 2:
            seed_positive_model = seed_objects[0][1]
            seed_wrong_model = seed_objects[1][1]

    status, body = client.request("GET", "/api/datasets/summary")
    if status != 200:
        _fail(f"summary {status} döndü: {body}")
        print("SONUÇ: FAIL")
        return 1
    toplam = sum(
        sum(model["totals"].values()) for model in body.get("models", [])
    )
    if toplam == 0:
        _fail("dataset örneği yok; --seed kullanın veya Doğrulama "
              "sekmesinden karar üretin")
        print("SONUÇ: FAIL")
        return 1

    models = body.get("models", [])
    _ok(f"kırılım: {len(models)} model, "
        + ", ".join(
            f"{m['model_id']}={sum(m['totals'].values())}" for m in models
        ))
    hedef = (
        next((m for m in models if m["model_id"] == seed_positive_model), None)
        if seed_positive_model
        else next(
            (
                m
                for m in models
                if m["totals"].get("correct", 0)
                + m["totals"].get("corrected", 0)
                > 0
            ),
            None,
        )
    )
    if hedef is None:
        _fail("pozitif örnekli model yok; export kabulü koşulamaz")
        print("SONUÇ: FAIL")
        return 1
    model_id = hedef["model_id"]

    # Export işi: 202 → poll → done → zip doğrulaması.
    status, body = client.request(
        "POST", "/api/datasets/export",
        {"model_id": model_id, "verdict": "positive"},
    )
    if status != 202:
        _fail(f"export başlatma {status} döndü: {body}")
        print("SONUÇ: FAIL")
        return 1
    job_id = body["job"]["job_id"]
    _mark_export_job(web_dsn, job_id)
    _ok(f"export işi açıldı (job {job_id})")

    # İş koşarken: erken indirme 409, aynı kapsam ikinci istek 409.
    status, body = client.request(
        "GET", f"/api/datasets/exports/{job_id}/download"
    )
    early_download = status
    status, body = client.request(
        "POST", "/api/datasets/export",
        {"model_id": model_id, "verdict": "positive"},
    )
    duplicate_status = status

    son = time.time() + args.timeout
    job = None
    while time.time() < son:
        status, body = client.request(f"GET", f"/api/datasets/exports/{job_id}")
        job = body.get("job", {})
        if job.get("status") in ("done", "failed"):
            break
        time.sleep(1.5)
    if not job or job.get("status") != "done":
        _fail(f"iş 'done' olmadı: {job}")
        print("SONUÇ: FAIL")
        return 1
    _ok(f"iş tamamlandı: {job['image_count']} görüntü, "
        f"{job['sample_count']} örnek, {job['byte_size']} bayt")
    if early_download == 409:
        _ok("iş bitmeden indirme 409 export_not_ready")
    elif early_download == 200:
        _warn("iş erken bitti; export_not_ready kontrolü zamanlanamadı")
    else:
        _fail(f"erken indirme {early_download} döndü")
    if duplicate_status == 409:
        _ok("aynı model+kapsam ikinci istek 409 export_in_progress")
    elif duplicate_status == 202:
        _warn("iş erken bitti; export_in_progress kontrolü zamanlanamadı")
    else:
        _fail(f"çifte export isteği {duplicate_status} döndü")

    status, headers, payload = client.raw(
        "GET", f"/api/datasets/exports/{job_id}/download"
    )
    content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"),
        None,
    )
    if status != 200 or content_type != "application/zip":
        _fail(f"indirme {status} / {content_type} döndü")
        print("SONUÇ: FAIL")
        return 1
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = set(archive.namelist())
    if "data.yaml" in names and "manifest.json" in names:
        _ok(f"zip YOLO düzeninde ({len(names)} girdi)")
    else:
        _fail(f"zip düzeni eksik: {sorted(names)[:6]}")
    manifest = json.loads(archive.read("manifest.json"))
    label_files = [name for name in names if name.startswith("labels/")]
    image_files = {name for name in names if name.startswith("images/")}
    if len(label_files) == manifest["image_count"] and all(
        "images/" + name[len("labels/"):-4] + ".jpg" in image_files
        for name in label_files
    ):
        _ok("kare başına görüntü + etiket eşleşmesi tam")
    else:
        _fail("etiket/görüntü eşleşmesi bozuk")

    # §9 kabulü: normalize etiketler DB'deki final_bbox/frame ile eşleşmeli.
    if web_dsn:
        import psycopg

        yaml_names = [
            line.split(": ", 1)[1]
            for line in archive.read("data.yaml").decode().splitlines()
            if line.startswith("  ")
        ]
        dogru = eksik = 0
        with psycopg.connect(web_dsn) as conn:
            expected_names = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT class_name
                    FROM public.detection_types
                    WHERE model_id = %s
                    ORDER BY class_index NULLS LAST, class_name, type_id
                    """,
                    (model_id,),
                ).fetchall()
            ]
            rows = conn.execute(
                """
                SELECT s.original_sha, s.final_bbox, s.frame_w, s.frame_h,
                       s.final_class_name
                FROM webapp.dataset_samples AS s
                WHERE s.model_id = %s AND s.verdict IN ('correct', 'corrected')
                  AND s.original_sha IS NOT NULL AND s.final_bbox IS NOT NULL
                """,
                (model_id,),
            ).fetchall()
        if yaml_names == expected_names:
            _ok("data.yaml sınıf sözlüğü detection_types ile birebir")
        else:
            _fail(
                "data.yaml sınıf sözlüğü DB ile farklı: "
                f"zip={yaml_names!r} db={expected_names!r}"
            )
        for sha, bbox, frame_w, frame_h, class_name in rows:
            label_name = f"labels/{sha}.txt"
            if label_name not in names:
                eksik += 1
                continue
            beklenen = (
                ((bbox[0] + bbox[2]) / 2) / frame_w,
                ((bbox[1] + bbox[3]) / 2) / frame_h,
                (bbox[2] - bbox[0]) / frame_w,
                (bbox[3] - bbox[1]) / frame_h,
            )
            satirlar = archive.read(label_name).decode().splitlines()
            for satir in satirlar:
                parcalar = satir.split()
                if yaml_names[int(parcalar[0])] != class_name:
                    continue
                bulunan = tuple(float(p) for p in parcalar[1:])
                if all(abs(a - b) <= 1e-4 for a, b in zip(beklenen, bulunan)):
                    dogru += 1
                    break
        if dogru >= 1 and eksik == 0:
            _ok(f"normalize etiketler final_bbox/frame ile eşleşti "
                f"({dogru} örnek, tolerans 1e-4)")
        else:
            _fail(f"etiket eşleşmesi: doğru={dogru} eksik={eksik}")
    else:
        _warn("ROADVISION_WEB_DSN yok; etiket-DB eşleşmesi atlandı")

    # wrong kapsamı: ayrı seçilebilir, boş etiketli background üretir.
    wrong_model = seed_wrong_model or next(
        (m["model_id"] for m in models if m["totals"].get("wrong", 0) > 0),
        None,
    )
    if wrong_model:
        status, body = client.request(
            "POST", "/api/datasets/export",
            {"model_id": wrong_model, "verdict": "wrong"},
        )
        if status == 202:
            wrong_job = body["job"]["job_id"]
            _mark_export_job(web_dsn, wrong_job)
            son = time.time() + args.timeout
            while time.time() < son:
                status, body = client.request(
                    "GET", f"/api/datasets/exports/{wrong_job}"
                )
                if body.get("job", {}).get("status") in ("done", "failed"):
                    break
                time.sleep(1.5)
            status, headers, payload = client.raw(
                "GET", f"/api/datasets/exports/{wrong_job}/download"
            )
            if status == 200:
                warc = zipfile.ZipFile(io.BytesIO(payload))
                etiketler = [
                    name for name in warc.namelist()
                    if name.startswith("labels/")
                ]
                if etiketler and all(
                    warc.read(name) == b"" for name in etiketler
                ):
                    _ok(f"wrong export'u boş etiketli background üretti "
                        f"({len(etiketler)} kare)")
                else:
                    _fail("wrong export'unda dolu etiket bulundu")
            else:
                _fail(f"wrong export indirme {status} döndü")
        else:
            _fail(f"wrong export başlatma {status} döndü: {body}")
    else:
        _warn("wrong örneği yok; hard-negative kontrolü atlandı")

    status, body = client.request("GET", "/api/stats/overview")
    if status == 200 and all(
        key in body for key in ("detections", "reviews", "models")
    ) and "coverage" in body["reviews"]:
        _ok(f"stats/overview: {body['detections']['total']} tespit, "
            f"kapsama %{round(body['reviews']['coverage'] * 100)}")
    else:
        _fail(f"stats/overview {status} döndü: {body}")

    print("SONUÇ: " + ("FAIL" if _FAILED else "PASS"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
