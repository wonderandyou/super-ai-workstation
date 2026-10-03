#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 制作 —— 工作站后端模块（2026-10-02）
=============================================
把「立绘 → 分层 PSD → Live2D 模型(moc3)」整条链路包成一个后台任务。

拆层两种方式（界面二选一）：
    local   本地引擎   —— 本机显卡跑 See-Through（C:\\SeeThrough\\venv）
    online  在线官方演示 —— 作者官方 ModelScope 免费演示（需登录态，见 live2d/ms_token.py）

后面两步固定走本机 PSD2Live 的 MCP：导入 PSD → 导出 moc3/cmo3。

只用标准库；真正的实现在 live2d/ 目录里（seethrough_client / seethrough_local /
psd2live_mcp / live2d_pipeline）。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

BASE = os.path.dirname(os.path.abspath(__file__))
LIVE2D_DIR = os.path.join(BASE, "live2d")
if LIVE2D_DIR not in sys.path:
    sys.path.insert(0, LIVE2D_DIR)

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
MAT_DIR = os.path.join(DESKTOP, "Live2D素材")
OUT_DIR = os.path.join(DESKTOP, "Live2D成品")
INPUT_DIR = os.path.join(MAT_DIR, "_输入")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_MB = 40

import paths as _paths          # ★ 统一路径层
VENV_PY = _paths.seethrough_py()
LOCAL_RUNNER = os.path.join(LIVE2D_DIR, "seethrough_local.py")
LOCAL_MODELS = _paths.seethrough_models()
PSD2LIVE_EXE = os.environ.get(
    "PSD2LIVE_EXE", r"D:\AI工作站\downloads\PSD2Live-1.6.0\PSD2Live\PSD2Live.exe")
PSD2LIVE_PORT = 23871

JOBS = {}
_LOCK = threading.Lock()

# ★ 任务留档（2026-10-02 踩过：任务状态只在内存里，工作站一重启就全丢 →
#   前端只剩一句红色的「没有这个任务」，用户不知道发生了什么）
JOBS_DIR = os.path.join(BASE, "data", "live2d_jobs")


def _persist(job):
    """把任务快照写盘（进度、日志尾巴），重启后还能看出「刚才跑到哪、为什么断的」"""
    try:
        os.makedirs(JOBS_DIR, exist_ok=True)
        rec = {}
        for k in ("id", "state", "stage", "pct", "text", "error", "engine", "name",
                  "image", "outDir", "matDir", "files", "psd", "t0", "elapsed"):
            rec[k] = job.get(k)
        rec["log"] = (job.get("log") or [])[-80:]
        rec["savedAt"] = time.time()
        tmp = os.path.join(JOBS_DIR, job["id"] + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(JOBS_DIR, job["id"] + ".json"))
    except Exception:
        pass


def _load_persisted(jid):
    if not jid or "/" in jid or "\\" in jid or ".." in jid:
        return None
    p = os.path.join(JOBS_DIR, jid + ".json")
    if not os.path.isfile(p):
        return None
    try:
        # ★ utf-8-sig：别的工具（PowerShell）写的 JSON 可能带 BOM，
        #   用普通 utf-8 读会直接 json 报错 → 留档就白留了 ✗
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def running_jobs():
    """还有几个任务在跑（重启工作站前先看这个！）"""
    with _LOCK:
        return [{"id": j["id"], "name": j.get("name"), "engine": j.get("engine"),
                 "state": j.get("state"), "pct": j.get("pct"), "text": j.get("text")}
                for j in JOBS.values() if j.get("state") in ("queued", "running")]


# ---------------------------------------------------------------------------
#  小工具
# ---------------------------------------------------------------------------
def _port_open(port, host="127.0.0.1", timeout=0.8):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  显存看门（★ 2026-10-02 踩过：ComfyUI 常驻 ~7 GB，本地引擎只剩 1.6 GB →
#  4bit 量化那步直接 0xC0000005 硬崩，日志里只有一句「退出码 3221225477」）
# ---------------------------------------------------------------------------
NEED_VRAM_MB = 5600          # 本地引擎峰值实测 5.78 GB
COMFY_PORT_DEFAULT = 8188


