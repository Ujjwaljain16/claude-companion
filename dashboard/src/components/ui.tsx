import { clsx, type ClassValue } from "clsx";
import Link from "next/link";
import { twMerge } from "tailwind-merge";
import type { Tone } from "@/lib/format";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return <section className={cn("rounded-xl border border-line bg-card p-5 shadow-sm", className)}>{children}</section>;
}

export function CardTitle({ children, hint }: { children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="text-sm font-semibold text-fg">{children}</h2>
      {hint ? <span className="text-xs text-muted">{hint}</span> : null}
    </div>
  );
}

const toneClass: Record<Tone, string> = {
  ok: "bg-ok/12 text-ok ring-ok/25",
  warn: "bg-warn/12 text-warn ring-warn/30",
  bad: "bg-bad/12 text-bad ring-bad/25",
  info: "bg-accent/12 text-accent ring-accent/25",
  idle: "bg-muted/10 text-muted ring-muted/25",
};

export function Badge({ tone = "idle", children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", toneClass[tone])}>
      {children}
    </span>
  );
}

export function Button({ children, variant = "primary", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  return (
    <button
      {...props}
      className={cn(
        "inline-flex items-center rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50",
        variant === "primary" && "bg-accent text-white hover:bg-accent/90",
        variant === "ghost" && "border border-line text-fg hover:bg-fg/5",
        variant === "danger" && "border border-bad/40 text-bad hover:bg-bad/10",
        props.className,
      )}
    >
      {children}
    </button>
  );
}

export function LinkButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link href={href} className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-sm font-medium text-fg hover:bg-fg/5">
      {children}
    </Link>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 flex items-center gap-2 text-2xl font-semibold tabular-nums text-fg">
        {value}
        {tone ? <span className={cn("size-2 rounded-full", { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", info: "bg-accent", idle: "bg-muted" }[tone])} /> : null}
      </div>
      {sub ? <div className="mt-1 text-xs text-muted">{sub}</div> : null}
    </Card>
  );
}

export function Table({ head, children, empty }: { head: React.ReactNode[]; children: React.ReactNode; empty?: string }) {
  const rows = Array.isArray(children) ? children.filter(Boolean) : children;
  const isEmpty = Array.isArray(rows) && rows.length === 0;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{rows}</tbody>
      </table>
      {isEmpty ? <p className="px-3 py-6 text-center text-sm text-muted">{empty ?? "Nothing here yet."}</p> : null}
    </div>
  );
}

export function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn("px-3 py-2 align-top text-fg", className)}>{children}</td>;
}

export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions}
    </header>
  );
}

export function GatewayError({ error }: { error: string }) {
  return (
    <Card className="border-bad/40">
      <CardTitle>Gateway unavailable</CardTitle>
      <p className="text-sm text-muted">{error}</p>
      <pre className="mt-3 rounded-md bg-fg/5 p-3 text-xs text-fg">uv run companion-gateway</pre>
    </Card>
  );
}

export function Code({ children }: { children: React.ReactNode }) {
  return <code className="rounded bg-fg/5 px-1.5 py-0.5 font-mono text-xs text-fg">{children}</code>;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="max-h-80 overflow-auto rounded-md bg-fg/5 p-3 font-mono text-xs text-fg">{JSON.stringify(value, null, 2)}</pre>;
}
