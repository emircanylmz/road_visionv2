import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import { AuthGate, RequireAdmin } from "./auth";
import { Layout } from "./components/Layout";
import { AdminPage } from "./pages/AdminPage";
import { ArchivePage } from "./pages/ArchivePage";
import { LoginPage } from "./pages/LoginPage";
import { LogsPage } from "./pages/LogsPage";
import { RegisterPage } from "./pages/RegisterPage";
import { VerifyPage } from "./pages/VerifyPage";
import {
  Navigate,
  RouterProvider,
  usePathname,
} from "./router";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

function AppRoutes() {
  const pathname = usePathname();
  if (pathname === "/giris") return <LoginPage />;
  if (pathname === "/kayit") return <RegisterPage />;
  if (
    pathname !== "/loglar" &&
    pathname !== "/arsiv" &&
    pathname !== "/dogrulama" &&
    pathname !== "/yonetim"
  ) {
    return <Navigate to="/loglar" replace />;
  }
  const page =
    pathname === "/yonetim" ? (
      <RequireAdmin>
        <AdminPage />
      </RequireAdmin>
    ) : pathname === "/arsiv" ? (
      <ArchivePage />
    ) : pathname === "/dogrulama" ? (
      <VerifyPage />
    ) : (
      <LogsPage />
    );
  return (
    <AuthGate>
      <Layout>{page}</Layout>
    </AuthGate>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider>
        <AppRoutes />
      </RouterProvider>
    </QueryClientProvider>
  </StrictMode>,
);