def gpu_free_mb():
    """当前显卡空闲显存（MB）；查不到返回 None"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def comfy_loaded_mb():
    """ComfyUI 占的显存（MB）；拿不到返回 None"""
    return None if gpu_free_mb() is None else gpu_free_mb()   # 只是语义别名，见下


def gpu_consumers():
    """当前占着显卡的计算进程名（Windows 下查不到每个占多少显存，只能列出是谁）"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name",
                              "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 2:
            continue
        nm = os.path.basename(parts[1]) or parts[0]
        if nm not in names:
            names.append(nm)
    return names


def free_vram_unload_comfy(port=None, log=None):
    """让 ComfyUI 卸载模型把显存还回来（**不杀它**）——它下次要用会自己重新加载 ✓"""
    port = int(port or COMFY_PORT_DEFAULT)
    before = gpu_free_mb()
    if not _port_open(port):
        return {"ok": False, "before": before, "after": before,
                "note": "ComfyUI 没在运行，无需卸载",
                "consumers": gpu_consumers()}
    try:
        data = json.dumps({"unload_models": True, "free_memory": True}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/free" % port, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        if log:
            log("已让 ComfyUI 卸载模型释放显存")
    except Exception as e:
        if log:
            log("让 ComfyUI 释放显存失败：%s" % str(e)[:120])
    time.sleep(2)
    after = gpu_free_mb()
    return {"ok": True, "before": before, "after": after,
            "freed": (after - before) if (before is not None and after is not None) else None,
            "consumers": gpu_consumers()}


CRASH_HINTS = {
    3221225477: "显卡驱动级崩溃（0xC0000005 访问违例）—— 九成是**显存不足**。"
                "先腾显存：本地千问那边点「停止后端」，或关掉壁纸引擎 / QQ 等占显卡的程序，再试 ✓",
    3221225725: "栈溢出（0xC00000FD）—— 多半是内存也吃紧了，先关掉别的程序",
    3221225786: "进程被中断（0xC000013A）",
}


def explain_exit_code(code):
    try:
        code = int(code) & 0xFFFFFFFF
    except Exception:
        return str(code)
    return CRASH_HINTS.get(code, "")


def _creds_dir():
    la = os.environ.get("LOCALAPPDATA")
    return os.path.join(la, "Live2D") if la else LIVE2D_DIR


def studio_token_present():
    p = os.path.join(_creds_dir(), "studio_token.txt")
    if os.path.isfile(p) and os.path.getsize(p) > 8:
        return True
    return bool(os.environ.get("ST_STUDIO_TOKEN"))


def local_engine_ready():
    if not os.path.isfile(VENV_PY):
        return False, "还没建本地环境（C:\\SeeThrough\\venv）"
    for sub in ("layerdiff", "marigold"):
        p = os.path.join(LOCAL_MODELS, sub)
        if not os.path.isdir(p):
            return False, "模型没下全：缺 %s" % sub
    free = gpu_free_mb()
    if free is not None and free < NEED_VRAM_MB:
        return False, ("显存不够：现在只剩 %.1f GB，本地拆层要 %.1f GB"
                       "（多半是 ComfyUI / 壁纸引擎占着）——点「腾显存」再试"
                       % (free / 1024.0, NEED_VRAM_MB / 1024.0))
    return True, "本机显卡运行，不联网、不排队%s" % (
        "（空闲显存 %.1f GB）" % (free / 1024.0) if free is not None else "")


def psd2live_state():
    if not _port_open(PSD2LIVE_PORT):
        return {"running": False, "exe": os.path.isfile(PSD2LIVE_EXE)}
    try:
        import psd2live_mcp
        c = psd2live_mcp.McpClient()
        c.connect()
        r = c.tool("inspect", {"scope": "project"})
        txt = psd2live_mcp.tool_result_text(r)
        return {"running": True, "raw": txt[:200], "loaded": '"loaded":true' in txt.replace(" ", "")}
    except Exception as e:
        return {"running": True, "error": str(e)[:200]}


def engines_status():
    lok, lnote = local_engine_ready()
    free = gpu_free_mb()
    return {
        "ok": True,
        "engines": [
            {"id": "local", "name": "本地引擎（本机显卡）", "ready": lok, "note": lnote,
             "badge": "本地"},
            {"id": "online", "name": "在线官方演示（作者 ModelScope）", "ready": studio_token_present(),
             "note": "免费，但共享队列要排队（约 10 分钟）；需登录态",
             "badge": "API"},
        ],
        "psd2live": psd2live_state(),
        "matDir": MAT_DIR,
        "outDir": OUT_DIR,
        "localNote": lnote,
        "vram": {"freeMb": free, "needMb": NEED_VRAM_MB},
    }


def _job_log(job, msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with _LOCK:
        job["log"].append(line)
        if len(job["log"]) > 400:
            del job["log"][:100]
        _persist(job)
    print("[live2d] %s" % msg, flush=True)


def _set(job, pct=None, text=None, stage=None):
    with _LOCK:
        if pct is not None:
            job["pct"] = max(0, min(100, int(pct)))
        if text is not None:
            job["text"] = text
        if stage is not None:
            job["stage"] = stage
        _persist(job)


# ---------------------------------------------------------------------------
#  PSD2Live：确保在跑 / 需要时重开引擎
# ---------------------------------------------------------------------------
def ensure_psd2live(job, allow_restart=True):
    import psd2live_mcp
    if not _port_open(PSD2LIVE_PORT):
        _job_log(job, "PSD2Live 没在跑，拉起来…")
        if not os.path.isfile(PSD2LIVE_EXE):
            raise RuntimeError("找不到 PSD2Live：%s" % PSD2LIVE_EXE)
        subprocess.Popen([PSD2LIVE_EXE], cwd=os.path.dirname(PSD2LIVE_EXE),
                         creationflags=0x08000000)
        for _ in range(60):
            time.sleep(1)
            if _port_open(PSD2LIVE_PORT):
                break
        else:
            raise RuntimeError("PSD2Live 60 秒还没起来")
        time.sleep(3)

    c = psd2live_mcp.McpClient()
    c.connect()
    txt = psd2live_mcp.tool_result_text(c.tool("inspect", {"scope": "project"}))
    loaded = '"loaded":true' in txt.replace(" ", "")
    if loaded:
        if not allow_restart:
            raise RuntimeError("PSD2Live 里已经开着工程，MCP 不允许覆盖导入；"
                               "请勾选「自动清空 PSD2Live 工程」")
        _job_log(job, "PSD2Live 里有旧工程 → 重开引擎清空（未保存的改动会丢）")
        import live2d_pipeline as pl
        pl.restart_engine(lambda m: _job_log(job, m.strip()))
        c = psd2live_mcp.McpClient()
        c.connect()
    return c


# ---------------------------------------------------------------------------
#  一条龙任务
# ---------------------------------------------------------------------------
def start_make(req):
    """req: {image(路径), name, engine, resolution, seed, tblr, allow_restart}"""
    img = (req.get("image") or "").strip()
    if not img or not os.path.isfile(img):
        return {"ok": False, "error": "找不到立绘图片：%s" % img}
    engine = req.get("engine") or "local"
    if engine not in ("local", "online"):
        return {"ok": False, "error": "拆层方式不认识：%s" % engine}

    name = (req.get("name") or "").strip() or os.path.splitext(os.path.basename(img))[0]
    bad = '<>:"/\\|?*'
    name = "".join(c for c in name if c not in bad).strip()[:48] or "未命名"

    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid, "state": "queued", "stage": "split", "pct": 0,
        "text": "排队中…", "log": [], "error": "", "engine": engine, "name": name,
        "image": img, "outDir": os.path.join(OUT_DIR, name),
        "matDir": os.path.join(MAT_DIR, name), "files": [], "psd": "",
        "t0": time.time(),
    }
    with _LOCK:
        JOBS[jid] = job

    t = threading.Thread(target=_run, args=(job, req), daemon=True)
    t.start()
    return {"ok": True, "job": jid, "name": name}


def _run(job, req):
    tag = {"local": "本地引擎", "online": "在线官方演示"}[job["engine"]]
    try:
        os.makedirs(job["matDir"], exist_ok=True)
        job["state"] = "running"
        _job_log(job, "开始：%s" % os.path.basename(job["image"]))
        _job_log(job, "拆层方式：%s" % tag)

        # ---------------- ① 拆层 ----------------
        _set(job, 2, "① 拆层中…（%s）" % tag, "split")
        if job["engine"] == "local":
            psd = _split_local(job, req)
        else:
            psd = _split_online(job, req)
        job["psd"] = psd
        _job_log(job, "分层 PSD：%s" % psd)
        _set(job, 78, "① 拆层完成")

        # ---------------- ② 导入 PSD2Live ----------------
        _set(job, 80, "② 导入 PSD2Live…", "import")
        import psd2live_mcp
        c = ensure_psd2live(job, allow_restart=bool(req.get("allow_restart", True)))
        res = c.tool("asset", {"request": {"mode": "psd", "path": psd}})
        txt = psd2live_mcp.tool_result_text(res)
        if '"error"' in txt:
            raise RuntimeError("导入 PSD 失败：%s" % txt[:200])
        _job_log(job, "导入成功：%s" % txt[:160])

        # ---------------- ③ 导出模型 ----------------
        _set(job, 90, "③ 导出 Live2D 模型…", "export")
        os.makedirs(job["outDir"], exist_ok=True)
        import live2d_pipeline as pl
        state = pl.find_state(c)
        if not state:
            raise RuntimeError("拿不到 PSD2Live 的 state，无法导出")
        r = c.tool("export", {"state": state, "output_directory": job["outDir"]})
        etxt = psd2live_mcp.tool_result_text(r)
        if '"error"' in etxt:
            raise RuntimeError("导出失败：%s" % etxt[:200])
        files = []
        try:
            files = [f["path"] for f in (json.loads(etxt).get("files") or [])]
        except Exception:
            for m in re.finditer(r'"path":"([^"]+)"', etxt):
                files.append(m.group(1).replace("\\\\", "\\"))
        job["files"] = files
        _job_log(job, "导出 %d 个文件 → %s" % (len(files), job["outDir"]))

        job["state"] = "done"
        job["elapsed"] = round(time.time() - job["t0"], 1)
        _set(job, 100, "完成 ✓  用了 %.1f 分钟" % (job["elapsed"] / 60.0))
        _job_log(job, "完成，用时 %.1f 分钟" % (job["elapsed"] / 60.0))
    except Exception as e:
        job["state"] = "error"
        job["error"] = "%s: %s" % (type(e).__name__, e)
        _set(job, None, "出错：%s" % job["error"][:200])
        _job_log(job, "✗ %s" % job["error"])


def _split_local(job, req):
    """本机显卡跑 See-Through（子进程，解析 [ST] PCT 行）"""
    ok, note = local_engine_ready()
    if not ok:
        # ★ 显存不够时先自动让 ComfyUI 让一让（不杀它），还不够才报错
        if "显存不够" in note:
            _job_log(job, "显存不足，先让 ComfyUI 卸载模型…")
            r = free_vram_unload_comfy(log=lambda m: _job_log(job, m))
            if r.get("freed"):
                _job_log(job, "释放了约 %.1f GB" % (r["freed"] / 1024.0))
        ok, note = local_engine_ready()
        if not ok:
            raise RuntimeError("本地引擎没就绪：%s" % note)
    cmd = [VENV_PY, "-u", LOCAL_RUNNER,
           "--image", job["image"], "--out", job["matDir"],
           "--resolution", str(int(req.get("resolution") or 1024)),
           "--seed", str(int(req.get("seed") or 42))]
    if req.get("tblr"):
        cmd.append("--tblr")
    _job_log(job, "启动本地引擎：%s" % " ".join(os.path.basename(x) for x in cmd[:3]))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # 显存碎片少一点，能少几次莫名奇妙的 CUDA 崩
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    try:   # 引擎自己那份带时间戳的日志（出问题能查）
        env["ST_LOCAL_LOG"] = os.path.join(job["matDir"], "引擎日志.txt")
    except Exception:
        pass
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         cwd=LIVE2D_DIR, env=env, text=True, encoding="utf-8",
                         errors="replace", bufsize=1,
                         creationflags=0x08000000)
    psd = None
    last_pct = 0
    for line in p.stdout:
        line = (line or "").rstrip()
        if not line:
            continue
        m = re.match(r"\[ST\]\s*PCT\s+(\d+)\s+(.*)", line)
        if m:
            n, txt = int(m.group(1)), m.group(2)
            # 本地引擎的 0-100 映射到整条流程的 2-76
            _set(job, 2 + int(n * 0.74), "① " + txt)
            last_pct = n
            continue
        m = re.match(r"\[ST\]\s*DONE\s+(.+)", line)
        if m:
            psd = m.group(1).strip()
            continue
        if line.startswith("[ST]"):
            _job_log(job, "引擎：" + line[4:].strip())
            continue
        # tqdm 进度条：只有「以百分比打头」的才是推理进度条；
        # 模型加载那种 "Loading weights: 100%|…" 不算（踩过：进度条一上来就 42%）✗
        m = re.match(r"\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)", line)
        if m:
            inner = int(m.group(1))
            cur, tot = int(m.group(2)), int(m.group(3))
            if tot >= 5:
                band = {"layerediff": (12, 55), "marigold": (62, 85)}.get(job.get("_stage"), (12, 55))
                _set(job, 2 + int((band[0] + (band[1] - band[0]) * inner / 100.0) * 0.74),
                     "① 推理中 %d%%（%d/%d 步）" % (inner, cur, tot))
            continue
        m = re.search(r"\[ST\]\s*STAGE\s+(\w+)", line)
        if m:
            job["_stage"] = m.group(1)
    code = p.wait()
    if code != 0:
        hint = explain_exit_code(code)
        raise RuntimeError("本地引擎退出码 %s%s" % (code, ("\n★ " + hint) if hint else "（看上面日志）"))
    if not psd or not os.path.isfile(psd):
        import glob
        cand = sorted(glob.glob(os.path.join(job["matDir"], "*.psd")))
        if not cand:
            raise RuntimeError("本地引擎没产出 PSD")
        psd = cand[-1]
    return psd


