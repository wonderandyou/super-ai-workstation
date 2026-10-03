#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— 本地生图模块（ComfyUI + Qwen-Image-2.1）
=============================================================
职责：
  1. 硬件检测（用 nvidia-smi，WMI 报的显存不准）
  2. ComfyUI 安装状态检测 / 一键下载安装（带断点续传 + SHA256 校验）
  3. 启动 / 停止 ComfyUI 后端
  4. 调 ComfyUI API 出图

下载源只用正规渠道：ModelScope（阿里官方）+ 官方 GitHub Releases。
   个人加速代理（gh-proxy / ghfast / hf-mirror 之流）一律不用 ✗
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
#  下载源
# --------------------------------------------------------------------------
MODELSCOPE = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"
MODEL_REPO = "Comfy-Org/Qwen-Image-2.1"

# ComfyUI 便携包（官方 GitHub Releases —— 不经任何第三方代理）
COMFY_VER = "v0.38.0"
COMFY_ASSET = "ComfyUI_windows_portable_nvidia.7z"
COMFY_URLS = [
    # 铁律一：只走官方 GitHub Releases —— 个人加速代理（gh-proxy / ghfast 等）一律不用 ✗
    #   国内直连 GitHub 可能很慢；下不动就如实报错，绝不偷偷换代理 ✓
    "https://github.com/Comfy-Org/ComfyUI/releases/download/%s/%s" % (COMFY_VER, COMFY_ASSET),
]

SEVENZR_URLS = [
    "https://www.7-zip.org/a/7zr.exe",
]

# 三个模型（SHA256 取自 ModelScope 接口，与本地已装文件一致）
MODEL_FILES = [
    {
        "key": "unet",
        "repo_path": "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
        "dest": os.path.join("ComfyUI", "models", "diffusion_models"),
        "name": "qwen_image_2.1_int8_convrot.safetensors",
        "size": 7256783064,
        "sha256": "cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d",
        "label": "扩散模型（出图主力）",
    },
    {
        "key": "clip",
        "repo_path": "text_encoders/qwen3vl_8b_w4a8.safetensors",
        "dest": os.path.join("ComfyUI", "models", "text_encoders"),
        "name": "qwen3vl_8b_w4a8.safetensors",
        "size": 6312105364,
        "sha256": "7754425e55e7bea2bfde4dde59a4cc236cb44e5ee9c215ea66ef8d47012824eb",
        "label": "文本编码器（读懂提示词）",
    },
    {
        "key": "vae",
        "repo_path": "vae/qwen_image_2.1_vae_bf16.safetensors",
        "dest": os.path.join("ComfyUI", "models", "vae"),
        "name": "qwen_image_2.1_vae_bf16.safetensors",
        "size": 675509688,
        "sha256": "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9",
        "label": "VAE（把潜空间还原成图）",
    },
]

UNET = MODEL_FILES[0]["name"]
CLIP = MODEL_FILES[1]["name"]
VAE = MODEL_FILES[2]["name"]

MODELS_TOTAL = sum(f["size"] for f in MODEL_FILES)

# 候选 ComfyUI 安装位置（自动探测）
# ★ 第一位 = 统一路径层：安装版 → <安装目录>\engines\ComfyUI；
#   开发机老位置还在 → 仍返回 D:\ComfyUI ✓
import paths as _paths
COMFY_HINTS = [
    _paths.comfy(),
    r"D:\ComfyUI",
    r"C:\ComfyUI",
    os.path.join(ROOT, "local", "ComfyUI_windows_portable"),
    os.path.join(ROOT, "local"),
    os.path.expanduser(r"~\ComfyUI"),
]

JOBS = {}
JOB_LOCK = threading.Lock()
COMFY_PROC = None


# --------------------------------------------------------------------------
#  工具
# --------------------------------------------------------------------------
def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
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
    line = time.strftime("%H:%M:%S") + "  " + msg
    j["log"].append(line)
    if len(j["log"]) > 400:
        del j["log"][:100]
    j["stage"] = msg


def job_set(jid, **kw):
    j = JOBS.get(jid)
    if j:
        j.update(kw)


