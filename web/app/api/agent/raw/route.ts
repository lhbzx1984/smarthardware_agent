import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

/** 二进制产物代理：渲染图 / 爆炸图 / 预览 SVG / STEP / STL 直接透传字节流 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const runId = sp.get("run_id");
  const path = sp.get("path");
  if (!runId || !path) return new Response("missing run_id / path", { status: 400 });

  const qs = new URLSearchParams({ path });
  const base = sp.get("base");
  if (base) qs.set("base", base);

  try {
    const r = await fetch(
      `${BASE}/api/runs/${encodeURIComponent(runId)}/raw?${qs.toString()}`,
      { cache: "no-store" }
    );
    if (!r.ok) return new Response("not found", { status: r.status });
    const type = r.headers.get("content-type") || "application/octet-stream";
    return new Response(await r.arrayBuffer(), {
      status: 200,
      headers: { "Content-Type": type, "Cache-Control": "public, max-age=300" },
    });
  } catch (e) {
    return new Response((e as Error).message, { status: 502 });
  }
}
