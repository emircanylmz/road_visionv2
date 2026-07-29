// Oturum bağlamı: /api/auth/me tek doğruluk kaynağıdır; 401'de giriş
// sayfasına yönlendirilir. Devre dışı bırakılan kullanıcı bir sonraki
// istekte otomatik düşer (sunucu oturumu iptal eder).

import { createContext, useContext, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import { Navigate, usePathname } from "./router";
import type { MeResponse, UserInfo } from "./types";

const AuthContext = createContext<UserInfo | null>(null);

export function useUser(): UserInfo {
  const user = useContext(AuthContext);
  if (!user) throw new Error("useUser yalnız AuthGate altında kullanılır");
  return user;
}

export function useMe() {
  return useQuery<MeResponse, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<MeResponse>("/api/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/auth/logout", { method: "POST" }),
    onSettled: () => queryClient.clear(),
  });
}

export function AuthGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const me = useMe();
  if (me.isPending) {
    return (
      <div className="grid min-h-screen place-items-center bg-deep">
        <div className="eyebrow">RoadVision yükleniyor…</div>
      </div>
    );
  }
  if (me.isError || !me.data) {
    return pathname === "/giris" ? null : <Navigate to="/giris" replace />;
  }
  return (
    <AuthContext.Provider value={me.data.user}>{children}</AuthContext.Provider>
  );
}

export function RequireAdmin({ children }: { children: ReactNode }) {
  const user = useUser();
  if (user.role !== "admin") return <Navigate to="/loglar" replace />;
  return <>{children}</>;
}
