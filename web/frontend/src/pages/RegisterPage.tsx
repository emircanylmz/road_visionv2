import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Link } from "../router";

export function RegisterPage() {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/register", {
        method: "POST",
        body: { email, full_name: fullName, password },
      });
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Sunucuya ulaşılamadı.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-deep p-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-6">
        <div className="eyebrow">RoadVision</div>
        <h1 className="mb-5 text-lg font-semibold tracking-tight">
          Üyelik başvurusu
        </h1>

        {done ? (
          <div className="rounded-lg border border-ok-border bg-ok-bg px-3 py-3 text-sm text-ok-text">
            Kayıt alındı. Hesabınız yönetici onayından sonra açılacak;
            onaylandığında <Link className="underline" to="/giris">giriş yapabilirsiniz</Link>.
          </div>
        ) : (
          <form onSubmit={submit}>
            {error && (
              <div className="mb-4 rounded-lg border border-danger/40 bg-danger-bg px-3 py-2 text-sm text-danger">
                {error}
              </div>
            )}
            <label className="mb-3 block">
              <span className="eyebrow">Ad soyad</span>
              <input
                className="field mt-1"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                minLength={2}
                required
              />
            </label>
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
              <span className="eyebrow">Parola (en az 10 karakter)</span>
              <input
                className="field mt-1"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={10}
                autoComplete="new-password"
                required
              />
            </label>
            <button className="btn-accent w-full" disabled={busy}>
              {busy ? "Gönderiliyor…" : "Başvuruyu gönder"}
            </button>
          </form>
        )}

        <p className="mt-4 text-center text-sm text-muted">
          Zaten üye misiniz?{" "}
          <Link className="text-accent hover:text-accent-hover" to="/giris">
            Giriş yapın
          </Link>
        </p>
      </div>
    </div>
  );
}
