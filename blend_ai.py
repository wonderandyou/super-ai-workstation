# -*- coding: utf-8 -*-
"""
AI 溶图 —— 把抠好的主体自然放进风景照
=====================================================================
两个引擎，各说各的实话：

【方案 A · 算法合成】algo
    纯算法，**不涉及扩散模型**。所以它**不会重新打光** ——
    主体是「室内白光下拍的」，背景是「黄昏逆光」，光向是改不了的。
    它能做好的是三件事：
      1. **色调匹配**（Reinhard 色彩迁移）—— 把主体的均值和方差对齐到背景对应区域，
         解决「主体比背景亮/艳一个档」这个最明显的假点
      2. **边缘羽化** —— 沿 alpha 边缘做过渡，不留硬切边
      3. **接触阴影** —— 在主体脚下加一层柔和暗影，让它「站得住」
    4. **去色溢/饱和度/亮度** 可微调
    秒级出图、零依赖、不占显存。

【方案 B · Qwen 溶图】qwen  ← 2026-10-01 接进来的
    走 ComfyUI 的 **Qwen-Image-Edit-2509-Fusion**（扩散模型 + 两个 LoRA）：
    它**会重新打光、纠正产品透视角度**，把主体真正「融」进背景，
    而不是贴上去再调色。
    代价：吃显存（8G 卡能跑，但最好别同时开别的后端）、
    一次 **约 1~2 分钟**、需要 ComfyUI 在跑（启动必须带 cuDNN workaround，
    见 D:\\AI工作站\\_start_comfy_nocudnn.bat，否则 VAEEncode 会报
    CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH）。

素材要求（两个引擎一样）：
    背景：一张**纯风景照**（不要拼图/带字）
    主体：**纯透明底 PNG**（白底图机器分不清哪里是主体）
    没有的话去「✂️ AI 抠图」先抠一张 —— 界面上有「前往抠图」按钮。

★ 素材文件的坑（两个引擎都受益）：
    方案 B 把素材送进 ComfyUI 时，**文件名带内容哈希** ——
    内容没变就复用同一份（正常命中它自己的缓存 ✓），
    内容变了就换个新名字（不会拿旧图糊弄你 ✗）。
    这样避开了「同名同 seed → 直接用缓存 → 4 秒"完成"」那个坑。
"""

import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid

from PIL import Image, ImageFilter, ImageChops, ImageEnhance

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
BG_DIR = os.path.join(DATA, "溶图背景")
FG_DIR = os.path.join(DATA, "溶图主体")
OUT_DIR = os.path.join(DATA, "溶图出片")

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_MB = 40

JOBS = {}
JOB_LOCK = threading.Lock()

# --------------------------------------------------------------------------
#  方案 B 用到的 ComfyUI 连接与模型（和 _test_blend_b.py 里验证过的一致）
# --------------------------------------------------------------------------
COMFY = "http://127.0.0.1:8188"
import paths as _paths          # ★ 统一路径层（安装版→安装目录；开发机→老位置）
COMFY_ROOT = _paths.comfy_dir()
COMFY_IN = os.path.join(COMFY_ROOT, "input")
COMFY_OUT = os.path.join(COMFY_ROOT, "output")

QWEN_UNET = "qwen_image_edit_2509_int8_convrot.safetensors"
QWEN_CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_VAE = "qwen_image_vae.safetensors"
QWEN_LORA_LIGHT = "Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors"
QWEN_LORA_FUSION = "Qwen-Image-Edit-2509-Fusion.safetensors"
QWEN_TRIGGER = "溶图,纠正产品透视角度和光影并使产品融入背景"
QWEN_EST_SECONDS = 90.0          # 只用来画进度条，不是承诺

ENGINES = [
    {"id": "algo", "name": "方案 A · 算法合成（快、零依赖）",
     "hint": "色调匹配 + 羽化 + 接触阴影，秒级出图。不重新打光 —— 光向和背景不一致改不了。",
     "params": "algo"},
    {"id": "qwen", "name": "方案 B · Qwen 溶图（慢、吃显存）",
     "hint": "扩散模型真融合：会重新打光、纠正透视。约 1~2 分钟，需要 ComfyUI 在跑。"
             "★「补充要求」那栏很关键 —— 写清「全身入镜 / 站在哪里 / 远景近景」；"
             "实测同一组素材：留空会切成大头照，写「人物全身入镜，站在林间小路上，远景，"
             "保持背景不变」就得到全身站在小路上 ✓",
     "params": "ai"},
]


