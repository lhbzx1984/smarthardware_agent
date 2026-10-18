export const dynamic = "force-dynamic";

const BASE = process.env.AGENT_WORKER_URL || "http://127.0.0.1:8001";

export async function GET() {
  try {
    const r = await fetch(`${BASE}/health`, { cache: "no-store" });
    return new Response(await r.text(), {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ ok: false, mode: "offline", error: (e as Error).message });
  }
}
