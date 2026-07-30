// Doğrulama sayfası (WEB_PLANI.md §5/§7): karar bekleyen kuyruğu + editör.
// Klavye: D = doğru, E = düzelt, Y = yanlış, → = atla. Kutu editörü SVG
// viewBox'ını kare boyutuna sabitler; sürükleme kare pikselinde çalışır ve
// corrected_bbox her zaman kare koordinatında gönderilir (§4.6 — gidiş-
// dönüş ölçek sapması ±1 px kabulü sunucu testleriyle sabittir). Semantic
// (roadline) tespitlerde düzeltme modu gizlenir; API de reddeder.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, ApiError, formatTs } from "../api";
import type {
  ArchiveModelNode,
  CaptureDetail,
  DetectionPage,
  DetectionRow,
} from "../types";

type Verdict = "correct" | "corrected" | "wrong";

interface ReviewBody {
  object_id: number;
  verdict: Verdict;
  corrected_bbox?: number[];
  corrected_class?: string;
  note?: string;
}

export function VerifyPage() {
  const queryClient = useQueryClient();
  const [modelFilter, setModelFilter] = useState("");
  const [onlyWithImage, setOnlyWithImage] = useState(true);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const tree = useQuery({
    queryKey: ["archive-types"],
    queryFn: () => api<{ models: ArchiveModelNode[] }>("/api/archive/types"),
    staleTime: 60_000,
  });

  const queueKey = ["verify-queue", modelFilter, onlyWithImage];
  const queue = useInfiniteQuery({
    queryKey: queueKey,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "30" });
      if (modelFilter) params.append("model_id", modelFilter);
      if (onlyWithImage) params.append("only_with_image", "true");
      if (pageParam) params.append("cursor", pageParam);
      return api<DetectionPage>("/api/verify/queue?" + params.toString());
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const records = useMemo(
    () => (queue.data ? queue.data.pages.flatMap((p) => p.records) : []),
    [queue.data],
  );
  const active =
    records.find((record) => record.id === activeId) ?? records[0] ?? null;

  const review = useMutation({
    mutationFn: (body: ReviewBody) =>
      api<{ review: { verdict: Verdict } }>("/api/reviews", {
        method: "POST",
        body,
      }),
    onSuccess: (_data, body) => {
      setFlash(
        body.verdict === "correct"
          ? "Doğru işaretlendi"
          : body.verdict === "wrong"
            ? "Yanlış işaretlendi"
            : "Düzeltme kaydedildi",
      );
      setEditing(false);
      advance(body.object_id);
      queryClient.invalidateQueries({ queryKey: ["verify-queue"] });
      queryClient.invalidateQueries({ queryKey: ["archive-types"] });
      queryClient.invalidateQueries({ queryKey: ["archive"] });
    },
    onError: (error) => {
      setFlash(
        error instanceof ApiError ? error.message : "Karar kaydedilemedi.",
      );
    },
  });

  const advance = useCallback(
    (fromId: number) => {
      const index = records.findIndex((record) => record.id === fromId);
      const next = records[index + 1] ?? records[index - 1] ?? null;
      setActiveId(next ? next.id : null);
      if (index >= records.length - 3 && queue.hasNextPage) {
        queue.fetchNextPage();
      }
    },
    [records, queue],
  );

  const modelNode = tree.data?.models.find(
    (model) => model.model_id === active?.model_id,
  );
  const isSemantic = modelNode ? modelNode.task !== "detect" : false;

  // Klavye kısayolları (giriş alanlarında devre dışı).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (
        !active ||
        review.isPending ||
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT"
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "d") {
        review.mutate({ object_id: active.id, verdict: "correct" });
      } else if (key === "y") {
        review.mutate({ object_id: active.id, verdict: "wrong" });
      } else if (key === "e" && !isSemantic) {
        setEditing((current) => !current);
      } else if (event.key === "ArrowRight") {
        advance(active.id);
      } else {
        return;
      }
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, advance, isSemantic, review]);

  return (
    <div className="flex h-full min-h-0 min-w-0 gap-4">
      <aside className="flex min-h-0 w-72 shrink-0 flex-col">
        <div className="mb-3 shrink-0 rounded-xl border border-border-soft bg-panel p-3">
          <div className="eyebrow mb-1">Model</div>
          <select
            className="field"
            value={modelFilter}
            onChange={(event) => {
              setModelFilter(event.target.value);
              setActiveId(null);
            }}
          >
            <option value="">Tümü</option>
            {(tree.data?.models ?? []).map((model) => (
              <option key={model.model_id} value={model.model_id}>
                {model.display_name}
              </option>
            ))}
          </select>
          <label className="mt-2 flex items-center gap-2 text-sm text-soft">
            <input
              type="checkbox"
              checked={onlyWithImage}
              onChange={(event) => {
                setOnlyWithImage(event.target.checked);
                setActiveId(null);
              }}
            />
            Yalnız görüntülü
          </label>
        </div>

        <div
          className="min-h-0 flex-1 space-y-1.5 overflow-y-auto overscroll-contain pr-1 [scrollbar-gutter:stable]"
          aria-label="Doğrulama kuyruğu"
          tabIndex={0}
        >
          {records.map((record) => (
            <button
              key={record.id}
              type="button"
              onClick={() => {
                setActiveId(record.id);
                setEditing(false);
              }}
              className={
                "flex w-full items-center gap-2 rounded-lg border px-2 py-1.5 text-left text-sm " +
                (active?.id === record.id
                  ? "border-accent bg-chip"
                  : "border-border-soft bg-card hover:bg-hover")
              }
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">
                  {record.type_display_name}
                </span>
                <span className="block font-mono text-[11px] text-faint">
                  {formatTs(record.ts)}
                </span>
              </span>
              {record.confidence != null && (
                <span className="font-mono text-xs text-muted">
                  %{Math.round(record.confidence * 100)}
                </span>
              )}
            </button>
          ))}
          {queue.hasNextPage && (
            <button
              className="btn-ghost w-full"
              onClick={() => queue.fetchNextPage()}
              disabled={queue.isFetchingNextPage}
            >
              Daha fazla yükle
            </button>
          )}
          {!queue.isPending && records.length === 0 && (
            <div className="rounded-lg border border-border-soft p-4 text-center text-sm text-muted">
              Karar bekleyen tespit yok.
            </div>
          )}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        {flash && (
          <div className="mb-3 shrink-0 rounded-lg border border-border-soft bg-panel px-3 py-2 text-sm text-soft">
            {flash}
          </div>
        )}
        {active ? (
          <Editor
            key={active.id}
            record={active}
            isSemantic={isSemantic}
            modelNode={modelNode}
            editing={editing}
            setEditing={setEditing}
            busy={review.isPending}
            onDecide={(body) => review.mutate(body)}
            onSkip={() => advance(active.id)}
          />
        ) : (
          <div className="rounded-xl border border-border-soft p-8 text-center text-sm text-muted">
            Kuyruk boş — tüm tespitler karara bağlanmış.
          </div>
        )}
      </section>
    </div>
  );
}

