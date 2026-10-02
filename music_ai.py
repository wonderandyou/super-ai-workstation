# -*- coding: utf-8 -*-
"""
AI 音乐工坊 —— 原生桥接到工作站
=====================================================================
策略：**不重写、直接复用**桌面的音乐工坊（`<用户目录>\\Desktop\\AI音乐工坊\\app.py`）。

为什么这么做：
    那边的 49 KB 里已经把很多硬骨头啃完了 —— 分块解码、歌词内嵌进 FLAC 的
    Vorbis Comment、Demucs 声音包制作、AI 写词、分段拼接兜底…重写一遍只会
    引入新 bug。它的 HTTP 服务器在 `main()` 里且有 `if __name__` 保护，
    所以**可以被安全 import**。

怎么复用：
    用 importlib 按文件路径加载，模块名取 `aiws_music` ——
    不能叫 `app`，否则和工作站自己的 `app` 撞名。
    它模块级用 `__file__` 算自己的 ROOT，所以相对路径都对着它自己的目录，没问题。

工作站这边只做三件事：
    1. 把它的 JOBS / 函数暴露成工作站风格的接口
    2. 按需启动 ComfyUI（音乐生成必须它）
    3. 顺手管一下「AI 音乐工坊」那个独立服务（网页版还能单独用）
"""

import importlib.util
import os
import sys
import threading
import time

MUSIC_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "AI音乐工坊")
MUSIC_APP = os.path.join(MUSIC_DIR, "app.py")
INNER_NAME = "aiws_music"

_mod = None
_load_err = ""
_lock = threading.Lock()


def _load():
    """把音乐工坊的 app.py 当模块加载（只加载一次）"""
    global _mod, _load_err
    if _mod is not None:
        return _mod
    with _lock:
        if _mod is not None:
            return _mod
        if not os.path.isfile(MUSIC_APP):
            _load_err = "找不到音乐工坊：%s" % MUSIC_APP
            return None
        try:
            if MUSIC_DIR not in sys.path:
                sys.path.insert(0, MUSIC_DIR)
            spec = importlib.util.spec_from_file_location(INNER_NAME, MUSIC_APP)
            m = importlib.util.module_from_spec(spec)
            sys.modules[INNER_NAME] = m
            spec.loader.exec_module(m)
            _mod = m
            _load_err = ""
        except Exception as e:
            _load_err = str(e)
            _mod = None
    return _mod


# 模块加载时就试一次（失败也不抛，让 status() 去报）
_load()


