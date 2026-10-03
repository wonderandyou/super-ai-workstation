# -*- coding: utf-8 -*-
"""
AI 抠图 —— 原生桥接到工作站
=====================================================================
策略：**不重写、直接复用**已有的抠图工具：
    <用户目录>\\Documents\\抠图\\抠图-AI.py

那里面已经把细节做透了，重写只会退化：
    · RMBG-1.4 / RMBG-2.0(BiRefNet) / 2.0-int8 三个 ONNX 模型，按模型分别做归一化
      （1.4 用 min-max 拉伸，2.0 用 ImageNet 均值方差 —— 搞混了边缘会烂）
    · estimate_bg 估背景色 → decontaminate **去色溢**（抠出来的白蕾丝不会带一圈白边）
    · postprocess 支持 --hard 阈值收边、--lo/--hi 电平压缩
    · crop 裁边、avatar 生成头像、preview 出棋盘格/深色底预览

它用 onnxruntime + PIL + numpy，这几样正好都在系统 Python 3.11 里，
所以可以按文件路径 import 复用（和 music_ai.py 一个套路）。
ONNX 会话很占内存，所以**按模型缓存**，并且提供释放接口。
"""

import importlib.util
import os
import sys
import threading
import time

import paths as _paths          # ★ 统一路径层
MATTING_DIR = _paths.matting_dir()
MATTING_APP = os.path.join(MATTING_DIR, "抠图-AI.py")
INNER_NAME = "aiws_matting"

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
IN_DIR = os.path.join(DATA, "抠图素材")
OUT_DIR = os.path.join(DATA, "抠图出片")

SRC_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
SRC_MAX_MB = 40

_mod = None
_load_err = ""
_lock = threading.Lock()
JOBS = {}
JOB_LOCK = threading.Lock()


def _load():
    global _mod, _load_err
    if _mod is not None:
        return _mod
    with _lock:
        if _mod is not None:
            return _mod
        if not os.path.isfile(MATTING_APP):
            _load_err = "找不到抠图工具：%s" % MATTING_APP
            return None
        try:
            if MATTING_DIR not in sys.path:
                sys.path.insert(0, MATTING_DIR)
            spec = importlib.util.spec_from_file_location(INNER_NAME, MATTING_APP)
            m = importlib.util.module_from_spec(spec)
            sys.modules[INNER_NAME] = m
            spec.loader.exec_module(m)          # 它只有 __main__ 里才跑 CLI，导入是安全的
            _mod = m
            _load_err = ""
        except Exception as e:
            _load_err = str(e)
            _mod = None
    return _mod


_load()


# --------------------------------------------------------------------------
def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


def models_status():
    """三个模型的就绪情况和文件大小"""
    m = _load()
    if m is None:
        return []
    out = []
    for k, v in m.MODELS.items():
        p = os.path.join(m.MODEL_DIR, v["file"])
        ok = os.path.isfile(p)
        out.append({
            "name": k,
            "file": v["file"],
            "note": v.get("note", ""),
            "ready": ok,
            "sizeText": human(os.path.getsize(p)) if ok else "-",
        })
    return out


def status():
    m = _load()
    if m is None:
        return {"ok": False, "error": _load_err}
    try:
        import onnxruntime  # noqa
        ort_ver = onnxruntime.__version__
    except Exception:
        ort_ver = "缺失"
    loaded = []
    try:
        loaded = list(getattr(m, "_SESS", {}).keys())
    except Exception:
        pass
    return {
        "ok": True,
        "mattingDir": MATTING_DIR,
        "models": models_status(),
        "onnxruntime": ort_ver,
        "loadedSessions": loaded,
        "sources": list_sources(),
        "outputs": list_outputs(),
        "srcDir": IN_DIR,
        "outDir": OUT_DIR,
        "defaultModel": "rmbg-2.0",
        # 第二个引擎：ComfyUI 内置 BiRefNet
        "engines": [
            {"id": "onnx", "name": "本地工具（RMBG ONNX）",
             "note": "带去色溢，边缘更干净；三个模型可选", "ready": True},
            {"id": "comfy", "name": "ComfyUI 内置 BiRefNet",
             "note": "用 ComfyUI 的节点跑；需要 ComfyUI 在跑（会自动启动，慢一些）",
             "ready": os.path.isfile(os.path.join(
                 _paths.comfy_models(), "background_removal", COMFY_BG_MODEL)),
             "comfyAlive": _comfy_alive()},
        ],
        "defaultEngine": "onnx",
    }


