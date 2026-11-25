import type { Metadata } from "next";
import { Suspense } from "react";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Claude Companion",
  description: "Local AI gateway dashboard",
};

// Every page reads live gateway state at request time, so the shell
// prerenders and the content streams in under these boundaries.
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <div className="flex min-h-screen">
          <Suspense fallback={<div className="w-56 shrink-0 border-r border-line bg-card" />}>
            <Nav />
          </Suspense>
          <main className="min-w-0 flex-1 px-8 py-7">
            <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>{children}</Suspense>
          </main>
        </div>
      </body>
    </html>
  );
}
