# -*- coding: utf-8 -*-
"""
声音包朗读（F5-TTS）—— 工作站的「📖 声音包朗读」标签页后端
================================================================
为什么用子进程而不是 import：
    F5-TTS 环境（D:\\F5TTS\\venv）是 torch 2.11+cu128，
    工作站自己用的是 ComfyUI 那套（2.14+cu130）。两套不能混，
    所以这里只负责「用 F5-TTS 自己的解释器把 worker 跑起来」，
    然后从它的 stdout 逐行解析进度。

F5-TTS 侧的三条关键经验（详见 D:\\F5TTS\\f5patch.py 注释）：
    1. 必须 import f5patch，否则 0xC0000005 / safetensors 报错
    2. 给 ref_text 就跳过 Whisper 转写（快，也不会崩）
    3. torchaudio.load 要兜底到 soundfile
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid

# --------------------------------------------------------------------------
#  常量
# --------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
import paths as _paths          # ★ 统一路径层
F5_DIR = _paths.f5tts()
F5_PY = os.path.join(F5_DIR, "venv", "Scripts", "python.exe")
F5_SRC = os.path.join(F5_DIR, "f5-tts", "src")
F5_CKPT = os.path.join(F5_DIR, "checkpoints")
WORKER = os.path.join(ROOT, "_tts_worker.py")

REF_DIR = _paths.ref_voice_dir()                   # 和音乐工坊共用同一个声音包目录
REF_EXT = (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus", ".aac")

DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(DATA, "朗读出片")

JOBS = {}
JOB_LOCK = threading.Lock()

MAX_CHARS = 600          # 单次朗读文字上限（太长会很久，也容易爆显存）

# F5-TTS 加载模型时的内存需求（实测：模型 1.35 GB + torch + Whisper 中间态）
NEED_COMMIT_MB = 3500    # 低于这个可用提交量就别启动，否则必然 os error 1455


def mem_info():
    """查物理内存和「提交量」余量。

    踩过的坑：可用物理内存还剩几 GB 不代表能跑 —— 真正卡死的是**提交量**
    （commit charge）。Windows 页面文件是自动管理时扩得慢，峰值一来就报
    `os error 1455 页面文件太小`，然后 MemoryError。
    """
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return {
            "physTotalMB": round(st.ullTotalPhys / 2 ** 20),
            "physFreeMB": round(st.ullAvailPhys / 2 ** 20),
            "commitTotalMB": round(st.ullTotalPageFile / 2 ** 20),
            "commitFreeMB": round(st.ullAvailPageFile / 2 ** 20),
        }
    except Exception:
        return {}


# --------------------------------------------------------------------------
#  工具
# --------------------------------------------------------------------------
def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


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
    if len(j["log"]) > 400:
        del j["log"][:100]


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
#  声音包清单
# --------------------------------------------------------------------------
def list_voices():
    items = []
    try:
        os.makedirs(REF_DIR, exist_ok=True)
        for f in sorted(os.listdir(REF_DIR)):
            fp = os.path.join(REF_DIR, f)
            if os.path.isfile(fp) and f.lower().endswith(REF_EXT):
                st = os.stat(fp)
                items.append({
                    "name": f,
                    "size": st.st_size,
                    "sizeText": human(st.st_size),
                    "time": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
    except Exception:
        pass
    return items


def voice_path(name):
    """只允许声音包目录里的文件名，防目录穿越"""
    n = os.path.basename(name or "")
    p = os.path.join(REF_DIR, n)
    return p if (n and os.path.isfile(p)) else ""


# --------------------------------------------------------------------------
#  状态
# --------------------------------------------------------------------------
def status():
    ready = os.path.isfile(F5_PY) and os.path.isfile(WORKER)
    ckpt_ok = os.path.isfile(os.path.join(
        F5_CKPT, "F5TTS_v1_Base", "model_1250000.safetensors"))
    return {
        "ok": True,
        "ready": bool(ready and ckpt_ok),
        "f5Dir": F5_DIR,
        "f5Py": os.path.isfile(F5_PY),
        "worker": os.path.isfile(WORKER),
        "checkpoint": ckpt_ok,
        "refDir": REF_DIR,
        "outDir": OUT_DIR,
        "voices": list_voices(),
        "maxChars": MAX_CHARS,
        "needCommitMB": NEED_COMMIT_MB,
        "mem": mem_info(),
    }


# --------------------------------------------------------------------------
#  朗读任务
# --------------------------------------------------------------------------
def tts_worker(jid, p):
    """起 F5-TTS 的子进程，逐行读它的 stdout 更新进度"""
    try:
        text = (p.get("text") or "").strip()
        if not text:
            raise RuntimeError("没有要朗读的文字")
        if len(text) > MAX_CHARS:
            raise RuntimeError("文字太长（%d 字），上限 %d 字" % (len(text), MAX_CHARS))

        ref = voice_path(p.get("voice"))
        if not ref:
            raise RuntimeError("请先选一个声音包")

        ref_text = (p.get("refText") or "").strip()
        if not ref_text:
            # 不给也行，但会走 Whisper 转写（慢，而且那步以前崩过）
            job_log(jid, "提示：没填参考文本，会走 Whisper 自动识别（慢一些）")

        os.makedirs(OUT_DIR, exist_ok=True)
        name = re.sub(r'[\\/:*?"<>|\s]+', "_", (p.get("name") or "").strip())[:32]
        if not name:
            name = time.strftime("朗读_%Y%m%d-%H%M%S")
        out = os.path.join(OUT_DIR, name + ".wav")
        n = 1
        while os.path.exists(out):
            out = os.path.join(OUT_DIR, "%s(%d).wav" % (name, n))
            n += 1

        args = [F5_PY, WORKER,
                "--ref", ref, "--out", out, "--text", text]
        if ref_text:
            args += ["--ref-text", ref_text]

        job_set(jid, stage="正在加载模型", percent=5,
                detail="F5-TTS 首次加载约 10~20 秒")
        job_log(jid, "声音包：%s" % os.path.basename(ref))
        job_log(jid, "文字 %d 字" % len(text))

        # 内存预检：不够就别启动，省得等一分多钟才报 os error 1455
        mi = mem_info()
        if mi:
            job_log(jid, "内存：可用物理 %d MB，可用提交量 %d MB" % (
                mi["physFreeMB"], mi["commitFreeMB"]))
            if mi["commitFreeMB"] < NEED_COMMIT_MB:
                raise RuntimeError(
                    "内存不够（可用提交量 %d MB，至少需要 %d MB）。\n"
                    "把 ComfyUI 或浏览器关掉一些再试；更治本的是把 Windows 虚拟内存"
                    "设成固定值（初始 32768 MB / 最大 65536 MB）。"
                    % (mi["commitFreeMB"], NEED_COMMIT_MB))

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # 铁律一：不设镜像（只用官方源；下不动就如实报错，不偷偷换个人镜像）
        env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        env["PYTHONPATH"] = F5_DIR + os.pathsep + F5_SRC
        flags = 0x08000000 if os.name == "nt" else 0

        pr = subprocess.Popen(args, env=env, cwd=F5_DIR, creationflags=flags,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace", bufsize=1)
        t0 = time.time()
        errbuf = []
        for line in iter(pr.stdout.readline, ""):
            line = (line or "").rstrip()
            if not line:
                continue
            # 进度条那种刷屏的过滤掉，只留有用信息
            if re.match(r"^\s*\d+%\|", line) or line.startswith(("  ", "\t")):
                continue
            low = line.lower()
            if "traceback" in low or "error" in low:
                errbuf.append(line)
            el = time.time() - t0
            if line.startswith("STAGE:"):
                job_set(jid, stage=line[6:].strip(),
                        percent=round(min(95, 8 + 87 * (1 - pow(2.718, -el / 40.0))), 1))
            elif not line.startswith("SKIP:"):
                job_log(jid, line[:200])
            if el > 1800:
                pr.kill()
                raise RuntimeError("超时（30 分钟）")
        pr.wait()

        if pr.returncode != 0 or not os.path.isfile(out):
            tail = "\n".join(errbuf[-6:]) or ("退出码 %s" % pr.returncode)
            raise RuntimeError("朗读失败：%s" % tail[:400])

        size = os.path.getsize(out)
        # 读一下时长
        secs = 0.0
        try:
            import wave
            with wave.open(out, "rb") as w:
                secs = round(w.getnframes() / float(w.getframerate() or 1), 2)
        except Exception:
            secs = round(size / (24000 * 2), 2)

        job_set(jid, state="done", percent=100, stage="完成", result={
            "name": os.path.basename(out), "path": out,
            "size": size, "sizeText": human(size),
            "seconds": secs, "cost": round(time.time() - t0, 1)})
        job_log(jid, "✓ 生成完成：%s（%s / %.1f 秒）" % (
            os.path.basename(out), human(size), secs))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))
