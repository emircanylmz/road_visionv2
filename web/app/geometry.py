"""Kutu geometrisi ve ölçek yardımcıları (WEB_PLANI.md §4.6, Faz 4).

Sözleşme: ``bbox`` her zaman **işlenen kare** pikselinde ``[x1,y1,x2,y2]``
biçimindedir. Arayüz görüntüyü hangi boyutta gösterirse göstersin,
``scale_bbox`` ile görüntü koordinatına iner ve düzeltilmiş kutuyu aynı
oranla kare koordinatına geri çevirir; gidiş-dönüş sapması ±1 piksel
içinde kalır (Faz 4 kabul maddesi, testte sabitlenir).
"""

from __future__ import annotations

__all__ = ["validate_bbox", "scale_bbox", "clamp_bbox"]

# Kayan nokta gidiş-dönüşünde sınır değerlerine tanınan tolerans (piksel).
_EPSILON = 1.0


def validate_bbox(
    bbox: list[float] | tuple[float, ...],
    frame_w: int,
    frame_h: int,
) -> tuple[float, float, float, float]:
    """Kutuyu doğrular ve normalize (float dörtlüsü) döndürür.

    Kurallar (§6 ``POST /reviews``): dört sayı, ``x1 < x2``, ``y1 < y2`` ve
    kare sınırları içinde (±1 px tolerans; ölçek gidiş-dönüşü sınır
    pikselinde taşma üretebilir, taşma ``clamp_bbox`` ile kareye oturtulur).
    Hatalar ``ValueError`` üretir; route bunu 400 ``invalid_bbox``a çevirir.
    """

    if len(bbox) != 4:
        raise ValueError("kutu dört sayı olmalı: [x1, y1, x2, y2]")
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError("kutu değerleri sayı olmalı") from exc
    for value in (x1, y1, x2, y2):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("kutu değerleri sonlu olmalı")
    if not (x1 < x2 and y1 < y2):
        raise ValueError("kutu ters: x1 < x2 ve y1 < y2 olmalı")
    if frame_w <= 0 or frame_h <= 0:
        raise ValueError("kare boyutu pozitif olmalı")
    if (
        x1 < -_EPSILON
        or y1 < -_EPSILON
        or x2 > frame_w + _EPSILON
        or y2 > frame_h + _EPSILON
    ):
        raise ValueError(
            f"kutu kare dışında: kare {frame_w}×{frame_h}, "
            f"kutu [{x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}]"
        )
    return clamp_bbox((x1, y1, x2, y2), frame_w, frame_h)


def clamp_bbox(
    bbox: tuple[float, float, float, float],
    frame_w: int,
    frame_h: int,
) -> tuple[float, float, float, float]:
    """Kutuyu kare sınırlarına oturtur (tolerans taşmaları için)."""

    x1, y1, x2, y2 = bbox
    return (
        min(max(x1, 0.0), float(frame_w)),
        min(max(y1, 0.0), float(frame_h)),
        min(max(x2, 0.0), float(frame_w)),
        min(max(y2, 0.0), float(frame_h)),
    )


def scale_bbox(
    bbox: tuple[float, float, float, float] | list[float],
    from_wh: tuple[int, int],
    to_wh: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Kutuyu bir koordinat uzayından diğerine oranla taşır.

    ``scale_bbox(scale_bbox(b, A, B), B, A)`` gidiş-dönüşü, her koordinatta
    ±1 piksel içinde kalır (test_geometry sabitler).
    """

    from_w, from_h = from_wh
    to_w, to_h = to_wh
    if min(from_w, from_h, to_w, to_h) <= 0:
        raise ValueError("ölçek boyutları pozitif olmalı")
    sx = to_w / from_w
    sy = to_h / from_h
    x1, y1, x2, y2 = (float(value) for value in bbox)
    return (x1 * sx, y1 * sy, x2 * sx, y2 * sy)