def _json_req(url, payload=None, timeout=60):
    """最小 HTTP 客户端（只用标准库，不引第三方依赖）"""
    import urllib.request
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def comfy_alive():
    """ComfyUI 在不在？返回 (bool, 说明)"""
    try:
        st = _json_req(COMFY + "/system_stats", timeout=5)
        ver = (st.get("system") or {}).get("comfyui_version") or "?"
        return True, "ComfyUI %s 在跑 ✓" % ver
    except Exception as e:
        return False, "ComfyUI 没在跑（%s）" % str(e)[:60]


def _stage_material(path, tag):
    """把素材复制进 ComfyUI 的 input 目录，文件名带内容哈希。

    为什么带哈希：ComfyUI 的 LoadImage 按**文件名**缓存。
    内容换了名字不变 → 它拿旧图出来糊弄你（踩过：4 秒"完成"，输出还是上一个）✗
    名字跟着内容走 = 内容没变就正常命中缓存 ✓、内容变了必然重跑 ✓
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    ext = (os.path.splitext(path)[1] or ".png").lower()
    if ext not in IMG_EXT:
        ext = ".png"
    name = "blend_%s_%s%s" % (h.hexdigest()[:10], tag, ext)
    dst = os.path.join(COMFY_IN, name)
    if not (os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(path)):
        os.makedirs(COMFY_IN, exist_ok=True)
        shutil.copy2(path, dst)
    return name


def build_qwen_fusion(bg_name, fg_name, extra="", steps=8, megapixels=1.0,
                      seed=None, prefix="AI溶图/溶图"):
    """Qwen-Image-Edit-2509-Fusion 工作流（16 节点，只用 ComfyUI 核心节点）

    参数是照 _test_blend_b.py 抄的，那里实测 96 秒出图 ✓ 别乱改：
      两个 LoRA 串联（先 Lightning 加速 → 再 Fusion 溶图）
      ModelSamplingAuraFlow shift=3.0 → CFGNorm strength=1.0
      KSampler steps=8 / cfg=1.0 / euler / simple
      ImageScaleToTotalPixels megapixels=1.0, resolution_steps=8（漏了会验证失败 ✗）
    """
    p = QWEN_TRIGGER + (("," + extra) if extra else "")
    if seed is None:
        seed = int(time.time() * 1000) % (2 ** 31)
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": QWEN_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},

        "10": {"class_type": "LoadImage", "inputs": {"image": bg_name}},
        "11": {"class_type": "LoadImage", "inputs": {"image": fg_name}},

        "12": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["10", 0], "upscale_method": "lanczos",
                          "megapixels": float(megapixels), "resolution_steps": 8}},

        "4": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": QWEN_LORA_LIGHT,
                         "strength_model": 1.0}},
        "5": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["4", 0], "lora_name": QWEN_LORA_FUSION,
                         "strength_model": 1.0}},
        "6": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["5", 0], "shift": 3.0}},
        "7": {"class_type": "CFGNorm",
              "inputs": {"model": ["6", 0], "strength": 1.0}},

        "20": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["2", 0], "prompt": p, "vae": ["3", 0],
                          "image1": ["10", 0], "image2": ["11", 0]}},
        "21": {"class_type": "TextEncodeQwenImageEditPlus",
               "inputs": {"clip": ["2", 0], "prompt": "", "vae": ["3", 0],
                          "image1": ["10", 0], "image2": ["11", 0]}},

        "30": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["12", 0], "vae": ["3", 0]}},

        "40": {"class_type": "KSampler",
               "inputs": {"model": ["7", 0], "positive": ["20", 0], "negative": ["21", 0],
                          "latent_image": ["30", 0], "seed": int(seed),
                          "steps": int(steps), "cfg": 1.0,
                          "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},

        "50": {"class_type": "VAEDecode",
               "inputs": {"samples": ["40", 0], "vae": ["3", 0]}},
        "60": {"class_type": "SaveImage",
               "inputs": {"images": ["50", 0], "filename_prefix": prefix}},
    }


def _safe_name(s):
    return ("".join(c for c in (s or "") if c not in '\\/:*?"<>|').strip()[:40]) or "溶图"


def _unique_out(name, fmt):
    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, name + "." + fmt)
    n = 1
    while os.path.exists(dst):
        dst = os.path.join(OUT_DIR, "%s(%d).%s" % (name, n, fmt))
        n += 1
    return dst



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


def job_log(jid, m):
    j = JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + m)
    if len(j["log"]) > 200:
        del j["log"][:60]


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


def _listing(d, only_alpha=False):
    items = []
    try:
        os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(d), reverse=True):
            fp = os.path.join(d, f)
            if not (os.path.isfile(fp) and f.lower().endswith(IMG_EXT)):
                continue
            st = os.stat(fp)
            info = {"name": f, "sizeText": human(st.st_size),
                    "time": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))}
            if only_alpha:
                # 标出有没有透明通道 —— 溶图只认带 alpha 的
                try:
                    with Image.open(fp) as im:
                        info["hasAlpha"] = (im.mode in ("RGBA", "LA")
                                            or "transparency" in im.info)
                        info["size"] = "%dx%d" % im.size
                except Exception:
                    info["hasAlpha"] = False
            items.append(info)
    except Exception:
        pass
    return items


def status():
    cok, cmsg = comfy_alive()
    return {
        "ok": True,
        "backgrounds": _listing(BG_DIR),
        "subjects": _listing(FG_DIR, only_alpha=True),
        "outputs": _listing(OUT_DIR),
        "bgDir": BG_DIR, "fgDir": FG_DIR, "outDir": OUT_DIR,
        "mattingDir": os.path.join(os.path.expanduser("~"), "Documents", "抠图"),
        "engines": ENGINES,
        "comfyOk": cok,
        "comfyMsg": cmsg,
        "comfyUrl": COMFY,
    }


def path_of(kind, name):
    n = os.path.basename(name or "")
    d = {"bg": BG_DIR, "fg": FG_DIR, "out": OUT_DIR}.get(kind)
    if not (n and d):
        return ""
    p = os.path.join(d, n)
    return p if os.path.isfile(p) else ""


# --------------------------------------------------------------------------
#  核心：溶图
# --------------------------------------------------------------------------
def _reinard_transfer(fg, region):
    """Reinhard 色彩迁移：把 fg 的每个通道拉成 region 的均值/标准差。

    这是「一眼假」最有效的解法 —— 主体比背景亮一档、艳一档，肉眼立刻能看出来。
    对齐均值方差之后，亮度色温就基本融进去了。

    做法在 RGB 上（严格点该用 LAB，但 RGB 对「亮度+色偏」已经够用，而且快）。
    """
    import numpy as np
    f = np.asarray(fg.convert("RGB"), dtype=np.float32)
    r = np.asarray(region.convert("RGB"), dtype=np.float32)
    out = np.empty_like(f)
    for c in range(3):
        fm, fs = f[:, :, c].mean(), f[:, :, c].std() + 1e-6
        rm, rs = r[:, :, c].mean(), r[:, :, c].std() + 1e-6
        out[:, :, c] = (f[:, :, c] - fm) * (rs / fs) + rm
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"))


def _feather(alpha, radius):
    """边缘羽化：对 alpha 做一点模糊，硬切边就变成柔和过渡"""
    if radius <= 0:
        return alpha
    return alpha.filter(ImageFilter.GaussianBlur(radius))


def _contact_shadow(size, box, softness, strength):
    """在主体脚下做一层柔和暗影 —— 让它「踩在地上」而不是飘着"""
    w, h = size
    sh = Image.new("L", size, 0)
    x0, y0, x1, y1 = box
    # 影子的形状：比主体矮、比主体宽一点，落在底部
    sw = max(4, int((x1 - x0) * 1.06))
    shh = max(3, int((y1 - y0) * 0.11))
    cx = (x0 + x1) // 2
    left = max(0, cx - sw // 2)
    top = max(0, y1 - shh // 2)
    from PIL import ImageDraw
    d = ImageDraw.Draw(sh)
    d.ellipse([left, top, left + sw, top + shh], fill=int(255 * strength))
    return sh.filter(ImageFilter.GaussianBlur(max(1, softness)))


def blend_worker(jid, p):
    """按 engine 分派：algo = 方案 A（算法），qwen = 方案 B（Qwen Fusion）"""
    eng = str((p or {}).get("engine") or "algo").strip().lower()
    if eng in ("qwen", "b", "ai", "fusion", "方案b", "方案 b"):
        return _blend_worker_qwen(jid, p)
    return _blend_worker_algo(jid, p)


def _blend_worker_algo(jid, p):
    try:
        bg_p = path_of("bg", p.get("bg"))
        if not bg_p:
            raise RuntimeError("请先选一张背景（风景照）")
        sub_p = path_of("fg", p.get("fg"))
        if not sub_p:
            raise RuntimeError("请先选一张主体（抠好的透明底 PNG）")

        job_set(jid, stage="读取素材", percent=10)
        bg = Image.open(bg_p).convert("RGB")
        fg = Image.open(sub_p).convert("RGBA")
        job_log(jid, "背景 %dx%d　主体 %dx%d" % (bg.size[0], bg.size[1],
                                               fg.size[0], fg.size[1]))

        # 主体有没有透明通道？没有就提醒
        a = fg.getchannel("A")
        amin, amax = a.getextrema()
        if amin >= 250:
            job_log(jid, "⚠️ 这张主体看起来**没有透明区域**（整张不透明）—— " +
                        "如果边缘发硬，去「✂️ AI 抠图」重新抠一张")
        else:
            job_log(jid, "主体带透明通道 ✓（alpha %d~%d）" % (amin, amax))

        # ---- 摆放 ----
        scale = float(p.get("scale") or 0.5)
        fw = max(8, int(bg.size[0] * scale))
        fh = max(8, int(fg.size[1] * fw / float(fg.size[0])))
        fg2 = fg.resize((fw, fh), Image.LANCZOS)
        if p.get("flip"):
            fg2 = fg2.transpose(Image.FLIP_LEFT_RIGHT)

        cx = int(bg.size[0] * float(p.get("x") if p.get("x") is not None else 0.5))
        cy = int(bg.size[1] * float(p.get("y") if p.get("y") is not None else 0.62))
        x0 = cx - fw // 2
        y0 = cy - fh // 2
        # 允许出画（负坐标），裁剪时按画布处理
        x1, y1 = x0 + fw, y0 + fh

        # ---- 取背景里对应的那块，用来对齐色调 ----
        bx0, by0 = max(0, x0), max(0, y0)
        bx1, by1 = min(bg.size[0], x1), min(bg.size[1], y1)
        if bx1 - bx0 < 8 or by1 - by0 < 8:
            raise RuntimeError("主体跑到画布外面了 —— 调一下位置或大小")

        job_set(jid, stage="匹配色调", percent=30)
        region = bg.crop((bx0, by0, bx1, by1))
        strength = float(p.get("colorMatch") if p.get("colorMatch") is not None else 0.75)
        r_fg = _reinard_transfer(fg2, region)
        if strength < 1.0:
            # 按比例混合，避免色调匹配过头把主体本身颜色洗掉
            r_fg = Image.blend(fg2.convert("RGB"), r_fg, max(0.0, min(1.0, strength)))
        job_log(jid, "色调匹配强度 %.0f%%" % (strength * 100))

        # 饱和度微调（可选）
        sat = float(p.get("saturation") or 1.0)
        if abs(sat - 1.0) > 0.01:
            r_fg = ImageEnhance.Color(r_fg).enhance(sat)
            job_log(jid, "饱和度 ×%.2f" % sat)

        # 亮度微调
        bri = float(p.get("brightness") or 1.0)
        if abs(bri - 1.0) > 0.01:
            r_fg = ImageEnhance.Brightness(r_fg).enhance(bri)
            job_log(jid, "亮度 ×%.2f" % bri)

        # ---- 羽化 alpha ----
        job_set(jid, stage="羽化边缘", percent=50)
        feather = float(p.get("feather") if p.get("feather") is not None else 1.2)
        alpha = _feather(fg2.getchannel("A"), feather)
        # 电平压缩，把残留的半透明噪点推干净
        lo = float(p.get("alphaLo") if p.get("alphaLo") is not None else 0.04)
        hi = float(p.get("alphaHi") if p.get("alphaHi") is not None else 0.96)
        import numpy as np
        av = np.asarray(alpha, dtype=np.float32) / 255.0
        av = np.clip((av - lo) / max(1e-6, hi - lo), 0.0, 1.0)
        alpha = Image.fromarray((av * 255).astype("uint8"))

        fg_rgba = r_fg.convert("RGBA")
        fg_rgba.putalpha(alpha)

        # ---- 接触阴影 ----
        out = bg.copy()
        sv = float(p.get("shadow") if p.get("shadow") is not None else 0.4)
        if sv > 0.01:
            job_set(jid, stage="加接触阴影", percent=65)
            # 阴影画在整张画布上（要考虑出画的情况，所以先建大图）
            sh_full = Image.new("L", bg.size, 0)
            sh_local = _contact_shadow((max(1, x1 - x0), max(1, y1 - y0)),
                                       (0, 0, max(1, x1 - x0), max(1, y1 - y0)),
                                       softness=max(2, (y1 - y0) * 0.06),
                                       strength=sv)
            # 贴到画布对应位置（负坐标要裁）
            px0, py0 = max(0, x0), max(0, y0)
            px1, py1 = min(bg.size[0], x1), min(bg.size[1], y1)
            if px1 > px0 and py1 > py0:
                sx0 = px0 - x0
                sy0 = py0 - y0
                sh_full.paste(sh_local.crop((sx0, sy0, sx0 + (px1 - px0),
                                             sy0 + (py1 - py0))), (px0, py0))
            out = Image.composite(Image.new("RGB", bg.size, (0, 0, 0)), out,
                                  sh_full.point(lambda v: int(v * 0.85)))
            job_log(jid, "接触阴影 %.0f%%" % (sv * 100))

        # ---- 合成 ----
        job_set(jid, stage="合成", percent=80)
        layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        if x1 > 0 and y1 > 0 and x0 < bg.size[0] and y0 < bg.size[1]:
            cx0, cy0 = max(0, -x0), max(0, -y0)
            cx1, cy1 = min(fw, bg.size[0] - x0), min(fh, bg.size[1] - y0)
            if cx1 > cx0 and cy1 > cy0:
                layer.paste(fg_rgba.crop((cx0, cy0, cx1, cy1)), (x0 + cx0, y0 + cy0))
        out = Image.alpha_composite(out.convert("RGBA"), layer).convert("RGB")

        # ---- 输出 ----
        job_set(jid, stage="保存", percent=92)
        os.makedirs(OUT_DIR, exist_ok=True)
        base = os.path.splitext(os.path.basename(bg_p))[0]
        name = (p.get("name") or "").strip()
        if not name:
            name = "%s_溶图_%s" % (base[:20], time.strftime("%Y%m%d-%H%M%S"))
        name = "".join(c for c in name if c not in '\\/:*?"<>|').strip()[:40] or "溶图"
        fmt = (p.get("format") or "png").lower()
        dst = os.path.join(OUT_DIR, name + ("." + fmt))
        n = 1
        while os.path.exists(dst):
            dst = os.path.join(OUT_DIR, "%s(%d).%s" % (name, n, fmt))
            n += 1
        if fmt == "jpg":
            out.save(dst, "JPEG", quality=95)
        else:
            out.save(dst, "PNG")

        size = os.path.getsize(dst)
        job_set(jid, state="done", percent=100, stage="完成", result={
            "name": os.path.basename(dst), "size": size, "sizeText": human(size),
            "w": out.size[0], "h": out.size[1],
            "seconds": round(time.time() - JOBS[jid]["t0"], 1)})
        job_log(jid, "✓ 完成：%s（%s　%dx%d）" % (os.path.basename(dst), human(size),
                                              out.size[0], out.size[1]))
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


# --------------------------------------------------------------------------
#  方案 B：Qwen-Image-Edit-2509-Fusion（走 ComfyUI，扩散模型真融合）
# --------------------------------------------------------------------------
def _blend_worker_qwen(jid, p):
    try:
        bg_p = path_of("bg", p.get("bg"))
        if not bg_p:
            raise RuntimeError("请先选一张背景（风景照）")
        sub_p = path_of("fg", p.get("fg"))
        if not sub_p:
            raise RuntimeError("请先选一张主体（抠好的透明底 PNG）")

        job_set(jid, stage="检查 ComfyUI", percent=5)
        ok, msg = comfy_alive()
        job_log(jid, msg)
        if not ok:
            raise RuntimeError(
                "方案 B 要 ComfyUI 在跑 —— 去「🐋 本地千问生图」点「启动引擎」，"
                "或者双击 D:\\AI工作站\\_start_comfy_nocudnn.bat"
                "（★ 必须用这个启动：少了那两个 cuDNN 环境变量，VAEEncode 会报错）")

        job_set(jid, stage="送素材进 ComfyUI", percent=12)
        bg_name = _stage_material(bg_p, "bg")
        fg_name = _stage_material(sub_p, "fg")
        job_log(jid, "背景 %s　主体 %s" % (os.path.basename(bg_p), os.path.basename(sub_p)))
        job_log(jid, "已送进 ComfyUI input：%s / %s" % (bg_name, fg_name))

        steps = int(p.get("aiSteps") or 8)
        mp = float(p.get("aiMegapixels") or 1.0)
        seed = p.get("seed")
        try:
            seed = int(seed) if seed not in (None, "") else None
        except Exception:
            seed = None
        default_name = "%s_AI溶图_%s" % (
            os.path.splitext(os.path.basename(bg_p))[0][:20],
            time.strftime("%Y%m%d-%H%M%S"))
        want_name = _safe_name(p.get("name") or default_name)
        fmt = (p.get("format") or "png").lower()
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in ("png", "jpg", "webp"):
            fmt = "png"

        wf = build_qwen_fusion(bg_name, fg_name,
                               extra=(p.get("extra") or "").strip(),
                               steps=steps, megapixels=mp,
                               seed=seed, prefix="AI溶图/" + want_name)
        job_set(jid, stage="提交任务", percent=18)
        try:
            r = _json_req(COMFY + "/prompt", {"prompt": wf}, timeout=180)
        except Exception as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:400]
            except Exception:
                pass
            raise RuntimeError("提交给 ComfyUI 失败：%s %s" % (str(e)[:160], body))
        pid = r.get("prompt_id")
        if not pid:
            raise RuntimeError("ComfyUI 没收这个工作流：" +
                               json.dumps(r, ensure_ascii=False)[:300])
        job_log(jid, "任务号 %s（%d 节点 / %d 步 / seed %s）" % (pid, len(wf), steps, seed))
        job_log(jid, "扩散模型要跑一会儿，预计 %d 秒上下 —— 进度条按时间估的，"
                     "不是假数据 ✓" % int(QWEN_EST_SECONDS))

        t0 = time.time()
        last = -30
        while True:
            time.sleep(3)
            el = time.time() - t0
            if el > 1800:
                raise RuntimeError("等了 30 分钟还没结果 —— 去 ComfyUI 窗口/日志看看")
            try:
                h = _json_req(COMFY + "/history/" + pid, timeout=30)
            except Exception:
                continue
            if pid not in h:
                pct = min(85.0, 20.0 + el / QWEN_EST_SECONDS * 60.0)
                job_set(jid, stage="采样中", percent=round(pct, 1),
                        detail="已等 %d 秒（预计约 %d 秒）" % (el, int(QWEN_EST_SECONDS)))
                if el - last >= 25:
                    last = el
                    job_log(jid, "[%4ds] 还在采样…" % el)
                continue

            ent = h[pid] or {}
            st = ent.get("status") or {}
            if st.get("status_str") == "error":
                detail = ""
                for m in (st.get("messages") or []):
                    if isinstance(m, (list, tuple)) and len(m) > 1 and m[0] == "execution_error":
                        info = m[1] or {}
                        detail = "%s：%s" % (info.get("node_type"),
                                             info.get("exception_message"))
                raise RuntimeError("ComfyUI 执行出错 —— " + (detail or "看它的日志"))

            files = []
            for _nid, o in (ent.get("outputs") or {}).items():
                for im in (o.get("images") or []):
                    fp = os.path.join(COMFY_OUT, im.get("subfolder") or "",
                                      im.get("filename") or "")
                    if os.path.isfile(fp):
                        files.append(fp)
            if not files:
                continue

            job_set(jid, stage="保存出片", percent=94)
            src = max(files, key=os.path.getmtime)
            dst = _unique_out(want_name, fmt)
            if fmt == "png":
                shutil.copy2(src, dst)
            else:
                with Image.open(src) as im:
                    im.convert("RGB").save(
                        dst, "JPEG" if fmt == "jpg" else "WEBP", quality=95)
            size = os.path.getsize(dst)
            with Image.open(dst) as im:
                w, h = im.size
            secs = round(time.time() - JOBS[jid]["t0"], 1)
            job_set(jid, state="done", percent=100, stage="完成", result={
                "name": os.path.basename(dst), "size": size, "sizeText": human(size),
                "w": w, "h": h, "seconds": secs, "engine": "qwen"})
            job_log(jid, "✓ 完成：%s（%s　%dx%d　整件事用了 %s 秒，其中扩散约 %d 秒）"
                    % (os.path.basename(dst), human(size), w, h, secs, el))
            return
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))
