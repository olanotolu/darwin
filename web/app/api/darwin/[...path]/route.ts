// Same-origin proxy to the DARWIN FastAPI backend. The backend sends no CORS
// headers (and we do not change backend behaviour), so the browser talks to
// Next and Next forwards server-side. Upstream base is read at REQUEST time:
//   DARWIN_API (server-only) > NEXT_PUBLIC_DARWIN_API > http://127.0.0.1:8000
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function upstream(): string {
  return (process.env.DARWIN_API || process.env.NEXT_PUBLIC_DARWIN_API || "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const base = upstream();
  const url = `${base}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const init: RequestInit = { method: req.method, headers: { "content-type": "application/json" }, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") init.body = await req.text();
  try {
    const r = await fetch(url, init);
    const body = await r.text();
    return new Response(body, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "application/json", "x-darwin-upstream": base },
    });
  } catch (e) {
    // Connection refused while uvicorn is still fitting the startup model, or backend down.
    return Response.json(
      { error: "backend_unreachable", upstream: base, detail: String((e as Error)?.cause ?? e) },
      { status: 503, headers: { "x-darwin-upstream": base } },
    );
  }
}

export const GET = forward;
export const POST = forward;
