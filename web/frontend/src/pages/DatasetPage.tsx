// Dataset sayfası (WEB_PLANI.md §7): model × tür × karar kırılımı, export
// işleri ve indirme. İşler pending/running'deyken liste 2,5 sn'de bir
// yenilenir; zip DB'de saklandığından indirme bağlantısı oturum korumalı
// download ucuna gider. Örnek galerisi Arşiv sayfasıdır: karar filtreleri
// oradadır, buradan tek tıkla o görünüme geçilir.

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, formatTs } from "../api";
import { Link } from "../router";
import type {
  DatasetSummary,
  ExportJob,
  StatsOverview,
} from "../types";

const VERDICT_LABEL: Record<string, string> = {
  correct: "Doğru",
  corrected: "Düzeltildi",
  wrong: "Yanlış",
};

const STATUS_LABEL: Record<ExportJob["status"], string> = {
  pending: "Bekliyor",
  running: "Koşuyor",
  done: "Hazır",
  failed: "Başarısız",
};

export function DatasetPage() {
  const queryClient = useQueryClient();
  const [exportModel, setExportModel] = useState("");
  const [exportScope, setExportScope] = useState<"positive" | "wrong">(
    "positive",
  );
  const [flash, setFlash] = useState<string | null>(null);

  const overview = useQuery({
    queryKey: ["stats-overview"],
    queryFn: () => api<StatsOverview>("/api/stats/overview"),
    staleTime: 30_000,
  });

  const summary = useQuery({
    queryKey: ["dataset-summary"],
    queryFn: () => api<DatasetSummary>("/api/datasets/summary"),
  });

  const jobs = useQuery({
    queryKey: ["export-jobs"],
    queryFn: () => api<{ jobs: ExportJob[] }>("/api/datasets/exports?limit=20"),
    refetchInterval: (query) =>
      query.state.data?.jobs.some(
        (job) => job.status === "pending" || job.status === "running",
      )
        ? 2500
        : false,
  });

  const startExport = useMutation({
    mutationFn: (body: { model_id: string; verdict: "positive" | "wrong" }) =>
      api<{ job: ExportJob }>("/api/datasets/export", {
        method: "POST",
        body,
      }),
    onSuccess: (data) => {
      setFlash(`Export işi açıldı (#${data.job.job_id}).`);
      queryClient.invalidateQueries({ queryKey: ["export-jobs"] });
    },
    onError: (error) => {
      setFlash(
        error instanceof ApiError ? error.message : "Export başlatılamadı.",
      );
    },
  });

  const models = summary.data?.models ?? [];
  const exportableModels = useMemo(
    () =>
      models.filter((model) => {
        const totals = model.totals;
        return exportScope === "positive"
          ? (totals.correct ?? 0) + (totals.corrected ?? 0) > 0
          : (totals.wrong ?? 0) > 0;
      }),
    [models, exportScope],
  );

  return (
    <div className="min-w-0 space-y-4">
      {overview.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            label="Toplam tespit"
            value={overview.data.detections.total.toLocaleString("tr-TR")}
            hint={`son 24 saat: ${overview.data.detections.last_24h}`}
          />
          <StatCard
            label="Doğrulama kapsaması"
            value={
              "%" + Math.round(overview.data.reviews.coverage * 100)
            }
            hint={`${overview.data.reviews.total} karar · son 24 saat: ${overview.data.reviews.last_24h}`}
          />
          <StatCard
            label="Görüntülü örnek"
            value={overview.data.dataset.samples_with_image.toLocaleString(
              "tr-TR",
            )}
            hint="copy-on-verify deposunda"
          />
          <StatCard
            label="Aktif export işi"
            value={String(overview.data.export_jobs_active)}
            hint={
              (overview.data.reviews.verdicts.wrong ?? 0) +
              " yanlış işaretli tespit"
            }
          />
        </div>
      )}

      <section className="rounded-xl border border-border-soft bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">
            Model × tür × karar kırılımı
          </h2>
          <Link className="btn-ghost" to="/arsiv">
            Örnek galerisi (Arşiv) →
          </Link>
        </div>
        {models.length === 0 ? (
          <div className="p-4 text-center text-sm text-muted">
            Henüz doğrulanmış örnek yok — Doğrulama sekmesinden karar verin.
          </div>
        ) : (
          models.map((model) => (
            <div key={model.model_id} className="mb-4 last:mb-0">
              <div className="mb-1.5 flex items-baseline gap-2">
                <span className="font-mono text-sm text-text">
                  {model.model_id}
                </span>
                <span className="text-xs text-muted">
                  {Object.entries(model.totals)
                    .map(
                      ([verdict, count]) =>
                        `${VERDICT_LABEL[verdict] ?? verdict}: ${count}`,
                    )
                    .join(" · ")}
                </span>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-hairline text-left text-xs text-faint">
                    <th className="py-1 pr-2 font-medium">Nihai sınıf</th>
                    <th className="py-1 pr-2 text-right font-medium">Doğru</th>
                    <th className="py-1 pr-2 text-right font-medium">
                      Düzeltildi
                    </th>
                    <th className="py-1 pr-2 text-right font-medium">Yanlış</th>
                    <th className="py-1 text-right font-medium">Görüntülü</th>
                  </tr>
                </thead>
                <tbody>
                  {model.types.map((type) => (
                    <tr
                      key={type.final_type_id}
                      className="border-b border-hairline/50 last:border-0"
                    >
                      <td className="py-1 pr-2">{type.final_class_name}</td>
                      <td className="py-1 pr-2 text-right font-mono">
                        {type.counts.correct || "—"}
                      </td>
                      <td className="py-1 pr-2 text-right font-mono">
                        {type.counts.corrected || "—"}
                      </td>
                      <td className="py-1 pr-2 text-right font-mono">
                        {type.counts.wrong || "—"}
                      </td>
                      <td className="py-1 text-right font-mono">
                        {type.with_image}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </section>

      <section className="rounded-xl border border-border-soft bg-panel p-4">
        <h2 className="mb-3 text-sm font-semibold">YOLO export</h2>
        {flash && (
          <div className="mb-3 rounded-lg border border-border-soft bg-card px-3 py-2 text-sm text-soft">
            {flash}
          </div>
        )}
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <label>
            <div className="eyebrow mb-1">Kapsam</div>
            <select
              className="field w-56"
              value={exportScope}
              onChange={(event) => {
                setExportScope(event.target.value as "positive" | "wrong");
                setExportModel("");
              }}
            >
              <option value="positive">
                Pozitif (doğru + düzeltilmiş etiketler)
              </option>
              <option value="wrong">
                Yanlış (hard-negative / background)
              </option>
            </select>
          </label>
          <label>
            <div className="eyebrow mb-1">Model</div>
            <select
              className="field w-56"
              value={exportModel}
              onChange={(event) => setExportModel(event.target.value)}
            >
              <option value="">Seçin…</option>
              {exportableModels.map((model) => (
                <option key={model.model_id} value={model.model_id}>
                  {model.model_id}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn-accent"
            disabled={!exportModel || startExport.isPending}
            onClick={() =>
              startExport.mutate({
                model_id: exportModel,
                verdict: exportScope,
              })
            }
          >
            Export başlat
          </button>
          <span className="pb-2 text-xs text-muted">
            Etiketler nihai (final) değerlerden, koordinatlar kare boyutuna
            normalize üretilir; yanlış kapsamı boş etiketli background
            görüntüleri verir.
          </span>
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-xs text-faint">
              <th className="py-1 pr-2 font-medium">İş</th>
              <th className="py-1 pr-2 font-medium">Model / kapsam</th>
              <th className="py-1 pr-2 font-medium">Durum</th>
              <th className="py-1 pr-2 text-right font-medium">
                Görüntü / örnek
              </th>
              <th className="py-1 pr-2 text-right font-medium">Boyut</th>
              <th className="py-1 font-medium">İndir</th>
            </tr>
          </thead>
          <tbody>
            {(jobs.data?.jobs ?? []).map((job) => (
              <tr
                key={job.job_id}
                className="border-b border-hairline/50 last:border-0"
              >
                <td className="py-1.5 pr-2 font-mono text-xs">
                  #{job.job_id}
                  <span className="ml-2 text-faint">
                    {formatTs(job.created_at)}
                  </span>
                </td>
                <td className="py-1.5 pr-2 font-mono text-xs">
                  {job.model_id} / {job.verdict_scope}
                </td>
                <td className="py-1.5 pr-2">
                  <span
                    className={
                      "chip " +
                      (job.status === "done"
                        ? "text-ok-text border-ok-border bg-ok-bg"
                        : job.status === "failed"
                          ? "text-danger border-danger/40 bg-danger-bg"
                          : "text-warning border-warning/40 bg-warning-bg")
                    }
                    title={job.error ?? undefined}
                  >
                    {STATUS_LABEL[job.status]}
                  </span>
                </td>
                <td className="py-1.5 pr-2 text-right font-mono text-xs">
                  {job.image_count ?? "—"} / {job.sample_count ?? "—"}
                </td>
                <td className="py-1.5 pr-2 text-right font-mono text-xs">
                  {job.byte_size != null
                    ? (job.byte_size / 1024).toFixed(0) + " KB"
                    : "—"}
                </td>
                <td className="py-1.5">
                  {job.status === "done" ? (
                    <a
                      className="btn-ghost inline-block"
                      href={`/api/datasets/exports/${job.job_id}/download`}
                    >
                      zip indir
                    </a>
                  ) : (
                    <span className="text-xs text-faint">—</span>
                  )}
                </td>
              </tr>
            ))}
            {(jobs.data?.jobs ?? []).length === 0 && (
              <tr>
                <td colSpan={6} className="p-4 text-center text-sm text-muted">
                  Henüz export işi yok.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-border-soft bg-panel p-3">
      <div className="eyebrow">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-text">{value}</div>
      <div className="mt-0.5 text-xs text-muted">{hint}</div>
    </div>
  );
}
