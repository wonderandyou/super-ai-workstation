# -*- coding: utf-8 -*-
"""
声音包朗读 —— F5-TTS 侧的 worker
=====================================================================
这个文件**必须用 F5-TTS 自己的解释器跑**：
    D:\\F5TTS\\venv\\Scripts\\python.exe

命令行：
    python _tts_worker.py --ref <声音包.wav> --out <输出.wav> --text <要念的字>
                          [--ref-text <参考音频里念的是什么>]

进度用 `STAGE:xxx` 一行行打给父进程，父进程解析后更新界面进度条。
后面的说明性输出加 `SKIP:` 前缀，父进程会忽略。

★ 关键：必须先 import f5patch
   它解决三件事（详见 D:\\F5TTS\\f5patch.py 注释）：
     1. safetensors 读不了这个权重文件 → 改用 numpy 自解析
     2. vocos 声码器不再联网抓，用本地已校验副本
     3. torchaudio.load 兜底到 soundfile（新版 torchaudio 走 torchcodec，DLL 不匹配）
"""
# ★ 朗读需要 ffmpeg **命令行**：F5-TTS 用 transformers 的 ASR 管线转录参考音频，
#   它内部硬调命令名 "ffmpeg"（transformers/pipelines/audio_utils.py:ffmpeg_read）✗
#   而这台机器上原本没装 ffmpeg ✗
#
#   用 PyPI 官方的 imageio-ffmpeg 自带的二进制（正规渠道，不碰任何第三方下载站）✓
#   但它文件名是 `ffmpeg-win-x86_64-v7.1.exe` ✗ 名字对不上，
#   光加目录到 PATH 没用 → **复制一个叫 ffmpeg.exe 的副本**再进 PATH ✓
#   （踩过：只加 PATH 不改名，transformers 照样报 "ffmpeg was not found" ✗）
import os as _os
import shutil as _sh
try:
    import imageio_ffmpeg as _iff
    _ff = _iff.get_ffmpeg_exe()
    if _ff and _os.path.isfile(_ff):
        _os.environ["IMAGEIO_FFMPEG_EXE"] = _ff
        _d = _os.path.join(_os.environ.get("TEMP", "."), "aiws_ffmpeg")
        _os.makedirs(_d, exist_ok=True)
        _dst = _os.path.join(_d, "ffmpeg.exe")
        try:
            if (not _os.path.isfile(_dst)
                    or _os.path.getsize(_dst) != _os.path.getsize(_ff)):
                _sh.copy2(_ff, _dst)
        except Exception:
            pass
        _use = _d if _os.path.isfile(_dst) else _os.path.dirname(_ff)
        _os.environ["PATH"] = _use + _os.pathsep + _os.environ.get("PATH", "")
        print("[tts] ffmpeg 就位：%s" % _os.path.join(_use, "ffmpeg.exe"), flush=True)
    else:
        print("[tts] imageio-ffmpeg 里没找到 ffmpeg", flush=True)
except Exception as _e:
    print("[tts] 没配上 ffmpeg（%s）—— 转录参考音频可能会失败" % _e, flush=True)

import argparse
import os
import sys
import time

F5_DIR = r"D:\F5TTS"
F5_SRC = os.path.join(F5_DIR, "f5-tts", "src")
sys.path.insert(0, F5_DIR)
sys.path.insert(0, F5_SRC)

# 铁律一：不设 HF_ENDPOINT 镜像 —— 模型只从官方源取（本机已有缓存，正常不触发下载）
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

print("SKIP:路径 f5=%s" % F5_DIR, flush=True)


def stage(s):
    print("STAGE:" + s, flush=True)


def _tame_hiss(path, rate_hint=None):
    """压掉齿音/嘶声，让朗读不那么尖。

    依据实测频谱：F5-TTS 的输出在 6~8kHz 有个大凸起（比 4kHz 高 12dB），
    正常人声该比基频低 30~40dB，这里只低 20dB → 听着尖 ✓
    只压高频，**不动 250Hz~4kHz**（人声清晰度全靠这段）✓
    """
    import numpy as _n
    import soundfile as _sf
    x, sr = _sf.read(path, dtype="float32", always_2d=True)
    mono = x.mean(axis=1)
    n = len(mono)
    if n < 256:
        return False
    fq = _n.fft.rfftfreq(n, 1.0 / sr)
    safe = _n.maximum(fq, 1.0)
    g = _n.zeros_like(fq)
    g -= 7.0 / (1.0 + (6000.0 / safe) ** 6)          # ① 6k 以上 -7dB
    g -= 4.0 / (1.0 + (8000.0 / safe) ** 6)          # ② 8k 以上再 -4dB
    g -= 2.0 * _n.exp(-0.5 * ((_n.log2(safe / 4700.0)) / 0.30) ** 2)   # ③ 4~5.5k -2dB
    spec = _n.fft.rfft(mono)
    y = _n.fft.irfft(spec * (10.0 ** (g / 20.0)), n=n)
    pk = float(_n.abs(y).max())
    if pk > 0.99:
        y = y * (0.99 / pk)
    # 单声道写回（F5-TTS 本来就是单声道；多声道时各声道用同一处理结果更稳）
    out = _n.repeat(y[:, None], x.shape[1], axis=1) if x.shape[1] > 1 else y
    _sf.write(path, out, sr)
    return True


