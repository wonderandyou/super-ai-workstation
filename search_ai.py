# -*- coding: utf-8 -*-
"""
AI 搜索引擎 —— 联网搜索 + 分点论述 + 来源
=====================================================================
做法**完全照 DSH 内核的 `dsh-web-search-deepseek`**（不是我自己编的）：
    端点  https://api.deepseek.com/anthropic/v1/messages   （DeepSeek 的 Anthropic 兼容接口）
    鉴权  x-api-key: <DeepSeek Key>  ＋  Authorization: Bearer <同一个 key>
    标头  anthropic-version: 2023-06-01
    工具  原生服务器工具 {"type": "web_search_20250305", "name": "web_search", "max_uses": N}
    来源  **只从结构化的 web_search_tool_result 块里取**（url / title / page_age），
          绝不从回答文本里抓 URL —— 内核文档专门强调了这一点 ✓
    摘录  文本块 citations[] 里的 cited_text，按 URL 归位当摘要

为什么绕这么一圈：DeepSeek 的 **Chat Completions / Responses 接口都不支持联网**
（官方文档里 `web_search` 是被「忽略」的内置工具），
只有这条 Anthropic 兼容的 Messages 通道带**服务端原生联网搜索** ✓

限制（内核文档也写了）：一次搜索会消耗一个完整模型轮次（几十秒 + 生成 token），
    所以这里的进度条是按时间估的，不是假数据。

配置（读工作站的 data/config.json）：
    dsKey          DeepSeek API Key（明文，本机；在「⚙️ 设置」里填）
    dsSearchBase   端点基址，默认 https://api.deepseek.com/anthropic/v1
    dsSearchModel  模型名，默认 deepseek-v4-flash
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HIST_DIR = os.path.join(DATA, "搜索历史")

BASE_DEFAULT = "https://api.deepseek.com/anthropic/v1"
MODEL_DEFAULT = "deepseek-v4-flash"
API_VERSION = "2023-06-01"
MAX_USES = 5
MAX_TOKENS = 4096
EST_SECONDS = 45.0          # 只用来画进度条

JOBS = {}
JOB_LOCK = threading.Lock()

# 给模型的指令：既要它搜，也要它按「分点论述 + 标来源」来答
PROMPT = """请联网搜索后回答下面这个问题，用简体中文。

要求：
1. 先给**分点论述**的答案：3~6 点，每点先结论后理由，一点一到三句；
2. 每个论点后面用 [1] [2] 这样的标号标出它依据的来源编号；
3. **只依据搜到的资料**，不要凭记忆编；资料不足就直说"资料不足"，不要凑；
4. 优先采信正规来源（官方文档 / 政府与高校网站 / 权威媒体 / 厂商官网），
   不要采用来路不明的聚合站、内容农场、论坛灌水；
5. 不要输出参考网址列表（那部分由界面自己列），也不要写"根据搜索结果"这种废话。

