// Tek fetch sarmalayıcısı: hata zarfını çözer, durum değiştiren isteklere
// rv_csrf çerezindeki belirteci X-RoadVision-CSRF başlığıyla ekler
// (double-submit; WEB_PLANI.md §8).

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function readCookie(name: string): string | null {
  const prefix = name + "=";
  for (const part of document.cookie.split(";")) {
    const item = part.trim();
    if (item.startsWith(prefix)) {
      return decodeURIComponent(item.slice(prefix.length));
    }
  }
  return null;
}

const UNSAFE = new Set(["POST", "PATCH", "PUT", "DELETE"]);

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (UNSAFE.has(method)) {
    const csrf = readCookie("rv_csrf");
    if (csrf) headers["X-RoadVision-CSRF"] = csrf;
  }
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    const err = (data as { error?: { code?: string; message?: string } })
      ?.error;
    throw new ApiError(
      response.status,
      err?.code ?? "error",
      err?.message ?? "Beklenmeyen bir hata oluştu.",
    );
  }
  return data as T;
}

export function formatTs(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString("tr-TR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