function Editor({
  record,
  isSemantic,
  modelNode,
  editing,
  setEditing,
  busy,
  onDecide,
  onSkip,
}: {
  record: DetectionRow;
  isSemantic: boolean;
  modelNode: ArchiveModelNode | undefined;
  editing: boolean;
  setEditing: (value: boolean) => void;
  busy: boolean;
  onDecide: (body: ReviewBody) => void;
  onSkip: () => void;
}) {
  const capture = useQuery({
    queryKey: ["capture", record.capture_id],
    queryFn: () => api<CaptureDetail>("/api/captures/" + record.capture_id),
    enabled: record.capture_id !== null,
  });
  const frame = capture.data?.capture.original;
  const [box, setBox] = useState<number[] | null>(record.bbox);
  const [klass, setKlass] = useState(record.class_name);
  const [note, setNote] = useState("");

  const boxChanged =
    box !== null &&
    record.bbox !== null &&
    box.some((value, index) => Math.abs(value - record.bbox![index]) > 0.5);
  const classChanged = klass !== record.class_name;

  function saveCorrection() {
    const body: ReviewBody = { object_id: record.id, verdict: "corrected" };
    if (boxChanged && box) body.corrected_bbox = box;
    if (classChanged) body.corrected_class = klass;
    if (note.trim()) body.note = note.trim();
    onDecide(body);
  }

  return (
    <div className="flex h-full min-h-0 flex-col rounded-xl border border-border-soft bg-panel">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-hairline p-4">
        <div>
          <div className="text-sm font-semibold">
            {record.type_display_name}
            <span className="ml-2 font-mono text-xs text-faint">
              #{record.id}
            </span>
          </div>
          <div className="text-xs text-muted">
            {record.model_display_name} · {formatTs(record.ts)} · güven{" "}
            <span className="font-mono">
              {record.confidence != null ? record.confidence.toFixed(3) : "—"}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <button
            className="btn-accent"
            disabled={busy}
            onClick={() =>
              onDecide({ object_id: record.id, verdict: "correct" })
            }
            title="Kısayol: D"
          >
            Doğru (D)
          </button>
          {!isSemantic && (
            <button
              className={"btn-ghost" + (editing ? " ring-1 ring-accent/60" : "")}
              disabled={busy}
              onClick={() => setEditing(!editing)}
              title="Kutu veya etiketi düzelt · Kısayol: E"
            >
              Kutu / etiket düzelt (E)
            </button>
          )}
          <button
            className="btn-ghost text-danger"
            disabled={busy}
            onClick={() => onDecide({ object_id: record.id, verdict: "wrong" })}
            title="Kısayol: Y"
          >
            Yanlış (Y)
          </button>
          <button className="btn-ghost" onClick={onSkip} title="Kısayol: →">
            Atla (→)
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 [scrollbar-gutter:stable]">
        {record.original_media_id && frame ? (
          <BboxCanvas
            mediaId={record.original_media_id}
            frameW={frame.width}
            frameH={frame.height}
            box={box}
            editable={editing}
            onChange={setBox}
          />
        ) : record.annotated_media_id ? (
          <img
            src={"/api/media/" + record.annotated_media_id}
            alt={record.type_display_name}
            className="w-full rounded-lg border border-hairline bg-deep object-contain"
          />
        ) : (
          <div className="rounded-lg border border-hairline bg-deep p-8 text-center text-xs text-faint">
            Görüntü saklama süresi doldu — karar yine kaydedilir, örnek
            görüntüsüz işaretlenir.
          </div>
        )}

        {editing && !isSemantic && (
          <div className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-hairline bg-card p-3">
            <div className="w-full">
              <div className="text-sm font-semibold text-text">
                Eğitim etiketi düzeltme
              </div>
              <div className="mt-0.5 text-xs text-muted">
                Yalnız etiketi değiştirebilirsiniz; kutuyu taşımanız gerekmez.
                Seçenekler aynı modelin güvenli sınıf sözlüğünden gelir.
              </div>
            </div>
            <label>
              <div className="eyebrow mb-1">
                Doğru etiket ({record.model_id} sözlüğü)
              </div>
              <select
                className="field w-56"
                value={klass}
                onChange={(event) => setKlass(event.target.value)}
              >
                {(modelNode?.types ?? []).map((type) => (
                  <option key={type.type_id} value={type.class_name}>
                    {type.display_name}
                  </option>
                ))}
              </select>
              {classChanged && (
                <div className="mt-1 text-xs text-accent">
                  {record.type_display_name} →{" "}
                  {modelNode?.types.find((type) => type.class_name === klass)
                    ?.display_name ?? klass}
                </div>
              )}
            </label>
            <label className="min-w-0 flex-1">
              <div className="eyebrow mb-1">Not (isteğe bağlı)</div>
              <input
                className="field"
                value={note}
                maxLength={2000}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            {box && (
              <div className="font-mono text-xs text-muted">
                [{box.map((value) => value.toFixed(0)).join(", ")}]
              </div>
            )}
            <button
              className="btn-accent"
              disabled={busy || (!boxChanged && !classChanged)}
              onClick={saveCorrection}
            >
              Düzeltmeyi kaydet
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

type DragMode =
  | { kind: "move"; startX: number; startY: number; box: number[] }
  | { kind: "resize"; corner: number; box: number[] };

function BboxCanvas({
  mediaId,
  frameW,
  frameH,
  box,
  editable,
  onChange,
}: {
  mediaId: number;
  frameW: number;
  frameH: number;
  box: number[] | null;
  editable: boolean;
  onChange: (box: number[]) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<DragMode | null>(null);

  // Ekran pikselini kare pikseline çevirir (§4.6 oran kuralı).
  function toFrame(event: ReactPointerEvent): [number, number] {
    const rect = svgRef.current!.getBoundingClientRect();
    return [
      ((event.clientX - rect.left) * frameW) / rect.width,
      ((event.clientY - rect.top) * frameH) / rect.height,
    ];
  }

  function clamp(value: number, max: number) {
    return Math.min(Math.max(value, 0), max);
  }

  function onPointerMove(event: ReactPointerEvent) {
    const drag = dragRef.current;
    if (!drag || !box) return;
    const [fx, fy] = toFrame(event);
    if (drag.kind === "move") {
      const dx = fx - drag.startX;
      const dy = fy - drag.startY;
      const w = drag.box[2] - drag.box[0];
      const h = drag.box[3] - drag.box[1];
      const x1 = clamp(drag.box[0] + dx, frameW - w);
      const y1 = clamp(drag.box[1] + dy, frameH - h);
      onChange([x1, y1, x1 + w, y1 + h]);
    } else {
      const next = [...drag.box];
      const xi = drag.corner % 2 === 0 ? 0 : 2;
      const yi = drag.corner < 2 ? 1 : 3;
      next[xi] = clamp(fx, frameW);
      next[yi] = clamp(fy, frameH);
      onChange([
        Math.min(next[0], next[2]),
        Math.min(next[1], next[3]),
        Math.max(next[0], next[2]),
        Math.max(next[1], next[3]),
      ]);
    }
  }

  function endDrag(event: ReactPointerEvent) {
    dragRef.current = null;
    (event.target as Element).releasePointerCapture?.(event.pointerId);
  }

  const handleRadius = frameW / 90;
  const corners =
    box === null
      ? []
      : [
          [box[0], box[1]],
          [box[2], box[1]],
          [box[0], box[3]],
          [box[2], box[3]],
        ];

  return (
    <svg
      ref={svgRef}
      viewBox={"0 0 " + frameW + " " + frameH}
      className={
        "w-full rounded-lg border bg-deep " +
        (editable ? "border-accent/60 touch-none" : "border-hairline")
      }
      onPointerMove={editable ? onPointerMove : undefined}
      onPointerUp={editable ? endDrag : undefined}
      onPointerLeave={editable ? endDrag : undefined}
    >
      <image
        href={"/api/media/" + mediaId}
        width={frameW}
        height={frameH}
        preserveAspectRatio="none"
      />
      {box && (
        <>
          <rect
            x={box[0]}
            y={box[1]}
            width={box[2] - box[0]}
            height={box[3] - box[1]}
            fill="rgba(56, 217, 150, 0.12)"
            stroke="#38d996"
            strokeWidth={Math.max(2, frameW / 400)}
            style={{ cursor: editable ? "move" : "default" }}
            onPointerDown={
              editable
                ? (event) => {
                    const [fx, fy] = toFrame(event);
                    dragRef.current = {
                      kind: "move",
                      startX: fx,
                      startY: fy,
                      box: [...box],
                    };
                    (event.target as Element).setPointerCapture(
                      event.pointerId,
                    );
                  }
                : undefined
            }
          />
          {editable &&
            corners.map(([cx, cy], corner) => (
              <circle
                key={corner}
                cx={cx}
                cy={cy}
                r={handleRadius}
                fill="#38d996"
                stroke="#052014"
                strokeWidth={handleRadius / 4}
                style={{ cursor: "nwse-resize" }}
                onPointerDown={(event) => {
                  dragRef.current = { kind: "resize", corner, box: [...box] };
                  (event.target as Element).setPointerCapture(event.pointerId);
                }}
              />
            ))}
        </>
      )}
    </svg>
  );
}
