import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

/**
 * 人工确认闸门。
 *
 * GET  查当前有没有在等人的阶段（刷新页面后靠它把确认面板恢复出来）
 * POST 提交结论：approve = 放行下游；revise = 带着反馈重做本阶段
 *
 * 外观与结构是整条链的基线：用户没看过就往下建结构 / 画 PCB，
 * 一旦要改就是全链返工，所以这两步必须停下来等人。
 */
async function proxy(runId: string, init?: RequestInit) {
  const r = await fetch(
    `${BASE}/api/runs/${encodeURIComponent(runId)}/review`,
    { cache: "no-store", ...init },
  );
  return new Response(await r.text(), {
    status: r.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function GET(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) return Response.json({ pending: null }, { status: 200 });
  try {
    return await proxy(runId);
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}

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
    return await proxy(runId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