# --------------------------------------------------------------------------
#  硬件检测
# --------------------------------------------------------------------------
def detect_hardware():
    info = {"gpu": None, "vram": 0, "driver": None, "ram": 0, "nvidiaSmi": False,
            "verdict": "unknown", "reason": "", "cuda": None}
    # 内存
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        info["ram"] = st.ullTotalPhys
    except Exception:
        pass

    # GPU（nvidia-smi 才准）
    smi = shutil.which("nvidia-smi")
    if not smi:
        for c in (r"C:\Windows\System32\nvidia-smi.exe",
                  r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.exists(c):
                smi = c
                break
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=12,
                creationflags=0x08000000 if os.name == "nt" else 0)
            line = (out.stdout or "").strip().splitlines()
            if line:
                parts = [x.strip() for x in line[0].split(",")]
                info["nvidiaSmi"] = True
                info["gpu"] = parts[0]
                info["vram"] = int(float(parts[1])) * 1024 * 1024 if len(parts) > 1 else 0
                info["driver"] = parts[2] if len(parts) > 2 else None
        except Exception:
            pass

    if not info["gpu"]:
        # ★ 2026-10-02：原来这里用 `wmic path win32_videocontroller`，
        #   但 **Win11 26100 已经把 wmic 移除** → 这段永远拿不到显卡名 ✗
        #   改成优先用 PowerShell 的 CIM（家里任何 Win10/11 都有），wmic 只当最后的兜底
        for cmd in (["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_VideoController | "
                     "Where-Object { $_.Name -like '*NVIDIA*' } | Select-Object -First 1).Name"],
                    ["wmic", "path", "win32_videocontroller", "get", "name"]):
            if info["gpu"]:
                break
            try:
                flags = 0x08000000 if os.name == "nt" else 0
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                     encoding="utf-8", errors="replace",
                                     creationflags=flags).stdout or ""
                for ln in out.splitlines():
                    ln = ln.strip()
                    if ln and "Name" not in ln:
                        info["gpu"] = ln
                        break
            except Exception:
                continue

    # 结论
    gb = info["vram"] / 2**30
    mib = info["vram"] / (1024 * 1024)
    ram_gb = info["ram"] / 2**30
    if not info["nvidiaSmi"] or not info["vram"]:
        info["verdict"] = "no-gpu"
        info["reason"] = "没检测到 NVIDIA 显卡驱动（nvidia-smi 不可用）。本地出图需要 NVIDIA 显卡。"
    elif mib < 5800:
        info["verdict"] = "too-small"
        info["reason"] = "显存只有 %.1f GB。Qwen-Image-2.1 量化版最低需要 6 GB 左右，可能跑不动。" % gb
    elif mib < 7600:
        info["verdict"] = "tight"
        info["reason"] = "显存 %.1f GB，能跑但建议出 1K 图、少开别的程序。" % gb
    else:
        info["verdict"] = "ok"
        info["reason"] = "显存 %.1f GB，可以流畅运行（1024×1024 / 25 步 约 1 分钟一张）。" % gb

    if ram_gb and ram_gb < 12:
        info["reason"] += " 内存只有 %.1f GB，首次加载 13 GB 权重会比较吃力。" % ram_gb
    return info


# --------------------------------------------------------------------------
#  ComfyUI 定位 / 状态
# --------------------------------------------------------------------------
def _looks_like_comfy(p):
    """判断某个目录是不是 ComfyUI 根目录（含 main.py 的那个）"""
    if not p or not os.path.isdir(p):
        return None
    # 形式 1: <root>\ComfyUI\main.py  + <root>\python_embeded\
    if os.path.exists(os.path.join(p, "ComfyUI", "main.py")):
        return p
    # 形式 2: <root>\main.py 且同级有 models（用户自己 clone 的）
    if os.path.exists(os.path.join(p, "main.py")) and os.path.isdir(os.path.join(p, "models")):
        return os.path.dirname(p)
    return None


def resolve_comfy(cfg=None):
    """
    找出 ComfyUI 根目录（含 ComfyUI\\main.py 和 python_embeded\\ 的那一层）。
    返回 dict(root, main, python, models, found)
    """
    cands = []
    if cfg:
        cr = (cfg.get("comfyRoot") or "").strip()
        if cr:
            cands.append(cr)
    cands += COMFY_HINTS
    for c in cands:
        r = _looks_like_comfy(c)
        if r:
            py = os.path.join(r, "python_embeded", "python.exe")
            return {
                "root": r,
                "main": os.path.join(r, "ComfyUI", "main.py"),
                "python": py if os.path.exists(py) else "python",
                "models": os.path.join(r, "ComfyUI", "models"),
                "found": True,
            }
    return {"root": "", "main": "", "python": "", "models": "", "found": False}


