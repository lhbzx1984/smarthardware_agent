import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) return Response.json({ detail: "缺少 run_id" }, { status: 400 });
  try {
    const r = await fetch(`${BASE}/api/stream/${encodeURIComponent(runId)}`, {
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
    });
    if (!r.ok || !r.body) {
      return Response.json({ detail: `worker 返回 ${r.status}` }, { status: 502 });
    }
    return new Response(r.body, {
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch (e) {
    return Response.json({ detail: (e as Error).message }, { status: 502 });
  }
}
