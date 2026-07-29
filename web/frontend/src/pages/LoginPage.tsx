import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { Link, useNavigate } from "../router";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: { email, password },
      });
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      navigate("/loglar", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "error", "Sunucuya ulaşılamadı."));
    } finally {
      setBusy(false);
    }
  }

  const pending = error?.code === "pending_approval";

  return (
    <div className="grid min-h-screen place-items-center bg-deep p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-border bg-card p-6"
      >
        <div className="eyebrow">RoadVision</div>
        <h1 className="mb-5 text-lg font-semibold tracking-tight">
          Web Paneline giriş
        </h1>

        {error && (
          <div
            className={
              "mb-4 rounded-lg border px-3 py-2 text-sm " +
              (pending
                ? "border-warning/40 bg-warning-bg text-warning"
                : "border-danger/40 bg-danger-bg text-danger")
            }
          >
            {error.message}
          </div>
        )}

        <label className="mb-3 block">
          <span className="eyebrow">E-posta</span>
          <input
            className="field mt-1"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            required
          />
        </label>
        <label className="mb-5 block">
          <span className="eyebrow">Parola</span>
          <input
            className="field mt-1"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        <button className="btn-accent w-full" disabled={busy}>
          {busy ? "Giriş yapılıyor…" : "Giriş yap"}
        </button>

        <p className="mt-4 text-center text-sm text-muted">
          Hesabınız yok mu?{" "}
          <Link className="text-accent hover:text-accent-hover" to="/kayit">
            Kayıt olun
          </Link>
        </p>
      </form>
    </div>
  );
}