def check_models(cfg=None):
    """看三个模型在不在，返回每个的状态"""
    loc = resolve_comfy(cfg)
    out = []
    for f in MODEL_FILES:
        p = ""
        if loc["found"]:
            p = os.path.join(loc["models"],
                             "diffusion_models" if f["key"] == "unet" else
                             ("text_encoders" if f["key"] == "clip" else "vae"),
                             f["name"])
        ok = os.path.exists(p) and os.path.getsize(p) >= f["size"] * 0.995
        out.append({
            "key": f["key"], "name": f["name"], "label": f["label"],
            "size": f["size"], "sizeText": human(f["size"]),
            "path": p, "ok": ok,
            "actual": os.path.getsize(p) if os.path.exists(p) else 0,
        })
    return out


def check_7zr(cfg=None):
    """找一个能解压 .7z 的工具"""
    for c in (shutil.which("7z"), shutil.which("7zr"),
              r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe",
              os.path.join(ROOT, "local", "7zr.exe")):
        if c and os.path.exists(str(c)):
            return str(c)
    return ""


def port_open(port, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        s.close()


def comfy_api_ok(port=8188):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/system_stats" % int(port), timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def status(cfg=None):
    cfg = cfg or {}
    port = int(cfg.get("comfyPort") or 8188)
    loc = resolve_comfy(cfg)
    models = check_models(cfg)
    stats = comfy_api_ok(port)
    installed_models = sum(1 for m in models if m["ok"])
    missing_bytes = sum(m["size"] for m in models if not m["ok"])
    return {
        "platform": {"os": "windows", "pythonOk": True},
        "comfy": {
            "found": loc["found"],
            "root": loc["root"],
            "port": port,
            "running": bool(stats) or port_open(port),
            "apiOk": bool(stats),
            "version": (stats or {}).get("system", {}).get("comfyui_version") if stats else None,
            "device": ((stats or {}).get("devices") or [{}])[0].get("name") if stats else None,
        },
        "models": models,
        "modelsReady": installed_models == len(models),
        "missingBytes": missing_bytes,
        "missingText": human(missing_bytes),
        "sevenZip": check_7zr(cfg),
        "hardware": detect_hardware(),
        "needComfy": not loc["found"],
        "comfyAsset": {"name": COMFY_ASSET, "version": COMFY_VER,
                       "size": 1997159792, "sizeText": "1.86 GB"},
    }


# --------------------------------------------------------------------------
#  自带 ComfyUI 自定义节点（随包分发，自动部署）
# ==========================================================================
NODES_SRC = os.path.join(ROOT, "comfy_nodes")
AUTO_NODES = ["aiws_audio_chunked.py"]      # 分块音频解码：长音频必备，少了会崩


def custom_nodes_dir():
    """找到 ComfyUI 的 custom_nodes 目录（找不到返回空串）"""
    loc = resolve_comfy()
    if not loc:
        return ""
    d = os.path.join(loc["root"], "ComfyUI", "custom_nodes")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return ""
    return d


def deploy_nodes(force=False):
    """把随包带的节点放进 ComfyUI 的 custom_nodes（内容一样就跳过）"""
    d = custom_nodes_dir()
    if not d:
        return {"ok": False, "error": "还没找到 ComfyUI —— 先在「本地千问生图」里一键下载安装"}
    if not os.path.isdir(NODES_SRC):
        return {"ok": False, "error": "包里没带节点目录：%s" % NODES_SRC}
    done, skip = [], []
    for n in AUTO_NODES:
        src = os.path.join(NODES_SRC, n)
        dst = os.path.join(d, n)
        if not os.path.isfile(src):
            continue
        same = False
        if os.path.isfile(dst):
            try:
                same = open(src, "rb").read() == open(dst, "rb").read()
            except Exception:
                same = False
        if same and not force:
            skip.append(n)
            continue
        try:
            shutil.copy2(src, dst)
            done.append(n)
        except Exception as e:
            return {"ok": False, "error": "复制 %s 失败：%s" % (n, e)}
    return {"ok": True, "dir": d, "copied": done, "skipped": skip}


#  启动 / 停止 ComfyUI
# --------------------------------------------------------------------------
def start_comfy(cfg=None):
    global COMFY_PROC
    cfg = cfg or {}
    port = int(cfg.get("comfyPort") or 8188)
    if comfy_api_ok(port):
        deploy_nodes()   # 起之前顺手把自带节点放进去（一样就跳过）
        return {"ok": True, "already": True, "message": "ComfyUI 已经在运行"}
    loc = resolve_comfy(cfg)
    if not loc["found"]:
        return {"ok": False, "error": "还没安装 ComfyUI —— 先点「一键下载并安装」"}
    if not os.path.exists(loc["python"]):
        loc["python"] = "python"
    args = [loc["python"], "-s", loc["main"],
            "--windows-standalone-build", "--disable-auto-launch",
            "--port", str(port), "--listen", "127.0.0.1"]
    logdir = os.path.join(ROOT, "data")
    os.makedirs(logdir, exist_ok=True)
    logf = open(os.path.join(logdir, "comfy.log"), "a", encoding="utf-8", buffering=1)
    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00000200      # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        COMFY_PROC = subprocess.Popen(
            args, cwd=os.path.join(loc["root"], "ComfyUI"),
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=flags)
    except Exception as e:
        return {"ok": False, "error": "启动失败：%s" % e}
    return {"ok": True, "pid": COMFY_PROC.pid, "message": "已启动，正在加载模型…"}