def _split_online(job, req):
    """作者官方 ModelScope 演示（需要登录态 studio_token）"""
    import seethrough_client as st
    if not st.get_studio_token():
        raise RuntimeError("没有 ModelScope 登录态：先跑 live2d\\ms_token.py 换一次 studio_token")
    _job_log(job, "送到官方演示（免费共享队列，通常 5~15 分钟）…")
    t0 = time.time()

    def logline(msg):
        msg = (msg or "").strip()
        if not msg:
            return
        m = re.search(r"等结果（(\d+)s\)", msg)
        if m:
            sec = int(m.group(1))
            _set(job, 2 + min(62, int(sec / 9.0)), "① 官方队列排队/推理中… %ds" % sec)
            if sec % 60 < 20:
                _job_log(job, "已等 %d 秒" % sec)
            return
        if msg.startswith("["):
            msg = msg.split("]", 1)[-1].strip()
        _job_log(job, "演示：" + msg)
        if "上传" in msg or "上传立绘" in msg:
            _set(job, 4, "① 上传立绘…")
        elif "提交" in msg:
            _set(job, 8, "① 已提交，排队中…")
        elif "完成，返回" in msg:
            _set(job, 70, "① 出图了，下载中…")

    data = st.run_inference(job["image"], int(req.get("resolution") or 1024),
                            int(req.get("seed") or 42), bool(req.get("tblr")), log=logline)
    files = st._collect_files(data, logline)
    psd = None
    for i, f in enumerate(files):
        spath = f["path"]
        oname = f.get("orig_name") or os.path.basename(spath)
        if not os.path.splitext(oname)[1]:
            oname = os.path.basename(spath)
        dest = os.path.join(job["matDir"], oname)
        if os.path.exists(dest) and oname.lower().endswith((".png", ".webp", ".jpg")):
            stem, ext = os.path.splitext(oname)
            dest = os.path.join(job["matDir"], "%s_%02d%s" % (stem, i, ext))
        n = st.download(spath, dest)
        _job_log(job, "下载 %s (%.2f MB)" % (os.path.basename(dest), n / 1048576.0))
        if dest.lower().endswith(".psd"):
            psd = dest
    if not psd:
        raise RuntimeError("官方演示没返回 PSD")
    # 演示返回的文件名固定叫 seethrough_output.psd —— 改名成作品名，成品文件才好看
    nice = os.path.join(job["matDir"], "%s.psd" % job["name"])
    if os.path.abspath(psd) != os.path.abspath(nice):
        try:
            shutil.copy2(psd, nice)
            psd = nice
        except Exception:
            pass
    _job_log(job, "演示耗时 %.1f 分钟" % ((time.time() - t0) / 60.0))
    return psd


