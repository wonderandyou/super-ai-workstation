#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— 后端服务
=============================
只用 Python 标准库，不需要 pip 安装任何东西。

架构：
    web/           前端静态文件（标签页界面）
    data/config.json  配置（API Key、输出目录、默认模型…）
    data/history.json 出图历史
    output/        默认出图目录

接口：
    GET  /                    前端页面
    GET  /static/*            前端资源
    GET  /api/config          读配置
    POST /api/config          改配置
    POST /api/generate        提交生成任务 -> {job}
    GET  /api/progress?job=   查任务进度
    GET  /api/recent          最近出图
    POST /api/openfolder      打开输出目录
    POST /api/openfile        用系统程序打开某张图
    POST /api/delete          删一张图
    GET  /api/health          健康检查
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys

# ---------------------------------------------------------------------------
#  ★ 全局禁止弹黑框
#  工作站自己是用 pythonw（无控制台）跑的，但子进程仍可能自己弹一个一闪而过的
#  cmd 窗口（netstat / taskkill / schtasks / wmic 这类）。这里给 subprocess 打上
#  默认的 CREATE_NO_WINDOW：**一处生效、处处生效**，以后新增的调用也自动带上 ✓
#  （各 *_ai.py 里 `import subprocess` 拿到的是同一个模块对象，所以一样生效）
# ---------------------------------------------------------------------------
if os.name == "nt":
    _Popen_orig = subprocess.Popen

    class _NoWindowPopen(_Popen_orig):
        def __init__(self, *a, **kw):
            kw["creationflags"] = int(kw.get("creationflags") or 0) | 0x08000000
            super().__init__(*a, **kw)

    subprocess.Popen = _NoWindowPopen

import threading

# ---------------------------------------------------------------------------
#  ★ AI 生视频（智谱 CogVideoX-Flash，免费）—— 2026-10-01 新增
# ---------------------------------------------------------------------------
try:
    import video_gen_ai
    VGEN_OK = True
    _VGEN_ERR = ""
except Exception as _e:
    video_gen_ai = None
    VGEN_OK = False
    _VGEN_ERR = str(_e)

# ---------------------------------------------------------------------------
#  ★ Live2D 制作（立绘 → 分层 PSD → moc3）—— 2026-10-02
#  拆层两种方式：本地引擎（本机显卡）/ 在线官方演示（作者 ModelScope）
#  后面两步走本机 PSD2Live 的 MCP（导入 PSD → 导出模型）
# ---------------------------------------------------------------------------
try:
    import live2d_ai
    LIVE2D_OK = True
    _LIVE2D_ERR = ""
except Exception as _e:
    live2d_ai = None
    LIVE2D_OK = False
    _LIVE2D_ERR = str(_e)
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ---------------- AI 音乐工坊（独立服务，嵌进本工作站） ----------------
def port_open(port, host="127.0.0.1", timeout=0.6):
    """端口上有没有人在监听"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def listen_pids(port):
    """谁在监听这个端口（返回 PID 集合）"""
    flags = 0x08000000 if os.name == "nt" else 0
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags).stdout or ""
        for ln in out.splitlines():
            if (":%d " % int(port)) in ln and "LISTENING" in ln.upper():
                parts = ln.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
    except Exception:
        pass
    return pids


def kill_listen_port(port):
    """把监听某端口的进程结束掉（★ 只认 LISTENING 的那个 PID，不误杀别的）"""
    flags = 0x08000000 if os.name == "nt" else 0
    n = 0
    for pid in listen_pids(port):
        try:
            subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                           capture_output=True, creationflags=flags)
            n += 1
        except Exception:
            pass
    return n


_MUSIC_LOCK = threading.Lock()


def _mem_free_mb():
    """可用提交量（MB）。返回 None 表示查不到。

    为什么盯「提交量」而不是「可用物理内存」：这台机器 16 GB，
    真正卡死的是 commit charge —— 报错是 os error 1455「页面文件太小」。
    """
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
        return round(st.ullAvailPageFile / 2 ** 20)
    except Exception:
        return None


def ensure_music_app(port=7870):
    """确保音乐工坊在跑；没在跑就用 pythonw 静默拉起来（零控制台闪烁）。

    音乐工坊是独立工程（桌面\\AI音乐工坊），自带 app.py 监听 7870。
    **按需启动** —— 只有打开「🎵 AI 音乐工坊」标签页（会调 /api/music/status）时才拉起，
    不在工作站启动时拉。这台机器内存紧，同时开太多后端必爆提交量。
    """
    if port_open(port):
        return True
    with _MUSIC_LOCK:
        if port_open(port):              # 双检，避免并发重复启动
            return True
        # 内存不够就别硬上 —— 音乐工坊要驱动 ComfyUI 跑 ACE-Step，本身也要几百 MB
        free = _mem_free_mb()
        if free is not None and free < 1500:
            raise RuntimeError(
                "内存不够（可用提交量只剩 %d MB），先关掉 ComfyUI 或浏览器再打开这一页。\n"
                "治本办法：把 Windows 虚拟内存设成固定值（初始 32768 / 最大 65536 MB）。" % free)
        conf = load_config()
        mdir = conf.get("musicDir") or os.path.join(os.path.expanduser("~"), "Desktop", "AI音乐工坊")
        app = os.path.join(mdir, "app.py")
        if not os.path.isfile(app):
            raise RuntimeError("找不到音乐工坊：%s" % app)
        # 跟着当前解释器找 pythonw（不写死谁的用户目录）✓
        _pd = os.path.dirname(sys.executable)
        cands = [os.path.join(_pd, "pythonw.exe"),
                 os.path.join(_pd, "python.exe"), sys.executable]
        exe = next((c for c in cands if os.path.isfile(c)), sys.executable)
        flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
        subprocess.Popen([exe, app], cwd=mdir, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
        for _ in range(60):                                # 最多等 30 秒
            time.sleep(0.5)
            if port_open(port):
                return True
        raise RuntimeError("音乐工坊启动了但 %d 端口没起来" % port)


# 本地生图模块（ComfyUI + Qwen-Image-2.1）
try:
    import local_ai
    LOCAL_OK = True
except Exception as _e:          # 缺文件也不至于整个服务起不来
    local_ai = None
    LOCAL_OK = False
    _LOCAL_ERR = str(_e)

# DSH（DeepSeek Harness）安装 / 启动模块
try:
    import dsh_ai
    DSH_OK = True
except Exception as _e:
    dsh_ai = None
    DSH_OK = False
    _DSH_ERR = str(_e)

# 生视频模块（网易有道智云）
try:
    import video_ai
    VIDEO_OK = True
except Exception as _e:
    video_ai = None
    VIDEO_OK = False
    _VIDEO_ERR = str(_e)

# 电脑工具百宝箱（内存释放 + 多线程下载器）
try:
    import toolbox
    TOOL_OK = True
except Exception as _e:
    toolbox = None
    TOOL_OK = False
    _TOOL_ERR = str(_e)

# AI 溶图（把抠好的主体放进风景照）
try:
    import blend_ai
    BLEND_OK = True
except Exception as _e:
    blend_ai = None
    BLEND_OK = False
    _BLEND_ERR = str(_e)

# AI 搜索引擎（联网搜索 + 分点论述 + 来源；照 DSH 内核的 dsh-web-search-deepseek 做）
try:
    import search_ai
    SEARCH_OK = True
except Exception as _e:
    search_ai = None
    SEARCH_OK = False
    _SEARCH_ERR = str(_e)

# AI 人声分离（Demucs htdemucs，Meta 官方 MIT）
try:
    import stem_ai
    STEM_OK = True
except Exception as _e:
    stem_ai = None
    STEM_OK = False
    _STEM_ERR = str(_e)

# 作品库（汇总各标签页的产出，按来源分类）
try:
    import gallery_ai
    GALLERY_OK = True
except Exception as _e:
    gallery_ai = None
    GALLERY_OK = False
    _GALLERY_ERR = str(_e)

# 本地模型一键下载（来源只用 ModelScope 官方 / 厂商官方 CDN，下完逐个验官方哈希）
try:
    import model_ai
    MODEL_OK = True
except Exception as _e:
    model_ai = None
    MODEL_OK = False
    _MODEL_ERR = str(_e)


def _models_worker(jid, group):
    """后台跑模型下载。成功失败都要把状态标好 —— 不然界面会一直转 ✗"""
    try:
        ok = model_ai.install(jid, group)
        model_ai.job_set(jid, state="done" if ok else "failed")
    except Exception as e:
        model_ai.job_log(jid, "★ 出错：%s" % e)
        model_ai.job_set(jid, state="failed")


# 运行环境（引擎一键装）：给「声音包朗读」「AI 翻唱」装独立的 Python 环境
#   全程官方渠道：python.org 官方安装包 → **验签** → 静默安装 → venv →
#   PyPI 官方依赖（torch 走 PyTorch 官方索引）→ 克隆官方源码 ✓
try:
    import style_ai
    STYLE_OK = True
except Exception as _e:
    style_ai = None
    STYLE_OK = False
    _STYLE_ERR = str(_e)


try:
    import engine_ai
    ENGINE_OK = True
except Exception as _e:
    engine_ai = None
    ENGINE_OK = False
    _ENGINE_ERR = str(_e)

# AI 抠图（原生桥接 —— 复用 Documents\抠图 那套 RMBG ONNX 工具）
try:
    import matting_ai
    MATTING_OK = True
except Exception as _e:
    matting_ai = None
    MATTING_OK = False
    _MATTING_ERR = str(_e)

# AI 音乐工坊（原生桥接 —— 直接复用桌面那个工程的代码）
try:
    import music_ai
    MUSIC_OK = True
except Exception as _e:
    music_ai = None
    MUSIC_OK = False
    _MUSIC_ERR = str(_e)

# AI 翻唱（Demucs 分离 + Seed-VC 声音转换）
try:
    import cover_ai
    COVER_OK = True
except Exception as _e:
    cover_ai = None
    COVER_OK = False
    _COVER_ERR = str(_e)

# 声音包朗读（F5-TTS，跑在它自己的 venv 里）
try:
    import tts_ai
    TTS_OK = True
except Exception as _e:
    tts_ai = None
    TTS_OK = False
    _TTS_ERR = str(_e)

# 校园网一键登录（深澜 Srun）
try:
    import campus
    CAMPUS_OK = True
except Exception as _e:
    campus = None
    CAMPUS_OK = False
    _CAMPUS_ERR = str(_e)

# --------------------------------------------------------------------------
#  路径
# --------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

# ★ 版本号：从 web/index.html 里自动读（改了页面就不会忘同步）
def app_version():
    try:
        t = open(os.path.join(WEB, "index.html"), encoding="utf-8",
                 errors="replace").read()
        mm = re.search(r"βv(\d+\.\d+)", t)
        if mm:
            return "βv" + mm.group(1)
    except Exception:
        pass
    return "βv?"


APP_VERSION = app_version()

DATA = os.path.join(ROOT, "data")
CONFIG_PATH = os.path.join(DATA, "config.json")
HISTORY_PATH = os.path.join(DATA, "history.json")
os.makedirs(DATA, exist_ok=True)

PORT = 8200
HOST = "127.0.0.1"

# 火山方舟（豆包 Seedream）
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
ARK_GEN = ARK_BASE + "/images/generations"
ARK_MODELS = ARK_BASE + "/models"

DEFAULT_MODELS = [
    "doubao-seedream-5-0-pro-260628",
    "doubao-seedream-5-0-flash-260915",
    "doubao-seedream-5-0-260128",
    "doubao-seedream-4-5-251128",
    "doubao-seedream-4-0-250828",
    "doubao-seedream-4-0-20260415",
]

# 官方刊例价（元/张），来自原 HTA，可在设置里改
DEFAULT_PRICES = {
    "doubao-seedream-5-0-pro": {"low": 0.30, "high": 0.50},
    "doubao-seedream-5-0-flash": {"low": 0.10, "high": 0.20},
    "doubao-seedream-5-0": {"low": 0.20, "high": 0.35},
    "doubao-seedream-4-5": {"low": 0.20, "high": 0.30},
    "doubao-seedream-4-0": {"low": 0.15, "high": 0.25},
}

DEFAULT_CONFIG = {
    "apikey": "",
    "outputDir": os.path.join(os.path.expanduser("~"), "Desktop", "豆包出图"),
    # ★ 本地千问生图自己的输出目录
    #   （原来它错用了 outputDir，图全跑到豆包目录里去了 ✗）
    "qwenOutDir": os.path.join(os.path.expanduser("~"), "Desktop", "千问1生图"),
    "model": DEFAULT_MODELS[0],
    "size": "2K",
    "customSize": "",
    "format": "png",
    "watermark": False,
    "models": DEFAULT_MODELS,
    "prices": DEFAULT_PRICES,
    "refCostPerImage": 0.05,
    "siteTitle": "超级AI工作台",
    "siteAuthor": "Made by @奇迹与你",
    # ---- 本地千问（ComfyUI）----
    "comfyRoot": "",          # 空 = 自动探测
    "comfyPort": 8188,
    "installDir": "",         # 空 = 工程目录下的 local\
    # ---- DSH ----
    "dshPort": 3080,
    # ---- AI 音乐工坊（独立服务，嵌进本工作站）----
    "musicPort": 7870,
    "musicDir": os.path.join(os.path.expanduser("~"), "Desktop", "AI音乐工坊"),
    # 默认**不**开机自启 —— 这台机器内存紧，只有打开「🎵 AI 音乐工坊」标签页时才拉起
    "musicAutoStart": False,
    "musicStopOnLeave": False,   # 切走标签页是否顺手关掉（默认不关，免得来回切很慢）
    # ---- 生视频（网易有道智云）----
    "youdaoAppKey": "",
    "youdaoSecret": "",
    "videoOutDir": "",
    # ---- 校园网一键登录（深澜 Srun）----
    "campusPortal": "http://10.30.4.3",
    "campusUser": "",
    "campusPassword": "",
    "campusAcId": "4",
    "campusDomain": "@xsdianxin",
}

_lock = threading.Lock()
JOBS = {}          # job_id -> dict(state, ...)


# --------------------------------------------------------------------------
#  配置 / 历史
# --------------------------------------------------------------------------
def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = deep_merge(cfg, json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(items):
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items[:500], f, ensure_ascii=False, indent=2)
    os.replace(tmp, HISTORY_PATH)


# --------------------------------------------------------------------------
#  价格估算
# --------------------------------------------------------------------------
def price_of(cfg, model, size):
    prices = cfg.get("prices") or DEFAULT_PRICES
    key = None
    for k in sorted(prices.keys(), key=len, reverse=True):
        if model.lower().startswith(k.lower()):
            key = k
            break
    if not key:
        return 0.0
    band = (prices.get(key) or {}).get("low", 0.0)
    high = False
    s = str(size).upper()
    if s in ("2K", "4K"):
        high = True
    else:
        m = re.match(r"^(\d{3,5})x(\d{3,5})$", s)
        if m:
            high = max(int(m.group(1)), int(m.group(2))) >= 2000
    if high:
        band = (prices.get(key) or {}).get("high", band)
    return float(band)


# --------------------------------------------------------------------------
#  生成任务
# --------------------------------------------------------------------------
def http_json(url, payload=None, headers=None, method=None, timeout=60):
    data = None
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8", "replace"))


def http_bytes(url, headers=None, timeout=180):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def safe_name(text, limit=48):
    """把提示词压成一个能当文件名的短串"""
    t = re.sub(r"[\r\n\t]+", " ", str(text or "")).strip()
    t = re.sub(r"[\\/:*?\"<>|]+", "", t)
    t = re.sub(r"\s+", "_", t)
    if not t:
        return "seedream"
    out = []
    for ch in t:
        out.append(ch)
        if len("".join(out)) >= limit:
            break
    return "".join(out)[:limit]


def run_generate(job_id, cfg, req):
    """后台线程：调火山方舟 -> 拿图 -> 存盘 -> 写历史"""
    job = JOBS[job_id]
    try:
        key = (cfg.get("apikey") or "").strip()
        if not key:
            raise RuntimeError("还没设置 API Key —— 去「设置」标签页填一个")

        model = req.get("model") or cfg["model"]
        size = req.get("size") or cfg["size"]
        if size == "custom":
            size = (req.get("customSize") or cfg.get("customSize") or "").strip()
            if not re.match(r"^\d{3,5}x\d{3,5}$", size):
                raise RuntimeError("自定义尺寸格式应为 宽x高，例如 2048x1152")
        fmt = req.get("output_format") or cfg.get("format") or "png"
        wm = bool(req.get("watermark", cfg.get("watermark", False)))
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("请先写描述（prompt）")
        images = [x for x in (req.get("images") or []) if x]

        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "output_format": fmt,
            "response_format": "url",
            "watermark": wm,
        }
        if images:
            payload["image"] = images

        job["stage"] = "正在请求火山方舟…"
        job["refs"] = len(images)
        t0 = time.time()
        res = http_json(ARK_GEN, payload,
                        headers={"Authorization": "Bearer " + key},
                        timeout=300)

        items = res.get("data") or []
        if not items:
            raise RuntimeError("接口没返回图片：%s" % json.dumps(res, ensure_ascii=False)[:300])

        out_dir = cfg.get("outputDir") or DEFAULT_CONFIG["outputDir"]
        os.makedirs(out_dir, exist_ok=True)

        job["stage"] = "正在下载并保存…"
        saved = []
        ext = ".jpg" if str(fmt).lower() in ("jpeg", "jpg") else ".png"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, it in enumerate(items):
            if it.get("url"):
                blob, ctype = http_bytes(it["url"], timeout=300)
            elif it.get("b64_json"):
                blob = base64.b64decode(it["b64_json"])
            else:
                continue
            suffix = "" if len(items) == 1 else "-%d" % (i + 1)
            name = "seedream_%s_%s%s%s" % (stamp, safe_name(prompt), suffix, ext)
            path = os.path.join(out_dir, name)
            with open(path, "wb") as f:
                f.write(blob)
            saved.append({
                "file": path,
                "name": name,
                "size": len(blob),
                "model": model,
                "reqSize": size,
                "outSize": it.get("size") or size,
                "prompt": prompt,
                "format": fmt,
                "watermark": wm,
                "refs": len(images),
                "cost": price_of(cfg, model, size) + (len(images) > 1 and cfg.get("refCostPerImage", 0) * (len(images) - 1) or 0),
                "time": int(time.time()),
                "seconds": round(time.time() - t0, 1),
            })

        if not saved:
            raise RuntimeError("接口返回里没有可用的图片数据")

        with _lock:
            hist = load_history()
            hist = saved + hist
            save_history(hist)

        job["result"] = saved
        job["state"] = "done"
        job["stage"] = "完成"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
            info = json.loads(body)
            msg = (info.get("error") or {}).get("message") or body
            code = (info.get("error") or {}).get("code") or ""
        except Exception:
            msg, code = str(e), ""
        job["state"] = "error"
        job["error"] = explain_error(code, msg, e.code)
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)


# ★ 按「报错原文里的关键词」给中文解释 + 可操作建议
#   （平台的内容审核类拦截，error.code 常常是空的或跟在别处，
#     所以不能只靠 code 查表 —— 2026-10-02 主人实际踩到过 ✗）
_KEYWORD_HINTS = (
    ("copyright",
     "平台判定这张图可能涉及版权 ⚠️（是内容审核拦的，不是程序出错）\n"
     "→ 常见原因：提示词里点名了某个动漫/游戏角色，或者参考图是受版权保护的素材\n"
     "→ 三个改法，任选：\n"
     "   ① 把角色名换成外形描述（例：「蓝发双马尾少女」代替某个角色名）\n"
     "   ② 换一张你自己拍/自己生成的图当参考图\n"
     "   ③ 直接点「重新生成」再试一次 —— 审核有时会误判，重试常常就过了"),
    ("may be related to copyright",
     "同上：生成的图被判为可能侵权，改提示词或换参考图，或直接重试"),
    ("sensitive", "提示词里可能含敏感内容，改一改再试"),
    ("content policy", "被平台内容策略拦了，改提示词再试"),
    ("real person", "可能被当成真人肖像，换成虚构角色的描述再试"),
    ("celebrity", "可能被当成名人，换成虚构角色的描述再试"),
    ("prompt", "提示词有问题（可能是太长、含不支持的内容，或被审核命中）"),
)


def kw_hint(msg):
    """按报错原文匹配中文解释；没有就返回空串"""
    if not msg:
        return ""
    low = msg.lower()
    for k, h in _KEYWORD_HINTS:
        if k in low:
            return h
    return ""


def explain_error(code, msg, http_code=None):
    hints = {
        "AuthenticationError": "API Key 不对或没填 —— 去火山方舟控制台复制「API Key」",
        "ModelNotOpen": "该模型没开通 —— 控制台「开通管理」里勾选它（免费开通、按量计费）",
        "InvalidParameter": "参数有问题（提示词/尺寸/参考图）",
        "QuotaExceeded": "额度用完了，去控制台充值",
        "RateLimitExceeded": "请求太频繁，等一下再试",
        "AccessDenied": "没有权限访问该模型",
    }
    h = hints.get(code, "")
    out = msg or "请求失败"
    if http_code:
        out = "[HTTP %s] %s" % (http_code, out)
    if h:
        out += "\n→ " + h
    else:
        # code 没命中（内容审核这类常常没有标准 code）→ 按原文关键词兜底 ✓
        k = kw_hint(msg)
        if k:
            out += "\n\n" + k
    return out


# --------------------------------------------------------------------------
#  HTTP 处理
# --------------------------------------------------------------------------
class QuietServer(ThreadingHTTPServer):
    """浏览器提前断开连接时，不要往日志里刷一堆堆栈"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        et = sys.exc_info()[0]
        if et and issubclass(et, (ConnectionResetError, ConnectionAbortedError,
                                  BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "AIWorkstation/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("AIWS_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---------- 工具 ----------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, cache=False):
        if not os.path.isfile(path):
            self.send_json({"ok": False, "error": "not found: " + path}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif path.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        # ★ Windows 的注册表里没有这些扩展名，mimetypes 认不出来会退回 octet-stream ✗
        elif path.endswith(".webp"):
            ctype = "image/webp"
        elif path.endswith(".flac"):
            ctype = "audio/flac"
        elif path.endswith(".wav"):
            ctype = "audio/wav"
        elif path.endswith(".mp3"):
            ctype = "audio/mpeg"
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=60" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB, "index.html"))
        if path == "/api/health":
            return self.send_json({"ok": True, "time": time.time(), "port": PORT,
                                       "version": APP_VERSION,
                                       "name": "超级AI工作台"})
        # ---------------- AI 生视频 ----------------
        if path == "/api/vgen/status":
            if not VGEN_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载：%s" % _VGEN_ERR})
            cfg = load_config()
            st = video_gen_ai.check_status(cfg)
            return self.send_json({"ok": True, "status": st, "models": video_gen_ai.MODELS,
                                   "outDir": video_gen_ai.out_dir(cfg),
                                   "hasKey": bool((cfg.get("zhipuKey") or "").strip())})

        if path == "/api/vgen/progress":
            if not VGEN_OK:
                return self.send_json({"ok": False, "error": _VGEN_ERR})
            jid = (qs.get("job") or [""])[0]
            j = video_gen_ai.get_job(jid)
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, "job": j})

        # ---------------- Live2D 制作 ----------------
        if path == "/api/live2d/status":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": "Live2D 模块未加载：%s" % _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.engines_status())
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/progress":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            jid = (qs.get("job") or [""])[0]
            try:
                return self.send_json(live2d_ai.progress(jid))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/works":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.list_works())
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/psds":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.list_psds())
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/thumb":
            p = (qs.get("path") or [""])[0]
            if not p or not os.path.isfile(p):
                return self.send_json({"ok": False, "error": "文件不存在"})
            return self.send_file(p)

        if path == "/api/live2d/vram":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            return self.send_json({"ok": True, "freeMb": live2d_ai.gpu_free_mb(),
                                   "needMb": live2d_ai.NEED_VRAM_MB})

        # 有没有在跑的 Live2D 任务（★ 重启工作站前先查这个，别把任务干掉）
        if path == "/api/live2d/busy":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            running = live2d_ai.running_jobs()
            return self.send_json({"ok": True, "running": running, "count": len(running)})

        if path == "/api/config":
            cfg = load_config()
            safe = dict(cfg)
            safe["hasKey"] = bool((cfg.get("apikey") or "").strip())
            safe["apikeyMask"] = mask_key(cfg.get("apikey", ""))
            safe["hasDsKey"] = bool((cfg.get("dsKey") or "").strip())
            safe["dsKeyMask"] = mask_key(cfg.get("dsKey", ""))
            safe["root"] = ROOT
            safe["port"] = PORT
            safe.pop("apikey", None)
            safe.pop("dsKey", None)          # ★ 密钥只留本机后端，不进前端
            safe["hasZhipuKey"] = bool((cfg.get("zhipuKey") or "").strip())
            safe["zhipuKeyMask"] = mask_key(cfg.get("zhipuKey", ""))
            safe.pop("zhipuKey", None)       # ★ 智谱 Key 同样只留本机
            return self.send_json({"ok": True, "config": safe})
        if path == "/api/recent":
            return self.send_json({"ok": True, "items": load_history()[:120]})

        # 作品库：汇总各标签页的产出（?cat=blend 可以只看某一类）
        if path == "/api/gallery":
            if not GALLERY_OK:
                return self.send_json({"ok": False,
                                       "error": "作品库模块未加载：%s" % _GALLERY_ERR})
            cat = (qs.get("cat") or ["all"])[0]
            cfg = load_config()
            cfg["root"] = ROOT
            return self.send_json(gallery_ai.scan(cfg, cat=cat))

        # ---------------- 本地千问（ComfyUI）----------------
        if path == "/api/local/status":
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地模块未加载：%s" % _LOCAL_ERR})
            cfg = load_config()
            st = local_ai.status(cfg)
            st["installDir"] = cfg.get("installDir") or os.path.join(ROOT, "local")
            return self.send_json({"ok": True, "status": st})

        if path == "/api/local/progress":
            jid = (qs.get("job") or [""])[0]
            j = local_ai.JOBS.get(jid) if LOCAL_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json({
                "ok": True,
                "state": j.get("state"),
                "stage": j.get("stage"),
                "percent": j.get("percent"),
                "detail": j.get("detail"),
                "elapsed": round(time.time() - j["t0"], 1),
                "log": (j.get("log") or [])[-60:],
                "result": j.get("result"),
                "error": j.get("error"),
            })
        if path == "/api/progress":
            job_id = (qs.get("job") or [""])[0]
            job = JOBS.get(job_id)
            if not job:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json({
                "ok": True,
                "state": job.get("state"),
                "stage": job.get("stage"),
                "elapsed": round(time.time() - job["t0"], 1),
                "result": job.get("result"),
                "error": job.get("error"),
            })
        # 出图预览（按绝对路径读，限制在输出目录内）
        # ---------------- DSH（DeepSeek Harness）----------------
        if path == "/api/dsh/status":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            cfg = load_config()
            return self.send_json({"ok": True, "status": dsh_ai.status(cfg.get("dshPort"))})

        if path == "/api/dsh/openlog":
            # ★ open_in_explorer 是模块级函数（不是类方法）——
            #   写成 self.open_in_explorer 会 AttributeError，连接直接被关掉（踩过）
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            os.makedirs(d, exist_ok=True)
            return self.send_json({"ok": True, "opened": open_in_explorer(d)})
        if path == "/api/dsh/progress":
            jid = (qs.get("job") or [""])[0]
            j = dsh_ai.JOBS.get(jid) if DSH_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json({
                "ok": True, "state": j.get("state"), "stage": j.get("stage"),
                "percent": j.get("percent"), "detail": j.get("detail"),
                "elapsed": round(time.time() - j["t0"], 1),
                "log": (j.get("log") or [])[-80:],
                "result": j.get("result"), "error": j.get("error"),
            })

        # ---------------- 生视频（网易有道智云）----------------
        if path == "/api/music/status":
            # 音乐工坊是独立服务（默认 7870）。返回它在不在 + 顺手确保被拉起来。
            # 注意：do_GET 里没有全局 conf，必须现取。
            conf = load_config()
            port = int(conf.get("musicPort") or 7870)
            alive = port_open(port)
            started = False
            if not alive:
                try:
                    ensure_music_app(port)
                    started = True
                except Exception as e:
                    return self.send_json({"ok": True, "alive": False, "port": port,
                                           "error": str(e)})
            return self.send_json({"ok": True, "alive": alive or started, "port": port,
                                   "started": started})

        if path == "/api/music/stop":
            # 用完就关，把内存让出来。
            # 这台机器只有 16 GB，同时开工作站 + ComfyUI + 音乐工坊 + F5-TTS
            # 必然爆提交量（实测 os error 1455「页面文件太小」）。
            conf = load_config()
            port = int(conf.get("musicPort") or 7870)
            killed = 0
            flags = 0x08000000 if os.name == "nt" else 0
            try:
                out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", creationflags=flags).stdout or ""
                pids = set()
                for ln in out.splitlines():
                    if (":%d " % port) in ln and "LISTENING" in ln.upper():
                        parts = ln.split()
                        if parts and parts[-1].isdigit():
                            pids.add(parts[-1])
                for pid in pids:
                    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                                   capture_output=True, creationflags=flags)
                    killed += 1
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})
            return self.send_json({"ok": True, "killed": killed, "port": port})

        # ---------------- AI 溶图 ----------------
        if path == "/api/blend/status":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            return self.send_json(blend_ai.status())

        if path == "/api/blend/progress":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            j = blend_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, **j})

        if path == "/api/blend/file":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            kind = (qs.get("kind") or ["bg"])[0]
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = blend_ai.path_of(kind, f)
            if not fp:
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        # ---------------- AI 抠图 ----------------
        if path == "/api/matting/status":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            return self.send_json(matting_ai.status())

        if path == "/api/matting/progress":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            j = matting_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, **j})

        if path == "/api/matting/file":
            # 取素材或产物：kind=src|out；产物用 p=<jobid>/<文件名>
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            kind = (qs.get("kind") or ["src"])[0]
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            sub = urllib.parse.unquote((qs.get("p") or [""])[0])
            if kind == "src":
                fp = os.path.join(matting_ai.IN_DIR, f)
            else:
                rel = os.path.normpath(sub).replace("\\", "/")
                if rel.startswith("..") or rel.startswith("/") or not rel:
                    return self.send_json({"ok": False, "error": "非法路径"})
                fp = os.path.join(matting_ai.OUT_DIR, *rel.split("/"))
            if not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/matting/list":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            return self.send_json({"ok": True, "sources": matting_ai.list_sources(),
                                   "outputs": matting_ai.list_outputs()})

        # ---------------- AI 音乐工坊（原生）----------------
        if path == "/api/music/info":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            return self.send_json(music_ai.status())

        if path == "/api/music/progress":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            j = music_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, **j})

        if path == "/api/music/audio":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = music_ai.out_path(f)
            if not fp:
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/music/lyrics":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            return self.send_json({"ok": True, "lrc": music_ai.read_lrc(f),
                                   "has": bool(music_ai.lrc_for(f))})

        if path == "/api/music/refplay":
            # 试听声音包（它放在 ComfyUI 的 input 目录下）
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = os.path.join(music_ai._mod.REF_DIR, f) if music_ai._mod else ""
            if not f or not fp or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/music/list":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            return self.send_json({"ok": True, "items": music_ai.list_outputs()})

        # ---------------- AI 翻唱 ----------------
        if path == "/api/cover/status":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            return self.send_json(cover_ai.status())

        if path == "/api/cover/progress":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            j = cover_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, **j})

        if path == "/api/cover/audio":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = os.path.join(cover_ai.OUT_DIR, f)
            if not f or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/cover/src":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = os.path.join(cover_ai.SRC_DIR, f)
            if not f or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/cover/list":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            return self.send_json({"ok": True, "items": cover_ai.list_outputs(),
                                   "dir": cover_ai.OUT_DIR})

        # ---------------- 声音包朗读（F5-TTS）----------------
        if path == "/api/tts/status":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            return self.send_json(tts_ai.status())

        if path == "/api/tts/progress":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            jid = (qs.get("job") or [""])[0]
            j = tts_ai.get_job(jid)
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, **j})

        if path == "/api/tts/audio":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            f = os.path.basename(urllib.parse.unquote((qs.get("f") or [""])[0]))
            fp = os.path.join(tts_ai.OUT_DIR, f)
            if not f or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"}, 404)
            return self.send_file(fp)

        if path == "/api/tts/list":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            items = []
            try:
                os.makedirs(tts_ai.OUT_DIR, exist_ok=True)
                for f in sorted(os.listdir(tts_ai.OUT_DIR), reverse=True):
                    fp = os.path.join(tts_ai.OUT_DIR, f)
                    if os.path.isfile(fp) and f.lower().endswith(".wav"):
                        st = os.stat(fp)
                        items.append({"name": f,
                                      "sizeText": "%.2f MB" % (st.st_size / 2 ** 20),
                                      "time": time.strftime("%m-%d %H:%M",
                                                            time.localtime(st.st_mtime))})
            except Exception:
                pass
            return self.send_json({"ok": True, "items": items[:60], "dir": tts_ai.OUT_DIR})

        if path == "/api/video/status":
            if not VIDEO_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载：%s" % _VIDEO_ERR})
            return self.send_json({"ok": True, "status": video_ai.status(load_config())})

        if path == "/api/video/progress":
            jid = (qs.get("job") or [""])[0]
            j = video_ai.JOBS.get(jid) if VIDEO_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json({
                "ok": True, "state": j.get("state"), "stage": j.get("stage"),
                "percent": j.get("percent"), "detail": j.get("detail"),
                "elapsed": round(time.time() - j["t0"], 1),
                "log": (j.get("log") or [])[-60:],
                "result": j.get("result"), "error": j.get("error"),
            })

        # ---------------- 电脑工具百宝箱 ----------------
        if path == "/api/campus/status":
            if not CAMPUS_OK:
                return self.send_json({"ok": False, "error": "校园网模块未加载：%s" % _CAMPUS_ERR})
            return self.send_json({"ok": True, "status": campus.status(load_config())})

        if path == "/api/stem/status":
            if not STEM_OK:
                return self.send_json({"ok": False,
                                       "error": "分离模块未加载：%s" % _STEM_ERR})
            return self.send_json(stem_ai.status())

        if path == "/api/stem/progress":
            jid = (qs.get("job") or [""])[0]
            j = stem_ai.get_job(jid) if STEM_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json(dict(j, ok=True))
        if path == "/api/style/lib":
            return self.send_json({"ok": True, "items": style_ai.read_lib().get("items", [])})
        if path == "/api/style/progress":
            j = style_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, "job": j})

        if path == "/api/engine/status":
            return self.send_json(engine_ai.status())
        if path == "/api/engine/progress":
            j = engine_ai.get_job((qs.get("job") or [""])[0])
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"})
            return self.send_json({"ok": True, "job": j})
        if path == "/api/models/status":
            if not MODEL_OK:
                return self.send_json({"ok": False,
                                       "error": "模型模块未加载：%s" % _MODEL_ERR})
            return self.send_json(model_ai.status())

        if path == "/api/models/progress":
            jid = (qs.get("job") or [""])[0]
            j = model_ai.get_job(jid) if MODEL_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json(dict(j, ok=True))

        if path == "/api/stem/file":
            if not STEM_OK:
                return self.send_json({"ok": False, "error": "分离模块未加载"}, 404)
            kind = (qs.get("kind") or ["out"])[0]
            f = (qs.get("f") or [""])[0]
            base = stem_ai.OUT_DIR if kind == "out" else stem_ai.SRC_DIR
            fp = os.path.normpath(os.path.join(base, os.path.basename(f)))
            if not fp.startswith(os.path.normpath(base)) or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "找不到文件"}, 404)
            return self.send_file(fp)

        if path == "/api/search/status":
            if not SEARCH_OK:
                return self.send_json({"ok": False,
                                       "error": "搜索模块未加载：%s" % _SEARCH_ERR})
            return self.send_json(dict(search_ai.key_state(load_config()), ok=True))

        if path == "/api/search/progress":
            jid = (qs.get("job") or [""])[0]
            j = search_ai.get_job(jid) if SEARCH_OK else None
            if not j:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json(dict(j, ok=True))

        if path == "/api/toolbox/backup":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            return self.send_json(toolbox.backup_status())

        if path == "/api/toolbox/memory":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            return self.send_json({"ok": True, "memory": toolbox.memory_status(),
                                   "isAdmin": toolbox.is_admin()})

        if path == "/api/toolbox/dl/progress":
            jid = (qs.get("job") or [""])[0]
            p = toolbox.dl_progress(jid) if TOOL_OK else None
            if not p:
                return self.send_json({"ok": False, "error": "任务不存在"}, 404)
            return self.send_json(dict(p, ok=True))

        if path == "/api/image":
            return self.serve_output_image((qs.get("path") or [""])[0])
        if path == "/api/video/file":
            return self.serve_output_video((qs.get("path") or [""])[0])
        # 静态资源
        if path.startswith("/static/"):
            rel = path[len("/static/"):].replace("\\", "/")
            target = os.path.normpath(os.path.join(WEB, rel))
            if not target.startswith(os.path.normpath(WEB)):
                return self.send_json({"ok": False, "error": "bad path"}, 403)
            return self.send_file(target, cache=True)
        return self.send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_body()

        if path == "/api/config":
            cfg = load_config()
            for k in ("outputDir", "qwenOutDir", "zhipuKey", "videoOutDir", "model", "size", "customSize", "format",
                      "watermark", "models", "prices", "siteTitle", "siteAuthor",
                      "siteSlogan", "siteSlogan2",
                      "comfyRoot", "comfyPort", "installDir",
                "musicPort", "musicDir", "musicAutoStart",
                      "youdaoAppKey", "youdaoSecret", "videoOutDir",
                      "dsSearchBase", "dsSearchModel"):
                if k in body:
                    cfg[k] = body[k]
            if body.get("apikey"):
                cfg["apikey"] = body["apikey"].strip()
            if body.get("clearKey"):
                cfg["apikey"] = ""
            if body.get("dsKey"):
                cfg["dsKey"] = body["dsKey"].strip()
            if body.get("clearDsKey"):
                cfg["dsKey"] = ""
            save_config(cfg)
            out = dict(cfg)
            out["hasKey"] = bool((cfg.get("apikey") or "").strip())
            out["apikeyMask"] = mask_key(cfg.get("apikey", ""))
            out["hasDsKey"] = bool((cfg.get("dsKey") or "").strip())
            out["dsKeyMask"] = mask_key(cfg.get("dsKey", ""))
            out.pop("apikey", None)
            out.pop("dsKey", None)
            return self.send_json({"ok": True, "config": out})

        if path == "/api/generate":
            cfg = load_config()
            job_id = uuid.uuid4().hex[:12]
            JOBS[job_id] = {"state": "running", "stage": "排队中", "t0": time.time(),
                            "result": None, "error": None}
            t = threading.Thread(target=run_generate, args=(job_id, cfg, body), daemon=True)
            t.start()
            return self.send_json({"ok": True, "job": job_id})

        if path == "/api/openfolder":
            cfg = load_config()
            d = body.get("dir") or cfg.get("outputDir") or ""
            if not d or not os.path.isdir(d):
                return self.send_json({"ok": False, "error": "目录不存在：%s" % d})
            return self.send_json({"ok": True, "opened": open_in_explorer(d)})

        # ---------------- 本地千问（ComfyUI）----------------
        if path == "/api/local/start":
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地模块未加载：%s" % _LOCAL_ERR})
            return self.send_json(local_ai.start_comfy(load_config()))

        if path == "/api/local/stop":
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地模块未加载：%s" % _LOCAL_ERR})
            r = local_ai.stop_comfy(load_config())
            return self.send_json(r)

        if path == "/api/local/install":
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地模块未加载：%s" % _LOCAL_ERR})
            cfg = load_config()
            if body.get("installDir"):
                cfg["installDir"] = body["installDir"]
                save_config(cfg)
            jid = local_ai.new_job("install")
            threading.Thread(target=local_ai.install_worker, args=(jid, cfg), daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        # ---------------- AI 生视频（智谱 CogVideoX-Flash）----------------
        # ---------------- AI 生成提示词（免费 glm-4-flash）----------------
        if path == "/api/vgen/make-prompt":
            if not VGEN_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载：%s" % _VGEN_ERR})
            cfg = load_config()
            try:
                items = video_gen_ai.make_prompts(cfg, body)
                return self.send_json({"ok": True, "items": items, "count": len(items)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/vgen/generate":
            if not VGEN_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载：%s" % _VGEN_ERR})
            cfg = load_config()
            # 前端临时填的 Key 也可以（不落盘）；没填就用设置里的
            jid = video_gen_ai.new_job("video")
            video_gen_ai.start_generate(jid, cfg, body)
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/local/generate":
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地模块未加载：%s" % _LOCAL_ERR})
            cfg = load_config()
            jid = local_ai.new_job("generate")
            threading.Thread(target=local_ai.generate_worker, args=(jid, cfg, body), daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        # ---------------- Live2D 制作 ----------------
        if path == "/api/live2d/make":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": "Live2D 模块未加载：%s" % _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.start_make(body))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/import-psd":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.import_existing_psd(body))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/free-vram":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                cfg = load_config()
                r = live2d_ai.free_vram_unload_comfy(port=cfg.get("comfyPort") or 8188)
                r["freeMb"] = live2d_ai.gpu_free_mb()
                r["needMb"] = live2d_ai.NEED_VRAM_MB
                return self.send_json(r)
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        # ---------------- ★ 一键关掉所有后端（把内存/显存让出来）----------------
        if path == "/api/backends/stop-all":
            conf = load_config()
            conf["root"] = ROOT
            mem0 = _mem_free_mb()
            vram0 = live2d_ai.gpu_free_mb() if LIVE2D_OK else None
            items = []

            def _note(name, port, killed, note=""):
                items.append({"name": name, "port": port, "killed": killed, "note": note})

            # ① ComfyUI —— 本地千问 / 抠图 / 溶图 的引擎（最吃显存的那个）
            cport = int(conf.get("comfyPort") or 8188)
            if not port_open(cport):
                _note("ComfyUI（本地千问 / 抠图 / 溶图 的引擎）", cport, 0, "本来就没开")
            else:
                killed = 0
                note = ""
                if LOCAL_OK:
                    try:
                        r = local_ai.stop_comfy(conf) or {}
                        killed = len(r.get("killed") or [])
                    except Exception as e:
                        note = str(e)[:100]
                if not killed:                      # 一条都没杀着 → 按监听端口兜底
                    killed = kill_listen_port(cport)
                    if killed:
                        note = "按端口结束的"
                if not killed and port_open(cport):
                    note = "没能结束（可能权限不够，手动关一下 ComfyUI 窗口）"
                _note("ComfyUI（本地千问 / 抠图 / 溶图 的引擎）", cport, killed, note)

            # ② AI 音乐工坊
            mport = int(conf.get("musicPort") or 7870)
            n = kill_listen_port(mport)
            _note("AI 音乐工坊", mport, n, "" if n else "本来就没开")

            # ③ 小鲸鱼生图（独立界面，自带后端）
            n = kill_listen_port(8199)
            _note("小鲸鱼生图界面", 8199, n, "" if n else "本来就没开")

            # ④ PSD2Live（Live2D 建模，Java 应用，占的是内存不是显存）
            n = 0
            try:
                flags = 0x08000000 if os.name == "nt" else 0
                tl = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PSD2Live.exe", "/FO", "CSV", "/NH"],
                                    capture_output=True, text=True, creationflags=flags).stdout or ""
                if "PSD2Live" in tl:
                    subprocess.run(["taskkill", "/IM", "PSD2Live.exe", "/F", "/T"],
                                   capture_output=True, creationflags=flags)
                    n = tl.upper().count("PSD2LIVE")
            except Exception:
                pass
            _note("PSD2Live（Live2D 建模）", 23871, n, "" if n else "本来就没开")

            # ⑤ 站内释放：抠图的 ONNX 会话（就住在工作站进程里）
            freed_local = 0
            if MATTING_OK:
                try:
                    freed_local = int(matting_ai.free_models() or 0)   # 返回的是「释放了几个会话」
                except Exception:
                    freed_local = 0
            _note("站内模型内存（AI 抠图的 ONNX 会话）", None, 1 if freed_local else 0,
                  ("释放了 %d 个会话" % freed_local) if freed_local else "无需释放")

            time.sleep(1.5)                         # 等进程真的退出、内存回收
            mem1 = _mem_free_mb()
            vram1 = live2d_ai.gpu_free_mb() if LIVE2D_OK else None
            return self.send_json({
                "ok": True, "items": items,
                "mem": {"before": mem0, "after": mem1,
                        "freedMb": (mem1 - mem0) if (mem0 is not None and mem1 is not None) else None},
                "vram": {"before": vram0, "after": vram1,
                         "freedMb": (vram1 - vram0) if (vram0 is not None and vram1 is not None) else None},
            })

        if path == "/api/live2d/start-psd2live":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.start_psd2live())
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/live2d/upload":
            if not LIVE2D_OK:
                return self.send_json({"ok": False, "error": _LIVE2D_ERR})
            try:
                return self.send_json(live2d_ai.save_upload(body.get("name"), body.get("data")))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)[:300]})

        if path == "/api/openfile":
            f = body.get("file") or ""
            if not os.path.isfile(f):
                return self.send_json({"ok": False, "error": "文件不存在"})
            return self.send_json({"ok": True, "opened": open_in_explorer(f)})

        if path == "/api/delete":
            f = body.get("file") or ""
            if not os.path.isfile(f):
                return self.send_json({"ok": False, "error": "文件不存在"})
            try:
                os.remove(f)
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})
            with _lock:
                hist = [x for x in load_history() if x.get("file") != f]
                save_history(hist)
            return self.send_json({"ok": True})

        # ---------------- DSH（DeepSeek Harness）----------------
        if path == "/api/dsh/apikey":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            return self.send_json({"ok": True, "saved": dsh_ai.set_env_key(body.get("apikey") or "")})

        if path == "/api/dsh/install":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            jid = dsh_ai.new_job("install")
            threading.Thread(target=dsh_ai.install_worker,
                             args=(jid, body.get("apikey") or ""), daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/dsh/start":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            cfg = load_config()
            if body.get("port"):
                cfg["dshPort"] = int(body["port"])
                save_config(cfg)
            return self.send_json(dsh_ai.start_dsh(cfg.get("dshPort")))

        if path == "/api/dsh/stop":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            cfg = load_config()
            return self.send_json(dsh_ai.stop_dsh(cfg.get("dshPort")))

        if path == "/api/dsh/open":
            if not DSH_OK:
                return self.send_json({"ok": False, "error": "DSH 模块未加载：%s" % _DSH_ERR})
            cfg = load_config()
            port = cfg.get("dshPort")
            url = body.get("url") or dsh_ai.find_auth_url(port)
            if not url:
                return self.send_json({"ok": False,
                                       "error": "还没抓到授权链接 —— 请先点「启动 DSH」并等服务就绪"})
            return self.send_json({"ok": True, "url": url, "opened": open_url(url)})

        # ---------------- 生视频（网易有道智云）----------------
        # ---------------- 音乐工坊：停止（释放内存）----------------
        if path == "/api/music/stop":
            conf = load_config()
            port = int(conf.get("musicPort") or 7870)
            killed = 0
            flags = 0x08000000 if os.name == "nt" else 0
            try:
                out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", creationflags=flags).stdout or ""
                pids = set()
                for ln in out.splitlines():
                    if (":%d " % port) in ln and "LISTENING" in ln.upper():
                        parts = ln.split()
                        if parts and parts[-1].isdigit():
                            pids.add(parts[-1])
                for pid in pids:
                    subprocess.run(["taskkill", "/PID", pid, "/F", "/T"],
                                   capture_output=True, creationflags=flags)
                    killed += 1
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})
            return self.send_json({"ok": True, "killed": killed, "port": port,
                                   "memFreeMB": _mem_free_mb()})

        # ---------------- AI 溶图 ----------------
        if path == "/api/blend/upload":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            import base64 as _b64
            kind = (body.get("kind") or "bg").strip()
            if kind not in ("bg", "fg"):
                return self.send_json({"ok": False, "error": "kind 只能是 bg / fg"})
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(blend_ai.IMG_EXT):
                return self.send_json({"ok": False, "error": "只支持图片"})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > blend_ai.MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False,
                                       "error": "超过 %d MB" % blend_ai.MAX_MB})
            d = blend_ai.BG_DIR if kind == "bg" else blend_ai.FG_DIR
            os.makedirs(d, exist_ok=True)
            stem, ext = os.path.splitext(fname)
            target = os.path.join(d, fname)
            n = 1
            while os.path.exists(target):
                target = os.path.join(d, "%s(%d)%s" % (stem, n, ext))
                n += 1
            with open(target, "wb") as f:
                f.write(raw)
            # 主体顺手探测有没有透明通道
            info = {}
            if kind == "fg":
                try:
                    from PIL import Image as _I
                    with _I.open(target) as im:
                        info["hasAlpha"] = (im.mode in ("RGBA", "LA")
                                            or "transparency" in im.info)
                        info["size"] = "%dx%d" % im.size
                except Exception:
                    pass
            return self.send_json({"ok": True, "name": os.path.basename(target),
                                   "sizeText": blend_ai.human(len(raw)), "info": info})

        if path == "/api/blend/run":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            if not (body.get("bg") or "").strip():
                return self.send_json({"ok": False, "error": "请先选一张背景"})
            if not blend_ai.path_of("fg", body.get("fg")):
                return self.send_json({"ok": False, "error": "请先选一张主体（抠好的透明底图）"})
            jid = blend_ai.new_job("blend")
            threading.Thread(target=blend_ai.blend_worker, args=(jid, body),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/blend/openfolder":
            if not BLEND_OK:
                return self.send_json({"ok": False, "error": _BLEND_ERR})
            try:
                os.makedirs(blend_ai.OUT_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(blend_ai.OUT_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        # ---------------- AI 抠图 ----------------
        if path == "/api/matting/upload":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            import base64 as _b64
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(matting_ai.SRC_EXT):
                return self.send_json({"ok": False, "error": "只支持图片"})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > matting_ai.SRC_MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False,
                                       "error": "超过 %d MB" % matting_ai.SRC_MAX_MB})
            os.makedirs(matting_ai.IN_DIR, exist_ok=True)
            stem, ext = os.path.splitext(fname)
            target = os.path.join(matting_ai.IN_DIR, fname)
            n = 1
            while os.path.exists(target):
                target = os.path.join(matting_ai.IN_DIR, "%s(%d)%s" % (stem, n, ext))
                n += 1
            with open(target, "wb") as f:
                f.write(raw)
            return self.send_json({"ok": True, "name": os.path.basename(target),
                                   "sizeText": matting_ai.human(len(raw))})

        if path == "/api/matting/run":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            if not (body.get("source") or "").strip():
                return self.send_json({"ok": False, "error": "请先选一张图"})
            jid = matting_ai.new_job("matting")
            threading.Thread(target=matting_ai.run_worker, args=(jid, body),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/matting/free":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            n = matting_ai.free_models()
            return self.send_json({"ok": True, "freed": n})

        if path == "/api/matting/openfolder":
            if not MATTING_OK:
                return self.send_json({"ok": False, "error": _MATTING_ERR})
            # kind=out（默认）开出片目录，kind=src 开素材目录
            kind = (body.get("kind") or "out").strip()
            try:
                d = matting_ai.IN_DIR if kind == "src" else matting_ai.OUT_DIR
                os.makedirs(d, exist_ok=True)
                return self.send_json({"ok": True, "opened": open_in_explorer(d), "dir": d})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        # ---------------- AI 音乐工坊（原生）----------------
        if path == "/api/music/generate":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            st = music_ai.status()
            if not st.get("ok"):
                return self.send_json({"ok": False, "error": st.get("error")})
            if not st.get("modelOk"):
                return self.send_json({"ok": False, "error": "ACE-Step 模型不在"})
            jid = music_ai.start_generate(body or {})
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/music/aiwrite":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            try:
                # 优先用「⚙️ 设置」里那把 DeepSeek Key（和 AI 搜索共用）✓
                _k = (load_config().get("dsKey") or "").strip()
                # ★ ai_write 返回的是 (数据, 错误) 元组 —— 必须拆开！
                #   直接塞进 json 会变成数组，前端 d.tags 就取不到了 ✗（踩过）
                data, err = music_ai.ai_write((body.get("prompt") or "").strip(),
                                              {"llmKey": _k} if _k else None)
                if not data:
                    return self.send_json({"ok": False,
                                           "error": err or "AI 没给出内容，再试一次"})
                out = dict(data)
                out["ok"] = True
                return self.send_json(out)
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/music/refvoice/upload":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            import base64 as _b64
            m = music_ai._mod
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(m.REF_EXT):
                return self.send_json({"ok": False, "error": "不支持的音频格式"})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > m.REF_MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False, "error": "超过 %d MB" % m.REF_MAX_MB})
            os.makedirs(m.REF_DIR, exist_ok=True)
            stem, ext = os.path.splitext(fname)
            target = os.path.join(m.REF_DIR, fname)
            n = 1
            while os.path.exists(target):
                target = os.path.join(m.REF_DIR, "%s(%d)%s" % (stem, n, ext))
                n += 1
            with open(target, "wb") as f:
                f.write(raw)
            return self.send_json({"ok": True, "name": os.path.basename(target),
                                   "sizeText": m.human(len(raw))})

        if path == "/api/music/voicepack/upload":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            import base64 as _b64
            m = music_ai._mod
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(m.SRC_EXT):
                return self.send_json({"ok": False, "error": "不支持的格式"})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > m.SRC_MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False, "error": "超过 %d MB" % m.SRC_MAX_MB})
            os.makedirs(m.SRC_DIR, exist_ok=True)
            stem, ext = os.path.splitext(fname)
            target = os.path.join(m.SRC_DIR, fname)
            n = 1
            while os.path.exists(target):
                target = os.path.join(m.SRC_DIR, "%s(%d)%s" % (stem, n, ext))
                n += 1
            with open(target, "wb") as f:
                f.write(raw)
            info = {}
            try:
                info = m.probe_media(target) or {}
            except Exception:
                pass
            return self.send_json({"ok": True, "name": os.path.basename(target),
                                   "sizeText": m.human(len(raw)), "info": info})

        if path == "/api/music/voicepack/make":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            jid = music_ai.start_voicepack(body or {})
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/music/conf":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            try:
                music_ai.save_conf(body or {})
                return self.send_json({"ok": True})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/music/engine":
            # 启动/停止 ComfyUI（音乐生成必须它，但它最吃内存）
            if not LOCAL_OK:
                return self.send_json({"ok": False, "error": "本地千问模块没加载"})
            act = (body.get("action") or "").strip()
            if act == "start":
                try:
                    music_ai.ensure_comfy()
                    return self.send_json({"ok": True, "alive": True})
                except Exception as e:
                    return self.send_json({"ok": False, "error": str(e)})
            if act == "stop":
                try:
                    return self.send_json(local_ai.stop_comfy(load_config()))
                except Exception as e:
                    return self.send_json({"ok": False, "error": str(e)})
            return self.send_json({"ok": False, "error": "action 只能是 start / stop"})

        if path == "/api/music/openfolder":
            if not MUSIC_OK:
                return self.send_json({"ok": False, "error": _MUSIC_ERR})
            try:
                d = music_ai._mod.OUT
                os.makedirs(d, exist_ok=True)
                return self.send_json({"ok": True, "opened": open_in_explorer(d)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        # ---------------- AI 翻唱 ----------------
        if path == "/api/cover/upload":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            import base64 as _b64
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(cover_ai.SRC_EXT):
                return self.send_json({"ok": False, "error": "不支持的格式"})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > cover_ai.SRC_MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False,
                                       "error": "文件超过 %d MB" % cover_ai.SRC_MAX_MB})
            try:
                os.makedirs(cover_ai.SRC_DIR, exist_ok=True)
                stem, ext = os.path.splitext(fname)
                target = os.path.join(cover_ai.SRC_DIR, fname)
                n = 1
                while os.path.exists(target):
                    target = os.path.join(cover_ai.SRC_DIR, "%s(%d)%s" % (stem, n, ext))
                    n += 1
                with open(target, "wb") as f:
                    f.write(raw)
                return self.send_json({"ok": True, "name": os.path.basename(target),
                                       "sizeText": cover_ai.human(len(raw))})
            except Exception as e:
                return self.send_json({"ok": False, "error": "写入失败：%s" % e})

        if path == "/api/cover/run":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            st = cover_ai.status()
            if not st.get("ready"):
                return self.send_json({"ok": False, "error": "翻唱环境没准备好"})
            if not (body.get("source") or "").strip():
                return self.send_json({"ok": False, "error": "请先选一首歌"})
            if not cover_ai.voice_path(body.get("voice")):
                return self.send_json({"ok": False, "error": "请先选一个声音包"})
            jid = cover_ai.new_job("cover")
            threading.Thread(target=cover_ai.cover_worker, args=(jid, body),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/cover/openfolder":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            try:
                os.makedirs(cover_ai.OUT_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(cover_ai.OUT_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/cover/srcdel":
            if not COVER_OK:
                return self.send_json({"ok": False, "error": _COVER_ERR})
            f = os.path.basename(body.get("name") or "")
            fp = os.path.join(cover_ai.SRC_DIR, f)
            if not f or not os.path.isfile(fp):
                return self.send_json({"ok": False, "error": "文件不存在"})
            try:
                os.remove(fp)
                return self.send_json({"ok": True})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        # ---------------- 声音包朗读（F5-TTS）----------------
        if path == "/api/tts/run":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            st = tts_ai.status()
            if not st.get("ready"):
                return self.send_json({"ok": False,
                                       "error": "F5-TTS 环境没准备好（检查 %s）" % tts_ai.F5_DIR})
            if not (body.get("text") or "").strip():
                return self.send_json({"ok": False, "error": "请先输入要朗读的文字"})
            if not tts_ai.voice_path(body.get("voice")):
                return self.send_json({"ok": False, "error": "请先选一个声音包"})
            jid = tts_ai.new_job("tts")
            threading.Thread(target=tts_ai.tts_worker, args=(jid, body),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/tts/openfolder":
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            try:
                os.makedirs(tts_ai.OUT_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(tts_ai.OUT_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/tts/refopen":
            # 打开声音包目录（和音乐工坊共用的那个）
            if not TTS_OK:
                return self.send_json({"ok": False, "error": _TTS_ERR})
            try:
                os.makedirs(tts_ai.REF_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(tts_ai.REF_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/video/generate":
            if not VIDEO_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载：%s" % _VIDEO_ERR})
            cfg = load_config()
            mode = body.get("mode") or "text"
            jid = video_ai.new_job(mode)
            threading.Thread(target=video_ai.run_job,
                             args=(jid, cfg, mode, body), daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/video/template-list":
            if not VIDEO_OK:
                return self.send_json({"ok": False, "error": "视频模块未加载"})
            return self.send_json({"ok": True, "templates": video_ai.TEMPLATES})

        # ---------------- 电脑工具百宝箱 ----------------
        if path == "/api/campus/login":
            if not CAMPUS_OK:
                return self.send_json({"ok": False, "error": "校园网模块未加载：%s" % _CAMPUS_ERR})
            cfg = load_config()
            # 允许顺带保存账号密码
            changed = False
            for k in ("campusUser", "campusPassword", "campusDomain",
                      "campusPortal", "campusAcId"):
                if body.get(k) is not None and body.get(k) != "":
                    cfg[k] = str(body[k])
                    changed = True
            if changed:
                save_config(cfg)
            return self.send_json({"ok": True,
                                   "result": campus.login(cfg, bool(body.get("force")))})

        if path == "/api/campus/save":
            if not CAMPUS_OK:
                return self.send_json({"ok": False, "error": "校园网模块未加载"})
            cfg = load_config()
            for k in ("campusUser", "campusPassword", "campusDomain",
                      "campusPortal", "campusAcId"):
                if body.get(k) is not None:
                    cfg[k] = str(body[k])
            save_config(cfg)
            return self.send_json({"ok": True})

        if path == "/api/campus/autostart":
            if not CAMPUS_OK:
                return self.send_json({"ok": False, "error": "校园网模块未加载"})
            act = body.get("action") or "status"
            if act == "on":
                return self.send_json(campus.install_autostart())
            if act == "off":
                return self.send_json(campus.remove_autostart())
            if act == "removeOld":
                return self.send_json(campus.remove_old_task())
            return self.send_json({"ok": True, "status": campus.autostart_status()})

        if path == "/api/stem/upload":
            if not STEM_OK:
                return self.send_json({"ok": False, "error": "分离模块未加载：%s" % _STEM_ERR})
            import base64 as _b64
            fname = os.path.basename((body.get("name") or "").strip())
            data = body.get("data") or ""
            if not fname or not data:
                return self.send_json({"ok": False, "error": "缺文件名或内容"})
            if not fname.lower().endswith(stem_ai.SRC_EXT):
                return self.send_json({"ok": False,
                                       "error": "不支持的格式：%s" % os.path.splitext(fname)[1]})
            if "," in data and data.strip().startswith("data:"):
                data = data.split(",", 1)[1]
            try:
                raw = _b64.b64decode(data)
            except Exception as e:
                return self.send_json({"ok": False, "error": "解码失败：%s" % e})
            if len(raw) > stem_ai.SRC_MAX_MB * 1024 * 1024:
                return self.send_json({"ok": False,
                                       "error": "文件超过 %d MB —— 更大的直接放进 %s 文件夹就行"
                                                % (stem_ai.SRC_MAX_MB, stem_ai.SRC_DIR)})
            try:
                os.makedirs(stem_ai.SRC_DIR, exist_ok=True)
                stem, ext = os.path.splitext(fname)
                target = os.path.join(stem_ai.SRC_DIR, fname)
                n = 1
                while os.path.exists(target):
                    target = os.path.join(stem_ai.SRC_DIR, "%s(%d)%s" % (stem, n, ext))
                    n += 1
                with open(target, "wb") as f:
                    f.write(raw)
                return self.send_json({"ok": True, "name": os.path.basename(target),
                                       "sizeText": stem_ai.human(len(raw))})
            except Exception as e:
                return self.send_json({"ok": False, "error": "写入失败：%s" % e})

        if path == "/api/engine/install":
            eng = (body or {}).get("engine") or ""
            if eng not in engine_ai.ENGINES:
                return self.send_json({"ok": False, "error": "没有这个引擎：%s" % eng})
            jid = engine_ai.start_install(eng)
            return self.send_json({"ok": True, "job": jid})
        if path == "/api/style/delete":
            idx = int((body or {}).get("index", -1))
            lib = style_ai.read_lib()
            if 0 <= idx < len(lib.get("items", [])):
                lib["items"].pop(idx)
                style_ai.write_lib(lib)
            return self.send_json({"ok": True})
        if path == "/api/style/save":
            lib = style_ai.read_lib()
            lib.setdefault("items", []).insert(0, {
                "title": ((body or {}).get("title") or "").strip() or ("关键词 %d" % (len(lib["items"]) + 1)),
                "time": time.strftime("%m-%d %H:%M"),
                "modules": (body or {}).get("modules") or {},
                "prompt": (body or {}).get("prompt") or "",
            })
            lib["items"] = lib["items"][:200]
            style_ai.write_lib(lib)
            return self.send_json({"ok": True, "count": len(lib["items"])})
        if path == "/api/style/analyze":
            imgs = (body or {}).get("images") or []
            if not imgs:
                return self.send_json({"ok": False, "error": "没有收到图片"})
            saved = []
            up = os.path.join(DATA, "风格参考图")
            os.makedirs(up, exist_ok=True)
            for i, d in enumerate(imgs[:8]):
                try:
                    b64 = d.split(",", 1)[1] if "," in d else d
                    raw = base64.b64decode(b64)
                    p = os.path.join(up, "%s_%d.jpg" % (time.strftime("%Y%m%d-%H%M%S"), i))
                    with open(p, "wb") as f:
                        f.write(raw)
                    saved.append(p)
                except Exception as e:
                    return self.send_json({"ok": False, "error": "图片解码失败：%s" % e})
            jid = style_ai.start_analyze(saved)
            return self.send_json({"ok": True, "job": jid, "saved": len(saved)})
        if path == "/api/models/install":
            if not MODEL_OK:
                return self.send_json({"ok": False,
                                       "error": "模型模块未加载：%s" % _MODEL_ERR})
            g = (body.get("group") or "").strip()
            if not g:
                return self.send_json({"ok": False, "error": "没说装哪一组"})
            jid = "models-%s-%d" % (g, int(time.time()))
            threading.Thread(target=_models_worker, args=(jid, g), daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/stem/run":
            if not STEM_OK:
                return self.send_json({"ok": False, "error": "分离模块未加载：%s" % _STEM_ERR})
            st = stem_ai.status()
            if not st.get("ready"):
                return self.send_json({"ok": False,
                                       "error": "分离环境没准备好（缺 worker 脚本或 ComfyUI 的 Python）"})
            if not (body.get("source") or "").strip():
                return self.send_json({"ok": False, "error": "请先选一个音频文件"})
            jid = stem_ai.new_job("stem")
            threading.Thread(target=stem_ai.stem_worker, args=(jid, body),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/stem/openfolder":
            if not STEM_OK:
                return self.send_json({"ok": False, "error": "分离模块未加载：%s" % _STEM_ERR})
            try:
                os.makedirs(stem_ai.OUT_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(stem_ai.OUT_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/search/run":
            if not SEARCH_OK:
                return self.send_json({"ok": False,
                                       "error": "搜索模块未加载：%s" % _SEARCH_ERR})
            q = (body.get("query") or "").strip()
            if not q:
                return self.send_json({"ok": False, "error": "先说你想搜什么"})
            if len(q) > 500:
                return self.send_json({"ok": False, "error": "问题太长了（最多 500 字）"})
            cfg = load_config()
            if not (cfg.get("dsKey") or "").strip():
                return self.send_json({"ok": False,
                                       "error": "还没填 DeepSeek API Key —— 去「⚙️ 设置」里填一把再搜"})
            jid = search_ai.new_job("search")
            threading.Thread(target=search_ai.search_worker, args=(jid, cfg, q),
                             daemon=True).start()
            return self.send_json({"ok": True, "job": jid})

        if path == "/api/toolbox/backup/run":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            return self.send_json(toolbox.backup_run())

        if path == "/api/toolbox/backup/open":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            try:
                os.makedirs(toolbox.BACKUP_DIR, exist_ok=True)
                return self.send_json({"ok": True,
                                       "opened": open_in_explorer(toolbox.BACKUP_DIR)})
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)})

        if path == "/api/toolbox/memory/release":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            deep = bool(body.get("deep", True))
            # 要深度清理但当前没有管理员 -> 弹 UAC 提权跑一次
            if deep and not toolbox.is_admin():
                r = toolbox.release_memory_elevated()
                if r.get("ok"):
                    return self.send_json({"ok": True, "result": r["result"], "elevated": True})
                # 用户拒绝提权：退回非提权清理，别让人白点一次
                fb = toolbox.release_memory(deep=False)
                return self.send_json({"ok": True, "result": fb, "elevated": False,
                                       "note": r.get("error", "")})
            return self.send_json({"ok": True,
                                   "result": toolbox.release_memory(deep=deep),
                                   "elevated": toolbox.is_admin() and deep})

        if path == "/api/toolbox/dl/probe":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            url = (body.get("url") or "").strip()
            if not url:
                return self.send_json({"ok": False, "error": "请先填下载地址"})
            if not re.match(r"^https?://", url, re.I):
                return self.send_json({"ok": False, "error": "地址要以 http:// 或 https:// 开头"})
            return self.send_json(toolbox.probe_url(url))

        if path == "/api/toolbox/dl/start":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载：%s" % _TOOL_ERR})
            url = (body.get("url") or "").strip()
            folder = (body.get("folder") or "").strip()
            name = (body.get("name") or "").strip()
            if not url:
                return self.send_json({"ok": False, "error": "请先填下载地址"})
            if not folder or not os.path.isdir(folder):
                return self.send_json({"ok": False, "error": "保存目录不存在：%s" % folder})
            info = toolbox.probe_url(url)
            if not info.get("ok"):
                return self.send_json({"ok": False, "error": info.get("error", "无法访问")})
            name = name or info.get("name") or "download.bin"
            name = re.sub(r'[\\/:*?"<>|]+', "_", name)
            path_full = os.path.join(folder, name)
            jid = toolbox.new_dl(url, path_full, body.get("threads") or 8,
                                 info.get("size") or 0, info.get("ranges"))
            threading.Thread(target=toolbox.download_worker, args=(jid,), daemon=True).start()
            return self.send_json({"ok": True, "job": jid, "path": path_full,
                                   "name": name, "size": info.get("size"),
                                   "ranges": info.get("ranges")})

        if path == "/api/toolbox/dl/cancel":
            if not TOOL_OK:
                return self.send_json({"ok": False, "error": "工具箱模块未加载"})
            return self.send_json({"ok": True, "cancelled": toolbox.dl_cancel(body.get("job") or "")})

        if path == "/api/quit":
            self.send_json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0))).start()
            return

        return self.send_json({"ok": False, "error": "not found"}, 404)

    # ---------- 出图预览 ----------
    def serve_output_image(self, p):
        """读产出文件（作品库浏览、试听音频都用它）—— 只允许读白名单目录 ✓"""
        cfg = load_config()
        target = os.path.normpath(p or "")
        if not target or not os.path.isfile(target):
            return self.send_json({"ok": False, "error": "文件不存在"}, 404)
        dirs = [os.path.normpath(cfg.get("outputDir") or "")]
        if GALLERY_OK:
            dirs += gallery_ai.allowed_dirs(dict(cfg, root=ROOT))
        low = target.lower()
        if not any(d and low.startswith(d.lower()) for d in dirs):
            return self.send_json({"ok": False, "error": "越权路径"}, 403)
        return self.send_file(target, cache=True)

    # ---------- 视频预览（支持 Range，能拖进度条）----------
    def serve_output_video(self, p):
        cfg = load_config()
        out_dir = os.path.normpath(cfg.get("videoOutDir") or
                                   os.path.join(os.path.expanduser("~"), "Desktop", "AI视频"))
        target = os.path.normpath(p or "")
        if not target or not os.path.isfile(target):
            return self.send_json({"ok": False, "error": "文件不存在"}, 404)
        if not target.lower().startswith(out_dir.lower()):
            return self.send_json({"ok": False, "error": "越权路径"}, 403)
        size = os.path.getsize(target)
        rng = self.headers.get("Range")
        r = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if r:
            start = int(r.group(1) or 0)
            end = int(r.group(2)) if r.group(2) else size - 1
            end = min(end, size - 1)
            length = max(0, end - start + 1)
            with open(target, "rb") as f:
                f.seek(start)
                body = f.read(length)
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        with open(target, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(body)


def mask_key(k):
    k = (k or "").strip()
    if not k:
        return "(未设置)"
    if len(k) <= 8:
        return k[0] + "*" * (len(k) - 1)
    return k[:4] + "*" * 8 + k[-4:]


def open_url(url):
    """
    用系统默认浏览器打开网址。
    注意：不能用 explorer.exe <url> —— 那会被当成"文件"处理，
    在没有文件关联时弹出「该文件没有与之关联的应用」。
    必须走 ShellExecute（os.startfile）。
    """
    try:
        if os.name == "nt":
            os.startfile(url)          # ShellExecute，正确处理 http/https 协议关联
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        pass
    # 兜底：直接点名常见浏览器
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        try:
            if os.path.exists(exe):
                subprocess.Popen([exe, url])
                return True
        except Exception:
            continue
    return False


def open_in_explorer(target):
    """在资源管理器里打开文件夹 / 定位到某个文件"""
    try:
        if os.name == "nt":
            if os.path.isdir(target):
                subprocess.Popen(["explorer.exe", os.path.normpath(target)])
            else:
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(target)])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
#  启动
# --------------------------------------------------------------------------
def kill_port(port):
    """把占用某个端口的进程关掉（只对本机、只按端口找，用于版本接管）"""
    killed = []
    try:
        import subprocess as _sp
        out = _sp.run(["cmd", "/c", "netstat -ano -p tcp"], capture_output=True,
                      text=True, timeout=25, creationflags=0x08000000).stdout or ""
        pids = set()
        for ln in out.splitlines():
            if (":%d " % port) in ln and "LISTENING" in ln.upper():
                mm = re.search(r"(\d+)\s*$", ln.strip())
                if mm:
                    pids.add(mm.group(1))
        for pid in pids:
            _sp.run(["taskkill", "/F", "/PID", pid], capture_output=True,
                    timeout=15, creationflags=0x08000000)
            killed.append(int(pid))
    except Exception:
        pass
    return killed


def port_free(port):
    s = socket.socket()
    try:
        s.bind((HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe(port):
    """探测该端口上是不是已经有我们自己的实例在跑"""
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (HOST, port), timeout=2) as r:
            d = json.loads(r.read().decode("utf-8"))
            return d if d.get("ok") else None
    except Exception:
        return None


def probe_version(port):
    """问端口上那个实例的版本号；拿不到返回空串"""
    try:
        with urllib.request.urlopen(
                "http://%s:%d/api/health" % (HOST, port), timeout=3) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return (d.get("version") or "").strip()
    except Exception:
        return ""


def fatal(msg):
    """在没有控制台（pythonw）时也能把错误告诉用户"""
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "超级AI工作台 · 启动失败", 0x10)
    except Exception:
        pass


def main():
    global PORT
    want = PORT

    # ---- pythonw 启动时没有控制台，把输出落到日志文件 ----
    try:
        if sys.stdout is None or sys.stderr is None:
            LOGF = open(os.path.join(DATA, "server.log"), "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = LOGF
            if sys.stderr is None:
                sys.stderr = LOGF
            print("\n----- %s  启动 -----" % time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass

    # ---- 已经在跑？ ----
    # ★ 关键：必须是「同一个版本」才算已经在跑。
    #   否则会出现：旧版服务在后台没退（关网页 ≠ 关服务 ✗），
    #   双击新版却只是开个浏览器，看到的还是旧版界面 —— 用户以为没升级 ✗
    alive = probe(want)
    if alive:
        there = probe_version(want)
        if there and there != APP_VERSION:
            print("检测到端口 %d 上跑的是旧版本 %s（本机是 %s），自动接管…"
                  % (want, there, APP_VERSION))
            kill_port(want)
            time.sleep(1.8)
            alive = probe(want)
            if alive:
                print("旧的没能关掉，改为直接打开它（功能可能仍是旧版）")
                open_url("http://%s:%d/" % (HOST, want))
                return
        elif not there:
            # 拿不到版本（老版本没有 version 字段）—— 也当旧版处理 ✓
            print("端口 %d 上的实例没有版本信息（多半是旧版本），自动接管…" % want)
            kill_port(want)
            time.sleep(1.8)
            alive = probe(want)
            if alive:
                open_url("http://%s:%d/" % (HOST, want))
                return
        else:
            print("工作站 %s 已经在运行（端口 %d），正在打开浏览器…" % (there, want))
            open_url("http://%s:%d/" % (HOST, want))
            return

    for p in range(want, want + 20):
        if port_free(p):
            PORT = p
            break
    else:
        fatal("端口 %d-%d 全被占用，启动失败。\n请关掉占用这些端口的程序后重试。" % (want, want + 19))
        sys.exit(1)

    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)

    url = "http://%s:%d/" % (HOST, PORT)
    print("=" * 60)
    print("  大型 AI 工作站")
    print("  地址: " + url)
    print("  工程: " + ROOT)
    print("  关闭: 页面「设置 → 关闭服务」，或按 Ctrl+C")
    print("=" * 60)

    if os.environ.get("AIWS_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: open_url(url)).start()

    try:
        srv = QuietServer((HOST, PORT), Handler)
    except Exception as e:
        fatal("服务启动失败：%s\n端口：%d" % (e, PORT))
        sys.exit(1)

    srv.daemon_threads = True

    # ★ 这里**故意不启动任何后端**。
    #   这台机器只有 16 GB 物理内存，ComfyUI + 音乐工坊 + F5-TTS 同时开着必然爆提交量
    #   （实测报 os error 1455「页面文件太小」）。
    #   所以改成**按需启动**：只有你真的打开某个标签页，才把对应的后端拉起来。
    #   音乐工坊：由 /api/music/status 触发（打开「🎵 AI 音乐工坊」时会调）
    #   朗读    ：tts_ai 是每次任务起一个子进程，本来就按需
    #   ComfyUI ：由「本地千问生图」页的「启动引擎」按钮触发
    def _boot_hint():
        try:
            load_config()
        except Exception:
            pass
    threading.Thread(target=_boot_hint, daemon=True).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
