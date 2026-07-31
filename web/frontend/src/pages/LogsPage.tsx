// Loglar sayfası: masaüstü Oturum Günlüğü'nün kalıcı (PostgreSQL) karşılığı.
// Filtreler taslak/uygula ayrımıyla çalışır (masaüstü arşivindeki draft/
// applied deseni); liste (ts,id) keyset imleciyle "daha eski" yönünde büyür.

import { useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, formatTs } from "../api";
import type {
  LogCategory,
  LogDetail,
  LogLevel,
  LogPage,
  LogRecordRow,
  ModelInfo,
} from "../types";

const LEVELS: LogLevel[] = ["debug", "info", "warning", "error"];

const LEVEL_STYLE: Record<LogLevel, string> = {
  debug: "text-faint border-border-soft",
  info: "text-soft border-chipline",
  warning: "text-warning border-warning/40",
  error: "text-danger border-danger/40",
};

interface Filters {
  levels: LogLevel[];
  category: LogCategory | "";
  modelId: string;
  runId: string;
  tsFrom: string;
  tsTo: string;
}

const EMPTY: Filters = {
  levels: [],
  category: "",
  modelId: "",
  runId: "",
  tsFrom: "",
  tsTo: "",
};

function toQuery(filters: Filters, cursor: string | null): string {
  const params = new URLSearchParams();
  for (const level of filters.levels) params.append("level", level);
  if (filters.category) params.append("category", filters.category);
  if (filters.modelId) params.append("model_id", filters.modelId);
  if (filters.runId) params.append("run_id", filters.runId);
  if (filters.tsFrom)
    params.append("ts_from", new Date(filters.tsFrom).toISOString());
  if (filters.tsTo)
    params.append("ts_to", new Date(filters.tsTo).toISOString());
  params.append("limit", "100");
  if (cursor) params.append("cursor", cursor);
  return params.toString();
}

