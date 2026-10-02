#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— 生视频模块（网易有道智云 AIGC）
=====================================================
五种模式（对应有道智云「视频生成」产品）：
    文生视频      POST /proxy/http/text2video        签名 v3  input=prompt
    图生视频      POST /proxy/http/image2video       签名 v3  input=image
    模版生视频    POST /proxy/http/template2video    签名 v4
    参考生视频    POST /proxy/http/ref2video         签名 v3  input=prompt
    首尾帧生视频  POST /proxy/http/startEnd2video    签名 v4
    任务状态查询  POST /proxy/http/video-task-state  签名 v4

鉴权：sign = sha256(appKey + [input] + salt + curtime + appSecret)
     v3 带 input，v4 不带。
     input 规则：长度 ≤20 用原文；>20 用「前10字 + 长度 + 后10字」。

⚠️ 生成物地址只有 **1 小时有效期** —— 拿到后必须立刻下载存本地。
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
BASE = "https://openapi.youdao.com/proxy/http/"
APPKEY_URL = "https://ai.youdao.com/"
CONSOLE_URL = "https://ai.youdao.com/console/"

JOBS = {}
JLOCK = __import__("threading").Lock()


# --------------------------------------------------------------------------
#  五种模式定义
# --------------------------------------------------------------------------
MODES = {
    "text": {
        "label": "文生视频", "icon": "✍️", "endpoint": "text2video", "sign": 3,
        "inputField": "prompt", "desc": "输入一段文字，生成视频。支持通用风格和动漫风格。",
        "needPrompt": True, "needImages": 0, "needTemplate": False,
        "models": ["youdaoq1", "youdao1.5"],
        "extraFields": ["style"],
    },
    "image": {
        "label": "图生视频", "icon": "🖼️", "endpoint": "image2video", "sign": 3,
        "inputField": "image", "desc": "给一张图，让它动起来。可以加描述告诉它你想怎么动。",
        # needPrompt 控制「显示输入框」，promptOptional 控制「非必填」——
        # 之前两者用同一个开关，所以图生视频的输入框被一起藏掉了。
        "needPrompt": True, "promptOptional": True, "needImages": 1, "needTemplate": False,
        # ★ 图生视频只发这几个字段。画面比例由图片本身决定，再传 aspectRatio 会被打回；
        #   movementAmplitude 是文生视频的参数，这里也不要发。
        "allowFields": ["model", "prompt", "duration", "resolution"],
        "models": ["youdaoq1", "youdao1.5", "youdao2.0", "youdao2-pro", "youdao2-turbo"],
        "extraFields": [],
    },
    "template": {
        "label": "模版生视频", "icon": "🎬", "endpoint": "template2video", "sign": 4,
        "inputField": None, "desc": "套用预设特效模版（如与兽同行、异域公主），不用写提示词。",
        "needPrompt": False, "needImages": 1, "needTemplate": True,
        "models": [],
        "extraFields": ["area", "beast"],
    },
    "ref": {
        "label": "参考生视频", "icon": "🎯", "endpoint": "ref2video", "sign": 3,
        "inputField": "prompt", "desc": "给多张参考图 + 文字描述，生成视频。",
        "needPrompt": True, "needImages": 1, "needTemplate": False,
        "models": ["youdaoq1", "youdao1.5", "youdao2.0"],
        "extraFields": [],
    },
    "startend": {
        "label": "首尾帧生视频", "icon": "🎞️", "endpoint": "startEnd2video", "sign": 4,
        "inputField": None, "desc": "给首帧和尾帧两张图，自动补出中间的过渡动画。",
        "needPrompt": True, "needImages": 2, "needTemplate": False,
        "models": ["youdaoq1", "youdaoq1-classic", "youdao1.5", "youdao2.0",
                   "youdao2-pro", "youdao2-turbo"],
        "extraFields": [],
    },
}

