"""Doğrulama kararı kuralları (WEB_PLANI.md §4.3/§6, Faz 4) — saf modül.

Karar doğrulamasının tamamı burada, DB'siz test edilebilir fonksiyonlarda
yaşar; route katmanı yalnız bağlamı (tespit satırı + modelin sınıf
sözlüğü) toplar ve hataları HTTP koduna çevirir. Kurallar:

* ``corrected`` yalnız detect görevli modellerde geçerlidir; semantic
  (``roadline``) tespitleri sadece ``correct``/``wrong`` alabilir.
* ``corrected`` en az bir düzeltme taşımalıdır ve düzeltilmiş sınıf
  **tespitin geldiği modelin** sözlüğünden seçilmelidir.
* ``corrected_bbox`` kare koordinatındadır ve kare sınırları içinde
  doğrulanır (bkz. ``geometry``); kare boyutu bilinmiyorsa (görüntüsü
  retention ile silinmiş tespit) kutu düzeltmesi kabul edilmez.
* ``correct``/``wrong`` kararları düzeltme taşıyamaz.

``build_final`` nihai eğitim etiketini üretir: correct/wrong özgün
değerlerin dondurulmuş kopyası, corrected'da düzeltilen alan yenisiyle,
düzeltilmeyen alan özgünüyle doldurulur (§4.5 ``final_*`` kolonları).
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import validate_bbox

VERDICTS = ("correct", "corrected", "wrong")


class ReviewRuleError(Exception):
    """Kodlu kural ihlali; route 400'e çevirir."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DetectionContext:
    """Karar doğrulaması için gereken tespit bağlamı."""

    object_id: int
    model_id: str
    model_task: str  # roadvision_model_catalog.task; katalog dışıysa 'unknown'
    type_id: int
    class_name: str
    bbox: tuple[float, ...] | None
    frame_w: int | None  # §4.6: media_captures'ta saklanmaz; orijinal
    frame_h: int | None  # blobun boyutundan doldurulur ("yoksa görüntüden")


@dataclass(frozen=True, slots=True)
class NormalizedReview:
    """Doğrulanmış ve nihai etiketleri çözülmüş karar."""

    verdict: str
    corrected_bbox: tuple[float, float, float, float] | None
    corrected_type_id: int | None
    final_type_id: int
    final_class_name: str
    final_bbox: tuple[float, ...] | None


def normalize_review(
    ctx: DetectionContext,
    *,
    verdict: str,
    corrected_bbox: list[float] | None,
    corrected_class: str | None,
    model_types: dict[str, int],
) -> NormalizedReview:
    """Kararı kurallara göre doğrular ve nihai etiketiyle döndürür.

    ``model_types``: tespitin geldiği modelin ``class_name → type_id``
    sözlüğü. Çapraz-model sınıf adı doğal olarak bu sözlükte bulunmaz ve
    ``unknown_class`` ile reddedilir.
    """

    if verdict not in VERDICTS:
        raise ReviewRuleError("invalid_verdict", f"Geçersiz karar: {verdict!r}")

    has_correction = corrected_bbox is not None or corrected_class is not None

    if verdict != "corrected":
        if has_correction:
            raise ReviewRuleError(
                "unexpected_correction",
                "Düzeltme alanları yalnız 'corrected' kararıyla gönderilir.",
            )
        return NormalizedReview(
            verdict=verdict,
            corrected_bbox=None,
            corrected_type_id=None,
            final_type_id=ctx.type_id,
            final_class_name=ctx.class_name,
            final_bbox=tuple(ctx.bbox) if ctx.bbox is not None else None,
        )

    # verdict == 'corrected'
    if ctx.model_task != "detect":
        raise ReviewRuleError(
            "semantic_no_correction",
            "Semantic model tespitleri kutu/sınıf düzeltmesi alamaz; "
            "yalnız 'correct' veya 'wrong' işaretlenebilir.",
        )
    if not has_correction:
        raise ReviewRuleError(
            "correction_required",
            "'corrected' kararı en az bir düzeltme (kutu veya sınıf) "
            "taşımalıdır.",
        )

    corrected_type_id: int | None = None
    final_class_name = ctx.class_name
    if corrected_class is not None:
        if corrected_class not in model_types:
            raise ReviewRuleError(
                "unknown_class",
                f"'{corrected_class}' sınıfı {ctx.model_id} modelinin "
                "sözlüğünde yok; çapraz-model düzeltme yapılamaz.",
            )
        corrected_type_id = model_types[corrected_class]
        final_class_name = corrected_class
        if corrected_type_id == ctx.type_id and corrected_bbox is None:
            raise ReviewRuleError(
                "no_change",
                "Düzeltilmiş sınıf özgün sınıfla aynı ve kutu değişmemiş; "
                "karar 'correct' olmalı.",
            )

    normalized_bbox: tuple[float, float, float, float] | None = None
    if corrected_bbox is not None:
        if ctx.frame_w is None or ctx.frame_h is None:
            raise ReviewRuleError(
                "frame_unavailable",
                "Kare boyutu bilinmiyor (görüntü saklama süresi dolmuş "
                "olabilir); kutu düzeltmesi doğrulanamaz.",
            )
        try:
            normalized_bbox = validate_bbox(
                corrected_bbox, ctx.frame_w, ctx.frame_h
            )
        except ValueError as exc:
            raise ReviewRuleError("invalid_bbox", str(exc)) from exc

    final_type_id = (
        corrected_type_id if corrected_type_id is not None else ctx.type_id
    )
    final_bbox: tuple[float, ...] | None
    if normalized_bbox is not None:
        final_bbox = normalized_bbox
    else:
        final_bbox = tuple(ctx.bbox) if ctx.bbox is not None else None

    return NormalizedReview(
        verdict="corrected",
        corrected_bbox=normalized_bbox,
        corrected_type_id=corrected_type_id,
        final_type_id=final_type_id,
        final_class_name=final_class_name,
        final_bbox=final_bbox,
    )
