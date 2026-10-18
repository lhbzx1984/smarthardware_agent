import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

/**
 * 历史会话代理。一个文件覆盖三种读法，避免动态段在不同 Next 版本下的签名差异：
 *   GET /api/agent/runs                  -> 会话列表
 *   GET /api/agent/runs?run_id=x         -> 单个会话详情（含产物清单）
 *   GET /api/agent/runs?run_id=x&events=1&after=N -> 事件重放
 *   DELETE /api/agent/runs?run_id=x      -> 从历史移除（磁盘产物保留）
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const runId = sp.get("run_id");

  try {
    if (runId) {
      if (sp.get("events") === "1") {
        const after = sp.get("after") || "0";
        const r = await fetch(
          `${BASE}/api/runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(after)}`,
          { cache: "no-store" },
        );
        return new Response(await r.text(), {
          status: r.status,
          headers: { "Content-Type": "application/json" },
        });
      }
      const r = await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
      return new Response(await r.text(), {
        status: r.status,
        headers: { "Content-Type": "application/json" },
      });
    }

    const limit = sp.get("limit") || "50";
    const q = sp.get("q") || "";
    const r = await fetch(
      `${BASE}/api/runs?limit=${encodeURIComponent(limit)}&q=${encodeURIComponent(q)}`,
      { cache: "no-store" },
    );
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ runs: [], error: (e as Error).message }, { status: 200 });
  }
}

export async function DELETE(req: NextRequest) {
  const runId = req.nextUrl.searchParams.get("run_id");
  if (!runId) return Response.json({ error: "缺少 run_id" }, { status: 400 });
  try {
    const r = await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}

/**
 * POST /api/agent/runs?run_id=x&action=cancel  -> 停止正在跑的会话
 * 写操作也走 query 参数，与读取保持同一套路由约定。
 */
export async function POST(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const runId = sp.get("run_id");
  const action = sp.get("action");
  if (action !== "cancel") return Response.json({ error: "未知操作" }, { status: 400 });
  if (!runId) return Response.json({ error: "缺少 run_id" }, { status: 400 });
  try {
    const r = await fetch(`${BASE}/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 502 });
  }
}
