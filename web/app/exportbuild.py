"""YOLO export üreticisi (WEB_PLANI.md §6/§9 Faz 5) — saf modül.

Zip düzeni (Ultralytics YOLO klasör sözleşmesi):

    data.yaml            # nc + names (modelin sınıf sözlüğü)
    manifest.json        # iş özeti: sayımlar, atlananlar, üretim kuralları
    images/<sha>.jpg     # dataset_media'daki copy-on-verify baytları
    labels/<sha>.txt     # her satır: "idx cx cy w h" (0–1 normalize)

Kurallar:

* Etiketler ``final_*`` değerlerden üretilir; koordinat ``final_bbox``ın
  ``frame_w/frame_h``e bölümüyle normalize edilir (§4.6 — MEDIA_MAX_EDGE
  küçültmesinden bağımsız).
* Aynı kare (aynı ``original_sha``) birden çok örnek taşıyabilir; kare
  başına tek görüntü + tüm kutuları içeren tek etiket dosyası yazılır.
* ``wrong`` kapsamı hard-negative/background üretir: görüntü + **boş**
  etiket dosyası (YOLO'da boş .txt = arka plan görüntüsü sözleşmesi).
* Sınıf indeksi deterministiktir: ``class_index`` (NULLS LAST), ardından
  ``class_name`` sırasıyla 0..n-1; data.yaml'daki ``names`` ile birebir.
* Görüntüsüz örnek (retention karar öncesi silmişse) ve pozitif kapsamda
  kutusuz örnek atlanır; sayımları manifest'e yazılır.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

__all__ = [
    "ExportSample",
    "build_class_map",
    "yolo_line",
    "build_yolo_entries",
    "assemble_zip",
]


@dataclass(frozen=True, slots=True)
class ExportSample:
    """dataset_samples satırının export için gereken kesiti."""

    object_id: int
    verdict: str
    final_type_id: int
    final_bbox: tuple[float, ...] | None
    frame_w: int | None
    frame_h: int | None
    original_sha: str | None


def build_class_map(
    types: list[tuple[int, int | None, str]],
) -> tuple[list[str], dict[int, int]]:
    """``(type_id, class_index, class_name)`` listesinden YOLO haritası üretir.

    Dönüş: (``names`` sırası, ``type_id → yolo_index``). Sıra deterministik:
    ``class_index`` NULLS LAST, sonra ``class_name``, sonra ``type_id``.
    Çalışma zamanında eklenen katalog dışı sınıflar (class_index NULL)
    sözlüğün sonuna dizilir.
    """

    ordered = sorted(
        types,
        key=lambda item: (
            item[1] is None,
            item[1] if item[1] is not None else 0,
            item[2],
            item[0],
        ),
    )
    names = [class_name for _tid, _idx, class_name in ordered]
    mapping = {tid: index for index, (tid, _idx, _name) in enumerate(ordered)}
    return names, mapping


def yolo_line(
    bbox: tuple[float, ...],
    frame_w: int,
    frame_h: int,
    class_index: int,
) -> str:
    """xyxy kare-piksel kutusunu YOLO satırına çevirir (0–1'e kenetli)."""

    if frame_w <= 0 or frame_h <= 0:
        raise ValueError("kare boyutu pozitif olmalı")
    x1, y1, x2, y2 = bbox
    cx = ((x1 + x2) / 2.0) / frame_w
    cy = ((y1 + y2) / 2.0) / frame_h
    width = (x2 - x1) / frame_w
    height = (y2 - y1) / frame_h
    clamp = lambda value: min(max(value, 0.0), 1.0)  # noqa: E731
    return (
        f"{class_index} {clamp(cx):.6f} {clamp(cy):.6f} "
        f"{clamp(width):.6f} {clamp(height):.6f}"
    )


def build_yolo_entries(
    samples: list[ExportSample],
    type_to_index: dict[int, int],
    verdict_scope: str,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Örnekleri kare başına etiket satırlarına indirger.

    Dönüş: (``sha → satırlar`` — wrong kapsamında satırlar hep boş liste,
    dosya yine yazılır; sayaçlar: sample/image/skipped_no_image/
    skipped_no_bbox/skipped_unknown_type).
    """

    if verdict_scope not in ("positive", "wrong"):
        raise ValueError(f"geçersiz kapsam: {verdict_scope!r}")
    labels: dict[str, list[str]] = {}
    counters = {
        "sample_count": 0,
        "skipped_no_image": 0,
        "skipped_no_bbox": 0,
        "skipped_unknown_type": 0,
    }
    for sample in samples:
        if sample.original_sha is None:
            counters["skipped_no_image"] += 1
            continue
        if verdict_scope == "positive":
            if (
                sample.final_bbox is None
                or sample.frame_w is None
                or sample.frame_h is None
            ):
                counters["skipped_no_bbox"] += 1
                continue
            class_index = type_to_index.get(sample.final_type_id)
            if class_index is None:
                counters["skipped_unknown_type"] += 1
                continue
            labels.setdefault(sample.original_sha, []).append(
                yolo_line(
                    sample.final_bbox,
                    sample.frame_w,
                    sample.frame_h,
                    class_index,
                )
            )
        else:  # wrong: background görüntüsü, boş etiket dosyası
            labels.setdefault(sample.original_sha, [])
        counters["sample_count"] += 1
    counters["image_count"] = len(labels)
    return labels, counters


def assemble_zip(
    *,
    model_id: str,
    verdict_scope: str,
    names: list[str],
    labels: dict[str, list[str]],
    images: dict[str, bytes],
    counters: dict[str, int],
) -> bytes:
    """Deterministik zip üretir; JPEG'ler sıkıştırılmadan (STORED) girer."""

    yaml_lines = [
        f"# RoadVision dataset export — model: {model_id}, kapsam: {verdict_scope}",
        "path: .",
        "train: images",
        "val: images",
        f"nc: {len(names)}",
        "names:",
    ]
    yaml_lines += [f"  {index}: {name}" for index, name in enumerate(names)]
    manifest = dict(counters)
    manifest.update(
        {
            "model_id": model_id,
            "verdict_scope": verdict_scope,
            "names": names,
            "label_rule": "final_bbox / frame (§4.6); wrong = boş etiket",
        }
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "data.yaml", "\n".join(yaml_lines) + "\n",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for sha in sorted(labels):
            archive.writestr(
                f"labels/{sha}.txt",
                "\n".join(labels[sha]) + ("\n" if labels[sha] else ""),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            archive.writestr(
                f"images/{sha}.jpg",
                images[sha],
                compress_type=zipfile.ZIP_STORED,
            )
    return buffer.getvalue()
