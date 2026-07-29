// Uygulama kabuğu: masaüstü v2'nin ikon-ray navigasyonunun web karşılığı.
// Seçili sayfa, RailButton ile aynı dilde işaretlenir: chip zemini + solda
// 3px vurgu çizgisi (roadvision/qt/main_window.py RailButton stylesheet'i).

import type { ReactNode } from "react";
import { useLogout, useUser } from "../auth";
import { NavLink, useNavigate } from "../router";

function RailLink({
  to,
  glyph,
  label,
}: {
  to: string;
  glyph: string;
  label: string;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        [
          "flex h-14 w-14 flex-col items-center justify-center gap-0.5",
          "rounded-lg border-l-[3px] text-[10px] font-semibold",
          isActive
            ? "border-accent bg-chip text-text"
            : "border-transparent text-muted hover:text-text",
        ].join(" ")
      }
    >
      <span aria-hidden className="text-base leading-none">
        {glyph}
      </span>
      {label}
    </NavLink>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const user = useUser();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-[72px] flex-col items-center gap-2 border-r border-hairline bg-rail py-3">
        <div
          className="mb-2 grid h-10 w-10 place-items-center rounded-lg bg-accent font-mono text-sm font-bold text-accent-ink"
          title="RoadVision"
        >
          RV
        </div>
        <RailLink to="/loglar" glyph="≣" label="Loglar" />
        {user.role === "admin" && (
          <RailLink to="/yonetim" glyph="⛭" label="Yönetim" />
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-hairline bg-panel px-5 py-3">
          <div>
            <div className="eyebrow">RoadVision</div>
            <div className="text-sm font-semibold tracking-tight">
              Web Paneli
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="chip flex items-center gap-2">
              <span className="text-text">{user.full_name}</span>
              <span className="text-faint">·</span>
              <span
                className={
                  user.role === "admin" ? "text-accent" : "text-muted"
                }
              >
                {user.role === "admin" ? "yönetici" : "üye"}
              </span>
            </div>
            <button
              className="btn-ghost"
              onClick={() =>
                logout.mutate(undefined, {
                  onSettled: () => navigate("/giris", { replace: true }),
                })
              }
              disabled={logout.isPending}
            >
              Çıkış yap
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-5">{children}</main>
      </div>
    </div>
  );
}
