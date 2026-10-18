import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) return Response.json({ files: [] });
  try {
    const r = await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/files`, { cache: "no-store" });
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ files: [], error: (e as Error).message });
  }
}
