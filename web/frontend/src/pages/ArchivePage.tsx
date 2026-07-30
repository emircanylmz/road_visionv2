// Arşiv sayfası: masaüstü Tespit Arşivi'nin web karşılığı (WEB_PLANI.md §7).
// Kartlar işaretli (bbox çizili) kareyi gösterir; ayrıntı çekmecesi
// orijinal/işaretli geçişi sunar. Doğrulama etiketi §4.3 kuralını izler:
// karar satırı olmayan her tespit "doğrulanmadı"dır — yeni tespitler
// kendiliğinden bu kuyruğa düşer. Karar verme uçları Faz 4'te gelir.

import { useMemo, useState } from "react";
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
    <div className="flex min-w-0 gap-4">
      <section className="min-w-0 flex-1">
        <div className="mb-4 rounded-xl border border-border-soft bg-panel p-3">
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
              <span className="font-mono">{records.length} tespit yüklendi</span>
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

  return (
    <aside className="w-[30rem] shrink-0 self-start rounded-xl border border-border-soft bg-panel">
      <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
        <div>
          <div className="eyebrow">Tespit ayrıntısı</div>
          <div className="font-mono text-xs text-muted">#{record.id}</div>
        </div>
        <button className="btn-ghost" onClick={onClose}>
          Kapat
        </button>
      </div>

      <div className="p-4 text-sm">
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
              <img
                src={"/api/media/" + mediaId}
                alt={record.type_display_name}
                className="mb-3 w-full rounded-lg border border-hairline bg-deep object-contain"
              />
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
    </aside>
  );
}