def stop_comfy(cfg=None):
    """关掉 ComfyUI。

    ★ 2026-10-02 修：原来兜底用 `wmic process ... get CommandLine`，
      但 **wmic 在这台 Win11 上已经被移除** → 兜底永远失败，
      还返回 {"ok": True, "killed": []} 让人以为关成功了 ✗
      现在改成「按监听端口找 PID 再 taskkill」，不依赖 wmic ✓
    """
    global COMFY_PROC
    cfg = cfg or {}
    port = int(cfg.get("comfyPort") or 8188)
    killed = []
    if COMFY_PROC and COMFY_PROC.poll() is None:
        try:
            COMFY_PROC.terminate()
            killed.append(COMFY_PROC.pid)
        except Exception:
            pass
    # 兜底①：按端口找（最可靠，不依赖 wmic）
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags).stdout or ""
        for ln in out.splitlines():
            if (":%d " % port) in ln and "LISTENING" in ln.upper():
                pid = ln.split()[-1]
                if pid.isdigit() and int(pid) not in killed:
                    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                                   capture_output=True, creationflags=flags)
                    killed.append(int(pid))
    except Exception:
        pass
    # 兜底②：老机器上 wmic 还在的话，顺手按命令行再扫一遍（失败无所谓）
    if not killed:
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine"],
                capture_output=True, text=True, timeout=15)
            for ln in (out.stdout or "").splitlines():
                if "main.py" in ln and "ComfyUI" in ln:
                    m = re.search(r"(\d+)\s*$", ln.strip())
                    if m:
                        subprocess.run(["taskkill", "/F", "/PID", m.group(1)], capture_output=True)
                        killed.append(int(m.group(1)))
        except Exception:
            pass
    return {"ok": True, "killed": killed}


