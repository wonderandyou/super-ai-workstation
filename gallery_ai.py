# -*- coding: utf-8 -*-
"""
作品库 —— 把各标签页的产出汇总起来，按来源分类
=====================================================================
为什么要它：以前「作品库」只读 `data/history.json`，那里面**只有豆包生图的记录** ✗
           其他标签页出的图（本地千问、溶图、抠图…）根本看不到。

现在做法：**直接扫各标签页的产出目录** ✓
   分类、目录、图标全在这一个文件里 —— 以后加了新标签页，只改下面的 CATS ✓

用法（app.py 里调）：
    gallery_ai.scan(cfg)                 # 全部作品
    gallery_ai.scan(cfg, cat="blend")    # 只看某一类
    gallery_ai.allowed_dirs(cfg)         # 给「读文件」接口用的白名单目录
"""
import os
import time

HOME = os.path.expanduser("~")
DESKTOP = os.path.join(HOME, "Desktop")

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
AUD_EXT = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac", ".opus")


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


def cat_dirs(cfg):
    """各分类 → 产出目录。★ 加新标签页时在这里加一条就行

    deep=True 表示产出是**按子目录分开放**的（抠图就是：抠图出片\\<任务号>\\xxx.png），
    扫的时候要多进一层，否则这一类永远是 0 条 ✗（2026-10-02 修）
    """
    cfg = cfg or {}
    return [
        {"key": "qwen", "name": "本地千问生图", "icon": "🐋",
         "dir": cfg.get("qwenOutDir") or os.path.join(DESKTOP, "千问1生图")},
        {"key": "doubao", "name": "豆包 Seedream", "icon": "🎨",
         "dir": cfg.get("outputDir") or os.path.join(DESKTOP, "豆包出图")},
        {"key": "blend", "name": "AI 溶图", "icon": "🎨",
         "dir": os.path.join(cfg.get("root") or r"D:\AI工作站", "data", "溶图出片")},
        {"key": "matting", "name": "AI 抠图", "icon": "✂️", "deep": True,
         "dir": os.path.join(cfg.get("root") or r"D:\AI工作站", "data", "抠图出片")},
        {"key": "music", "name": "AI 音乐工坊", "icon": "🎵",
         "dir": os.path.join(DESKTOP, "AI音乐工坊", "输出")},
        {"key": "stem", "name": "AI 人声分离", "icon": "🎚️",
         "dir": os.path.join(cfg.get("root") or r"D:\AI工作站", "data", "分离出片")},
        {"key": "cover", "name": "AI 翻唱", "icon": "🎤",
         "dir": os.path.join(cfg.get("root") or r"D:\AI工作站", "data", "翻唱出片")},
        {"key": "tts", "name": "声音包朗读", "icon": "📖",
         "dir": os.path.join(cfg.get("root") or r"D:\AI工作站", "data", "朗读出片")},
        # Live2D 制作（2026-10-02）：成品在 桌面\Live2D成品\<作品名>\ ，也是分目录的
        {"key": "live2d", "name": "Live2D 制作", "icon": "🎭", "deep": True,
         "dir": os.path.join(DESKTOP, "Live2D成品")},
    ]


def iter_files(d, deep=False):
    """产出目录里的文件。

    普通分类只扫顶层 ✓；deep=True 的分类再进一层子目录（子目录名当 sub 带出来，
    前端可以拿它做标题/去重）。返回 (文件名, 完整路径, 子目录名)。
    """
    try:
        names = os.listdir(d)
    except Exception:
        return
    for f in names:
        p = os.path.join(d, f)
        if os.path.isfile(p):
            yield f, p, ""
        elif deep and os.path.isdir(p):
            try:
                for g in os.listdir(p):
                    q = os.path.join(p, g)
                    if os.path.isfile(q):
                        yield g, q, f
            except Exception:
                continue


def allowed_dirs(cfg):
    """允许「读文件」接口访问的目录（浏览作品、试听音频都要用）✓"""
    out = []
    for c in cat_dirs(cfg):
        if c["dir"]:
            out.append(os.path.normpath(c["dir"]))
    for k in ("outputDir", "videoOutDir"):
        v = (cfg or {}).get(k)
        if v:
            out.append(os.path.normpath(v))
    return out


def _kind(ext):
    if ext in IMG_EXT:
        return "image"
    if ext in AUD_EXT:
        return "audio"
    return "other"


def scan(cfg, cat="all", limit=400):
    """扫各产出目录，返回分类清单 + 作品列表（按时间倒序）"""
    cats = cat_dirs(cfg)
    want = [c for c in cats if cat in ("all", "", None) or c["key"] == cat]

    items = []
    counts = {}
    for c in cats:                                   # 先统计每类有多少（分类按钮上要显示）
        counts[c["key"]] = 0
        if not c["dir"] or not os.path.isdir(c["dir"]):
            continue
        try:
            for _name, _p, _sub in iter_files(c["dir"], c.get("deep")):
                counts[c["key"]] += 1
        except Exception:
            pass

    for c in want:
        d = c["dir"]
        if not d or not os.path.isdir(d):
            continue
        for f, p, sub in iter_files(d, c.get("deep")):
            ext = os.path.splitext(f)[1].lower()
            try:
                st = os.stat(p)
            except OSError:
                continue
            items.append({
                "cat": c["key"], "catName": c["name"], "icon": c["icon"],
                "file": p, "name": f, "sub": sub, "catDir": d,
                "size": st.st_size, "sizeText": human(st.st_size),
                "mtime": st.st_mtime,
                "time": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                "kind": _kind(ext),
            })

    items.sort(key=lambda x: x["mtime"], reverse=True)
    total = len(items)
    items = items[:limit]

    cat_list = [{"key": "all", "name": "全部", "icon": "🗂", "count": sum(counts.values())}]
    for c in cats:
        cat_list.append({"key": c["key"], "name": c["name"], "icon": c["icon"],
                         "count": counts.get(c["key"], 0)})

    return {
        "ok": True,
        "cats": cat_list,
        "items": items,
        "total": total,
        "shown": len(items),
        "imgCount": sum(1 for i in items if i["kind"] == "image"),
        "audCount": sum(1 for i in items if i["kind"] == "audio"),
        "curCat": cat if cat else "all",
    }
