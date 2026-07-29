import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from "react";

interface NavigateOptions {
  replace?: boolean;
}

type NavigateFn = (to: string, options?: NavigateOptions) => void;

interface RouterValue {
  pathname: string;
  navigate: NavigateFn;
}

const RouterContext = createContext<RouterValue | null>(null);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [pathname, setPathname] = useState(window.location.pathname);

  useEffect(() => {
    const onPopState = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = useCallback<NavigateFn>((to, options) => {
    if (options?.replace) {
      window.history.replaceState(null, "", to);
    } else {
      window.history.pushState(null, "", to);
    }
    setPathname(window.location.pathname);
  }, []);

  const value = useMemo(
    () => ({ pathname, navigate }),
    [navigate, pathname],
  );
  return (
    <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
  );
}

function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error("RouterProvider bulunamadı");
  return value;
}

export function usePathname(): string {
  return useRouter().pathname;
}

export function useNavigate(): NavigateFn {
  return useRouter().navigate;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
};

export function Link({ to, onClick, ...props }: LinkProps) {
  const navigate = useNavigate();

  function follow(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      props.target === "_blank"
    ) {
      return;
    }
    event.preventDefault();
    navigate(to);
  }

  return <a {...props} href={to} onClick={follow} />;
}

type NavLinkProps = Omit<LinkProps, "className"> & {
  className?: string | ((state: { isActive: boolean }) => string);
};

export function NavLink({ className, to, ...props }: NavLinkProps) {
  const pathname = usePathname();
  const resolvedClass =
    typeof className === "function"
      ? className({ isActive: pathname === to })
      : className;
  return <Link {...props} to={to} className={resolvedClass} />;
}

export function Navigate({
  to,
  replace = false,
}: {
  to: string;
  replace?: boolean;
}) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace }), [navigate, replace, to]);
  return null;
}