def progress(jid):
    job = JOBS.get(jid)
    if not job:
        # ★ 内存里没有 → 看磁盘留档：多半是工作站重启过
        rec = _load_persisted(jid)
        if not rec:
            return {"ok": False, "error": "没有这个任务（记录也被清掉了）"}
        was_running = rec.get("state") in ("queued", "running")
        log = list(rec.get("log") or [])
        if was_running:
            log += ["", "─" * 46,
                    "⚠ 工作站中间重启过，这个任务的进度跟丢了。",
                    "   · 本地引擎：引擎子进程也会随工作站一起结束，需要重跑",
                    "   · 在线演示：云端的活可能已经干完，但结果取不回来了，需要重跑",
                    "   → 点「开始制作」重跑一次即可，方式不变。"]
        return {
            "ok": True, "id": jid,
            "state": "interrupted" if was_running else (rec.get("state") or "lost"),
            "stage": rec.get("stage"), "pct": rec.get("pct") or 0,
            "text": ("任务被中断（工作站重启过）" if was_running
                     else (rec.get("text") or "任务已结束（记录留档）")),
            "error": rec.get("error") or "", "log": log,
            "outDir": rec.get("outDir"), "matDir": rec.get("matDir"),
            "psd": rec.get("psd") or "", "files": rec.get("files") or [],
            "name": rec.get("name"), "engine": rec.get("engine"),
            "elapsed": rec.get("elapsed"), "fromDisk": True,
        }
    with _LOCK:
        return {
            "ok": True, "id": jid, "state": job["state"], "stage": job["stage"],
            "pct": job["pct"], "text": job["text"], "error": job["error"],
            "log": job["log"][-60:], "outDir": job["outDir"], "matDir": job["matDir"],
            "psd": job["psd"], "files": job["files"], "name": job["name"],
            "engine": job["engine"], "elapsed": job.get("elapsed"),
        }