# 各模型支持的时长 / 分辨率
#
# 字段含义：
#   durations —— 时长下拉里给哪些秒数
#   res       —— {时长: [可选分辨率]}；键 0 表示「不分时长，所有时长都用这套」
#   resAll    —— 可选。不分时长的统一分辨率列表（比逐时长写更省事）
#   free      —— 可选。True 表示这一项还允许手填（下拉里会多一个「自定义…」）
#
# 说明：下面给的是「放宽后」的候选值。云端接口对个别组合可能报错，
#       真报错了它会返回明确错误码，照着提示换一个即可 —— 比一开始就锁死几个值好用。
_ALL360 = ["360p", "480p", "540p", "720p", "1080p"]
_ALL720 = ["480p", "540p", "720p", "1080p"]
_HI = ["720p", "1080p", "2k", "4k"]
_DUR8 = [2, 3, 4, 5, 6, 7, 8, 9, 10]
_DUR15 = [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15]

#   ⚠️ 踩过的坑：我一度把下面这些值「放宽」了（想着多给点选择总没错），
#      结果接口是**严格校验**的 —— youdaoq1 传 3/4/6/8 直接回
#      「model: youdaoq1 only support duration: 5;」被打回。
#      所以**下拉里只放已知能过的值**，想试别的用「自定义…」手填，
#      填错也没关系 —— 接口会把合法值写在 message 里告诉你（现在能显示出来了）。
MODEL_RULES = {
    # q1 系列：只支持 5 秒
    "youdaoq1":         {"durations": [5], "resAll": _ALL360, "free": True},
    "youdaoq1-classic": {"durations": [5], "resAll": _ALL360, "free": True},
    # 1.5 / 2.0：官方给的档位
    "youdao1.5":        {"durations": [4, 8], "res": {4: ["360p", "720p", "1080p"],
                                                      8: ["720p"]}, "free": True},
    "youdao2.0":        {"durations": [4, 8], "res": {4: ["360p", "720p", "1080p"],
                                                      8: ["720p"]}, "free": True},
    # pro / turbo
    "youdao2-pro":      {"durations": [2, 3, 4, 5, 6, 7, 8],
                         "resAll": ["720p", "1080p"], "free": True},
    "youdao2-turbo":    {"durations": [2, 3, 4, 5, 6, 7, 8],
                         "resAll": ["720p", "1080p"], "free": True},
}


def model_rule(mid):
    """把 MODEL_RULES 规整成 {durations: [...], res: {时长: [..]}} 的统一形状"""
    r = MODEL_RULES.get(mid) or {}
    durs = list(r.get("durations") or [4])
    res = dict(r.get("res") or {})
    if r.get("resAll"):
        res = {0: list(r["resAll"])}
    free = bool(r.get("free"))
    # 兜底：至少保证每个时长都能取到一套分辨率
    if not res:
        res = {0: ["720p"]}
    return {"durations": durs, "res": res, "free": free}

STYLES = [{"v": "general", "t": "通用风格"}, {"v": "anime", "t": "动漫风格"}]
ASPECTS = ["16:9", "9:16", "1:1"]
AMPLITUDES = [{"v": "auto", "t": "自动"}, {"v": "small", "t": "小"},
              {"v": "medium", "t": "中"}, {"v": "large", "t": "大"}]

# 模版（文档没给完整列表，这里放已知的 + 允许手填）
TEMPLATES = [
    {"v": "yd_werewolf_trans", "t": "狼人变身"},
    {"v": "beast_companion", "t": "与兽同行"},
    {"v": "exotic_princess", "t": "异域公主"},
]
AREAS = ["auto", "denmark", "uk", "africa", "china", "mexico", "switzerland",
         "russia", "italy", "korea", "thailand", "india", "japan"]
BEASTS = ["auto", "bear", "tiger", "elk", "snake", "lion", "wolf"]

ERRORS = {
    "0": "成功", "1": "未知错误，请联系客服",
    "101": "缺少必填参数（或参数写错）",
    "108": "应用ID无效 / 应用密钥不对 —— 去控制台核对「应用ID + 应用密钥」",
    "110": "该应用没开通这个服务 —— 控制台「修改应用」里勾上「视频生成」",
    "112": "请求的服务不存在",
    "202": "签名检验失败 —— 应用密钥不对",
    "206": "时间戳无效导致签名失败 —— 检查电脑时间",
    "207": "重放请求",
    "900001": "并发量过高，稍后重试",
    "901004": "当前模版不支持",
    "901050": "余额不足 —— 去有道智云控制台充值 / 买资源包",
    "9411": "请求过于频繁 —— 隔几十秒再试（连点太多次会触发）",
    "400": "接口不接受这次请求（参数或图片不合规）—— 看下面「接口原话」里的 message",
    "904001": "查询视频生成任务状态异常",
    "904002": "未找到视频生成任务",
    "905000": "文生视频服务异常",
    "905100": "图生视频服务异常",
    "905200": "模版生视频服务异常",
}


