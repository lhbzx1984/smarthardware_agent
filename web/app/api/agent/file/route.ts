import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const runId = sp.get("run_id");
  const path = sp.get("path");
  if (!runId || !path) return Response.json({ content: "" }, { status: 400 });
  try {
    const r = await fetch(
      `${BASE}/api/runs/${encodeURIComponent(runId)}/file?path=${encodeURIComponent(path)}`,
      { cache: "no-store" }
    );
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (e) {
    return Response.json({ content: "", error: (e as Error).message });
  }
}