def import_existing_psd(req):
    """已经有 PSD 时，只跑「导入 + 导出」两步。"""
    psd = (req.get("psd") or "").strip()
    if not os.path.isfile(psd):
        return {"ok": False, "error": "找不到 PSD：%s" % psd}
    name = (req.get("name") or os.path.splitext(os.path.basename(psd))[0]).strip()
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "state": "queued", "stage": "import", "pct": 0, "text": "排队中…",
           "log": [], "error": "", "engine": "psd", "name": name, "image": "",
           "outDir": os.path.join(OUT_DIR, name), "matDir": os.path.dirname(psd),
           "files": [], "psd": psd, "t0": time.time()}
    with _LOCK:
        JOBS[jid] = job

    def run():
        try:
            job["state"] = "running"
            _set(job, 10, "② 导入 PSD2Live…", "import")
            import psd2live_mcp
            c = ensure_psd2live(job, allow_restart=bool(req.get("allow_restart", True)))
            res = c.tool("asset", {"request": {"mode": "psd", "path": psd}})
            txt = psd2live_mcp.tool_result_text(res)
            if '"error"' in txt:
                raise RuntimeError("导入失败：%s" % txt[:200])
            _job_log(job, "导入成功：%s" % txt[:160])
            _set(job, 70, "③ 导出模型…", "export")
            os.makedirs(job["outDir"], exist_ok=True)
            import live2d_pipeline as pl
            state = pl.find_state(c)
            r = c.tool("export", {"state": state, "output_directory": job["outDir"]})
            etxt = psd2live_mcp.tool_result_text(r)
            if '"error"' in etxt:
                raise RuntimeError("导出失败：%s" % etxt[:200])
            try:
                job["files"] = [f["path"] for f in (json.loads(etxt).get("files") or [])]
            except Exception:
                pass
            job["state"] = "done"
            job["elapsed"] = round(time.time() - job["t0"], 1)
            _set(job, 100, "完成 ✓")
            _job_log(job, "导出 %d 个文件" % len(job["files"]))
        except Exception as e:
            job["state"] = "error"
            job["error"] = str(e)
            _set(job, None, "出错：%s" % str(e)[:200])
            _job_log(job, "✗ %s" % e)

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "job": jid, "name": name}