def explain(code, msg=""):
    c = str(code or "")
    tip = ERRORS.get(c, "")
    out = msg or tip or ("错误码 " + c)
    if tip and msg and tip not in msg:
        out = "%s\n→ %s" % (msg, tip)
    elif not msg and tip:
        out = tip
    return out


# --------------------------------------------------------------------------
#  配置
# --------------------------------------------------------------------------
def load_conf(cfg):
    return {
        "appKey": (cfg.get("youdaoAppKey") or "").strip(),
        "secret": (cfg.get("youdaoSecret") or "").strip(),
        "outDir": (cfg.get("videoOutDir") or
                   os.path.join(os.path.expanduser("~"), "Desktop", "AI视频")).strip(),
    }


def mask(k):
    k = (k or "").strip()
    if not k:
        return "(未设置)"
    return k[:4] + "*" * 6 + k[-4:] if len(k) > 10 else k[0] + "*" * (len(k) - 1)


# --------------------------------------------------------------------------
#  签名
# --------------------------------------------------------------------------
def make_sign(appkey, secret, salt, curtime, input_val=None, ver=3):
    if ver == 3:
        s = input_val or ""
        if len(s) > 20:
            s = s[:10] + str(len(s)) + s[-10:]
        raw = appkey + s + salt + curtime + secret
    else:
        raw = appkey + salt + curtime + secret
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def post_json(path, body, timeout=60):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"code": str(e.code), "msg": raw[:300]}
    except Exception as e:
        return -1, {"code": "-1", "msg": str(e)}


# --------------------------------------------------------------------------
#  任务
# --------------------------------------------------------------------------
def new_job(mode):
    jid = uuid.uuid4().hex[:12]
    with JLOCK:
        JOBS[jid] = {"kind": "video", "mode": mode, "state": "running",
                     "t0": time.time(), "stage": "准备中", "percent": 0.0,
                     "detail": "", "result": None, "error": None, "log": []}
    return jid


def jlog(jid, m):
    j = JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + m)
    if len(j["log"]) > 300:
        del j["log"][:80]
    j["stage"] = m


def jset(jid, **kw):
    j = JOBS.get(jid)
    if j:
        j.update(kw)


def _norm_images(images):
    """图片可以是 data:image/...;base64 或 URL；原样传给接口即可"""
    out = []
    for x in (images or []):
        x = (x or "").strip()
        if x:
            out.append(x)
    return out


