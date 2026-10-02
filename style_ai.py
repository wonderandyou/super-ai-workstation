# -*- coding: utf-8 -*-
r"""
风格关键词提取（AI 生图用）
=====================================================================
主人 2026-10-01 要的功能：
    给两个 AI 生图（豆包 Seedream / 本地千问）加
    「上传图片 → 调 DeepSeek API 做多模块识别 → 提取风格关键词 →
      关键词存本地 → 后期可选」

原理（已实测确认 ✓，见 MEMO §32）：
    DeepSeek 的 `deepseek-chat` **本身就有视觉能力**，
    用 OpenAI 标准格式发图片即可：
        content: [{"type":"text",...}, {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]
    实测「左蓝右黄」的图能准确答出，多图也能一起认 ✓

「多模块」= 8 个维度分别提取关键词：
    画风 / 主体 / 配色 / 光影 / 构图 / 氛围 / 质感 / 细节

关键词库存本地：`data\风格关键词库.json`

用法：
    python style_ai.py test <图片路径>      # 命令行试一张
"""
import base64
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CFG = os.path.join(DATA, "config.json")
LIB = os.path.join(DATA, "风格关键词库.json")

# 8 个识别模块（key 用于 JSON 字段，label 给界面看，hint 是喂给模型的说明）
MODULES = [
    ("style",   "画风", "整体画风与艺术流派"),
    ("subject", "主体", "画面主体及其关键特征"),
    ("palette", "配色", "主色调与配色关系"),
    ("light",   "光影", "光线方向、明暗与反射"),
    ("compo",   "构图", "构图方式与视角机位"),
    ("mood",    "氛围", "情绪与氛围感"),
    ("texture", "质感", "材质、笔触、颗粒"),
    ("detail",  "细节", "值得保留的细节元素"),
]

PROMPT = """你是图像风格分析专家。仔细看这张图，按下面 8 个模块提取**可直接用于 AI 绘画提示词**的风格关键词。

%s

要求：
1. 每个模块给 3~6 个关键词，用「、」分隔，中文为主（专业术语可保留英文）
2. 关键词要具体、可复现，不要空话（不要写「很好看」「很精美」这类）
3. 必须严格按下面的 JSON 结构输出，**不要任何解释、不要 markdown 代码块**：
%s
"""

JOBS = {}
JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 配置 / 库

def load_cfg():
    try:
        c = json.load(open(CFG, encoding="utf-8"))
    except Exception:
        c = {}
    return {
        "base": (c.get("llmBase") or c.get("dsBase") or "https://api.deepseek.com/v1").rstrip("/"),
        "key": c.get("dsKey") or c.get("llmKey") or "",
        "model": c.get("llmModel") or "deepseek-chat",
    }


def read_lib():
    try:
        d = json.load(open(LIB, encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"version": 1, "items": []}


def write_lib(d):
    os.makedirs(DATA, exist_ok=True)
    tmp = LIB + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIB)


# ---------------------------------------------------------------- 图片处理

def shrink_to_dataurl(path, max_side=1024, quality=85):
    """把图片压到合理大小再转 data URL（原图直接 base64 会太大）"""
    try:
        from PIL import Image
        im = Image.open(path)
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            k = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        b = buf.getvalue()
        return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii"), len(b), im.size
    except Exception:
        # 退路：原样读（GIF/WebP 之类）
        raw = open(path, "rb").read()
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp",
                "gif": "gif", "bmp": "bmp"}.get(ext, "png")
        return ("data:image/%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")),
                len(raw), (0, 0))


# ---------------------------------------------------------------- 调 API

