import { AppShell } from "../components/AppShell";
import { ProjectsPage } from "../features/projects/ProjectsPage";
import { ProjectWorkspacePage } from "../features/projects/ProjectWorkspacePage";
import { RunTracePage } from "../features/runs/RunTracePage";
import { Navigate, usePathname } from "./router";

export function App() {
  const pathname = usePathname();
  let page;
  if (pathname === "/") page = <Navigate to="/projects" replace />;
  else if (pathname === "/projects") page = <ProjectsPage />;
  else if (/^\/projects\/[^/]+$/.test(pathname)) page = <ProjectWorkspacePage />;
  else if (/^\/runs\/[^/]+$/.test(pathname)) page = <RunTracePage />;
  else page = <Navigate to="/projects" replace />;
  return (
    <AppShell>
      {page}
    </AppShell>
  );
}