def list_works():
    """列已做过的作品（成品目录里每个子目录）"""
    out = []
    if os.path.isdir(OUT_DIR):
        for d in sorted(os.listdir(OUT_DIR)):
            p = os.path.join(OUT_DIR, d)
            if not os.path.isdir(p):
                continue
            files = os.listdir(p)
            moc3 = [f for f in files if f.endswith(".moc3")]
            out.append({
                "name": d, "dir": p, "hasMoc3": bool(moc3),
                "moc3": os.path.join(p, moc3[0]) if moc3 else "",
                "count": len([f for f in files if os.path.isfile(os.path.join(p, f))]),
                "mtime": os.path.getmtime(p),
            })
    out.sort(key=lambda x: -x["mtime"])
    return {"ok": True, "works": out, "outDir": OUT_DIR}


def start_psd2live():
    if _port_open(PSD2LIVE_PORT):
        return {"ok": True, "already": True}
    if not os.path.isfile(PSD2LIVE_EXE):
        return {"ok": False, "error": "找不到 %s" % PSD2LIVE_EXE}
    subprocess.Popen([PSD2LIVE_EXE], cwd=os.path.dirname(PSD2LIVE_EXE),
                     creationflags=0x08000000)
    for _ in range(40):
        time.sleep(0.5)
        if _port_open(PSD2LIVE_PORT):
            return {"ok": True, "already": False}
    return {"ok": False, "error": "启动了但端口没起来"}