def _trim_edges(path, thr=0.001, keep=0.02):
    """温和裁掉首尾的**真静音**。

    ★ 为什么不用 F5-TTS 自带的 remove_silence ✗
      它按「相对峰值」的能量阈值裁 →「你好」的「你」是轻声起头，会被误裁掉 ✗
      这里用**绝对阈值** 0.001 = -60dBFS：
        · 数字静音 RMS ≈ 0.00003（-90dBFS）→ 会被裁 ✓
        · 语音哪怕很轻也 > 0.01（-40dBFS）→ 绝不碰 ✓
    实测开头有 655ms 空白（占全长的 42%），这样一裁就干净了 ✓
    """
    import numpy as _n
    import soundfile as _sf
    x, sr = _sf.read(path, dtype="float32", always_2d=True)
    mono = _n.abs(x).max(axis=1)
    idx = _n.nonzero(mono > thr)[0]
    if idx.size == 0:
        return False
    a = max(0, int(idx[0]) - int(sr * keep))
    b = min(len(mono), int(idx[-1]) + 1 + int(sr * keep))
    if a == 0 and b == len(mono):
        return False
    y = x[a:b]
    _sf.write(path, y, sr)
    print("SKIP:两头收干净：去掉开头 %.0f ms、结尾 %.0f ms"
          % (1000.0 * a / sr, 1000.0 * (len(mono) - b) / sr), flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="参考音频（声音包）")
    ap.add_argument("--out", required=True, help="输出 wav")
    ap.add_argument("--text", required=True, help="要朗读的文字")
    ap.add_argument("--ref-text", default="", help="参考音频里念的内容（给了就跳过 Whisper）")
    a = ap.parse_args()

    if not os.path.isfile(a.ref):
        print("错误：找不到参考音频 %s" % a.ref, flush=True)
        return 1

    stage("加载运行补丁")
    import f5patch                      # ★ 必须，否则起不来
    print("SKIP:f5patch 已加载", flush=True)

    stage("加载 F5-TTS 模型")
    t0 = time.time()
    from f5_tts.api import F5TTS
    t = F5TTS(device="cuda")
    print("SKIP:模型就绪 %.1f 秒" % (time.time() - t0), flush=True)

    stage("正在合成语音")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    # ★ remove_silence 必须显式关掉 ✗
    #   F5-TTS 的 infer 默认它 = True，而它是按**能量阈值**裁首尾的 ——
    #   「你好」的「你」是轻声起头、能量低 → 被当成静音切掉 ✗
    #   （主人念「你好，你没事吧」，输出的「你」就没了 ✓）
    #   默认关；想对比可设环境变量 AIWS_TTS_REMOVE_SILENCE=1 ✓
    _rm = (os.environ.get("AIWS_TTS_REMOVE_SILENCE") or "0").strip() == "1"
    t.infer(ref_file=a.ref,
            ref_text=a.ref_text,          # 空字符串 = 走 Whisper；给了就跳过
            gen_text=a.text,
            file_wave=a.out,
            remove_silence=_rm)

    if not os.path.isfile(a.out):
        print("错误：合成结束但没有输出文件", flush=True)
        return 1
    # ★ 两头收干净：只裁真静音（不碰轻声的字）✓
    try:
        _trim_edges(a.out)
    except Exception as _e:
        print("SKIP:收边跳过：%s" % _e, flush=True)

    # ★ 柔化：压掉 6~8kHz 的齿音/嘶声（实测这里比 4kHz 高 12dB，听着尖）✓
    try:
        if _tame_hiss(a.out):
            print("SKIP:已柔化（压掉 6k 以上齿音）", flush=True)
    except Exception as _e:
        print("SKIP:柔化跳过：%s" % _e, flush=True)

    print("SKIP:输出 %.1f KB" % (os.path.getsize(a.out) / 1024.0), flush=True)
    stage("完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("错误：%s" % e, flush=True)
        sys.exit(1)
