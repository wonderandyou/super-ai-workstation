# -*- coding: utf-8 -*-
"""
AI 人声分离 —— 工作站的「🎚️ 人声分离」标签页后端
=====================================================================
把一首歌（或一段视频）拆开：
    两轨模式：**人声** + **伴奏**
    四轨模式：人声 / 鼓 / 贝斯 / 其他

引擎：**Demucs `htdemucs`（Meta 官方，MIT 许可）** ——
      它装在 ComfyUI 那套 Python 里（`D:\\ComfyUI\\python_embeded`，带 torch+CUDA），
      所以这里**必须起子进程**跑（和翻唱、朗读一个道理，不能 import）。

模型：本机**已有缓存**：
      `~\\.cache\\torch\\hub\\checkpoints\\955717e8-8726e21a.th`（84 MB）
      ★ 按铁律一**不设任何镜像**：万一要重新下，只走官方 `huggingface.co`；
        官方下不动就如实报错，绝不偷偷换个人镜像 ✗

文件：
    后端   stem_ai.py（本文件）
    worker _stem_worker.py（跑在 ComfyUI 环境里，真正的分离逻辑）
    素材   data\\分离素材\\    出片 data\\分离出片\\    中间 data\\_stem_work\\
"""

import os
import re
import subprocess
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

COMFY_PY = r"D:\ComfyUI\python_embeded\python.exe"
WORKER = os.path.join(ROOT, "_stem_worker.py")
MODEL_CACHE = os.path.expanduser(r"~\.cache\torch\hub\checkpoints")

SRC_DIR = os.path.join(DATA, "分离素材")
OUT_DIR = os.path.join(DATA, "分离出片")
WORK_DIR = os.path.join(DATA, "_stem_work")

SRC_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus", ".aac",
           ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
SRC_MAX_MB = 120           # base64 上传的内存上限；更大的文件直接丢进「分离素材」文件夹
FMT = ("wav", "flac", "mp3")

JOBS = {}
JOB_LOCK = threading.Lock()

NEED_COMMIT_MB = 3500      # 分离的峰值需求（和翻唱分离那步差不多）

STEM_LABEL = {"vocals": "人声", "drums": "鼓", "bass": "贝斯", "other": "其他"}


# --------------------------------------------------------------------------
def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


def mem_info():
    try:
        import ctypes

        class MSX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MSX()
        st.dwLength = ctypes.sizeof(MSX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return {"physFreeMB": round(st.ullAvailPhys / 2 ** 20),
                "commitFreeMB": round(st.ullAvailPageFile / 2 ** 20)}
    except Exception:
        return {}


def model_cached():
    """htdemucs 权重在不在本地缓存（在就不用联网）"""
    try:
        for f in os.listdir(MODEL_CACHE):
            if f.endswith(".th") and os.path.getsize(os.path.join(MODEL_CACHE, f)) > 50 * 2 ** 20:
                return True
    except Exception:
        pass
    return False


def new_job(kind):
    jid = uuid.uuid4().hex[:12]
    with JOB_LOCK:
        JOBS[jid] = {"kind": kind, "state": "running", "t0": time.time(),
                     "stage": "准备中", "percent": 0.0, "detail": "",
                     "result": None, "error": None, "log": []}
    return jid


def job_log(jid, msg):
    j = JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + msg)
    if len(j["log"]) > 300:
        del j["log"][:80]


def job_set(jid, **kw):
    j = JOBS.get(jid)
    if j:
        j.update(kw)


def get_job(jid):
    j = JOBS.get(jid)
    if not j:
        return None
    d = dict(j)
    d["elapsed"] = round(time.time() - j["t0"], 1)
    return d


