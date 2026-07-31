// Arşiv sayfası: masaüstü Tespit Arşivi'nin web karşılığı (WEB_PLANI.md §7).
// Kartlar işaretli (bbox çizili) kareyi gösterir; ayrıntı çekmecesi
// orijinal/işaretli geçişi sunar. Doğrulama etiketi §4.3 kuralını izler:
// karar satırı olmayan her tespit "doğrulanmadı"dır — yeni tespitler
// kendiliğinden bu kuyruğa düşer. Karar verme uçları Faz 4'te gelir.

import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, ApiError, formatTs } from "../api";
import type {
  ArchiveModelNode,
  CaptureDetail,
  DetectionPage,
  DetectionRow,
  ReviewStatus,
} from "../types";

const REVIEW_LABEL: Record<ReviewStatus, string> = {
  unreviewed: "Doğrulanmadı",
  correct: "Doğru",
  corrected: "Düzeltildi",
  wrong: "Yanlış",
};

const REVIEW_STYLE: Record<ReviewStatus, string> = {
  unreviewed: "text-muted border-chipline",
  correct: "text-ok-text border-ok-border bg-ok-bg",
  corrected: "text-warning border-warning/40 bg-warning-bg",
  wrong: "text-danger border-danger/40 bg-danger-bg",
};

interface Filters {
  modelId: string;
  typeIds: number[];
  reviewStatuses: ReviewStatus[];
  runId: string;
  minConfidence: string;
  tsFrom: string;
  tsTo: string;
  onlyWithImage: boolean;
}

const EMPTY: Filters = {
  modelId: "",
  typeIds: [],
  reviewStatuses: [],
  runId: "",
  minConfidence: "",
  tsFrom: "",
  tsTo: "",
  onlyWithImage: false,
};

function toQuery(filters: Filters, cursor: string | null): string {
  const params = new URLSearchParams();
  if (filters.modelId) params.append("model_id", filters.modelId);
  for (const typeId of filters.typeIds)
    params.append("type_id", String(typeId));
  for (const status of filters.reviewStatuses)
    params.append("review_status", status);
  if (filters.runId) params.append("run_id", filters.runId);
  if (filters.minConfidence)
    params.append("min_confidence", filters.minConfidence);
  if (filters.tsFrom)
    params.append("ts_from", new Date(filters.tsFrom).toISOString());
  if (filters.tsTo)
    params.append("ts_to", new Date(filters.tsTo).toISOString());
  if (filters.onlyWithImage) params.append("only_with_image", "true");
  params.append("limit", "60");
  if (cursor) params.append("cursor", cursor);
  return params.toString();
}

