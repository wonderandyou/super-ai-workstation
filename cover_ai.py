# -*- coding: utf-8 -*-
"""
AI 翻唱 —— 工作站的「🎤 AI 翻唱」标签页后端
================================================================
流程（四步，跨两个 Python 环境）：
    ① 分离   上传的歌曲 → Demucs 剥出人声 + 伴奏     [ComfyUI 环境]
    ② 转换   人声 → Seed-VC 换成声音包的音色          [Seed-VC 环境]
    ③ 混音   转换后的人声 + 原伴奏 → 成品             [ComfyUI 环境]
    ④ 导出   flac + 顺手写个 lrc（如果歌词是现成的）

为什么分两个环境：
    Demucs 和 PyAV 在 ComfyUI 那套里（D:\\ComfyUI\\python_embeded，torch 2.14+cu130），
    Seed-VC 在自己的 venv 里（D:\\SeedVC\\venv，也是 2.14+cu130 但依赖各装各的）。
    两边不能互相 import，只能起子进程。

关于 f0：
    翻唱人声建议开 `--f0-condition`（跟原唱的韵律走），否则音高会飘。
    默认开；关掉会更像「同一个人念」而不是「同一个人唱」。
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))

import paths as _paths          # ★ 统一路径层
COMFY_PY = _paths.comfy_py()
SEEDVC_PY = _paths.seedvc_py()
SEEDVC_DIR = _paths.seedvc()
SEEDVC_SRC = os.path.join(SEEDVC_DIR, "seed-vc")

SEP_WORKER = os.path.join(ROOT, "_cover_sep.py")     # ComfyUI 环境：分离 / 混音
VC_WORKER = os.path.join(ROOT, "_cover_vc.py")       # Seed-VC 环境：声音转换
POLISH_WORKER = os.path.join(ROOT, "_cover_polish.py")  # ComfyUI 环境：去爆破音
SLIMREF_WORKER = os.path.join(ROOT, "_cover_slimref.py")  # ComfyUI 环境：裁短声音包
MAX_REF_SEC = 8.0            # 声音包最长用 8 秒（Seed-VC 推荐 3~10 秒）

REF_DIR = _paths.ref_voice_dir()                      # 和朗读、音乐工坊共用
REF_EXT = (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus", ".aac")

DATA = os.path.join(ROOT, "data")
SRC_DIR = os.path.join(DATA, "翻唱素材")              # 上传的原曲
OUT_DIR = os.path.join(DATA, "翻唱出片")
WORK_DIR = os.path.join(DATA, "_cover_work")          # 中间产物

SRC_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".opus", ".aac",
           ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v")
SRC_MAX_MB = 300

JOBS = {}
JOB_LOCK = threading.Lock()

NEED_COMMIT_MB = 4000      # 分离 + 转换的峰值需求比纯朗读更高


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
def list_voices():
    items = []
    try:
        os.makedirs(REF_DIR, exist_ok=True)
        for f in sorted(os.listdir(REF_DIR)):
            fp = os.path.join(REF_DIR, f)
            if os.path.isfile(fp) and f.lower().endswith(REF_EXT):
                st = os.stat(fp)
                items.append({"name": f, "sizeText": human(st.st_size)})
    except Exception:
        pass
    return items


def voice_path(name):
    n = os.path.basename(name or "")
    p = os.path.join(REF_DIR, n)
    return p if (n and os.path.isfile(p)) else ""


def list_sources():
    items = []
    try:
        os.makedirs(SRC_DIR, exist_ok=True)
        for f in sorted(os.listdir(SRC_DIR), reverse=True):
            fp = os.path.join(SRC_DIR, f)
            if os.path.isfile(fp):
                st = os.stat(fp)
                items.append({"name": f, "sizeText": human(st.st_size),
                              "time": time.strftime("%m-%d %H:%M",
                                                    time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def list_outputs():
    items = []
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        for f in sorted(os.listdir(OUT_DIR), reverse=True):
            fp = os.path.join(OUT_DIR, f)
            if os.path.isfile(fp) and f.lower().endswith((".flac", ".wav", ".mp3")):
                st = os.stat(fp)
                items.append({"name": f, "sizeText": human(st.st_size),
                              "time": time.strftime("%m-%d %H:%M",
                                                    time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def status():
    return {
        "ok": True,
        "ready": os.path.isfile(COMFY_PY) and os.path.isfile(SEEDVC_PY)
                 and os.path.isfile(SEP_WORKER) and os.path.isfile(VC_WORKER),
        "comfyPy": os.path.isfile(COMFY_PY),
        "seedvcPy": os.path.isfile(SEEDVC_PY),
        "seedvcSrc": os.path.isdir(SEEDVC_SRC),
        "voices": list_voices(),
        "sources": list_sources(),
        "outputs": list_outputs(),
        "srcDir": SRC_DIR,
        "outDir": OUT_DIR,
        "needCommitMB": NEED_COMMIT_MB,
        "mem": mem_info(),
    }


# --------------------------------------------------------------------------
def _run_step(jid, args, env_extra, label, pct0, pct1, cwd=None):
    """跑一个子进程，逐行读 stdout，遇到 STAGE: 就更新阶段"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 铁律一：不设镜像（只用官方源；下不动就如实报错，不偷偷换个人镜像）
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    env.update(env_extra or {})
    flags = 0x08000000 if os.name == "nt" else 0

    pr = subprocess.Popen(args, env=env, cwd=cwd, creationflags=flags,
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
                    percent=round(pct0 + (pct1 - pct0) *
                                  min(1.0, el / max(1.0, 45.0)), 1))
        elif line.startswith("SKIP:"):
            continue
        else:
            job_log(jid, line[:200])
        low = line.lower()
        if "traceback" in low or "error" in low:
            errbuf.append(line)
        if el > 3600:
            pr.kill()
            raise RuntimeError("%s 超时（60 分钟）" % label)
    pr.wait()
    if pr.returncode != 0:
        tail = "\n".join(errbuf[-6:]) or ("退出码 %s" % pr.returncode)
        raise RuntimeError("%s 失败：%s" % (label, tail[:400]))
    return True


