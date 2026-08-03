import {
  createContext,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

interface RouterContextValue {
  pathname: string;
  navigate: (to: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

function currentPathname() {
  return window.location.pathname.replace(/\/$/, "") || "/";
}

export function RouterProvider({ children }: PropsWithChildren) {
  const [pathname, setPathname] = useState(currentPathname);
  useEffect(() => {
    const onPopState = () => setPathname(currentPathname());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    const next = to.replace(/\/$/, "") || "/";
    if (options?.replace) window.history.replaceState(null, "", next);
    else window.history.pushState(null, "", next);
    setPathname(currentPathname());
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);
  const value = useMemo(() => ({ pathname, navigate }), [navigate, pathname]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

function useRouter() {
  const context = useContext(RouterContext);
  if (!context) throw new Error("router hooks require RouterProvider");
  return context;
}

export function usePathname() {
  return useRouter().pathname;
}

export function useNavigate() {
  return useRouter().navigate;
}

export function useParams(): { projectId?: string; runId?: string } {
  const pathname = usePathname();
  const project = pathname.match(/^\/projects\/([^/]+)$/);
  if (project) return { projectId: decodeURIComponent(project[1]) };
  const run = pathname.match(/^\/runs\/([^/]+)$/);
  if (run) return { runId: decodeURIComponent(run[1]) };
  return {};
}

interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
}

export function Link({ to, onClick, target, ...props }: LinkProps) {
  const navigate = useNavigate();
  function follow(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
      target === "_blank"
    ) return;
    event.preventDefault();
    navigate(to);
  }
  return <a {...props} href={to} target={target} onClick={follow} />;
}

export function NavLink({ to, className, ...props }: LinkProps) {
  const pathname = usePathname();
  const active = pathname === to || (to !== "/" && pathname.startsWith(`${to}/`));
  return (
    <Link
      {...props}
      to={to}
      className={[typeof className === "string" ? className : "", active ? "active" : ""]
        .filter(Boolean)
        .join(" ")}
    />
  );
}

export function Navigate({ to, replace = false }: { to: string; replace?: boolean }) {
  const navigate = useNavigate();
  useEffect(() => navigate(to, { replace }), [navigate, replace, to]);
  return null;
}
