"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "./ui";

const LINKS = [
  ["/", "Overview"],
  ["/desktop", "Claude Desktop"],
  ["/tools", "MCP Tools"],
  ["/providers", "Providers"],
  ["/models", "Models"],
  ["/agents", "Agents"],
  ["/workflows", "Agent Workflows"],
  ["/evaluations", "Evaluations"],
  ["/datasets", "Datasets"],
  ["/experiments", "Experiments"],
  ["/traces", "Traces"],
  ["/usage", "Usage"],
  ["/settings", "Settings"],
] as const;

export function Nav() {
  const path = usePathname();
  return (
    <nav className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-line bg-card px-3 py-5">
      <div className="mb-6 px-2">
        <div className="text-sm font-semibold text-fg">Claude Companion</div>
        <div className="text-xs text-muted">local AI gateway</div>
      </div>
      <ul className="space-y-0.5">
        {LINKS.map(([href, label]) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                className={cn(
                  "block rounded-md px-2 py-1.5 text-sm",
                  active ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-fg/5 hover:text-fg",
                )}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
