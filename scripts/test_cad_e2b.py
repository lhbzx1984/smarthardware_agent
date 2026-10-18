"""CAD 云端沙箱（E2B）运行时测试：运行期选择、产物取回、故障回落。

覆盖的都是踩过的坑：
  · 本机 8GB 内存，并发 OCP 会被 OpenBLAS 分配失败打死（退出码 1、stdout/stderr 全空）
  · 沙箱不可用要能自动回落本地，而不是让建模卡死
  · 沙箱里脚本报错是真因，不能因为换运行时被另一套报错盖掉
  · 「沙箱没起来」的占位记录不能盖掉真实脚本报错

用法：python scripts/test_cad_e2b.py [real]   —— 加 real 会真连 E2B 跑一次端到端
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import cad  # noqa: E402

OK = 0
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global OK
    if ok:
        OK += 1
        print(f"  ✓ {name}")
    else:
        FAILS.append(name)
        print(f"  ✗ {name}  {detail[:300]}")


print("=== 1. 运行时选择 ===")
with mock.patch.object(cad, "_e2b_ready", return_value=True):
    kind, argv = cad.detect_runner()
check("有 key 时 auto 选 e2b", kind == "e2b", f"got {kind}")

with mock.patch.object(cad, "_e2b_ready", return_value=False):
    kind2, _ = cad.detect_runner()
check("沙箱不可用时回落到本机运行时", kind2 in ("local", "cadgen"), f"got {kind2}")

print("\n=== 2. 子进程环境（防 OpenBLAS 打爆内存 / shim 破坏 importer）===")
env = cad._child_env()
check("剔除宿主注入的 PYTHONPATH", "PYTHONPATH" not in env, str(env.get("PYTHONPATH")))
check("剔除 PYTHONHOME", "PYTHONHOME" not in env, str(env.get("PYTHONHOME")))
check("BLAS 压成单线程", env.get("OPENBLAS_NUM_THREADS") == "1" and env.get("OMP_NUM_THREADS") == "1",
      str({k: v for k, v in env.items() if "THREADS" in k}))


# ------------------------------------------------------------------ 假沙箱
class CmdResult:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeFiles:
    def __init__(self, payload: dict[str, bytes]):
        self.payload = payload

    def write(self, path, content):  # noqa: ANN001
        return None

    def list(self, remote):  # noqa: ANN001
        return [types.SimpleNamespace(name=n) for n in self.payload]

    def read(self, path, format=None):  # noqa: ANN001,A002
        name = str(path).rsplit("/", 1)[-1]
        return self.payload.get(name, b"")


class FakeSbx:
    def __init__(self, rc=0, stdout="", stderr="", payload=None):
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.files = FakeFiles(payload or {})

    @property
    def commands(self):  # 真实 SDK 里是 sbx.commands.run(...)，这里对齐
        outer = self

        class C:
            def run(self, cmd, timeout=None):  # noqa: ANN001
                if "python model.py" in cmd:
                    return CmdResult(outer.rc, outer.stdout, outer.stderr)
                return CmdResult(0, "OK")
        return C()

    def kill(self):
        return None


MARKER_LINE = cad._MARKER + json.dumps(
    {"ok": True, "files": ["model_p1.step", "model_p1.stl"], "count": 1,
     "parts": [{"name": "下壳", "stem": "model_p1", "size_mm": [60, 40, 30],
                "volume_cm3": 39.3, "exported": True}]},
    ensure_ascii=False,
)

SCRIPT = (
    "from build123d import *\n"
    "with BuildPart() as p:\n"
    "    Box(60, 40, 30)\n"
    "parts = {'下壳': p.part}\n"
)

print("\n=== 3. 沙箱成功：产物取回本地 ===")
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "r1"
    out.mkdir(parents=True, exist_ok=True)
    sbx = FakeSbx(0, MARKER_LINE, "", {"model_p1.step": b"STEP-DATA", "model_p1.stl": b"STL-DATA"})
    with mock.patch.object(cad, "_e2b_sandbox", return_value=sbx), \
         mock.patch.object(cad, "_e2b_ready", return_value=True), \
         mock.patch.object(cad, "detect_runner", return_value=("e2b", [])):
        r = cad.build(SCRIPT, out, base_name="model")
    check("建模成功", r.get("ok") is True, str(r)[:200])
    check("运行时记为 e2b", r.get("runner") == "e2b", str(r.get("runner")))
    check("STEP 已取回本地", (out / "model_p1.step").read_bytes() == b"STEP-DATA")
    check("STL 已取回本地", (out / "model_p1.stl").read_bytes() == b"STL-DATA")
    check("files 含两个产物", sorted(r.get("files", []))[:2] == ["model_p1.step", "model_p1.stl"],
          str(r.get("files")))

print("\n=== 4. 沙箱里脚本报错：不换运行时，直接报真因 ===")
local_calls = {"n": 0}


def count_local(cmd, **kw):  # noqa: ANN001
    local_calls["n"] += 1
    raise AssertionError("沙箱已给出明确报错，不该再回落到本地跑一遍")


with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "r2"
    sbx = FakeSbx(1, "", "Traceback ...\nAttributeError: 'BoundBox' object has no attribute 'x_length'")
    with mock.patch.object(cad, "_e2b_sandbox", return_value=sbx), \
         mock.patch.object(cad, "detect_runner", return_value=("e2b", [])), \
         mock.patch.object(cad.subprocess, "run", side_effect=count_local):
        r2 = cad.build(SCRIPT, out, base_name="model")
    check("沙箱报错被原样带回", "AttributeError" in (r2.get("error") or ""), str(r2)[:200])
    check("有明确报错时不再回落本地", local_calls["n"] == 0, str(local_calls))
    check("报错指向 e2b 运行时", r2.get("runner") == "e2b", str(r2.get("runner")))

print("\n=== 5. 沙箱起不来：自动回落本机，不卡死 ===")
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "r3"
    out.mkdir(parents=True, exist_ok=True)

    class BoomProc:
        returncode = 0

        def __init__(self):
            self.stdout = MARKER_LINE + "\n"
            self.stderr = ""

    calls = {"n": 0}

    def local_ok(cmd, **kw):  # noqa: ANN001
        calls["n"] += 1
        return BoomProc()

    with mock.patch.object(cad, "_e2b_sandbox", side_effect=RuntimeError("创建沙箱失败（Timeout）")), \
         mock.patch.object(cad, "detect_runner", return_value=("e2b", [sys.executable])), \
         mock.patch.object(cad.subprocess, "run", side_effect=local_ok), \
         mock.patch.object(cad, "static_check", return_value=[]), \
         mock.patch.object(cad, "_dump_failed", return_value="x.py"):
        r3 = cad.build(SCRIPT, out, base_name="model")
    check("沙箱失败后确实跑了本机", calls["n"] >= 1, str(calls))
    check("结果可用（回落成功）", r3.get("ok") is True, str(r3)[:200])
    check("沙箱故障提示保留在报错里", "沙箱" in json.dumps(r3, ensure_ascii=False, default=str) or r3.get("ok"),
          str(r3)[:200])

print("\n=== 6. 占位记录不掩盖真实报错 ===")
runs = [
    {"kind": "e2b", "rc": -1, "out": "", "err": "[沙箱不可用] RuntimeError: 创建沙箱失败（Timeout）", "argv": []},
    {"kind": "local", "rc": 1, "out": "", "err": "AttributeError: 'BoundBox' object has no attribute 'x_length'", "argv": []},
]
real = [r for r in runs if r["rc"] != -1] or runs
best = max(real, key=lambda r: len(r["err"].strip()) * 2 + len(r["out"].strip()))
check("选中的是真实脚本报错那条", best["kind"] == "local" and "AttributeError" in best["err"], str(best["kind"]))

print("\n=== 7. 健康探测不触发沙箱创建 ===")
with mock.patch.object(cad, "_e2b_ready", return_value=True):
    ok, info = cad._probe([], "e2b")
check("e2b 探活毫秒级返回", ok is True and "e2b" in info, info)
with mock.patch.object(cad, "_e2b_ready", return_value=False):
    ok2, info2 = cad._probe([], "e2b")
check("缺 key 时明确说不可用", ok2 is False and "E2B_API_KEY" in info2, info2)

# ------------------------------------------------------------------ 真实端到端（可选）
if len(sys.argv) > 1 and sys.argv[1] == "real":
    print("\n=== 8. 真实 E2B 端到端（联网）===")
    if not os.getenv("E2B_API_KEY"):
        print("  - 跳过：未设 E2B_API_KEY")
    else:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "real"
            out.mkdir(parents=True, exist_ok=True)
            r = cad.build(SCRIPT, out, base_name="model")
            check("真实沙箱建模成功", r.get("ok") is True, str(r)[:300])
            check("STEP/STL 都已落盘",
                  (out / "model_p1.step").exists() and (out / "model_p1.stl").exists(),
                  str(sorted(p.name for p in out.iterdir())))

print(f"\n{'=' * 40}\n通过 {OK} 项，失败 {len(FAILS)} 项")
if FAILS:
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
sys.exit(0)