def analyze(paths, modules=None, timeout=180):
    """调 DeepSeek 视觉，一次拿全部分模块关键词。返回 (ok, dict|error)"""
    cfg = load_cfg()
    if not cfg["key"]:
        return False, "没配 API Key（去「设置」里填 DeepSeek API Key）"
    use = [m for m in MODULES if (not modules or m[0] in modules)]
    if not use:
        use = MODULES

    urls, sizes = [], []
    for p in paths:
        if not os.path.isfile(p):
            return False, "找不到图片：%s" % p
        u, n, wh = shrink_to_dataurl(p)
        urls.append(u)
        sizes.append((os.path.basename(p), n, wh))

    listing = "\n".join("%d. %s —— %s" % (i + 1, m[1], m[2]) for i, m in enumerate(use))
    skeleton = "{\n" + ",\n".join('  "%s": "关键词、关键词、关键词"' % m[0] for m in use) + "\n}"
    prompt = PROMPT % (listing, skeleton)

    content = [{"type": "text", "text": prompt}]
    for u in urls:
        content.append({"type": "image_url", "image_url": {"url": u}})
    payload = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
               "max_tokens": 1200, "temperature": 0.3}
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["key"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)["error"]["message"]
        except Exception:
            pass
        return False, "接口返回 %s：%s" % (e.code, str(body)[:220])
    except Exception as e:
        return False, "请求失败：%s" % str(e)[:200]

    try:
        text = d["choices"][0]["message"]["content"]
    except Exception:
        return False, "返回格式异常：%s" % json.dumps(d, ensure_ascii=False)[:200]

    # 容错：模型有时会裹 ```json
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        t = m.group(0)
    try:
        got = json.loads(t)
    except Exception:
        return False, "解析 JSON 失败，模型原文：%s" % text[:300]

    out = {}
    for k, label, _ in use:
        v = got.get(k)
        if isinstance(v, list):
            v = "、".join(str(x) for x in v)
        out[k] = {"label": label, "text": str(v or "").strip()}
    return True, {"modules": out, "images": sizes,
                  "raw": text[:1500], "model": cfg["model"]}


def build_prompt_text(mods):
    """把各模块关键词拼成一段可直接用的提示词"""
    parts = []
    for k, label, _ in MODULES:
        m = (mods or {}).get(k) or {}
        v = (m.get("text") if isinstance(m, dict) else str(m)) or ""
        if v.strip():
            parts.append("%s：%s" % (label, v.strip()))
    return "，".join(parts)


# ---------------------------------------------------------------- 任务封装

def new_job(kind):
    jid = "%s-%d" % (kind, int(time.time() * 1000) % 10**9)
    with JOB_LOCK:
        JOBS[jid] = {"state": "running", "t0": time.time(), "percent": 5,
                     "stage": "准备中", "detail": "", "log": [], "kind": kind}
    return jid


def job_log(jid, msg):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j:
            j["log"].append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
            j["log"] = j["log"][-200:]


def job_set(jid, **kw):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j:
            j.update(kw)


def get_job(jid):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if not j:
            return None
        out = dict(j)
        out["elapsed"] = int(time.time() - j["t0"])
        out["logText"] = "\n".join(j["log"][-100:])
        return out


def start_analyze(paths, modules=None):
    jid = new_job("style")
    with JOB_LOCK:
        JOBS[jid]["result"] = None

    def work():
        try:
            job_log(jid, "图片 %d 张，开始识别…" % len(paths))
            job_set(jid, stage="调用 DeepSeek 视觉识别", percent=25)
            ok, res = analyze(paths, modules)
            if not ok:
                job_log(jid, "✗ %s" % res)
                job_set(jid, state="failed", stage="失败", detail=str(res)[:200])
                return
            job_log(jid, "✓ 识别完成（%d 个模块）" % len(res["modules"]))
            job_set(jid, state="done", percent=100, stage="完成",
                    detail="识别到 %d 个模块 ✓" % len(res["modules"]))
            with JOB_LOCK:
                JOBS[jid]["result"] = res
        except Exception as e:
            job_log(jid, "✗ 异常：%s" % e)
            job_set(jid, state="failed", stage="失败", detail=str(e)[:200])

    threading.Thread(target=work, daemon=True).start()
    return jid


# ---------------------------------------------------------------- CLI

def _cli():
    if len(sys.argv) >= 3 and sys.argv[1] == "test":
        ok, res = analyze([sys.argv[2]])
        if not ok:
            print("✗ %s" % res)
            return 1
        for k, label, _ in MODULES:
            m = res["modules"].get(k, {})
            print("%-6s %s" % (label, m.get("text", "")))
        print("")
        print("拼成提示词：")
        print(build_prompt_text(res["modules"]))
        return 0
    print("用法：python style_ai.py test <图片路径>")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
