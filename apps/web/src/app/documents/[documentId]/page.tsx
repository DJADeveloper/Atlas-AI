import { SourceViewer } from "@/components/source-viewer";

export default async function DocumentPage({
  params,
  searchParams,
}: {
  params: Promise<{ documentId: string }>;
  searchParams: Promise<{ chunk?: string }>;
}) {
  const { documentId } = await params;
  const { chunk } = await searchParams;
  return <SourceViewer documentId={documentId} highlightChunkId={chunk ?? null} />;
}
