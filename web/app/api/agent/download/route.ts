import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

/**
 * 下载代理：单文件 / 按阶段打包 / 全量打包。
 * Content-Disposition 必须原样透传，否则浏览器不会另存（只会内联预览）。
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const runId = sp.get("run_id");
  if (!runId) return new Response("missing run_id", { status: 400 });

  const qs = new URLSearchParams();
  const path = sp.get("path");
  const stage = sp.get("stage");
  const scope = sp.get("scope");
  if (path) qs.set("path", path);
  if (stage) qs.set("stage", stage);
  if (scope) qs.set("scope", scope);

  try {
    const r = await fetch(
      `${BASE}/api/runs/${encodeURIComponent(runId)}/download?${qs.toString()}`,
      { cache: "no-store" }
    );
    if (!r.ok) return new Response(await r.text(), { status: r.status });
    const headers: Record<string, string> = {
      "Content-Type": r.headers.get("content-type") || "application/octet-stream",
    };
    const cd = r.headers.get("content-disposition");
    if (cd) headers["Content-Disposition"] = cd;
    const buf = await r.arrayBuffer();
    return new Response(buf, { status: 200, headers });
  } catch (e) {
    return new Response((e as Error).message, { status: 502 });
  }
}