export function ArchivePage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [selected, setSelected] = useState<DetectionRow | null>(null);

  const tree = useQuery({
    queryKey: ["archive-types"],
    queryFn: () => api<{ models: ArchiveModelNode[] }>("/api/archive/types"),
    staleTime: 60_000,
  });

  const detections = useInfiniteQuery({
    queryKey: ["archive", applied],
    queryFn: ({ pageParam }) =>
      api<DetectionPage>("/api/archive/detections?" + toQuery(applied, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const records = useMemo(
    () =>
      detections.data
        ? detections.data.pages.flatMap((page) => page.records)
        : [],
    [detections.data],
  );

  const selectedModel = tree.data?.models.find(
    (model) => model.model_id === draft.modelId,
  );

  function toggleType(typeId: number) {
    setDraft((current) => ({
      ...current,
      typeIds: current.typeIds.includes(typeId)
        ? current.typeIds.filter((item) => item !== typeId)
        : [...current.typeIds, typeId],
    }));
  }

  function toggleReview(status: ReviewStatus) {
    setDraft((current) => ({
      ...current,
      reviewStatuses: current.reviewStatuses.includes(status)
        ? current.reviewStatuses.filter((item) => item !== status)
        : [...current.reviewStatuses, status],
    }));
  }

  const archiveError =
    tree.error ?? (records.length === 0 ? detections.error : null);
  const archiveUnavailable =
    archiveError instanceof ApiError &&
    archiveError.code === "archive_unavailable";

  return (
    <div className="flex h-full min-h-0 min-w-0 gap-4">
      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="mb-4 max-h-[40%] shrink-0 overflow-y-auto overscroll-contain rounded-xl border border-border-soft bg-panel p-3 [scrollbar-gutter:stable]">
          <div className="flex flex-wrap items-end gap-3">
            <label>
              <div className="eyebrow mb-1">Model</div>
              <select
                className="field w-56"
                value={draft.modelId}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    modelId: event.target.value,
                    typeIds: [],
                  })
                }
              >
                <option value="">Tümü</option>
                {(tree.data?.models ?? []).map((model) => (
                  <option key={model.model_id} value={model.model_id}>
                    {model.display_name}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <div className="eyebrow mb-1">Doğrulama durumu</div>
              <div className="flex gap-1.5">
                {(Object.keys(REVIEW_LABEL) as ReviewStatus[]).map(
                  (status) => (
                    <button
                      key={status}
                      type="button"
                      onClick={() => toggleReview(status)}
                      className={
                        "chip " +
                        REVIEW_STYLE[status] +
                        (draft.reviewStatuses.includes(status)
                          ? " ring-1 ring-accent/60"
                          : " opacity-70 hover:opacity-100")
                      }
                    >
                      {REVIEW_LABEL[status]}
                    </button>
                  ),
                )}
              </div>
            </div>

            <label>
              <div className="eyebrow mb-1">En az güven</div>
              <input
                className="field w-24 font-mono"
                type="number"
                min={0}
                max={1}
                step={0.05}
                placeholder="0.00"
                value={draft.minConfidence}
                onChange={(event) =>
                  setDraft({ ...draft, minConfidence: event.target.value })
                }
              />
            </label>

            <label>
              <div className="eyebrow mb-1">Run</div>
              <input
                className="field w-24 font-mono"
                type="number"
                min={1}
                placeholder="tümü"
                value={draft.runId}
                onChange={(event) =>
                  setDraft({ ...draft, runId: event.target.value })
                }
              />
            </label>

            <label>
              <div className="eyebrow mb-1">Başlangıç</div>
              <input
                className="field w-52"
                type="datetime-local"
                value={draft.tsFrom}
                onChange={(event) =>
                  setDraft({ ...draft, tsFrom: event.target.value })
                }
              />
            </label>
            <label>
              <div className="eyebrow mb-1">Bitiş</div>
              <input
                className="field w-52"
                type="datetime-local"
                value={draft.tsTo}
                onChange={(event) =>
                  setDraft({ ...draft, tsTo: event.target.value })
                }
              />
            </label>

            <label className="flex items-center gap-2 pb-2 text-sm text-soft">
              <input
                type="checkbox"
                checked={draft.onlyWithImage}
                onChange={(event) =>
                  setDraft({ ...draft, onlyWithImage: event.target.checked })
                }
              />
              Yalnız görüntülü
            </label>

            <div className="ml-auto flex gap-2">
              <button
                className="btn-ghost"
                type="button"
                onClick={() => {
                  setDraft(EMPTY);
                  setApplied(EMPTY);
                  setSelected(null);
                }}
              >
                Sıfırla
              </button>
              <button
                className="btn-accent"
                type="button"
                onClick={() => {
                  setApplied(draft);
                  setSelected(null);
                }}
              >
                Filtreyi uygula
              </button>
            </div>
          </div>

          {selectedModel && (
            <div className="mt-3 border-t border-hairline pt-3">
              <div className="eyebrow mb-1.5">
                {selectedModel.display_name} türleri
              </div>
              <div className="flex flex-wrap gap-1.5">
                {selectedModel.types.map((type) => (
                  <button
                    key={type.type_id}
                    type="button"
                    onClick={() => toggleType(type.type_id)}
                    className={
                      "chip " +
                      (draft.typeIds.includes(type.type_id)
                        ? "bg-hover text-text ring-1 ring-accent/60"
                        : "opacity-70 hover:opacity-100")
                    }
                    title={
                      type.is_catalogued ? undefined : "Katalog dışı tür"
                    }
                  >
                    {type.display_name}
                    <span className="ml-1.5 font-mono text-faint">
                      {type.counts.total}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]"
          aria-label="Arşiv tespitleri"
          tabIndex={0}
        >
          {archiveError ? (
            <div className="rounded-xl border border-warning/40 bg-warning-bg p-5 text-sm text-warning">
              {archiveUnavailable
                ? "Tespit arşivine ulaşılamadı. Masaüstü uygulama PostgreSQL şema sürümü 3 migration'ını en az bir kez çalıştırmış olmalı."
                : "Tespit arşivi alınamadı. Bağlantıyı denetleyip yeniden deneyin."}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3">
                {records.map((record) => (
                  <DetectionCard
                    key={record.id}
                    record={record}
                    selected={selected?.id === record.id}
                    onSelect={() =>
                      setSelected(selected?.id === record.id ? null : record)
                    }
                  />
                ))}
              </div>

              {detections.isPending && (
                <div className="p-6 text-center text-sm text-muted">
                  Tespitler yükleniyor…
                </div>
              )}
              {!detections.isPending && records.length === 0 && (
                <div className="rounded-xl border border-border-soft p-6 text-center text-sm text-muted">
                  Filtreyle eşleşen tespit yok.
                </div>
              )}

              <div className="mt-3 flex items-center justify-between text-sm text-muted">
                <span className="font-mono">
                  {records.length} tespit yüklendi
                </span>
                <div className="flex gap-2">
                  <button
                    className="btn-ghost"
                    onClick={() => detections.refetch()}
                    disabled={detections.isRefetching}
                  >
                    Yenile
                  </button>
                  {detections.hasNextPage && (
                    <button
                      className="btn-ghost"
                      onClick={() => detections.fetchNextPage()}
                      disabled={detections.isFetchingNextPage}
                    >
                      {detections.isFetchingNextPage
                        ? "Yükleniyor…"
                        : "Daha eski tespitleri yükle"}
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </section>

      {selected && (
        <DetectionDrawer
          record={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function DetectionCard({
  record,
  selected,
  onSelect,
}: {
  record: DetectionRow;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={
        "overflow-hidden rounded-xl border text-left " +
        (selected
          ? "border-accent bg-chip"
          : "border-border-soft bg-card hover:bg-hover")
      }
    >
      {record.annotated_media_id ? (
        <img
          src={"/api/media/" + record.annotated_media_id}
          alt={record.type_display_name}
          loading="lazy"
          className="aspect-video w-full bg-deep object-contain"
        />
      ) : (
        <div className="grid aspect-video w-full place-items-center bg-deep text-xs text-faint">
          Görüntü saklama süresi doldu
        </div>
      )}
      <div className="p-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-semibold">
            {record.type_display_name}
          </span>
          <span className={"chip shrink-0 " + REVIEW_STYLE[record.review_status]}>
            {REVIEW_LABEL[record.review_status]}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-xs text-muted">
          <span className="truncate">{record.model_display_name}</span>
          {record.confidence != null && (
            <span className="font-mono">
              %{Math.round(record.confidence * 100)}
            </span>
          )}
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-faint">
          {formatTs(record.ts)}
        </div>
      </div>
    </button>
  );
}

function DetectionDrawer({
  record,
  onClose,
}: {
  record: DetectionRow;
  onClose: () => void;
}) {
  const [view, setView] = useState<"annotated" | "original">("annotated");
  const [expanded, setExpanded] = useState(false);
  const capture = useQuery({
    queryKey: ["capture", record.capture_id],
    queryFn: () => api<CaptureDetail>("/api/captures/" + record.capture_id),
    enabled: record.capture_id !== null,
  });
  const info = capture.data?.capture;
  const mediaId =
    view === "annotated"
      ? record.annotated_media_id
      : record.original_media_id;
  const viewLabel = view === "annotated" ? "İşaretli kare" : "Orijinal kare";

  useEffect(() => setExpanded(false), [record.id]);

  useEffect(() => {
    if (!expanded) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setExpanded(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

  return (
    <aside className="flex h-full min-h-0 w-[30rem] shrink-0 flex-col rounded-xl border border-border-soft bg-panel">
      <div className="flex shrink-0 items-center justify-between border-b border-hairline px-4 py-3">
        <div>
          <div className="eyebrow">Tespit ayrıntısı</div>
          <div className="font-mono text-xs text-muted">#{record.id}</div>
        </div>
        <button className="btn-ghost" onClick={onClose}>
          Kapat
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 text-sm [scrollbar-gutter:stable]">
        {record.capture_id ? (
          <>
            <div className="mb-2 flex gap-1.5">
              <button
                type="button"
                className={
                  "chip " +
                  (view === "annotated"
                    ? "bg-hover text-text ring-1 ring-accent/60"
                    : "opacity-70 hover:opacity-100")
                }
                onClick={() => setView("annotated")}
              >
                İşaretli kare
              </button>
              <button
                type="button"
                className={
                  "chip " +
                  (view === "original"
                    ? "bg-hover text-text ring-1 ring-accent/60"
                    : "opacity-70 hover:opacity-100")
                }
                onClick={() => setView("original")}
              >
                Orijinal kare
              </button>
            </div>
            {mediaId ? (
              <button
                type="button"
                className="group relative mb-3 block w-full overflow-hidden rounded-lg border border-hairline bg-deep"
                onClick={() => setExpanded(true)}
                aria-label={`${viewLabel}: tam ekran görüntüle`}
              >
                <img
                  src={"/api/media/" + mediaId}
                  alt={record.type_display_name}
                  className="max-h-[45vh] w-full object-contain"
                />
                <span className="absolute bottom-2 right-2 rounded-md border border-chipline bg-panel/90 px-2.5 py-1.5 text-xs font-semibold text-soft shadow-lg backdrop-blur-sm group-hover:text-text">
                  ⛶ Tam ekran görüntüle
                </span>
              </button>
            ) : (
              <div className="mb-3 rounded-lg border border-hairline bg-deep p-6 text-center text-xs text-faint">
                Görüntü saklama süresi doldu
              </div>
            )}
          </>
        ) : (
          <div className="mb-3 rounded-lg border border-hairline bg-deep p-6 text-center text-xs text-faint">
            Bu tespit için kare kaydı yok
          </div>
        )}

        <div className="space-y-1 text-soft">
          <div>
            <span className="text-faint">Tür: </span>
            {record.type_display_name}{" "}
            <span className="font-mono text-xs text-faint">
              ({record.class_name}
              {record.is_catalogued ? "" : " · katalog dışı"})
            </span>
          </div>
          <div>
            <span className="text-faint">Model: </span>
            {record.model_display_name}
          </div>
          <div>
            <span className="text-faint">Doğrulama: </span>
            <span className={"chip " + REVIEW_STYLE[record.review_status]}>
              {REVIEW_LABEL[record.review_status]}
            </span>
            {record.reviewed_at && (
              <span className="ml-2 font-mono text-xs text-faint">
                {formatTs(record.reviewed_at)}
              </span>
            )}
          </div>
          <div>
            <span className="text-faint">Güven / alan oranı: </span>
            <span className="font-mono text-xs">
              {record.confidence != null
                ? record.confidence.toFixed(3)
                : "—"}{" "}
              /{" "}
              {record.area_ratio != null
                ? record.area_ratio.toFixed(4)
                : "—"}
            </span>
          </div>
          <div>
            <span className="text-faint">Zaman / run: </span>
            <span className="font-mono text-xs">
              {formatTs(record.ts)} · {record.run_id ?? "—"}
            </span>
          </div>
          {record.bbox && (
            <div>
              <span className="text-faint">Kutu (xyxy, kare pikseli): </span>
              <span className="font-mono text-xs">
                [{record.bbox.map((value) => value.toFixed(1)).join(", ")}]
              </span>
            </div>
          )}
          {info && (
            <div className="mt-2 border-t border-hairline pt-2 text-xs text-muted">
              <div>
                Kaynak: {info.source_name ?? "—"} ({info.source_kind ?? "—"})
                {info.is_reprocess && " · yeniden işleme"}
              </div>
              <div className="font-mono">
                Kare: {info.original.width}×{info.original.height} ·{" "}
                {(info.original.byte_size / 1024).toFixed(0)} KB
              </div>
              {info.models.length > 0 && (
                <div>
                  Karedeki modeller:{" "}
                  {info.models
                    .map(
                      (model) => model.model_id + "×" + model.object_count,
                    )
                    .join(", ")}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {expanded && mediaId && info && (
        <ArchiveLightbox
          record={record}
          view={view}
          onViewChange={setView}
          originalMediaId={record.original_media_id}
          annotatedMediaId={record.annotated_media_id}
          originalFrame={info.original}
          annotatedFrame={info.annotated}
          onClose={() => setExpanded(false)}
        />
      )}
    </aside>
  );
}

interface ArchiveViewport {
  zoom: number;
  x: number;
  y: number;
}

const ARCHIVE_MIN_ZOOM = 1;
const ARCHIVE_MAX_ZOOM = 4;
const ARCHIVE_ZOOM_STEP = 0.5;

function ArchiveLightbox({
  record,
  view,
  onViewChange,
  originalMediaId,
  annotatedMediaId,
  originalFrame,
  annotatedFrame,
  onClose,
}: {
  record: DetectionRow;
  view: "annotated" | "original";
  onViewChange: (view: "annotated" | "original") => void;
  originalMediaId: number | null;
  annotatedMediaId: number | null;
  originalFrame: CaptureDetail["capture"]["original"];
  annotatedFrame: CaptureDetail["capture"]["annotated"];
  onClose: () => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{
    startClientX: number;
    startClientY: number;
    viewport: ArchiveViewport;
  } | null>(null);
  const [viewport, setViewport] = useState<ArchiveViewport>({
    zoom: ARCHIVE_MIN_ZOOM,
    x: 0,
    y: 0,
  });
  const mediaId =
    view === "annotated" ? annotatedMediaId : originalMediaId;
  const frame = view === "annotated" ? annotatedFrame : originalFrame;
  const viewLabel = view === "annotated" ? "İşaretli kare" : "Orijinal kare";
  const viewW = frame.width / viewport.zoom;
  const viewH = frame.height / viewport.zoom;

  useEffect(() => {
    setViewport({ zoom: ARCHIVE_MIN_ZOOM, x: 0, y: 0 });
    dragRef.current = null;
  }, [view, mediaId, frame.width, frame.height]);

  function clamp(value: number, max: number) {
    return Math.min(Math.max(value, 0), Math.max(0, max));
  }

  function changeZoom(delta: number) {
    setViewport((current) => {
      const zoom = Math.min(
        ARCHIVE_MAX_ZOOM,
        Math.max(ARCHIVE_MIN_ZOOM, current.zoom + delta),
      );
      if (zoom === current.zoom) return current;

      const currentW = frame.width / current.zoom;
      const currentH = frame.height / current.zoom;
      const nextW = frame.width / zoom;
      const nextH = frame.height / zoom;
      const centerX = current.x + currentW / 2;
      const centerY = current.y + currentH / 2;
      return {
        zoom,
        x: clamp(centerX - nextW / 2, frame.width - nextW),
        y: clamp(centerY - nextH / 2, frame.height - nextH),
      };
    });
  }

  function resetViewport() {
    setViewport({ zoom: ARCHIVE_MIN_ZOOM, x: 0, y: 0 });
    dragRef.current = null;
  }

  function switchView(nextView: "annotated" | "original") {
    if (nextView === view) return;
    resetViewport();
    onViewChange(nextView);
  }

  function onPointerMove(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || !svgRef.current) return;

    const rect = svgRef.current.getBoundingClientRect();
    const dragViewW = frame.width / drag.viewport.zoom;
    const dragViewH = frame.height / drag.viewport.zoom;
    const scale = Math.min(
      rect.width / dragViewW,
      rect.height / dragViewH,
    );
    setViewport({
      ...drag.viewport,
      x: clamp(
        drag.viewport.x -
          (event.clientX - drag.startClientX) / scale,
        frame.width - dragViewW,
      ),
      y: clamp(
        drag.viewport.y -
          (event.clientY - drag.startClientY) / scale,
        frame.height - dragViewH,
      ),
    });
  }

  function endDrag(event: ReactPointerEvent<SVGSVGElement>) {
    dragRef.current = null;
    (event.target as Element).releasePointerCapture?.(event.pointerId);
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-deep/80 p-5 backdrop-blur-md"
      role="dialog"
      aria-modal="true"
      aria-label={`${record.type_display_name} tam ekran görüntüsü`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden rounded-2xl border border-border bg-panel/95 shadow-2xl">
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-hairline px-4 py-3">
          <div className="mr-auto min-w-40">
            <div className="text-sm font-semibold">
              {record.type_display_name}
            </div>
            <div className="text-xs text-muted">
              {viewLabel} · #{record.id}
            </div>
          </div>

          <div
            className="flex items-center gap-1.5"
            aria-label="Arşiv görüntüsü seçimi"
          >
            <button
              type="button"
              className={
                "chip " +
                (view === "annotated"
                  ? "bg-hover text-text ring-1 ring-accent/60"
                  : "opacity-70 hover:opacity-100")
              }
              disabled={!annotatedMediaId}
              onClick={() => switchView("annotated")}
            >
              İşaretli kare
            </button>
            <button
              type="button"
              className={
                "chip " +
                (view === "original"
                  ? "bg-hover text-text ring-1 ring-accent/60"
                  : "opacity-70 hover:opacity-100")
              }
              disabled={!originalMediaId}
              onClick={() => switchView("original")}
            >
              Orijinal kare
            </button>
          </div>

          <div
            className="flex items-center gap-1.5 rounded-lg border border-hairline bg-card px-2 py-1"
            aria-label="Tam ekran yakınlaştırma kontrolleri"
          >
            <button
              type="button"
              className="btn-ghost"
              disabled={viewport.zoom <= ARCHIVE_MIN_ZOOM}
              onClick={() => changeZoom(-ARCHIVE_ZOOM_STEP)}
              aria-label="Uzaklaştır"
            >
              −
            </button>
            <span className="w-12 text-center font-mono text-xs text-soft">
              %{Math.round(viewport.zoom * 100)}
            </span>
            <button
              type="button"
              className="btn-ghost"
              disabled={viewport.zoom >= ARCHIVE_MAX_ZOOM}
              onClick={() => changeZoom(ARCHIVE_ZOOM_STEP)}
              aria-label="Yakınlaştır"
            >
              +
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={viewport.zoom === ARCHIVE_MIN_ZOOM}
              onClick={resetViewport}
            >
              Görünümü sıfırla
            </button>
          </div>

          <button type="button" className="btn-ghost" onClick={onClose}>
            Tam ekranı kapat
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col bg-deep p-3">
          <div className="mb-2 shrink-0 text-center text-xs text-muted">
            {viewport.zoom > ARCHIVE_MIN_ZOOM
              ? "Fotoğrafı sürükleyerek kaydırabilirsiniz"
              : "Yakınlaştırınca fotoğrafı sürükleyebilirsiniz"}
          </div>
          {mediaId ? (
            <svg
              ref={svgRef}
              viewBox={`${viewport.x} ${viewport.y} ${viewW} ${viewH}`}
              preserveAspectRatio="xMidYMid meet"
              className="min-h-0 w-full flex-1 touch-none select-none rounded-lg border border-hairline bg-deep"
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onPointerLeave={endDrag}
            >
              <image
                href={"/api/media/" + mediaId}
                width={frame.width}
                height={frame.height}
                preserveAspectRatio="none"
                style={{
                  cursor:
                    viewport.zoom > ARCHIVE_MIN_ZOOM ? "grab" : "default",
                }}
                onPointerDown={
                  viewport.zoom > ARCHIVE_MIN_ZOOM
                    ? (event) => {
                        dragRef.current = {
                          startClientX: event.clientX,
                          startClientY: event.clientY,
                          viewport: { ...viewport },
                        };
                        (event.target as Element).setPointerCapture(
                          event.pointerId,
                        );
                        event.preventDefault();
                      }
                    : undefined
                }
              />
            </svg>
          ) : (
            <div className="grid min-h-0 flex-1 place-items-center rounded-lg border border-hairline text-xs text-faint">
              Görüntü saklama süresi doldu
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
