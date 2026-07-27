"use client";

/** Sources management (M09): add a folder, reindex, see status. */

import { useCallback, useEffect, useState } from "react";

import { StatusBadge, toneForState } from "@atlas/ui";

import { DropZone } from "@/components/drop-zone";
import { api } from "@/lib/api";

interface SourceView {
  id: string;
  name: string;
  uri: string;
  status: string;
}

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceView[]>([]);
  const [name, setName] = useState("");
  const [uri, setUri] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const { data } = await api.GET("/api/v1/sources");
    if (data) {
      setSources(
        data.map((item) => ({
          id: item.id,
          name: item.name,
          uri: item.uri,
          status: item.status,
        })),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const addSource = useCallback(async () => {
    setError(null);
    const response = await api.POST("/api/v1/sources", {
      body: { kind: "folder", name, uri, config: {} },
    });
    if (response.error) {
      const problem = response.error as unknown as { detail?: unknown };
      setError(typeof problem.detail === "string" ? problem.detail : "failed to add source");
      return;
    }
    setName("");
    setUri("");
    await refresh();
  }, [name, uri, refresh]);

  const reindex = useCallback(
    async (sourceId: string) => {
      await api.POST("/api/v1/sources/{source_id}/reindex", {
        params: { path: { source_id: sourceId } },
      });
      await refresh();
    },
    [refresh],
  );

  return (
    <section data-testid="sources-screen">
      <h1 className="mb-4 text-xl font-semibold">Sources</h1>

      <DropZone onIndexed={() => void refresh()} />

      <p className="mb-2 text-xs uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Or watch a folder already on this machine
      </p>
      <form
        className="mb-6 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void addSource();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block text-xs text-zinc-500">Name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            data-testid="source-name"
            className="rounded border border-zinc-300 bg-transparent px-2 py-1.5 dark:border-zinc-700"
          />
        </label>
        <label className="flex-1 text-sm">
          <span className="mb-1 block text-xs text-zinc-500">Folder path</span>
          <input
            value={uri}
            onChange={(event) => setUri(event.target.value)}
            placeholder="/path/to/notes"
            data-testid="source-uri"
            className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1.5 dark:border-zinc-700"
          />
        </label>
        <button
          type="submit"
          data-testid="source-add"
          className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500"
        >
          Add folder
        </button>
      </form>
      {error !== null && (
        <p className="mb-4 text-sm text-red-700 dark:text-red-300">{error}</p>
      )}
      <ul className="divide-y divide-zinc-200 dark:divide-zinc-800" data-testid="source-list">
        {sources.map((source) => (
          <li key={source.id} className="flex items-center justify-between py-3 text-sm">
            <div>
              <p className="font-medium">{source.name}</p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">{source.uri}</p>
            </div>
            <div className="flex items-center gap-3">
              <StatusBadge label={source.status} tone={toneForState(source.status)} />
              <button
                type="button"
                onClick={() => void reindex(source.id)}
                className="rounded border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
              >
                Reindex
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