export function LogsPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<{ models: ModelInfo[] }>("/api/meta/models"),
    staleTime: 5 * 60_000,
  });

  const logs = useInfiniteQuery({
    queryKey: ["logs", applied],
    queryFn: ({ pageParam }) =>
      api<LogPage>("/api/logs?" + toQuery(applied, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const records = useMemo(
    () => (logs.data ? logs.data.pages.flatMap((page) => page.records) : []),
    [logs.data],
  );

  function toggleLevel(level: LogLevel) {
    setDraft((current) => ({
      ...current,
      levels: current.levels.includes(level)
        ? current.levels.filter((item) => item !== level)
        : [...current.levels, level],
    }));
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 gap-4">
      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="mb-4 max-h-[40%] shrink-0 overflow-y-auto overscroll-contain rounded-xl border border-border-soft bg-panel p-3 [scrollbar-gutter:stable]">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <div className="eyebrow mb-1">Seviye</div>
              <div className="flex gap-1.5">
                {LEVELS.map((level) => (
                  <button
                    key={level}
                    type="button"
                    onClick={() => toggleLevel(level)}
                    className={
                      "chip " +
                      LEVEL_STYLE[level] +
                      (draft.levels.includes(level)
                        ? " bg-hover ring-1 ring-accent/60"
                        : " opacity-70 hover:opacity-100")
                    }
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>

            <label>
              <div className="eyebrow mb-1">Kategori</div>
              <select
                className="field w-36"
                value={draft.category}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    category: event.target.value as Filters["category"],
                  })
                }
              >
                <option value="">Tümü</option>
                <option value="app">app</option>
                <option value="detection">detection</option>
              </select>
            </label>

            <label>
              <div className="eyebrow mb-1">Model</div>
              <select
                className="field w-52"
                value={draft.modelId}
                onChange={(event) =>
                  setDraft({ ...draft, modelId: event.target.value })
                }
              >
                <option value="">Tümü</option>
                {(models.data?.models ?? []).map((model) => (
                  <option key={model.model_id} value={model.model_id}>
                    {model.display_name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <div className="eyebrow mb-1">Çalışma no (Run)</div>
              <input
                className="field w-28 font-mono"
                type="number"
                min={1}
                value={draft.runId}
                onChange={(event) =>
                  setDraft({ ...draft, runId: event.target.value })
                }
                placeholder="tümü"
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

            <div className="ml-auto flex gap-2">
              <button
                className="btn-ghost"
                type="button"
                onClick={() => {
                  setDraft(EMPTY);
                  setApplied(EMPTY);
                  setSelectedId(null);
                }}
              >
                Sıfırla
              </button>
              <button
                className="btn-accent"
                type="button"
                onClick={() => {
                  setApplied(draft);
                  setSelectedId(null);
                }}
              >
                Filtreyi uygula
              </button>
            </div>
          </div>
        </div>

        <div
          className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-xl border border-border-soft [scrollbar-gutter:stable]"
          aria-label="Log kayıtları"
          tabIndex={0}
        >
          <table className="w-full min-w-[48rem] text-sm">
            <thead className="sticky top-0 z-10 bg-panel">
              <tr className="text-left">
                <th className="eyebrow px-3 py-2 font-semibold">Zaman</th>
                <th className="eyebrow px-3 py-2 font-semibold">Seviye</th>
                <th className="eyebrow px-3 py-2 font-semibold">Kategori</th>
                <th className="eyebrow px-3 py-2 font-semibold">Model</th>
                <th className="eyebrow px-3 py-2 font-semibold">Run</th>
                <th className="eyebrow px-3 py-2 font-semibold">Mesaj</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <LogRow
                  key={record.id}
                  record={record}
                  selected={record.id === selectedId}
                  onSelect={() =>
                    setSelectedId(
                      record.id === selectedId ? null : record.id,
                    )
                  }
                />
              ))}
            </tbody>
          </table>

          {logs.isPending && (
            <div className="p-6 text-center text-sm text-muted">
              Kayıtlar yükleniyor…
            </div>
          )}
          {logs.isError && (
            <div className="p-6 text-center text-sm text-danger">
              Kayıtlar alınamadı; filtreleri denetleyip yeniden deneyin.
            </div>
          )}
          {!logs.isPending && !logs.isError && records.length === 0 && (
            <div className="p-6 text-center text-sm text-muted">
              Filtreyle eşleşen kayıt yok.
            </div>
          )}
        </div>

        <div className="mt-3 flex shrink-0 items-center justify-between text-sm text-muted">
          <span className="font-mono">{records.length} kayıt yüklendi</span>
          <div className="flex gap-2">
            <button
              className="btn-ghost"
              onClick={() => logs.refetch()}
              disabled={logs.isRefetching}
            >
              Yenile
            </button>
            {logs.hasNextPage && (
              <button
                className="btn-ghost"
                onClick={() => logs.fetchNextPage()}
                disabled={logs.isFetchingNextPage}
              >
                {logs.isFetchingNextPage
                  ? "Yükleniyor…"
                  : "Daha eski kayıtları yükle"}
              </button>
            )}
          </div>
        </div>
      </section>

      {selectedId !== null && (
        <DetailDrawer id={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </div>
  );
}

function LogRow({
  record,
  selected,
  onSelect,
}: {
  record: LogRecordRow;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      onClick={onSelect}
      className={
        "cursor-pointer border-t border-hairline " +
        (selected ? "bg-chip" : "hover:bg-hover")
      }
    >
      <td className="whitespace-nowrap px-3 py-1.5 font-mono text-xs text-soft">
        {formatTs(record.ts)}
      </td>
      <td className="px-3 py-1.5">
        <span className={"chip " + LEVEL_STYLE[record.level]}>
          {record.level}
        </span>
      </td>
      <td className="px-3 py-1.5 text-muted">{record.category}</td>
      <td className="px-3 py-1.5 text-muted">{record.model_id ?? "—"}</td>
      <td className="px-3 py-1.5 font-mono text-xs text-muted">
        {record.run_id ?? "—"}
      </td>
      <td className="max-w-0 truncate px-3 py-1.5 text-soft">
        {record.has_payload && (
          <span className="mr-1.5 text-accent" title="Ayrıntı verisi var">
            ●
          </span>
        )}
        {record.message}
      </td>
    </tr>
  );
}

function DetailDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["log", id],
    queryFn: () => api<LogDetail>("/api/logs/" + id),
  });
  const record = detail.data?.record;

  return (
    <aside className="flex h-full min-h-0 w-[26rem] shrink-0 flex-col rounded-xl border border-border-soft bg-panel">
      <div className="flex shrink-0 items-center justify-between border-b border-hairline px-4 py-3">
        <div>
          <div className="eyebrow">Kayıt ayrıntısı</div>
          <div className="font-mono text-xs text-muted">#{id}</div>
        </div>
        <button className="btn-ghost" onClick={onClose}>
          Kapat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 text-sm [scrollbar-gutter:stable]">
        {detail.isPending && <div className="text-muted">Yükleniyor…</div>}
        {detail.isError && (
          <div className="text-danger">Ayrıntı alınamadı.</div>
        )}
        {record && (
          <>
            <div className="mb-3 space-y-1 text-soft">
              <div>
                <span className="text-faint">Zaman: </span>
                <span className="font-mono text-xs">{formatTs(record.ts)}</span>
              </div>
              <div>
                <span className="text-faint">Seviye / kategori: </span>
                {record.level} · {record.category}
              </div>
              <div>
                <span className="text-faint">Model / run: </span>
                {record.model_id ?? "—"} ·{" "}
                <span className="font-mono text-xs">
                  {record.run_id ?? "—"}
                </span>
              </div>
            </div>
            <div className="mb-3 rounded-lg border border-hairline bg-input p-3 text-soft">
              {record.message}
            </div>
            <div className="eyebrow mb-1">Payload</div>
            <pre className="max-h-96 overflow-auto rounded-lg border border-hairline bg-deep p-3 font-mono text-xs text-soft">
              {JSON.stringify(record.payload, null, 2)}
            </pre>
          </>
        )}
      </div>
    </aside>
  );
}
