# -*- coding: utf-8 -*-
r"""
AI 生视频（智谱 CogVideoX-Flash，免费）
=====================================================================
主人：做着玩、成本最低 → 云端免费视频生成

实测确认（2026-10-01）：
  端点  POST https://open.bigmodel.cn/api/paas/v4/videos/generations
  鉴权  Authorization: Bearer <智谱 API Key>
  异步  提交拿到 id → 查 GET /async-result/<id> → task_status: PROCESSING/SUCCESS
  结果  video_result[0].url（mp4，带水印）/ .cover_image_url
  实测  5.11 秒 1792×1024，约 68 秒出片，免费 ✓

对外接口（app.py 调用）：
  video_gen_ai.new_job(kind)
  video_gen_ai.get_job(jid)
  video_gen_ai.start_generate(jid, cfg, req)
  video_gen_ai.MODELS            可选模型表（含是否免费）
  video_gen_ai.check_status(cfg) 探测 Key 是否可用
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

BASE = "https://open.bigmodel.cn/api/paas/v4"
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

# 智谱视频模型（实测 cogvideox-flash 免费且可用）
MODELS = [
    {"id": "cogvideox-flash", "name": "CogVideoX-Flash（免费）", "free": True,
     "note": "免费，约 68 秒出 5 秒片，带「AI生成」水印"},
    {"id": "cogvideox-3", "name": "CogVideoX-3（付费）", "free": False,
     "note": "画质更好、可选 1080p/更长时长；按量计费"},
]

JOBS = {}
_LOCK = threading.Lock()


# --------------------------------------------------------------------------
#  任务表
# --------------------------------------------------------------------------
def new_job(kind="video"):
    jid = "vgen-%d" % (int(time.time() * 1000) % 100000000)
    with _LOCK:
        JOBS[jid] = {
            "id": jid, "kind": kind, "state": "running", "stage": "准备中",
            "percent": 0.0, "detail": "", "t0": time.time(),
            "log": [], "result": None, "error": "",
        }
    return jid


def get_job(jid):
    with _LOCK:
        j = JOBS.get(jid)
        if not j:
            return None
        d = dict(j)
        d["elapsed"] = round(time.time() - j["t0"], 1)
        d["logText"] = "\n".join(j["log"])
        return d


def job_set(jid, **kw):
    with _LOCK:
        j = JOBS.get(jid)
        if j:
            j.update(kw)


def job_log(jid, line):
    with _LOCK:
        j = JOBS.get(jid)
        if j:
            j["log"].append("%s  %s" % (time.strftime("%H:%M:%S"), line))
            j["log"] = j["log"][-400:]


# --------------------------------------------------------------------------
#  HTTP
# --------------------------------------------------------------------------
def _call(path, key, payload=None, method="POST", timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
            msg = (j.get("error") or {}).get("message") or raw
        except Exception:
            msg = raw
        raise RuntimeError("HTTP %s：%s" % (e.code, str(msg)[:300]))
    except Exception as e:
        raise RuntimeError(str(e)[:300])


def _key_of(cfg, req):
    k = (req.get("apiKey") or cfg.get("zhipuKey") or "").strip()
    if not k:
        raise RuntimeError("没填智谱 API Key —— 去「设置」里填，或在下面输入框临时填一个")
    return k


def out_dir(cfg):
    d = (cfg.get("videoOutDir") or "").strip() or os.path.join(DESKTOP, "AI视频")
    os.makedirs(d, exist_ok=True)
    return d


def check_status(cfg):
    """探测 Key 是否可用（调免费的 glm-4-flash，不花钱）"""
    k = (cfg.get("zhipuKey") or "").strip()
    if not k:
        return {"ok": False, "error": "还没填智谱 API Key"}
    try:
        r = _call("/chat/completions", k, {
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": "ping"}], "max_tokens": 3,
        }, timeout=60)
        return {"ok": True, "reply": ((r.get("choices") or [{}])[0].get("message") or {})
                .get("content", "")[:20]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# --------------------------------------------------------------------------
#  生成
# --------------------------------------------------------------------------
def _download(url, path, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
    return os.path.getsize(path)


def generate_worker(jid, cfg, req):
    try:
        cfg = cfg or {}
        req = dict(req or {})
        key = _key_of(cfg, req)
        prompt = (req.get("prompt") or "").strip()
        if not prompt and not req.get("imageUrl"):
            raise RuntimeError("得写点画面描述，或给一张图（图生视频）")

        model = (req.get("model") or "cogvideox-flash").strip()
        body = {"model": model, "prompt": prompt}
        # 参数（有就带，没有就用官方默认）
        if req.get("imageUrl"):
            body["image_url"] = req["imageUrl"]
        if req.get("size"):
            body["size"] = req["size"]
        if req.get("fps"):
            try:
                body["fps"] = int(req["fps"])
            except Exception:
                pass
        if req.get("withAudio") is not None:
            body["with_audio"] = bool(req["withAudio"])

        job_log(jid, "已提交任务（模型 %s%s）" % (
            model, "，图生视频" if req.get("imageUrl") else "，文生视频"))
        job_set(jid, stage="排队中", percent=5.0, detail="模型 %s" % model)

        r = _call("/videos/generations", key, body, timeout=180)
        task = r.get("id") or r.get("request_id")
        if not task:
            raise RuntimeError("没拿到任务 id：%s" % json.dumps(r, ensure_ascii=False)[:200])
        job_log(jid, "任务 id %s" % task)

        # 轮询（免费模型实测约 60~90 秒）
        t0 = time.time()
        vurl = curl = None
        while time.time() - t0 < 900:
            time.sleep(6)
            job_set(jid, stage="生成中", percent=min(90.0, 8 + (time.time() - t0) / 1.6),
                    detail="已等 %.0f 秒" % (time.time() - t0))
            try:
                j = _call("/async-result/%s" % task, key, None, method="GET", timeout=60)
            except Exception as e:
                job_log(jid, "查询出错（继续等）：%s" % str(e)[:80])
                continue
            st = (j.get("task_status") or "").upper()
            if st in ("SUCCESS", "SUCCEED"):
                for v in (j.get("video_result") or []):
                    vurl = v.get("url")
                    curl = v.get("cover_image_url")
                break
            if st in ("FAIL", "FAILED", "ERROR"):
                raise RuntimeError("生成失败：%s" % json.dumps(j, ensure_ascii=False)[:300])
        if not vurl:
            raise RuntimeError("超时没拿到视频地址（等了 15 分钟）")

        job_set(jid, stage="下载中", percent=93.0)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        d = out_dir(cfg)
        path = os.path.join(d, "智谱CogVideo_%s.mp4" % stamp)
        size = _download(vurl, path)
        job_log(jid, "已下载 %s（%.2f MB）" % (os.path.basename(path), size / 2 ** 20))

        # ★ 自动去掉免费模型的「AI生成」水印（crop-scale：裁掉后缩回原尺寸，无痕）✓
        #   实测对比：delogo 会留糊痕 ✗ / crop 会变小 ✗ / crop-scale 最佳 ✓
        if req.get("stripMark", True):
            try:
                import strip_ai_mark
                job_set(jid, stage="去水印", percent=96.0)
                rr = strip_ai_mark.strip(path, mode=str(req.get("stripMode") or "crop-scale"))
                job_log(jid, "已去水印（%s → %s，%s）" % (
                    rr["srcSize"], rr["outSize"], rr["mode"]))
                try:
                    os.remove(path)          # 原片不留（去水印后的顶上）
                except Exception:
                    pass
                path = rr["out"]
                size = os.path.getsize(path)
            except Exception as e:
                job_log(jid, "去水印失败，保留原片：%s" % str(e)[:140])

        cover = ""
        if curl:
            try:
                cover = os.path.join(d, "智谱CogVideo_%s_封面.png" % stamp)
                _download(curl, cover, timeout=180)
            except Exception:
                cover = ""

        # 让工作站自己把片子的规格读出来（时长/分辨率）
        meta = {}
        try:
            meta = _probe_media(path)
        except Exception:
            pass

        res = [{
            "file": path, "name": os.path.basename(path), "size": size,
            "cover": cover, "prompt": prompt, "model": model,
            "cost": 0 if model == "cogvideox-flash" else -1,
            "time": int(time.time()), "seconds": round(time.time() - t0, 1),
            "meta": meta,
        }]
        job_log(jid, "完成 ✓ 用时 %.0f 秒" % (time.time() - t0))
        job_set(jid, state="done", stage="完成", percent=100.0, result=res,
                detail="%s" % (meta.get("text") or ""))
    except Exception as e:
        job_log(jid, "出错：%s" % str(e)[:300])
        job_set(jid, state="error", stage="失败", error=str(e)[:400])


def _probe_media(path):
    """用 ffprobe/ffmpeg 读时长与分辨率（没有就跳过）"""
    import subprocess
    import glob as _glob
    import paths as _paths
    _bin = os.path.join(_paths.comfy(), "python_embeded", "Lib", "site-packages",
                        "imageio_ffmpeg", "binaries")
    _hits = sorted(_glob.glob(os.path.join(_bin, "ffmpeg*.exe")))
    cands = (_hits or [os.path.join(_bin, "ffmpeg.exe")]) + ["ffmpeg"]
    for exe in cands:
        if exe != "ffmpeg" and not os.path.isfile(exe):
            continue
        try:
            r = subprocess.run([exe, "-i", path], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60,
                               creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception:
            continue
        txt = r.stderr or ""
        dur, size = "", ""
        for ln in txt.splitlines():
            s = ln.strip()
            if s.startswith("Duration:"):
                dur = s.split("Duration:")[1].split(",")[0].strip()
            if "Video:" in s and "x" in s:
                for tok in s.split(","):
                    t = tok.strip()
                    if "x" in t and t.replace("x", "").replace(" ", "").isdigit():
                        size = t
                        break
        if dur or size:
            return {"duration": dur, "size": size,
                    "text": "%s · %s" % (dur, size) if (dur and size) else (dur or size)}
    return {}


def start_generate(jid, cfg, req):
    threading.Thread(target=generate_worker, args=(jid, cfg, req), daemon=True).start()
    return jid


# --------------------------------------------------------------------------
#  AI 生成提示词（用免费的 glm-4-flash）
# --------------------------------------------------------------------------
PROMPT_SYS = """你是专业的 AI 视频提示词工程师。用户给你一句话想法，你把它扩写成可直接投喂视频模型的提示词。