问题：%s"""


class SearchError(Exception):
    pass


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


def domain_of(url):
    try:
        h = urlparse(url).netloc
        return re.sub(r"^www\.", "", h)
    except Exception:
        return ""


# 来源分档（主人要求「只搜索正规渠道的内容」）：
#   0 = 权威（官方文档 / 政府 / 高校 / 百科 / 大厂官网 / 代码托管）
#   1 = 社区（问答、技术博客平台 —— 能参考，但不算权威）
#   2 = 其他（聚合站、内容农场、来路不明的站 —— 排最后）
TIER_OFFICIAL, TIER_COMMUNITY, TIER_OTHER = 0, 1, 2

_OFFICIAL_DOMAIN = (
    "deepseek.com", "volcengine.com", "aliyun.com", "tencent.com", "huawei.com",
    "microsoft.com", "openai.com", "anthropic.com", "google.com", "github.com",
    "gitlab.com", "python.org", "nodejs.org", "nvidia.com", "intel.com", "amd.com",
    "apple.com", "baidu.com", "bytedance.com", "moonshot.cn", "zhipuai.cn",
    "modelscope.cn", "pytorch.org", "comfy.org",
)
_COMMUNITY_DOMAIN = (
    "zhihu.com", "csdn.net", "juejin.cn", "cnblogs.com", "stackoverflow.com",
    "reddit.com", "medium.com", "segmentfault.com", "v2ex.com", "infoq.cn",
    "51cto.com", "oschina.net", "bilibili.com", "weibo.com", "jianshu.com",
)


def tier_of(site):
    s = (site or "").lower().strip()
    if not s:
        return TIER_OTHER
    if s.endswith((".gov", ".gov.cn", ".edu", ".edu.cn", ".ac.cn", ".mil")):
        return TIER_OFFICIAL
    if s.endswith(".org") or "wikipedia" in s:
        return TIER_OFFICIAL
    if s.startswith(("docs.", "developer.", "api-docs.", "api.", "platform.",
                     "console.", "help.", "support.")):
        return TIER_OFFICIAL
    for k in _OFFICIAL_DOMAIN:
        if s == k or s.endswith("." + k):
            return TIER_OFFICIAL
    for k in _COMMUNITY_DOMAIN:
        if s == k or s.endswith("." + k):
            return TIER_COMMUNITY
    return TIER_OTHER


def new_job(kind):
    jid = uuid.uuid4().hex[:12]
    with JOB_LOCK:
        JOBS[jid] = {"kind": kind, "state": "running", "t0": time.time(),
                     "stage": "准备中", "percent": 0, "detail": "",
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


# --------------------------------------------------------------------------
def key_state(cfg):
    k = (cfg.get("dsKey") or "").strip()
    return {"hasKey": bool(k), "keyMask": mask_key(k),
            "base": (cfg.get("dsSearchBase") or "").strip() or BASE_DEFAULT,
            "model": (cfg.get("dsSearchModel") or "").strip() or MODEL_DEFAULT,
            "maxUses": MAX_USES}


def mask_key(k):
    k = (k or "").strip()
    if not k:
        return ""
    if len(k) <= 10:
        return k[:2] + "***"
    return k[:6] + "…" + k[-4:]


def search(cfg, query, max_uses=None, timeout=300):
    """一次搜索：把 DeepSeek 的 Anthropic 兼容接口调起来，返回答案 + 来源"""
    key = (cfg.get("dsKey") or "").strip()
    if not key:
        raise SearchError("还没填 DeepSeek API Key —— 去「⚙️ 设置」里填一把，"
                          "在 platform.deepseek.com 生成")
    base = ((cfg.get("dsSearchBase") or "").strip() or BASE_DEFAULT).rstrip("/")
    model = (cfg.get("dsSearchModel") or "").strip() or MODEL_DEFAULT
    uses = int(max_uses or MAX_USES)

    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user",
                      "content": [{"type": "text", "text": PROMPT % query}]}],
        "tools": [{"type": "web_search_20250305", "name": "web_search",
                   "max_uses": uses}],
    }
    req = urllib.request.Request(
        base + "/messages",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "authorization": "Bearer " + key,
                 "anthropic-version": API_VERSION})

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        if e.code == 401:
            raise SearchError("Key 被拒了（401）—— 检查设置里的 DeepSeek API Key 是否有效。%s" % detail)
        if e.code == 429:
            raise SearchError("被限速了（429）—— 等一会儿再搜。%s" % detail)
        raise SearchError("接口返回 %s：%s" % (e.code, detail))
    except Exception as e:
        raise SearchError("请求失败：%s" % e)

    out = parse_response(resp, query)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def parse_response(resp, query):
    """从返回里取：答案文本 + 来源（只认结构化块）"""
    if resp.get("type") == "error" or resp.get("error"):
        err = resp.get("error") or {}
        raise SearchError("接口报错：%s" % (err.get("message") or json.dumps(
            err, ensure_ascii=False)[:300]))

    blocks = resp.get("content") or []

    # 1) 文本块 → 答案；顺手收 citations 里的摘录（按 URL 归位）
    texts, cite_map = [], {}
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "text":
            continue
        texts.append(b.get("text") or "")
        for c in (b.get("citations") or []):
            if not isinstance(c, dict):
                continue
            u = (c.get("url") or "").strip()
            t = (c.get("cited_text") or "").strip()
            if u and t and u not in cite_map:
                cite_map[u] = t

    # 2) 结构化搜索结果块 → 来源
    #    同一个站最多留 2 条（免得一个站刷屏）；每条打上权威等级
    sources, seen, searched, per_site = [], set(), 0, {}
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "web_search_tool_result":
            continue
        inner = b.get("content")
        if isinstance(inner, dict):            # 出错时 content 可能是个 error 对象
            continue
        for item in (inner or []):
            if not isinstance(item, dict):
                continue
            searched += 1
            if item.get("type") != "web_search_result":
                continue
            u = (item.get("url") or "").strip()
            if not u or u in seen:
                continue
            site = domain_of(u)
            if per_site.get(site, 0) >= 2:
                continue
            per_site[site] = per_site.get(site, 0) + 1
            seen.add(u)
            sources.append({
                "n": len(sources) + 1,
                "url": u,
                "title": (item.get("title") or "").strip() or u,
                "site": site,
                "date": (item.get("page_age") or "").strip(),
                "tier": tier_of(site),
                "snippet": re.sub(r"\s+", " ", cite_map.get(u, ""))[:400],
            })

    # 权威来源排前面（同档保持搜索原本的相关度顺序），排完重新编号
    sources.sort(key=lambda s: s["tier"])
    for i, s in enumerate(sources, 1):
        s["n"] = i
    tier_count = {0: 0, 1: 0, 2: 0}
    for s in sources:
        tier_count[s["tier"]] = tier_count.get(s["tier"], 0) + 1

    answer = "\n".join(t for t in texts if t).strip()
    # 把答案里可能带出来的参考列表去掉（主人要求参考网址单独列在最后，由界面渲染）
    answer = re.sub(r"\n*\s*(参考(资料|来源|网址)|References?)\s*[:：].*$", "",
                    answer, flags=re.S | re.I).strip()

    usage = resp.get("usage") or {}
    return {
        "ok": True, "query": query,
        "answer": answer,
        "points": split_points(answer),
        "sources": sources,
        "sourceCount": len(sources),
        "tierCount": {"official": tier_count.get(0, 0),
                      "community": tier_count.get(1, 0),
                      "other": tier_count.get(2, 0)},
        "searched": searched > 0,
        "model": resp.get("model") or "",
        "usage": {"in": usage.get("input_tokens"), "out": usage.get("output_tokens")},
    }


def split_points(answer):
    """把答案切成「点」，界面按点渲染

    踩过的坑：模型爱先甩一个 Markdown 标题（`**DeepSeek API 当前模型与价格**`），
    第一版把它切成了一个光秃秃的「点」✗ —— 这里把这种短标题并到**下一点**前面 ✓
    """
    if not answer:
        return []
    lines = [l.rstrip() for l in answer.splitlines()]
    pts, cur = [], []
    pat = re.compile(r"^\s*(?:[-*•]|\d+[.、)]|第[一二三四五六七八九十]+[、.])\s*")
    for l in lines:
        if pat.match(l) or (l.strip() and not cur and not l.strip().endswith("：")):
            if cur:
                pts.append("\n".join(cur).strip())
                cur = []
            cur.append(pat.sub("", l).strip())
        else:
            if l.strip():
                cur.append(l.strip())
    if cur:
        pts.append("\n".join(cur).strip())

    out, pending = [], []
    for p in pts:
        p = p.strip()
        if not p:
            continue
        bare = p.strip("*# 　").strip()
        is_title = (p.startswith(("**", "#")) or bare.endswith(("：", ":"))) and len(bare) <= 30
        if is_title:
            pending.append(p)
            continue
        if pending:
            p = "\n".join(pending) + "\n" + p
            pending = []
        out.append(p)
    if pending:
        out.append("\n".join(pending))
    return out[:12]


def save_history(query, res):
    """落一份历史，方便以后回看（只留最近 60 份）"""
    try:
        os.makedirs(HIST_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "".join(c for c in query if c not in '\\/:*?"<>|').strip()[:30] or "搜索"
        path = os.path.join(HIST_DIR, "%s_%s.json" % (stamp, name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"query": query, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "result": res}, f, ensure_ascii=False, indent=1)
        files = sorted(f for f in os.listdir(HIST_DIR) if f.endswith(".json"))
        for old in files[:-60]:
            try:
                os.remove(os.path.join(HIST_DIR, old))
            except Exception:
                pass
        return path
    except Exception:
        return ""


def search_worker(jid, cfg, query):
    try:
        job_set(jid, stage="联网搜索中", percent=10,
                detail="一次搜索要消耗一个完整模型轮次，大约 %d 秒" % int(EST_SECONDS))
        job_log(jid, "问题：%s" % query)
        job_log(jid, "端点：%s" % ((cfg.get("dsSearchBase") or "").strip() or BASE_DEFAULT))
        job_log(jid, "模型：%s　最多搜 %d 次" % ((cfg.get("dsSearchModel") or "").strip()
                                              or MODEL_DEFAULT, MAX_USES))

        t0 = time.time()
        res = search(cfg, query)
        el = time.time() - t0

        job_set(jid, stage="整理来源", percent=92)
        job_log(jid, "✓ 拿到 %d 个来源（用时 %d 秒，输入 %s / 输出 %s token）"
                % (res["sourceCount"], el, (res.get("usage") or {}).get("in"),
                   (res.get("usage") or {}).get("out")))
        if not res["searched"]:
            job_log(jid, "⚠️ 这次没拿到结构化搜索来源 —— 可能是问题不需要联网，"
                         "也可能是模型没触发搜索。答案照样给，但来源是空的。")
        res["history"] = save_history(query, res)
        job_set(jid, state="done", percent=100, stage="完成", result=res)
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))