def list_sources():
    items = []
    try:
        os.makedirs(IN_DIR, exist_ok=True)
        for f in sorted(os.listdir(IN_DIR), reverse=True):
            fp = os.path.join(IN_DIR, f)
            if os.path.isfile(fp) and f.lower().endswith(SRC_EXT):
                st = os.stat(fp)
                items.append({"name": f, "sizeText": human(st.st_size),
                              "time": time.strftime("%m-%d %H:%M",
                                                    time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def list_outputs():
    """最近产出。★ 抠图的成品是按「任务号子目录」放的（抠图出片\\<任务号>\\xxx.png），
    所以要进一层；只扫顶层会一条都列不出来 ✗（2026-10-02 修）"""
    items = []
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        pairs = []
        for f in os.listdir(OUT_DIR):
            fp = os.path.join(OUT_DIR, f)
            if os.path.isfile(fp):
                pairs.append((f, fp))
            elif os.path.isdir(fp):
                for g in os.listdir(fp):
                    gp = os.path.join(fp, g)
                    if os.path.isfile(gp):
                        pairs.append((g, gp))
        pairs.sort(key=lambda x: os.path.getmtime(x[1]), reverse=True)
        for f, fp in pairs:
            st = os.stat(fp)
            items.append({"name": f, "sizeText": human(st.st_size),
                          "time": time.strftime("%m-%d %H:%M",
                                                time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def free_models():
    """释放 ONNX 会话占的内存（这台机器只有 16 GB，模型一个就快 1 GB）"""
    m = _load()
    if m is None:
        return 0
    try:
        n = len(m._SESS)
        m._SESS.clear()
        import gc
        gc.collect()
        return n
    except Exception:
        return 0


# --------------------------------------------------------------------------
#  任务
# --------------------------------------------------------------------------
def new_job(kind):
    jid = uuid_hex()
    with JOB_LOCK:
        JOBS[jid] = {"kind": kind, "state": "running", "t0": time.time(),
                     "stage": "准备中", "percent": 0.0, "detail": "",
                     "result": None, "error": None, "log": []}
    return jid


def uuid_hex():
    import uuid
    return uuid.uuid4().hex[:12]


def job_log(jid, msg):
    j = JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + msg)
    if len(j["log"]) > 300:
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


def run_worker(jid, p):
    """按引擎分发：onnx（默认，本地 RMBG 工具）/ comfy（ComfyUI 内置 BiRefNet）"""
    engine = (p.get("engine") or "onnx").strip()
    if engine == "comfy":
        return run_comfy_worker(jid, p)
    return run_onnx_worker(jid, p)


# --------------------------------------------------------------------------
#  引擎二：ComfyUI 内置 BiRefNet
# --------------------------------------------------------------------------
COMFY_PORT = 8188
COMFY_IN = _paths.comfy_input()
COMFY_OUT = _paths.comfy_output()
COMFY_BG_MODEL = "birefnet.safetensors"


def _comfy_alive():
    try:
        import socket
        with socket.create_connection(("127.0.0.1", COMFY_PORT), timeout=0.8):
            return True
    except Exception:
        return False


def _ensure_comfy(jid):
    """ComfyUI 是按需启动的，抠图要用它就得先拉起来。

    ★ 不要自己拼启动命令 —— 一开始我就是自己写的，结果 7 分钟起不来。
      工作站里 local_ai.start_comfy 已经把参数调对了
      （关键差别：creationflags 多了 CREATE_NEW_PROCESS_GROUP，而且把
       stdout 写进 data/comfy.log，出问题能查）。直接调它。
    """
    if _comfy_alive():
        return True
    job_log(jid, "ComfyUI 没在跑，正在启动…")
    job_set(jid, stage="启动 ComfyUI", percent=5,
            detail="加载模型要 1~3 分钟，日志在 data/comfy.log")

    started = False
    try:
        import local_ai                       # 工作站自己的模块，启动逻辑已被验证
        r = local_ai.start_comfy(_load_config_safe())
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "启动失败")
        started = True
        job_log(jid, r.get("message") or "已发出启动命令")
    except Exception as e:
        job_log(jid, "调 local_ai 启动失败（%s），改用自带命令" % str(e)[:80])

    if not started:
        # 兜底：自己起，但要照着 local_ai 的参数来，并且写日志
        import subprocess
        exe = _paths.comfy_py()
        main = os.path.join(_paths.comfy_dir(), "main.py")
        if not os.path.isfile(exe) or not os.path.isfile(main):
            raise RuntimeError("找不到 ComfyUI：%s" % exe)
        logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(logdir, exist_ok=True)
        logf = open(os.path.join(logdir, "comfy.log"), "a", encoding="utf-8", buffering=1)
        flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
        subprocess.Popen([exe, "-s", main, "--windows-standalone-build",
                          "--disable-auto-launch", "--port", str(COMFY_PORT),
                          "--listen", "127.0.0.1"],
                         cwd=_paths.comfy_dir(), creationflags=flags,
                         stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL)

    for i in range(180):                     # 最多等 6 分钟
        time.sleep(2)
        if _comfy_alive():
            job_log(jid, "ComfyUI 端口已就绪（%.0f 秒），但模型还在加载" % ((i + 1) * 2))
            return True
    # 起不来时把日志尾部捞出来给人看
    tail = ""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "comfy.log"), encoding="utf-8",
                  errors="replace") as f:
            tail = "".join(f.readlines()[-6:])[:400]
    except Exception:
        pass
    raise RuntimeError("ComfyUI 没起来。日志尾部：\n%s" % (tail or "（读不到日志）"))


def _load_config_safe():
    try:
        import app as _a
        return _a.load_config()
    except Exception:
        return {}


def _comfy_post(path, obj, timeout=60):
    import json as _j
    import urllib.request as _u
    req = _u.Request("http://127.0.0.1:%d%s" % (COMFY_PORT, path),
                     data=_j.dumps(obj).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
    with _u.urlopen(req, timeout=timeout) as r:
        return _j.loads(r.read().decode("utf-8"))


def _comfy_get(path, timeout=30):
    import json as _j
    import urllib.request as _u
    with _u.urlopen("http://127.0.0.1:%d%s" % (COMFY_PORT, path), timeout=timeout) as r:
        return _j.loads(r.read().decode("utf-8"))


def run_comfy_worker(jid, p):
    """用 ComfyUI 内置的 BiRefNet 抠图（LoadBackgroundRemovalModel + RemoveBackground）"""
    import shutil
    try:
        name = os.path.basename(p.get("source") or "")
        src = os.path.join(IN_DIR, name)
        if not os.path.isfile(src):
            raise RuntimeError("找不到图片，请先上传")

        bgp = os.path.join(_paths.comfy_models(), "background_removal", COMFY_BG_MODEL)
        if not os.path.isfile(bgp):
            raise RuntimeError("ComfyUI 的抠图模型不在：%s" % bgp)

        _ensure_comfy(jid)

        # 图要放进 ComfyUI 的 input 目录它才认
        os.makedirs(COMFY_IN, exist_ok=True)
        cname = "matting_%s_%s" % (jid, name)
        shutil.copy2(src, os.path.join(COMFY_IN, cname))

        job_set(jid, stage="提交给 ComfyUI", percent=20,
                detail="内置 BiRefNet")
        wf = {
            "1": {"class_type": "LoadImage", "inputs": {"image": cname}},
            "2": {"class_type": "LoadBackgroundRemovalModel",
                  "inputs": {"bg_removal_name": COMFY_BG_MODEL}},
            # RemoveBackground 输出的是前景 mask（1 = 主体）
            "3": {"class_type": "RemoveBackground",
                  "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
            # 把 mask 当 alpha 拼回原图 → RGBA
            "4": {"class_type": "JoinImageWithAlpha",
                  "inputs": {"image": ["1", 0], "alpha": ["3", 0]}},
            "5": {"class_type": "SaveImage",
                  "inputs": {"images": ["4", 0],
                             "filename_prefix": "matting/%s" % jid}},
        }
        r = _comfy_post("/prompt", {"prompt": wf}, 60)
        pid = r.get("prompt_id")
        if not pid:
            raise RuntimeError("提交失败：%s" % str(r)[:300])

        job_set(jid, stage="推理中", percent=35, detail="等 ComfyUI 出结果")
        t0 = time.time()
        outfile = None
        while True:
            time.sleep(2)
            el = time.time() - t0
            if el > 600:
                raise RuntimeError("ComfyUI 超过 10 分钟没出结果")
            try:
                h = _comfy_get("/history/%s" % pid)
            except Exception:
                continue
            if pid not in h:
                continue
            st = h[pid].get("status") or {}
            outs = h[pid].get("outputs") or {}
            if not outs:
                if st.get("status_str") == "error":
                    msg = ""
                    for m in (st.get("messages") or []):
                        if m[0] == "execution_error":
                            msg = str(m[1].get("exception_message"))[:200]
                    raise RuntimeError("ComfyUI 执行出错：%s" % (msg or "未知"))
                continue
            for nid, o in outs.items():
                for im in (o.get("images") or []):
                    outfile = os.path.join(COMFY_OUT, im.get("subfolder") or "",
                                           im.get("filename") or "")
            break

        if not outfile or not os.path.isfile(outfile):
            raise RuntimeError("ComfyUI 说完成了，但找不到输出文件")

        # 搬到我们自己的出片目录，顺便出预览图
        jid_dir = os.path.join(OUT_DIR, jid)
        os.makedirs(jid_dir, exist_ok=True)
        base = os.path.splitext(name)[0]
        dst = os.path.join(jid_dir, "%s-ComfyUI透明底.png" % base)
        shutil.copy2(outfile, dst)

        made = [{"name": os.path.basename(dst), "size": os.path.getsize(dst),
                 "sizeText": human(os.path.getsize(dst))}]
        try:
            _make_preview(dst, base, jid_dir, made, p)
        except Exception as e:
            job_log(jid, "（预览图生成失败，不影响结果：%s）" % str(e)[:80])

        dt = round(time.time() - t0, 1)
        job_set(jid, state="done", percent=100, stage="完成",
                result={"files": made, "dir": jid_dir, "seconds": dt,
                        "model": "comfy/" + COMFY_BG_MODEL, "source": name,
                        "engine": "comfy"})
        job_log(jid, "✓ 完成，产出 %d 个文件，用时 %.1f 秒" % (len(made), dt))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


def _make_preview(png_path, base, jid_dir, made, p):
    """出裁边 / 头像 / 棋盘格 / 深色底 预览 —— 和本地工具产物对齐"""
    from PIL import Image
    im = Image.open(png_path).convert("RGBA")

    def add(img, tag, path):
        img.save(path)
        made.append({"name": os.path.basename(path), "size": os.path.getsize(path),
                     "sizeText": human(os.path.getsize(path))})

    # 裁掉透明边
    bb = im.getbbox()
    if bb and p.get("crop"):
        add(im.crop(bb), "裁边", os.path.join(jid_dir, "%s-ComfyUI透明底-裁边.png" % base))

    # 头像
    av = int(p.get("avatar") or 0)
    if av > 0:
        w, h = im.size
        s = min(w, h)
        sq = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
        add(sq.resize((av, av), Image.LANCZOS), "头像",
            os.path.join(jid_dir, "%s-ComfyUI头像%d.png" % (base, av)))

    # 两种预览
    for tag, bg in (("棋盘格", None), ("深色底", (28, 30, 34, 255))):
        canvas = Image.new("RGBA", im.size, (255, 255, 255, 255))
        if bg is None:
            blk = 16
            for y in range(0, im.height, blk):
                for x in range(0, im.width, blk):
                    c = (233, 237, 242, 255) if ((x // blk + y // blk) % 2 == 0) else (255, 255, 255, 255)
                    canvas.paste(Image.new("RGBA", (blk, blk), c), (x, y))
        else:
            canvas = Image.new("RGBA", im.size, bg)
        canvas.alpha_composite(im)
        add(canvas.convert("RGB"), tag,
            os.path.join(jid_dir, "%s-ComfyUI预览-%s.png" % (base, tag)))


def run_onnx_worker(jid, p):
    """引擎一：本地 RMBG ONNX 工具（复用 Documents\\抠图\\抠图-AI.py）"""
    m = _load()
    try:
        if m is None:
            raise RuntimeError(_load_err)
        name = os.path.basename(p.get("source") or "")
        src = os.path.join(IN_DIR, name)
        if not os.path.isfile(src):
            raise RuntimeError("找不到图片，请先上传")
        model = p.get("model") or "rmbg-2.0"
        if model not in m.MODELS:
            raise RuntimeError("不认识的模型：%s" % model)

        os.makedirs(OUT_DIR, exist_ok=True)
        jid_dir = os.path.join(OUT_DIR, jid)
        os.makedirs(jid_dir, exist_ok=True)

        job_log(jid, "图片：%s" % name)
        job_log(jid, "模型：%s（%s）" % (model, m.MODELS[model].get("note", "")))
        job_set(jid, stage="加载模型", percent=8,
                detail="首次加载约 5~30 秒" if model not in m._SESS else "已在内存里")

        # 复用它的参数对象（process 只用到这几个字段）
        class A(object):
            pass
        a = A()
        a.out = jid_dir
        a.model = model
        a.size = int(p.get("size") or 0) or None
        a.lo = float(p.get("lo") if p.get("lo") is not None else 0.02)
        a.hi = float(p.get("hi") if p.get("hi") is not None else 0.98)
        a.hard = p.get("hard")
        a.hard = float(a.hard) if a.hard not in (None, "", 0) else None
        a.hardw = float(p.get("hardw") or 0.08)
        a.no_decontam = bool(p.get("noDecontam"))
        a.preview = True                       # 默认出预览图，方便对比
        a.crop = bool(p.get("crop"))
        a.avatar = int(p.get("avatar") or 0)
        a.quiet = True
        a.cpu = bool(p.get("cpu"))

        # 它用全局 USE_GPU 控制设备
        m.USE_GPU = not a.cpu
        job_set(jid, stage="推理中", percent=25,
                detail="用显卡" if m.USE_GPU else "用 CPU（慢）")

        t0 = time.time()
        m.process(src, a)
        dt = round(time.time() - t0, 1)

        made = []
        for f in sorted(os.listdir(jid_dir)):
            fp = os.path.join(jid_dir, f)
            if os.path.isfile(fp):
                made.append({"name": f, "size": os.path.getsize(fp),
                             "sizeText": human(os.path.getsize(fp))})
        if not made:
            raise RuntimeError("抠图跑完了但没产出文件")

        job_set(jid, state="done", percent=100, stage="完成",
                result={"files": made, "dir": jid_dir, "seconds": dt,
                        "model": model, "source": name})
        job_log(jid, "✓ 完成，产出 %d 个文件，用时 %.1f 秒" % (len(made), dt))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))