每条提示词必须包含这些要素（自然融进一段话里，不要罗列）：
  1. 主体：外貌/材质/服饰等可辨识细节
  2. 动作：具体、连续、可视化（不要"很漂亮"这种空话）
  3. 镜头：推/拉/摇/移/跟/环绕/俯拍/仰拍 里选一个，并说明节奏
  4. 光线与时间：比如"午后斜射的暖阳""清晨薄雾里的冷光"
  5. 环境：地点 + 一两个氛围细节
  6. 风格收尾：如"电影感""浅景深""胶片质感""细腻光影"

硬性要求：
  · 中文，一段话，60~150 字
  · 不分点、不换行、不加 Markdown、不加引号
  · 不出现"请""生成""图片""视频""提示词"这类指令词
  · 只输出提示词本身，不要任何解释或编号
"""


def make_prompts(cfg, req):
    """把一句话想法扩写成 3 条视频提示词候选"""
    key = (req.get("apiKey") or (cfg or {}).get("zhipuKey") or "").strip()
    if not key:
        raise RuntimeError("没填智谱 API Key")
    idea = (req.get("idea") or "").strip()
    if not idea:
        raise RuntimeError("先写一句你的想法")
    style = (req.get("style") or "").strip() or "电影感写实"
    seconds = str(req.get("seconds") or "5")

    user = ("我的想法：%s\n风格偏好：%s\n时长：%s 秒\n\n"
            "请给出 3 条不同的提示词，用一行一条的纯文本输出，"
            "每条前面加「1. 」「2. 」「3. 」，除此之外不要任何多余文字。"
            % (idea, style, seconds))

    r = _call("/chat/completions", key, {
        "model": "glm-4-flash",
        "messages": [{"role": "system", "content": PROMPT_SYS},
                     {"role": "user", "content": user}],
        "temperature": 0.85,
    }, timeout=90)
    txt = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    # 拆成 3 条（容错：带编号 / 带换行 / 带引号都能拆）
    out = []
    for line in str(txt).replace("\r", "").split("\n"):
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[\s\-*•]*\d+[\.、\)]?\s*", "", s)   # 去掉 "1. " "2、"
        s = s.strip().strip('"').strip("“”").strip("「」").strip()
        if len(s) >= 12:
            out.append(s)
    if not out:
        raise RuntimeError("模型没返回可用的提示词：%s" % str(txt)[:160])
    return out[:3]