# --------------------------------------------------------------------------
#  下载 / 安装
# --------------------------------------------------------------------------
def http_get_stream(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def download_file(urls, dest, expect_size=0, sha256="", jid=None, label="",
                  progress_cb=None):
    """
    下载到 dest，支持断点续传；返回 (ok, message)
    urls 可以是字符串或列表（依次尝试）
    """
    if isinstance(urls, str):
        urls = [urls]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"

    # 已完成且校验通过？
    if os.path.exists(dest) and expect_size and abs(os.path.getsize(dest) - expect_size) < 1024:
        if not sha256 or sha256_of(dest) == sha256:
            return True, "已存在"
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except Exception:
            pass

    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    last_err = ""
    for u in urls:
        try:
            headers = {"User-Agent": "Mozilla/5.0 AIWorkstation"}
            if have > 0:
                headers["Range"] = "bytes=%d-" % have
            with http_get_stream(u, headers, timeout=60) as r:
                code = getattr(r, "status", 200) or 200
                if have > 0 and code != 206:       # 服务器不支持续传 → 重头来
                    have = 0
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                total = int(r.headers.get("Content-Length") or 0) + have
                if expect_size:
                    total = expect_size
                mode = "ab" if have and code == 206 else "wb"
                done = have
                t0 = time.time()
                last_t = t0
                last_d = done
                h = hashlib.sha256()
                if mode == "ab" and os.path.exists(tmp):
                    # 续传时把已有部分补进哈希
                    with open(tmp, "rb") as f:
                        while True:
                            b = f.read(1 << 22)
                            if not b:
                                break
                            h.update(b)
                with open(tmp, mode) as f:
                    while True:
                        buf = r.read(1 << 20)
                        if not buf:
                            break
                        f.write(buf)
                        h.update(buf)
                        done += len(buf)
                        now = time.time()
                        if now - last_t >= 0.5:
                            spd = (done - last_d) / max(0.001, now - last_t)
                            last_t, last_d = now, done
                            if progress_cb:
                                progress_cb(done, total, spd)
                            if jid:
                                pct = (done / total * 100.0) if total else 0
                                job_set(jid, percent=round(pct, 1))
            # 下载完 → 校验
            if expect_size and abs(os.path.getsize(tmp) - expect_size) > 1024:
                last_err = "大小不对：%s / 应为 %s" % (
                    human(os.path.getsize(tmp)), human(expect_size))
                continue
            if sha256:
                if jid:
                    job_log(jid, "正在校验 %s 的 SHA256…" % (label or os.path.basename(dest)))
                got = sha256_of(tmp)
                if got.lower() != sha256.lower():
                    last_err = "SHA256 不匹配（下载可能损坏）"
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                    have = 0
                    continue
            os.replace(tmp, dest)
            return True, "完成"
        except Exception as e:
            last_err = "%s" % e
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            continue
    return False, last_err or "下载失败"


def sha256_of(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def install_worker(jid, cfg):
    """一键安装：7zr -> ComfyUI 便携包 -> 解压 -> 三个模型"""
    try:
        cfg = cfg or {}
        inst = (cfg.get("installDir") or _paths.engines_root()).strip()
        os.makedirs(inst, exist_ok=True)
        job_set(jid, percent=0, detail="准备安装目录")

        # ---------- 0. 先看磁盘够不够 ----------
        loc0 = resolve_comfy(cfg)
        need = 0
        if not loc0["found"]:
            need += 1997159792                      # 便携包（解压后还要更多）
            need += 6 * 1024 ** 3                   # 解压膨胀余量
        for f in MODEL_FILES:
            sub = ("diffusion_models" if f["key"] == "unet" else
                   ("text_encoders" if f["key"] == "clip" else "vae"))
            if loc0["found"] and os.path.exists(os.path.join(loc0["models"], sub, f["name"])):
                continue
            need += f["size"]
        try:
            import shutil as _sh
            free = _sh.disk_usage(inst).free
            job_log(jid, "磁盘检查：需要约 %s，可用 %s" % (human(need), human(free)))
            if free < need + 2 * 1024 ** 3:
                raise RuntimeError(
                    "磁盘空间不够：还需要约 %s，当前盘只剩 %s。\n"
                    "请先清理空间，或在设置里把安装目录改到别的盘。"
                    % (human(need + 2 * 1024 ** 3), human(free)))
        except RuntimeError:
            raise
        except Exception:
            pass
        job_set(jid, percent=0, detail="准备安装目录")

        # ---------- 1. 7zr（解压工具）----------
        z = check_7zr(cfg)
        if not z:
            job_log(jid, "[1/4] 下载解压工具 7zr.exe（600 KB）")
            z = os.path.join(inst, "7zr.exe")
            ok, msg = download_file(SEVENZR_URLS, z, jid=jid, label="7zr.exe")
            if not ok:
                raise RuntimeError("下载 7zr.exe 失败：%s" % msg)
            job_log(jid, "    解压工具就绪")
        else:
            job_log(jid, "[1/4] 已有解压工具：%s" % z)

        # ---------- 2. ComfyUI 便携包 ----------
        loc = resolve_comfy(cfg)
        if not loc["found"]:
            pkg = os.path.join(inst, COMFY_ASSET)
            job_log(jid, "[2/4] 下载 ComfyUI 便携包（1.86 GB，官方 GitHub Releases）")
            job_set(jid, stage="下载 ComfyUI 便携包", percent=0)
            # ★ 大小与 sha256 都取自 GitHub 官方 API（releases/tags/v0.38.0）：
            #   以前这里写的是 1997159792（错的，跟官方差 163 KB），会导致下载完被判失败 ✗
            ok, msg = download_file(COMFY_URLS, pkg, expect_size=1994326521,
                                    sha256="8f137eac345707fd7e42bcf8e29377415243011ca15522a86aed6c77331fbd56",
                                    jid=jid, label="ComfyUI 便携包")
            if not ok:
                raise RuntimeError("下载 ComfyUI 失败：%s" % msg)

            job_log(jid, "[3/4] 解压 ComfyUI（约 1~3 分钟，请稍等）")
            job_set(jid, stage="解压 ComfyUI", percent=0, detail="解压中…")
            r = subprocess.run([z, "x", pkg, "-o" + inst, "-y", "-bso0", "-bsp0"],
                               capture_output=True, text=True, timeout=3600,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            if r.returncode != 0:
                raise RuntimeError("解压失败（7z 返回 %d）：%s" % (r.returncode, (r.stderr or "")[:200]))
            try:
                os.remove(pkg)
                job_log(jid, "    已删除便携包原档，省出 1.86 GB")
            except Exception:
                pass
            loc = resolve_comfy({"comfyRoot": os.path.join(inst, "ComfyUI_windows_portable")})
            if not loc["found"]:
                loc = resolve_comfy({})
            if not loc["found"]:
                raise RuntimeError("解压完了但找不到 ComfyUI\\main.py，目录结构可能不对")
            job_log(jid, "    ComfyUI 就绪：%s" % loc["root"])
        else:
            job_log(jid, "[2/4] 已检测到 ComfyUI：%s，跳过下载" % loc["root"])
            job_log(jid, "[3/4] 跳过解压")

        # ---------- 3. 三个模型 ----------
        job_log(jid, "[4/4] 下载模型（共 13.27 GB，支持断点续传）")
        total_need = 0
        done_before = 0
        todo = []
        for f in MODEL_FILES:
            sub = ("diffusion_models" if f["key"] == "unet" else
                   ("text_encoders" if f["key"] == "clip" else "vae"))
            dest = os.path.join(loc["models"], sub, f["name"])
            ok = os.path.exists(dest) and abs(os.path.getsize(dest) - f["size"]) < 1024
            if ok:
                job_log(jid, "    ✓ %s 已存在（%s）" % (f["label"], human(f["size"])))
                continue
            todo.append((f, dest))
            total_need += f["size"]

        if not todo:
            job_log(jid, "    三个模型都已就绪，无需下载")
        else:
            job_log(jid, "    需要下载 %s / 3 个文件" % human(total_need))
            for idx, (f, dest) in enumerate(todo, 1):
                job_set(jid, stage="下载 %s" % f["label"])
                job_log(jid, "    [%d/%d] %s（%s）" % (idx, len(todo), f["name"], human(f["size"])))

                def cb(done, total, spd, _f=f, _base=done_before, _need=total_need):
                    overall = (_base + done) / _need * 100.0
                    job_set(jid, percent=round(overall, 1),
                            detail="%s / %s   %s/s" % (human(done), human(total), human(spd)))

                url = MODELSCOPE.format(repo=MODEL_REPO,
                                        path=urllib.parse.quote(f["repo_path"]))
                ok, msg = download_file([url], dest, expect_size=f["size"],
                                        sha256=f["sha256"], jid=jid,
                                        label=f["name"], progress_cb=cb)
                if not ok:
                    raise RuntimeError("下载 %s 失败：%s" % (f["name"], msg))
                done_before += f["size"]
                job_log(jid, "    ✓ %s 完成" % f["name"])

        job_log(jid, "全部完成！")
        job_set(jid, state="done", percent=100,
                result={"comfyRoot": loc["root"], "installDir": inst})
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


# --------------------------------------------------------------------------
#  出图
# --------------------------------------------------------------------------
ASPECTS = {"1:1": (1, 1), "3:4": (3, 4), "4:3": (4, 3),
           "2:3": (2, 3), "3:2": (3, 2), "16:9": (16, 9), "9:16": (9, 16)}


def calc_size(aspect, megapixels):
    a, b = ASPECTS.get(aspect or "1:1", (1, 1))
    total = max(0.2, float(megapixels or 1.0)) * 1_000_000
    h = (total / (a / b)) ** 0.5
    w = h * a / b
    return max(256, int(round(w / 32) * 32)), max(256, int(round(h / 32) * 32))


def _comfy_input_dir(cfg):
    r"""找 ComfyUI **真正的** input 目录 ✓

    踩过：`resolve_comfy()` 返回的是便携版根（`D:\ComfyUI`），
    而真正的主目录是 `D:\ComfyUI\ComfyUI`（含 main.py / custom_nodes / models）✗
    → 底图存到了外面，LoadImage 找不到文件 → ComfyUI 直接回 400 ✓

    判据：哪个目录里有 custom_nodes 或 main.py，它就是 ComfyUI 主目录 ✓
    """
    loc = resolve_comfy(cfg or {}) or {}
    root = str(loc.get("root") or "").rstrip("\\/")
    if not root:
        raise RuntimeError("没找到 ComfyUI 目录")
    cands = [root, os.path.join(root, "ComfyUI")]
    for c in cands:
        if not os.path.isdir(c):
            continue
        if os.path.isdir(os.path.join(c, "custom_nodes")) or \
           os.path.isfile(os.path.join(c, "main.py")):
            return os.path.join(c, "input")
    return os.path.join(root, "input")


def _prep_init_image(cfg, dataurl, w, h):
    r"""图生图：把底图缩放成目标尺寸，存进 ComfyUI 的 input 目录，返回文件名 ✓

    为什么要缩放：VAEEncode 会保留原图尺寸 ✗ 不先对齐的话，
    输出尺寸会跟着底图走，用户在界面上选的宽高比就失效了 ✓
    """
    import base64 as _b64
    import io as _io
    try:
        from PIL import Image
    except Exception as e:
        raise RuntimeError("缺 Pillow，无法处理底图：%s" % e)
    s = str(dataurl or "")
    raw = s.split(",", 1)[1] if "," in s else s
    data = _b64.b64decode(raw)
    im = Image.open(_io.BytesIO(data))
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    inp = _comfy_input_dir(cfg)
    os.makedirs(inp, exist_ok=True)
    name = "aiws_init_%s_%dx%d.png" % (time.strftime("%Y%m%d-%H%M%S"), w, h)
    im.save(os.path.join(inp, name), "PNG")
    return name


def build_workflow(p):
    """Qwen-Image-2.1 工作流（复用已验证的节点组合，全为 ComfyUI 核心节点）

    ★ 2026-10-02 修的两个坑：
      ① 种子：界面上写着「0 = 随机」，但 0 原样传给 KSampler 就是**固定种子 0**，
         于是每次出的图一模一样 ✗ → 这里把 0 / -1 / 空 真的换成随机种子
      ② 参考图：这个模型是**编辑模型**，参考图必须经 TextEncodeQwenImage21 的
         `images.image_1` 喂进视觉塔，latent 也要取该节点的输出（denoise=1.0）。
         原来走 LoadImage→VAEEncode 的话，**参考图根本没进模型**，出图纯看提示词 ✗
    """
    import math
    import random

    w, h = calc_size(p.get("aspect") or "1:1", p.get("megapixels") or 1.0)

    try:
        seed = int(p.get("seed") or 0)
    except Exception:
        seed = 0
    if seed in (0, -1):                       # 界面上「0 = 随机」要说话算数 ✓
        seed = random.randint(1, 2 ** 31 - 1)

    wf = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "5": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": p.get("prompt") or "",
                         "negative_prompt": p.get("negative") or "",
                         "resolution": int(p.get("resolution") or 1024)}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "7": {"class_type": "QwenImage21Cache",
              "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["7", 0],
            "seed": seed,
            "steps": max(1, min(60, int(p.get("steps") or 25))),
            "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
            "positive": ["5", 0], "negative": ["5", 1],
            "latent_image": ["6", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "aiws_qwen"}},
    }

    init = str(p.get("initImage") or "").strip()
    if init:
        # 参考图模式：走这个编辑模型的官方用法 ✓
        wf["4"] = {"class_type": "LoadImage", "inputs": {"image": init}}
        wf["5"]["inputs"]["vae"] = ["3", 0]                  # ★ 给了 vae 才会编码参考图
        wf["5"]["inputs"]["images.image_1"] = ["4", 0]       # ★ 参考图进视觉塔
        # 节点会按 resolution 和参考图比例算出空 latent —— 让它的尺寸等于用户选的尺寸
        wf["5"]["inputs"]["resolution"] = int(round(math.sqrt(max(1, w * h))))
        wf["8"]["inputs"]["latent_image"] = ["5", 2]         # ★ 用节点给的 latent
        wf["8"]["inputs"]["denoise"] = 1.0                   # 这条路必须 1.0（节点如此设计）
        wf.pop("6", None)                                    # 不再需要空 latent 节点
    else:
        wf["8"]["inputs"]["denoise"] = 1.0
    return wf, (w, h)


def comfy_post(port, path, obj, timeout=60):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def comfy_get(port, path, timeout=60):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def generate_worker(jid, cfg, req):
    try:
        cfg = cfg or {}
        port = int(cfg.get("comfyPort") or 8188)
        if not comfy_api_ok(port):
            raise RuntimeError("ComfyUI 后端没在运行 —— 先点右上角「启动后端」")
        # ★ 图生图：先把底图对齐到目标尺寸再拼工作流 ✓
        req = dict(req or {})
        _w, _h = calc_size(req.get("aspect") or "1:1", req.get("megapixels") or 1.0)
        if req.get("initImage"):
            job_log(jid, "正在处理底图（缩放到 %d×%d）…" % (_w, _h))
            req["initImage"] = _prep_init_image(cfg, req["initImage"], _w, _h)
            job_log(jid, "底图就绪 —— 参考图模式（由模型决定改动幅度，不再看「重绘强度」）")
        wf, (w, h) = build_workflow(req)
        used_seed = wf["8"]["inputs"]["seed"]
        job_log(jid, "已提交任务（%s，%d×%d，%s 步，种子 %d）" % (
            "参考图模式" if req.get("initImage") else "文生图",
            w, h, req.get("steps") or 25, used_seed))
        job_set(jid, stage="排队中")
        r = comfy_post(port, "/prompt", {"prompt": wf, "client_id": "aiws-" + jid})
        pid = r.get("prompt_id")
        if not pid:
            raise RuntimeError("提交被拒绝：%s" % json.dumps(r, ensure_ascii=False)[:300])

        t0 = time.time()
        while True:
            time.sleep(1.5)
            el = time.time() - t0
            job_set(jid, percent=round(min(95, 95 * (1 - pow(2.718, -el / 60.0))), 1),
                    stage="生成中", detail="已等待 %d 秒" % el)
            if el > 1800:
                raise RuntimeError("超时（30 分钟）")
            hh = comfy_get(port, "/history/" + pid, timeout=20)
            if pid not in hh:
                continue
            entry = hh[pid]
            outs = (entry.get("outputs") or {}).get("10", {}).get("images") or []
            if not outs:
                st = entry.get("status") or {}
                if st.get("status_str") == "error":
                    raise RuntimeError("ComfyUI 报错，详见 data/comfy.log")
                continue
            # 保存图片
            # ★ 原来这里错用了 outputDir —— 那是**豆包生图**的目录，
            #   所以千问出的图全跑进「豆包出图」，作品库里看着像两家反了 ✗
            out_dir = (cfg.get("qwenOutDir")
                       or os.path.join(os.path.expanduser("~"), "Desktop", "千问1生图"))
            os.makedirs(out_dir, exist_ok=True)
            saved = []
            stamp = time.strftime("%Y%m%d-%H%M%S")
            for i, im in enumerate(outs):
                q = "?filename=%s&subfolder=%s&type=output" % (
                    urllib.parse.quote(im.get("filename", "")),
                    urllib.parse.quote(im.get("subfolder", "") or ""))
                with urllib.request.urlopen("http://127.0.0.1:%d/view%s" % (port, q), timeout=180) as rr:
                    blob = rr.read()
                name = "qwen_%s_%dx%d_%02d.png" % (stamp, w, h, i + 1)
                path = os.path.join(out_dir, name)
                with open(path, "wb") as f:
                    f.write(blob)
                saved.append({"file": path, "name": name, "size": len(blob),
                              "prompt": req.get("prompt") or "", "model": "Qwen-Image-2.1 (本地)",
                              "reqSize": "%dx%d" % (w, h), "cost": 0, "seed": used_seed,
                              "initImage": bool(req.get("initImage")),
                              "time": int(time.time()), "seconds": round(time.time() - t0, 1)})
            job_set(jid, state="done", percent=100, stage="完成", result=saved)
            return
    except Exception as e:
        job_set(jid, state="error", error=str(e))


# 模块加载时就试一次：ComfyUI 已装的话，顺手把自带节点放进去（失败不抛）
try:
    _NODES_RESULT = deploy_nodes()
except Exception as _e:
    _NODES_RESULT = {"ok": False, "error": str(_e)}