# --------------------------------------------------------------------------
#  状态
# --------------------------------------------------------------------------
def status():
    _tune_conf()
    m = _load()
    if m is None:
        return {"ok": False, "error": _load_err}
    try:
        voices = []
        try:
            os.makedirs(m.REF_DIR, exist_ok=True)
            for f in sorted(os.listdir(m.REF_DIR)):
                fp = os.path.join(m.REF_DIR, f)
                if os.path.isfile(fp) and f.lower().endswith(m.REF_EXT):
                    st = os.stat(fp)
                    voices.append({"name": f, "sizeText": m.human(st.st_size)})
        except Exception:
            pass

        outs = []
        try:
            os.makedirs(m.OUT, exist_ok=True)
            for f in sorted(os.listdir(m.OUT), reverse=True)[:60]:
                fp = os.path.join(m.OUT, f)
                if os.path.isfile(fp) and f.lower().endswith((".flac", ".wav", ".mp3")):
                    st = os.stat(fp)
                    outs.append({"name": f, "sizeText": m.human(st.st_size),
                                 "time": time.strftime("%m-%d %H:%M",
                                                       time.localtime(st.st_mtime))})
        except Exception:
            pass

        gpu = {}
        try:
            gpu = m.gpu_info() or {}
        except Exception:
            pass

        return {
            "ok": True,
            "musicDir": MUSIC_DIR,
            "comfyAlive": bool(m.comfy_alive()),
            "modelOk": os.path.isfile(m.MODEL_PATH),
            "modelSizeText": m.human(os.path.getsize(m.MODEL_PATH))
                             if os.path.isfile(m.MODEL_PATH) else "-",
            "voices": voices,
            "outputs": outs,
            "outDir": m.OUT,
            "refDir": m.REF_DIR,
            "srcDir": m.SRC_DIR,
            "gpu": gpu,
            "conf": {
                "shift": m.load_conf().get("shift"),
                "steps": m.load_conf().get("steps"),
                "cfg": m.load_conf().get("cfg"),
                "hasKey": bool((m.load_conf().get("llmKey") or "").strip()),
            },
            "languages": m.LANGUAGES,
            "keyscales": m.KEYSCALES,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ensure_comfy():
    """音乐生成必须 ComfyUI 在跑；不在就拉起来（按需）"""
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    if m.comfy_alive():
        return True
    m.start_comfy(None)
    ok = m.wait_comfy(300)
    if not ok:
        raise RuntimeError("ComfyUI 起来了但接口没就绪")
    return True


# --------------------------------------------------------------------------
#  任务（直接复用音乐工坊自己的 JOBS 和 worker）
# --------------------------------------------------------------------------
def new_job(kind):
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    return m.new_job(kind)


def get_job(jid):
    m = _load()
    if m is None:
        return None
    j = m.JOBS.get(jid)
    if not j:
        return None
    d = dict(j)
    d["elapsed"] = round(time.time() - j["t0"], 1)
    return d


def _tune_conf():
    """生成前的配置体检 —— 长音频特别吃采样步数。

    实测：120 秒的歌在 steps=8 时后半段会退化成一堆静音 ✗
          提到 16 之后只剩尾部那点自然留白 ✓（100 秒本来就没事）
    老师那边新装的音乐工坊默认是 8，所以这里自动补齐 ✓
    """
    try:
        m = _load()
        if m is None:
            return
        c = m.load_conf()
        changed = []
        if int(c.get("steps") or 8) < 16:
            c["steps"] = 16
            changed.append("steps 8→16")
        if changed:
            m.save_conf(c)
            print("[music_ai] 配置体检：%s（长音频需要更多采样步数）" % "、".join(changed))
    except Exception as e:
        print("[music_ai] 配置体检跳过：%s" % e)


def start_generate(params, want_comfy=True):
    """开始写歌。params 和音乐工坊 /api/generate 一致。"""
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    if not os.path.isfile(m.MODEL_PATH):
        raise RuntimeError("ACE-Step 模型不在：%s" % m.MODEL_PATH)
    jid = m.new_job("music")
    if want_comfy:
        def run():
            try:
                m.jset(jid, stage="启动 ComfyUI", percent=2)
                ensure_comfy()
            except Exception as e:
                m.jset(jid, state="error", error=str(e))
                m.jlog(jid, "启动引擎失败：" + str(e))
                return
            m.generate_worker(jid, params)
        threading.Thread(target=run, daemon=True).start()
    else:
        threading.Thread(target=m.generate_worker, args=(jid, params), daemon=True).start()
    return jid


def start_voicepack(params):
    """开始做声音包（Demucs 剥人声 → 截取 → 归一化 → 导出）"""
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    jid = m.new_job("voicepack")
    threading.Thread(target=m.voicepack_worker, args=(jid, params), daemon=True).start()
    return jid


def ai_write(prompt, conf_extra=None):
    """AI 写词（走 OpenAI 兼容接口，Key 存在音乐工坊自己的 config 里）"""
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    conf = m.load_conf()
    if conf_extra:
        conf.update(conf_extra)
    # ★ 配置里存的是 llmKey（音乐工坊的 call_llm 也读这个）
    #   以前这里写成 apikey，字段对不上 → 一调就报「还没填 API Key」✗
    #   工作站那边会把「⚙️ 设置」里的 DeepSeek Key 通过 conf_extra 传进来 ✓
    if not (conf.get("llmKey") or "").strip():
        raise RuntimeError("还没填 DeepSeek 的 API Key —— 去「⚙️ 设置」里填一下就行")
    return m.call_llm(conf, prompt)


def list_outputs():
    m = _load()
    if m is None:
        return []
    items = []
    try:
        os.makedirs(m.OUT, exist_ok=True)
        for f in sorted(os.listdir(m.OUT), reverse=True)[:120]:
            fp = os.path.join(m.OUT, f)
            if os.path.isfile(fp) and f.lower().endswith((".flac", ".wav", ".mp3")):
                st = os.stat(fp)
                items.append({"name": f, "sizeText": m.human(st.st_size),
                              "time": time.strftime("%m-%d %H:%M",
                                                    time.localtime(st.st_mtime))})
    except Exception:
        pass
    return items


def out_path(name):
    m = _load()
    if m is None:
        return ""
    n = os.path.basename(name or "")
    p = os.path.join(m.OUT, n)
    return p if (n and os.path.isfile(p)) else ""


def lrc_for(name):
    """同名 .lrc 的路径（有就返回）"""
    p = out_path(name)
    if not p:
        return ""
    l = os.path.splitext(p)[0] + ".lrc"
    return l if os.path.isfile(l) else ""


def read_lrc(name):
    p = lrc_for(name)
    if not p:
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def save_conf(patch):
    m = _load()
    if m is None:
        raise RuntimeError(_load_err)
    c = m.load_conf()
    c.update(patch or {})
    m.save_conf(c)
    return True