def submit(conf, mode, p):
    """提交一个生成任务，返回 (ok, taskId_or_error, data)"""
    m = MODES.get(mode)
    if not m:
        return False, "未知模式：%s" % mode, {}
    appkey, secret = conf["appKey"], conf["secret"]
    if not appkey or not secret:
        return False, "还没填「应用ID / 应用密钥」—— 去本页设置里填", {}

    imgs = _norm_images(p.get("images"))
    if len(imgs) < m["needImages"]:
        return False, "这个模式至少要 %d 张图片" % m["needImages"], {}
    prompt = (p.get("prompt") or "").strip()[:1500]

    salt = str(uuid.uuid4())
    curtime = str(int(time.time()))

    if m["inputField"] == "prompt":
        iv = prompt
    elif m["inputField"] == "image":
        iv = imgs[0] if imgs else ""
    else:
        iv = None

    body = {
        "appKey": appkey, "salt": salt, "curtime": curtime,
        "sign": make_sign(appkey, secret, salt, curtime, iv, m["sign"]),
    }

    if mode == "template":
        body["template"] = (p.get("template") or "").strip()
        if not body["template"]:
            return False, "模版生视频必须选一个模版", {}
        if p.get("area") and body["template"] == "exotic_princess":
            body["area"] = p["area"]
        if p.get("beast") and body["template"] == "beast_companion":
            body["beast"] = p["beast"]
        if imgs:
            body["images"] = imgs
        if p.get("bgm") is not None:
            body["bgm"] = bool(p["bgm"])
        if p.get("watermark") is not None:
            body["watermark"] = bool(p["watermark"])
    else:
        body["model"] = p.get("model") or (m["models"][0] if m["models"] else "")
        if prompt:
            body["prompt"] = prompt
        if imgs:
            body["images" if len(imgs) > 1 or mode == "startend" else "image"] = \
                imgs if (len(imgs) > 1 or mode == "startend") else imgs[0]
        if p.get("duration"):
            body["duration"] = int(p["duration"])
        if p.get("resolution"):
            body["resolution"] = p["resolution"]
        # ★ 白名单：只有「这个模式明确接受」的字段才发。
        #   踩过的坑：图生视频带了 aspectRatio / movementAmplitude 直接被接口打回 400，
        #   而错误里连 msg 都没有，只能靠二分法试出来。宁可少发，让它用默认值。
        allow = m.get("allowFields")
        opt = {
            "aspectRatio": p.get("aspectRatio"),
            "movementAmplitude": p.get("movementAmplitude"),
            "style": p.get("style") if mode == "text" else None,
            "seed": int(p["seed"]) if p.get("seed") else None,
        }
        for k, v in opt.items():
            if not v:
                continue
            if allow is not None and k not in allow:
                continue
            body[k] = v
        # bgm / watermark 也走白名单
        for k in ("bgm", "watermark"):
            if p.get(k) is None:
                continue
            if allow is not None and k not in allow:
                continue
            body[k] = bool(p[k])

    st, d = post_json(m["endpoint"], body)
    code = str(d.get("code"))
    if code != "0":
        # ★ 有道接口给的是 `message`，不是 `msg`！
        #   之前只读 msg，所以永远拿到空串，界面上只剩「错误码 400」这种没用的提示。
        #   这里两个都读，外加 successful 标记。
        api_msg = (d.get("message") or d.get("msg") or "").strip()
        if not api_msg and d.get("successful") is False:
            api_msg = "接口没给具体原因"
        try:
            safe = dict(body)
            for k in ("image", "images"):
                if k in safe:
                    v = safe[k]
                    if isinstance(v, list):
                        safe[k] = ["<base64 %d 字符>" % len(str(x)) for x in v]
                    else:
                        safe[k] = "<base64 %d 字符>" % len(str(v))
            safe["sign"] = safe.get("sign", "")[:8] + "…"
            detail = "HTTP %s  code=%s\n接口原话：%s\n请求体：%s" % (
                st, code,
                json.dumps(d, ensure_ascii=False)[:600],
                json.dumps(safe, ensure_ascii=False)[:700])
        except Exception:
            detail = "HTTP %s  code=%s  %s" % (st, code, str(d)[:400])
        return False, explain(code, api_msg), {"detail": detail, "raw": d}
    tid = (d.get("data") or {}).get("taskId")
    if not tid:
        return False, "接口没返回 taskId：%s" % json.dumps(d, ensure_ascii=False)[:200], d
    return True, tid, d


def query(conf, task_id):
    salt = str(uuid.uuid4())
    curtime = str(int(time.time()))
    body = {
        "appKey": conf["appKey"], "salt": salt, "curtime": curtime,
        "sign": make_sign(conf["appKey"], conf["secret"], salt, curtime, None, 4),
        "taskId": task_id,
    }
    st, d = post_json("video-task-state", body, timeout=40)
    return d


def download(url, dest, timeout=600):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "AIWorkstation"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    os.replace(tmp, dest)
    return os.path.getsize(dest)


def safe_name(s, limit=28):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", str(s or "")).strip()
    s = re.sub(r"\s+", "_", s)
    return (s[:limit] or "video")


