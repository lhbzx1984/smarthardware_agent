"""回归：① 宿主机绝对路径写文件被误判越界  ② 出图已生成但下载被自签证书挡住。"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

import config  # noqa: E402
import pathutil  # noqa: E402
import tools  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


print("== 1. 路径归一化 ==")
run = Path(tempfile.mkdtemp(prefix="pathssl_"))
root_s = str(run).replace("\\", "/")

check("剥 \\\\?\\ 前缀",
      pathutil.strip_extended(r"\\?\C:\a\b.svg") == r"C:\a\b.svg",
      pathutil.strip_extended(r"\\?\C:\a\b.svg"))
check("UNC 形态还原", pathutil.strip_extended(r"\\?\UNC\srv\share\a") == r"\\srv\share\a")
check("普通路径不受影响", pathutil.strip_extended("/10_appearance/x.svg") == "/10_appearance/x.svg")

abs_in = f"{root_s}/10_appearance/orthographic.svg"
check("工作区内绝对路径 → 相对", pathutil.normalize(abs_in, run) == "10_appearance/orthographic.svg")
check("工作区内路径归一化后不含盘符",
      not pathutil.is_foreign(pathutil.normalize(abs_in, run)))
check("带 \\\\?\\ 的本 run 绝对路径 → 相对",
      pathutil.normalize("\\\\?\\" + abs_in.replace("/", "\\"), run) == "10_appearance/orthographic.svg")
check("相对路径原样返回", pathutil.normalize("10_appearance/x.svg", run) == "10_appearance/x.svg")
check("虚拟路径原样返回", pathutil.normalize("/10_appearance/x.svg", run) == "/10_appearance/x.svg")
check("别处盘符路径 → is_foreign", pathutil.is_foreign(pathutil.normalize("D:/other/x.svg", run)))
check("UNC → is_foreign", pathutil.is_foreign(pathutil.normalize(r"\\srv\share\x.svg", run)))
check("工作区内路径不是 foreign", not pathutil.is_foreign(pathutil.normalize(abs_in, run)))
check("大小写不敏感剥前缀", pathutil.strip_extended("\\\\?\\C:\\a") == "C:\\a")

print("== 2. WorkspaceBackend 解析（模拟模型传绝对路径）==")
try:
    from deepagents.backends import FilesystemBackend  # noqa: F401
    from main_agent import WorkspaceBackend

    be = WorkspaceBackend(root_dir=str(run), virtual_mode=True)
    p = be._resolve_path(abs_in)
    check("本 run 绝对路径解析成功", str(p).replace("\\", "/").endswith("10_appearance/orthographic.svg"), str(p))
    check("解析结果落在 run 目录内", str(p).replace("\\", "/").startswith(root_s), str(p))

    p2 = be._resolve_path("\\\\?\\" + abs_in.replace("/", "\\"))
    check("带 \\\\?\\ 前缀也能解析", str(p2).replace("\\", "/").endswith("10_appearance/orthographic.svg"), str(p2))

    p3 = be._resolve_path("/home/user/10_appearance/x.svg")
    check("/home/user 折回工作区根", str(p3).replace("\\", "/").endswith("10_appearance/x.svg"), str(p3))

    try:
        be._resolve_path("D:/outside/x.svg")
        check("工作区外路径被拒绝", False, "没抛异常")
    except ValueError as e:
        check("工作区外路径被拒绝", True)
        check("拒绝信息是中文且可读", "工作区" in str(e), str(e))

    # 复现原始报错：模型把带 `\\?\` 前缀的绝对路径传进来（它多半是从上游报错里抄的），
    # 基类拿它跟 root 做字符串比较 → 明明是工作区内的文件也被判越界。
    (run / "10_appearance").mkdir(parents=True, exist_ok=True)
    base = FilesystemBackend(root_dir=str(run), virtual_mode=True)
    raw_key = "\\\\?\\" + abs_in.replace("/", "\\")
    try:
        base._resolve_path(raw_key)
        base_rejects, base_err = False, "（未抛异常）"
    except ValueError as e:
        base_rejects, base_err = True, str(e)
    check("（对照）未修复时会误判越界", base_rejects, base_err[:160])
    check("（对照）报错里的路径带 \\\\?\\ 前缀", "\\\\?\\" in base_err, base_err[:160])

    # 顺带确认：Windows 上 Path.resolve() 是否自带该前缀（决定要不要一直保留归一化）
    probe = (run / "10_appearance" / "orthographic.svg").resolve()
    print(f"       [环境] resolve() 是否带 \\\\?\\ 前缀：{'是' if str(probe).startswith(chr(92)*2 + '?') else '否'}")
except Exception as e:  # noqa: BLE001
    check("WorkspaceBackend 可构造", False, f"{type(e).__name__}: {e}")

print("== 3. tools._safe_target ==")
tmp_ws = Path(tempfile.mkdtemp(prefix="ws_"))
config.WORKSPACE = tmp_ws
sink: list[dict] = []
tools.bind_context(tmp_ws / "r1", lambda e: sink.append(e))
(run2 := tmp_ws / "r1").mkdir(parents=True, exist_ok=True)

t, err = tools._safe_target("10_appearance/render-01.png")
check("相对路径可用", t is not None and str(t).startswith(str(run2)), f"{t} {err}")
check("返回值不带 \\\\?\\ 前缀", t is not None and "?" not in str(t), str(t))
check("返回值能 relative_to 工作区", t is not None and t.relative_to(run2).as_posix() == "10_appearance/render-01.png")

abs2 = f"{str(run2).replace(chr(92), '/')}/10_appearance/orthographic.svg"
t2, err2 = tools._safe_target(abs2)
check("模型传绝对路径也能落盘", t2 is not None and t2.relative_to(run2).as_posix() == "10_appearance/orthographic.svg", f"{t2} {err2}")

t3, err3 = tools._safe_target("D:/evil/x.png")
check("工作区外路径被拒", t3 is None and "工作区" in err3, err3)
t4, err4 = tools._safe_target("../../etc/passwd")
check(".. 越界被拒", t4 is None, err4)

out = tools.save_artifact.func(path=abs2, content="<svg/>")
check("save_artifact 接受绝对路径", "已写入 10_appearance/orthographic.svg" in out, out[:120])
check("落盘事件路径是相对路径",
      any(e.get("type") == "artifact" and e.get("path") == "10_appearance/orthographic.svg" for e in sink))
check("文件真的在 run 目录里", (run2 / "10_appearance" / "orthographic.svg").exists())

print("== 4. 出图下载 TLS 降级 ==")
import imagegen  # noqa: E402

check("_is_cert_error 认 SSL 校验失败",
      imagegen._is_cert_error(RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")))
check("_is_cert_error 不认普通网络错误", not imagegen._is_cert_error(RuntimeError("getaddrinfo failed")))

chain = imagegen._ssl_chain()
check("默认链路至少 2 档", len(chain) >= 2, str([n for _, n in chain]))
check("最后一档是关闭校验", str(chain[-1][1]).startswith("关闭证书校验"), str([n for _, n in chain]))

# 模拟：前两档抛证书错误，第三档成功
calls: list[str] = []


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"PNG-BYTES"


def fake_urlopen(req, timeout=None, context=None):
    calls.append("no-ctx" if context is None else ("insecure" if getattr(context, "verify_mode", 1) == 0 else "verify"))
    if len(calls) < 3:
        raise RuntimeError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate")
    return _FakeResp()


orig = imagegen.urllib.request.urlopen
imagegen.urllib.request.urlopen = fake_urlopen
try:
    dest = run2 / "10_appearance" / "render-01.png"
    n, note = imagegen._download("https://cdn.example.com/a.png", dest)
    ok_dl = n == len(b"PNG-BYTES") and dest.exists()
    check("证书失败后自动降级并下载成功", ok_dl, f"{n} {note}")
    check("降级说明可读", "关闭证书校验" in note, note)
    check("三档都试过", len(calls) == 3, str(calls))
finally:
    imagegen.urllib.request.urlopen = orig

# 非证书错误不该无限换档
calls2: list[str] = []


def fake_urlopen2(req, timeout=None, context=None):
    calls2.append("x")
    raise RuntimeError("getaddrinfo failed")


imagegen.urllib.request.urlopen = fake_urlopen2
try:
    try:
        imagegen._download("https://cdn.example.com/a.png", run2 / "x.png", tries=3)
        check("非证书错误最终抛错", False)
    except RuntimeError as e:
        check("非证书错误最终抛错", "getaddrinfo" in str(e))
    check("非证书错误按退避重试 3 次", len(calls2) == 3, str(calls2))
finally:
    imagegen.urllib.request.urlopen = orig

print()
print(f"通过 {PASS} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
