import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { NavLinks, ThemeToggle } from "@/components/nav";

export const metadata: Metadata = {
  title: "Atlas",
  description: "Local-first AI OS — chat over your own documents.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-white text-zinc-900 antialiased dark:bg-zinc-950 dark:text-zinc-100">
        <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-4">
          <header className="flex items-center justify-between border-b border-zinc-200 py-3 dark:border-zinc-800">
            <div className="flex items-center gap-6">
              <span className="text-lg font-semibold tracking-tight">Atlas</span>
              <NavLinks />
            </div>
            <ThemeToggle />
          </header>
          <main className="flex-1 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
