import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

/**
 * 续跑：复用已有会话的产物，从指定阶段（默认下一个未完成阶段）接着往下做。
 * 智能硬件各阶段是串行依赖链，所以「接着做」比「重跑一遍」更合理。
 */
export async function POST(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) return Response.json({ error: "missing run_id" }, { status: 400 });
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  try {
    const r = await fetch(
      `${BASE}/api/runs/${encodeURIComponent(runId)}/resume`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      }
    );
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