# --------------------------------------------------------------------------
def _listing(d, exts):
    items = []
    try:
        os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(d), reverse=True):
            fp = os.path.join(d, f)
            if not (os.path.isfile(fp) and f.lower().endswith(exts)):
                continue
            st = os.stat(fp)
            items.append({"name": f, "sizeText": human(st.st_size), "size": st.st_size,
                          "time": time.strftime("%m-%d %H:%M",
                                                time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def status():
    return {
        "ok": True,
        "ready": os.path.isfile(COMFY_PY) and os.path.isfile(WORKER),
        "comfyPy": os.path.isfile(COMFY_PY),
        "worker": os.path.isfile(WORKER),
        "modelCached": model_cached(),
        "sources": _listing(SRC_DIR, SRC_EXT),
        "outputs": _listing(OUT_DIR, (".wav", ".flac", ".mp3")),
        "srcDir": SRC_DIR, "outDir": OUT_DIR,
        "formats": list(FMT),
        "needCommitMB": NEED_COMMIT_MB,
        "mem": mem_info(),
    }


# --------------------------------------------------------------------------
def _run_worker(jid, args, label):
    """跑 worker，逐行读 stdout：STAGE: 更新阶段，SKIP: 忽略，其余进日志"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 不设 HF_ENDPOINT 镜像（铁律一）：模型已在本地缓存，真要下也只走官方
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    flags = 0x08000000 if os.name == "nt" else 0

    pr = subprocess.Popen([COMFY_PY, WORKER] + [str(a) for a in args],
                          env=env, cwd=ROOT, creationflags=flags,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace", bufsize=1)
    t0 = time.time()
    errbuf = []
    for line in iter(pr.stdout.readline, ""):
        line = (line or "").rstrip()
        if not line:
            continue
        if re.match(r"^\s*\d+%\|", line) or line.startswith(("  ", "\t")):
            continue
        el = time.time() - t0
        if line.startswith("STAGE:"):
            job_set(jid, stage=line[6:].strip(),
                    percent=round(min(92.0, 5 + el / 90.0 * 85.0), 1))
        elif line.startswith("SKIP:"):
            continue
        else:
            job_log(jid, line[:200])
        low = line.lower()
        if "traceback" in low or "error" in low or "错误" in line:
            errbuf.append(line)
        if el > 3600:
            pr.kill()
            raise RuntimeError("%s 超时（60 分钟）" % label)
    pr.wait()
    if pr.returncode != 0:
        tail = "\n".join(errbuf[-6:]) or ("退出码 %s" % pr.returncode)
        raise RuntimeError("%s 失败：%s" % (label, tail[:400]))


def stem_worker(jid, p):
    """分离主流程"""
    try:
        if not os.path.isfile(COMFY_PY):
            raise RuntimeError("找不到 ComfyUI 的 Python：%s" % COMFY_PY)
        if not os.path.isfile(WORKER):
            raise RuntimeError("找不到分离 worker：%s" % WORKER)

        src_name = os.path.basename(p.get("source") or "")
        src = os.path.join(SRC_DIR, src_name)
        if not os.path.isfile(src):
            raise RuntimeError("找不到音频文件，请先上传")

        mode = (p.get("mode") or "two").lower()
        if mode not in ("two", "four"):
            mode = "two"
        fmt = (p.get("format") or "wav").lower()
        if fmt not in FMT:
            fmt = "wav"

        mi = mem_info()
        if mi:
            job_log(jid, "内存：可用物理 %d MB，可用提交量 %d MB"
                    % (mi.get("physFreeMB", 0), mi.get("commitFreeMB", 0)))
            if mi.get("commitFreeMB", 9999) < NEED_COMMIT_MB:
                raise RuntimeError(
                    "内存不够（可用提交量 %d MB，至少要 %d MB）。\n"
                    "先关掉 ComfyUI 或其他后端再试。" % (mi.get("commitFreeMB"), NEED_COMMIT_MB))

        if not model_cached():
            job_log(jid, "⚠️ 本地没找到 Demucs 权重缓存 —— 会去**官方 huggingface.co** 下"
                         "（按铁律不换镜像；下不动就直说，不偷偷换）")

        stem = re.sub(r'[\\/:*?"<>|\s]+', "_", os.path.splitext(src_name)[0])[:40] or "音频"
        nice = re.sub(r'[\\/:*?"<>|\s]+', "_", (p.get("name") or "").strip())[:40]
        stamp = time.strftime("%Y%m%d-%H%M%S")

        wdir = os.path.join(WORK_DIR, jid)
        os.makedirs(wdir, exist_ok=True)
        os.makedirs(OUT_DIR, exist_ok=True)

        job_log(jid, "文件：%s（%s）" % (src_name, human(os.path.getsize(src))))
        job_log(jid, "模式：%s　格式：%s"
                % ("两轨（人声 + 伴奏）" if mode == "two" else "四轨（人声/鼓/贝斯/其他）", fmt))

        job_set(jid, stage="加载 Demucs 模型", percent=5)
        _run_worker(jid, [src, wdir, mode, fmt], "分离")

        # 把产物收进出片目录
        made = []
        if mode == "two":
            for key, tag in (("vocals", "人声"), ("instrumental", "伴奏")):
                s = os.path.join(wdir, key + "." + fmt)
                if os.path.isfile(s):
                    base = "%s_%s_%s" % ((nice or stem), tag, stamp)
                    d = os.path.join(OUT_DIR, "%s.%s" % (base, fmt))
                    n = 1
                    while os.path.exists(d):
                        d = os.path.join(OUT_DIR, "%s(%d).%s" % (base, n, fmt))
                        n += 1
                    os.replace(s, d)
                    made.append({"name": os.path.basename(d), "size": os.path.getsize(d),
                                 "sizeText": human(os.path.getsize(d)), "stem": tag})
        else:
            # 四轨：平铺在出片目录里（不放子目录 —— 免得下载链接要处理相对路径）
            for key, tag in STEM_LABEL.items():
                s = os.path.join(wdir, key + "." + fmt)
                if os.path.isfile(s):
                    base = "%s_四轨_%s_%s" % ((nice or stem), tag, stamp)
                    d = os.path.join(OUT_DIR, "%s.%s" % (base, fmt))
                    n = 1
                    while os.path.exists(d):
                        d = os.path.join(OUT_DIR, "%s(%d).%s" % (base, n, fmt))
                        n += 1
                    os.replace(s, d)
                    made.append({"name": os.path.basename(d), "size": os.path.getsize(d),
                                 "sizeText": human(os.path.getsize(d)), "stem": tag})

        if not made:
            raise RuntimeError("分离跑完了但没找到产物 —— 去看看中间目录：%s" % wdir)

        try:
            import shutil
            shutil.rmtree(wdir, ignore_errors=True)
        except Exception:
            pass

        job_set(jid, state="done", percent=100, stage="完成", result={
            "mode": mode, "format": fmt, "files": made, "count": len(made),
            "seconds": round(time.time() - jid_t0(jid), 1)})
        job_log(jid, "✓ 分离完成：%s" % "、".join(f["name"] for f in made))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


def jid_t0(jid):
    j = JOBS.get(jid)
    return j["t0"] if j else time.time()
