import type { PropsWithChildren } from "react";
import { Link, NavLink } from "../app/router";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/projects" className="brand" aria-label="Epiphany Studio 首页">
          <span className="brand-mark" aria-hidden="true">✦</span>
          <span>
            <strong>Epiphany Studio</strong>
            <small>生活素材与表达工作台</small>
          </span>
        </Link>
        <nav aria-label="主导航">
          <NavLink to="/projects">Projects</NavLink>
          <a href="/api/docs" target="_blank" rel="noreferrer">API</a>
        </nav>
      </header>
      <main>{children}</main>
    </div>
  );
}
