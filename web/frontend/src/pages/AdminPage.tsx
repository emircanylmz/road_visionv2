// Yönetim sayfası: Faz 1 uçlarının arayüzü — üyelik onay akışı, aktif
// oturumlar ve denetim kayıtları. Durum değiştiren istekler CSRF başlığını
// api() sarmalayıcısından otomatik alır.

import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, ApiError, formatTs } from "../api";
import type { AuditEntry, SessionInfo, UserInfo } from "../types";

type Tab = "uyeler" | "oturumlar" | "denetim";
type StatusFilter = UserInfo["status"] | "";

const STATUS_LABEL: Record<UserInfo["status"], string> = {
  pending: "Bekleyen",
  approved: "Onaylı",
  rejected: "Reddedilen",
  disabled: "Kapalı",
};

const ACTION_LABEL: Record<string, string> = {
  approve_user: "üyelik onayı",
  reject_user: "üyelik reddi",
  disable_user: "hesap kapatma",
  revoke_session: "oturum iptali",
};

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("uyeler");
  return (
    <div>
      <div className="mb-4 flex gap-1.5">
        {(
          [
            ["uyeler", "Üyeler"],
            ["oturumlar", "Oturumlar"],
            ["denetim", "Denetim"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={
              "chip " +
              (tab === key
                ? "bg-hover text-text ring-1 ring-accent/60"
                : "opacity-70 hover:opacity-100")
            }
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "uyeler" && <UsersTab />}
      {tab === "oturumlar" && <SessionsTab />}
      {tab === "denetim" && <AuditTab />}
    </div>
  );
}

function UsersTab() {
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const users = useQuery({
    queryKey: ["admin-users", status],
    queryFn: () =>
      api<{ users: UserInfo[] }>(
        "/api/admin/users" + (status ? "?status=" + status : ""),
      ),
  });

  const action = useMutation({
    mutationFn: ({
      userId,
      verb,
    }: {
      userId: number;
      verb: "approve" | "reject" | "disable";
    }) =>
      api("/api/admin/users/" + userId + "/" + verb, { method: "POST" }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "İşlem başarısız."),
  });

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        {(["pending", "approved", "rejected", "disabled", ""] as const).map(
          (key) => (
            <button
              key={key || "all"}
              onClick={() => setStatus(key)}
              className={
                "chip " +
                (status === key
                  ? "bg-hover text-text ring-1 ring-accent/60"
                  : "opacity-70 hover:opacity-100")
              }
            >
              {key ? STATUS_LABEL[key] : "Tümü"}
            </button>
          ),
        )}
      </div>

      {error && (
        <div className="mb-3 rounded-lg border border-danger/40 bg-danger-bg px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-border-soft">
        <table className="w-full text-sm">
          <thead className="bg-panel text-left">
            <tr>
              <th className="eyebrow px-3 py-2 font-semibold">Ad soyad</th>
              <th className="eyebrow px-3 py-2 font-semibold">E-posta</th>
              <th className="eyebrow px-3 py-2 font-semibold">Durum</th>
              <th className="eyebrow px-3 py-2 font-semibold">Başvuru</th>
              <th className="eyebrow px-3 py-2 text-right font-semibold">
                İşlem
              </th>
            </tr>
          </thead>
          <tbody>
            {(users.data?.users ?? []).map((user) => (
              <tr key={user.user_id} className="border-t border-hairline">
                <td className="px-3 py-2">
                  {user.full_name}
                  {user.role === "admin" && (
                    <span className="ml-2 text-xs text-accent">yönetici</span>
                  )}
                </td>
                <td className="px-3 py-2 text-muted">{user.email}</td>
                <td className="px-3 py-2 text-muted">
                  {STATUS_LABEL[user.status]}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-muted">
                  {formatTs(user.created_at)}
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="inline-flex gap-2">
                    {user.status !== "approved" && (
                      <button
                        className="btn-accent px-3 py-1 text-xs"
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({
                            userId: user.user_id,
                            verb: "approve",
                          })
                        }
                      >
                        Onayla
                      </button>
                    )}
                    {user.status === "pending" && (
                      <button
                        className="btn-ghost px-3 py-1 text-xs text-danger"
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({
                            userId: user.user_id,
                            verb: "reject",
                          })
                        }
                      >
                        Reddet
                      </button>
                    )}
                    {user.status === "approved" && (
                      <button
                        className="btn-ghost px-3 py-1 text-xs text-danger"
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({
                            userId: user.user_id,
                            verb: "disable",
                          })
                        }
                      >
                        Devre dışı bırak
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.isPending && (
          <div className="p-6 text-center text-sm text-muted">
            Üyeler yükleniyor…
          </div>
        )}
        {!users.isPending && (users.data?.users ?? []).length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            Bu durumda üye yok.
          </div>
        )}
      </div>
    </section>
  );
}

function SessionsTab() {
  const queryClient = useQueryClient();
  const sessions = useQuery({
    queryKey: ["admin-sessions"],
    queryFn: () => api<{ sessions: SessionInfo[] }>("/api/admin/sessions"),
    refetchInterval: 30_000,
  });
  const revoke = useMutation({
    mutationFn: (sessionId: string) =>
      api("/api/admin/sessions/" + sessionId, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-sessions"] });
      queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  return (
    <div className="overflow-hidden rounded-xl border border-border-soft">
      <table className="w-full text-sm">
        <thead className="bg-panel text-left">
          <tr>
            <th className="eyebrow px-3 py-2 font-semibold">Kullanıcı</th>
            <th className="eyebrow px-3 py-2 font-semibold">Açılış</th>
            <th className="eyebrow px-3 py-2 font-semibold">Son etkinlik</th>
            <th className="eyebrow px-3 py-2 font-semibold">IP</th>
            <th className="eyebrow px-3 py-2 text-right font-semibold">
              İşlem
            </th>
          </tr>
        </thead>
        <tbody>
          {(sessions.data?.sessions ?? []).map((session) => (
            <tr key={session.session_id} className="border-t border-hairline">
              <td className="px-3 py-2 text-soft">{session.email}</td>
              <td className="px-3 py-2 font-mono text-xs text-muted">
                {formatTs(session.created_at)}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-muted">
                {formatTs(session.last_seen_at)}
              </td>
              <td className="px-3 py-2 font-mono text-xs text-muted">
                {session.ip ?? "—"}
              </td>
              <td className="px-3 py-2 text-right">
                <button
                  className="btn-ghost px-3 py-1 text-xs text-danger"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(session.session_id)}
                >
                  Oturumu kapat
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!sessions.isPending &&
        (sessions.data?.sessions ?? []).length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            Aktif oturum yok.
          </div>
        )}
    </div>
  );
}

function AuditTab() {
  const audit = useInfiniteQuery({
    queryKey: ["audit"],
    queryFn: ({ pageParam }) =>
      api<{ entries: AuditEntry[]; next_before_id: number | null }>(
        "/api/admin/audit?limit=50" +
          (pageParam ? "&before_id=" + pageParam : ""),
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before_id,
  });
  const entries = audit.data?.pages.flatMap((page) => page.entries) ?? [];

  return (
    <div>
      <div className="overflow-hidden rounded-xl border border-border-soft">
        <table className="w-full text-sm">
          <thead className="bg-panel text-left">
            <tr>
              <th className="eyebrow px-3 py-2 font-semibold">Zaman</th>
              <th className="eyebrow px-3 py-2 font-semibold">Yönetici</th>
              <th className="eyebrow px-3 py-2 font-semibold">İşlem</th>
              <th className="eyebrow px-3 py-2 font-semibold">Hedef</th>
              <th className="eyebrow px-3 py-2 font-semibold">Ayrıntı</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.audit_id} className="border-t border-hairline">
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                  {formatTs(entry.created_at)}
                </td>
                <td className="px-3 py-2 text-soft">{entry.actor_email}</td>
                <td className="px-3 py-2 text-soft">
                  {ACTION_LABEL[entry.action] ?? entry.action}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-muted">
                  {entry.target}
                </td>
                <td className="max-w-0 truncate px-3 py-2 font-mono text-xs text-faint">
                  {entry.detail ? JSON.stringify(entry.detail) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!audit.isPending && entries.length === 0 && (
          <div className="p-6 text-center text-sm text-muted">
            Denetim kaydı yok.
          </div>
        )}
      </div>
      {audit.hasNextPage && (
        <button
          className="btn-ghost mt-3"
          onClick={() => audit.fetchNextPage()}
          disabled={audit.isFetchingNextPage}
        >
          Daha eski kayıtlar
        </button>
      )}
    </div>
  );
}