def cover_worker(jid, p):
    """翻唱主流程"""
    try:
        src_name = os.path.basename(p.get("source") or "")
        src = os.path.join(SRC_DIR, src_name)
        if not os.path.isfile(src):
            raise RuntimeError("找不到歌曲文件，请先上传")

        ref = voice_path(p.get("voice"))
        if not ref:
            raise RuntimeError("请先选一个声音包")

        mi = mem_info()
        if mi:
            job_log(jid, "内存：可用物理 %d MB，可用提交量 %d MB" % (
                mi.get("physFreeMB"), mi.get("commitFreeMB")))
            if mi.get("commitFreeMB", 9999) < NEED_COMMIT_MB:
                raise RuntimeError(
                    "内存不够（可用提交量 %d MB，至少需要 %d MB）。\n"
                    "先关掉 ComfyUI 或其他后端再试；治本是把虚拟内存设成固定值"
                    "（初始 32768 / 最大 65536 MB）。" % (mi.get("commitFreeMB"), NEED_COMMIT_MB))

        stem = os.path.splitext(src_name)[0]
        stem = re.sub(r'[\\/:*?"<>|\s]+', "_", stem)[:40]
        wdir = os.path.join(WORK_DIR, jid)
        os.makedirs(wdir, exist_ok=True)
        os.makedirs(OUT_DIR, exist_ok=True)

        vocals = os.path.join(wdir, "vocals.wav")
        inst = os.path.join(wdir, "instrumental.wav")
        conv = os.path.join(wdir, "converted.wav")

        # 名字
        name = re.sub(r'[\\/:*?"<>|\s]+', "_", (p.get("name") or "").strip())[:40]
        if not name:
            name = "%s_翻唱_%s" % (stem, time.strftime("%Y%m%d-%H%M%S"))
        out = os.path.join(OUT_DIR, name + ".flac")
        n = 1
        while os.path.exists(out):
            out = os.path.join(OUT_DIR, "%s(%d).flac" % (name, n))
            n += 1

        job_log(jid, "歌曲：%s" % src_name)
        job_log(jid, "声音包：%s" % os.path.basename(ref))

        # ---- ① 分离 ----
        job_set(jid, stage="① 分离人声和伴奏", percent=3)
        _run_step(jid, [COMFY_PY, SEP_WORKER, "separate", src, vocals, inst],
                  None, "分离", 3, 38)

        polished = os.path.join(WORK_DIR, "人声_已去爆破.wav")

        # ---- ①·B 声音包太长就临时裁短 ----
        #   ★ 踩过大坑：Seed-VC 的处理窗口 = 30秒 − 参考音频长度 ✗
        #     40~50 秒的声音包会把窗口挤没 → 273 秒的人声只转换出 136 秒，
        #     剩下的一半还是原唱（主人听到的「还有原唱女声」）✓
        ref_use = ref
        slim = os.path.join(WORK_DIR, "声音包_裁短.wav")
        try:
            _run_step(jid, [COMFY_PY, SLIMREF_WORKER, ref, slim, str(MAX_REF_SEC)],
                      None, "整理声音包", 39, 40)
            if os.path.isfile(slim):
                import wave as _w
                with _w.open(slim, "rb") as _f:
                    _d = _f.getnframes() / float(_f.getframerate() or 1)
                with _w.open(ref, "rb") as _f:
                    _d0 = _f.getnframes() / float(_f.getframerate() or 1)
                if _d < _d0:
                    ref_use = slim
                    job_log(jid, "声音包 %.1f 秒 → 裁成 %.1f 秒（太长会把换声窗口挤没）" % (_d0, _d))
        except Exception as _e:
            job_log(jid, "· 声音包裁短跳过：%s" % _e)

        # ---- ② 声音转换 ----
        job_set(jid, stage="② 换成声音包的音色", percent=40)
        vc_args = [SEEDVC_PY, VC_WORKER,
                   "--source", vocals, "--target", ref_use, "--output", conv,
                   "--steps", str(int(p.get("steps") or 30)),
                   "--cfg", str(float(p.get("cfgRate") or 0.7)),
                   "--length-adjust", str(float(p.get("lengthAdjust") or 1.0)),
                   "--semi-tone", str(int(p.get("semiTone") or 0))]
        if p.get("f0", True):
            vc_args.append("--f0")
        _run_step(jid, vc_args, {"PYTHONPATH": SEEDVC_SRC}, "声音转换", 40, 84,
                  cwd=SEEDVC_SRC)

        # ---- ②·B 去爆破音（换声会放大 p/t/k 的低频气流冲击）----
        polish = float(p.get("polish") if p.get("polish") is not None else 0.6)
        if polish > 0.01:
            job_set(jid, stage="②·B 去爆破音", percent=85)
            _run_step(jid, [COMFY_PY, POLISH_WORKER, conv, polished, str(polish)],
                      None, "去爆破音", 84, 86)
            if os.path.isfile(polished):
                conv = polished
        else:
            job_log(jid, "· 去爆破音：关（强度 0）")

        # ---- ③ 混音 ----
        job_set(jid, stage="③ 和伴奏混音", percent=86)
        _run_step(jid, [COMFY_PY, SEP_WORKER, "mix", conv, inst, out,
                        str(float(p.get("vocalGain") or 1.0)),
                        str(float(p.get("instGain") or 0.9))],
                  None, "混音", 86, 98)

        if not os.path.isfile(out):
            raise RuntimeError("混音结束但没生成文件")

        size = os.path.getsize(out)
        secs = 0.0
        try:
            import wave
            with wave.open(out, "rb") as w:
                secs = round(w.getnframes() / float(w.getframerate() or 1), 2)
        except Exception:
            secs = 0.0

        job_set(jid, state="done", percent=100, stage="完成", result={
            "name": os.path.basename(out), "path": out,
            "size": size, "sizeText": human(size),
            "seconds": secs, "cost": round(time.time() - jid_t0(jid), 1)})
        job_log(jid, "✓ 翻唱完成：%s（%s）" % (os.path.basename(out), human(size)))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


def jid_t0(jid):
    j = JOBS.get(jid)
    return j["t0"] if j else time.time()