def save_upload(name, data_url):
    """前端选图 -> base64 -> 落到 桌面\\Live2D素材\\_输入\\ ，返回绝对路径"""
    import base64
    fname = os.path.basename((name or "").strip()) or "立绘.png"
    if not fname.lower().endswith(IMG_EXT):
        return {"ok": False, "error": "只支持图片：%s" % ", ".join(IMG_EXT)}
    data = data_url or ""
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    if not data:
        return {"ok": False, "error": "没收到图片内容"}
    try:
        raw = base64.b64decode(data)
    except Exception as e:
        return {"ok": False, "error": "解码失败：%s" % e}
    if len(raw) > MAX_MB * 1024 * 1024:
        return {"ok": False, "error": "超过 %d MB" % MAX_MB}
    os.makedirs(INPUT_DIR, exist_ok=True)
    stem, ext = os.path.splitext(fname)
    target = os.path.join(INPUT_DIR, fname)
    n = 1
    while os.path.exists(target):
        target = os.path.join(INPUT_DIR, "%s_%d%s" % (stem, n, ext))
        n += 1
    with open(target, "wb") as f:
        f.write(raw)
    return {"ok": True, "path": target, "name": os.path.basename(target),
            "sizeText": "%.2f MB" % (len(raw) / 1048576.0)}


def list_psds():
    """列出手上已有的分层 PSD（素材目录 + 在线演示的默认输出）"""
    out = []
    roots = [MAT_DIR, os.path.join(BASE, "workspace", "layerdiff_output"),
             os.path.join(BASE, "workspace", "layerdiff_output_blockswap")]
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dirpath, _dirnames, filenames in os.walk(r):
            for f in filenames:
                if f.lower().endswith(".psd"):
                    p = os.path.join(dirpath, f)
                    try:
                        out.append({"path": p, "name": f, "mtime": os.path.getmtime(p),
                                    "mb": round(os.path.getsize(p) / 1048576.0, 2)})
                    except OSError:
                        pass
    out.sort(key=lambda x: -x["mtime"])
    return {"ok": True, "psds": out[:60], "matDir": MAT_DIR}