def run_job(jid, cfg, mode, p):
    """后台：提交 -> 轮询 -> 立刻下载（地址只有 1 小时有效）"""
    conf = load_conf(cfg)
    try:
        m = MODES[mode]
        jlog(jid, "正在提交「%s」…" % m["label"])
        jset(jid, stage="提交中", percent=3)
        ok, res, raw = submit(conf, mode, p)
        if not ok:
            # 把详情写进任务日志 —— 不然界面上只有一句「错误码 400」没法排查
            if isinstance(raw, dict) and raw.get("detail"):
                for ln in str(raw["detail"]).splitlines():
                    jlog(jid, ln)
            raise RuntimeError(res)
        tid = res
        price = (raw.get("data") or {}).get("price")
        jlog(jid, "已受理，任务号 %s%s" % (tid, ("　价格 " + str(price) + " 元") if price else ""))
        jset(jid, stage="生成中", percent=8, detail="任务号 " + tid)

        t0 = time.time()
        last = ""
        while True:
            time.sleep(6)
            el = time.time() - t0
            d = query(conf, tid)
            dd = d.get("data") or {}
            st = dd.get("state") or ""
            jset(jid, percent=round(min(94, 8 + 86 * (1 - pow(2.718, -el / 150.0))), 1),
                 detail="已等 %d 秒　状态 %s" % (el, st))
            if st != last:
                jlog(jid, "状态：%s" % st)
                last = st
            if st == "success":
                urls = dd.get("videoUrls") or []
                covers = dd.get("coverUrls") or []
                if not urls:
                    raise RuntimeError("状态成功但没返回视频地址")
                out_dir = conf["outDir"]
                os.makedirs(out_dir, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                base = safe_name(p.get("prompt") or m["label"])
                saved = []
                for i, u in enumerate(urls):
                    name = "%s_%s_%s_%02d.mp4" % (base, mode, stamp, i + 1)
                    path = os.path.join(out_dir, name)
                    jlog(jid, "正在下载视频（地址 1 小时后失效，必须马上存）…")
                    jset(jid, stage="下载中", percent=96)
                    size = download(u, path)
                    cover_name = ""
                    if i < len(covers) and covers[i]:
                        cover_name = os.path.splitext(name)[0] + "_封面.jpg"
                        try:
                            download(covers[i], os.path.join(out_dir, cover_name))
                        except Exception:
                            cover_name = ""
                    saved.append({"file": path, "name": name, "size": size,
                                  "cover": cover_name, "mode": m["label"],
                                  "prompt": p.get("prompt") or "",
                                  "model": p.get("model") or p.get("template") or "",
                                  "reqSize": "%s / %s" % (p.get("resolution") or "-",
                                                          p.get("duration") or "-"),
                                  "cost": float(price or 0), "time": int(time.time()),
                                  "seconds": round(time.time() - t0, 1)})
                jlog(jid, "完成")
                jset(jid, state="done", percent=100, stage="完成", result=saved)
                return
            if st in ("failed", "sensitive", "not_found"):
                raise RuntimeError("生成失败：%s %s" % (st, explain(dd.get("errorCode") or "",
                                                                "")))
            if el > 1800:
                raise RuntimeError("超时（30 分钟）")
    except Exception as e:
        jset(jid, state="error", error=str(e))
        jlog(jid, "失败：" + str(e))


def status(cfg):
    conf = load_conf(cfg)
    return {
        "modes": [{"key": k, "label": v["label"], "icon": v["icon"], "desc": v["desc"],
                   "needPrompt": v["needPrompt"], "needImages": v["needImages"],
                   # promptOptional：显示描述框但不必填（图生视频就是这一档）
                   "promptOptional": bool(v.get("promptOptional", False)),
                   "needTemplate": v["needTemplate"], "models": v["models"],
                   "extraFields": v["extraFields"]} for k, v in MODES.items()],
        "modelRules": {k: model_rule(k) for k in MODEL_RULES},
        "styles": STYLES, "aspects": ASPECTS, "amplitudes": AMPLITUDES,
        "templates": TEMPLATES, "areas": AREAS, "beasts": BEASTS,
        "appKeySet": bool(conf["appKey"]), "appKeyMask": mask(conf["appKey"]),
        "secretSet": bool(conf["secret"]), "secretMask": mask(conf["secret"]),
        "outDir": conf["outDir"],
        "ready": bool(conf["appKey"] and conf["secret"]),
        "appKeyUrl": APPKEY_URL, "consoleUrl": CONSOLE_URL,
    }
