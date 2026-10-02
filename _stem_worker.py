# -*- coding: utf-8 -*-
"""
人声分离 worker —— ★ 必须用 ComfyUI 那套 Python 跑（Demucs 装在那里面）：

    D:\\ComfyUI\\python_embeded\\python.exe _stem_worker.py <输入> <输出目录> <two|four> <wav|flac|mp3>

two  = 出两个文件：vocals（人声）+ instrumental（鼓+贝斯+其他）
four = 出四个文件：vocals / drums / bass / other

进度用 `STAGE:` 打给父进程；`SKIP:` 前缀的行父进程会忽略。

★ 铁律一：**不设 HF_ENDPOINT 镜像**。模型本机已有缓存
   （`~/.cache/torch/hub/checkpoints/955717e8-8726e21a.th`）；
   万一要重新下，也只走官方源，下不动就如实报错 —— 不偷偷换个人镜像 ✗
"""
import os
import sys


def stage(s):
    print("STAGE:" + s, flush=True)


def skip(s):
    print("SKIP:" + s, flush=True)


def export(tensor, path, samplerate, fmt):
    """导出音频：**首选用 soundfile（libsndfile）** —— 它写的头是对的（时长 / 总样本数都正确）✓

    这里踩过两个坑，都实测过：
      1. Demucs 自带的 `save_audio` 写 flac / mp3 要系统装 ffmpeg **命令行** ✗
         （报 `Saving as .flac requires ffmpeg to be installed.`）
      2. PyAV 单帧写 flac **不写总样本数** → 播放器读出来时长 0 秒 ✗（试过补 duration 也没用）
    所以：soundfile 优先，失败再退回 PyAV（至少文件能播）。
    """
    import numpy as np

    data = tensor.detach().cpu().numpy()
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[0] > 2:                  # 保险：超过两声道取前两路
        data = data[:2]
    arr = np.ascontiguousarray(np.clip(data, -1.0, 1.0).T.astype("float32"))   # (N, 声道)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    fmt_container = {"wav": "WAV", "flac": "FLAC", "mp3": "MP3"}.get(fmt, "WAV")
    fmt_sub = {"wav": "PCM_16", "flac": "PCM_16", "mp3": "MPEG_LAYER_III"}.get(fmt, "PCM_16")
    try:
        import soundfile as sf
        sf.write(path, arr, int(samplerate), format=fmt_container, subtype=fmt_sub)
        return
    except Exception as e:
        skip("soundfile 导出失败（%s），退回 PyAV" % str(e)[:80])

    # ---- 退路：PyAV（自带编解码器，不需要外部 ffmpeg）----
    import av
    pcm = (arr.T * 32767.0).astype(np.int16)      # (声道, N)
    ch = pcm.shape[0]
    layout = "mono" if ch == 1 else "stereo"
    if fmt == "mp3":
        codec, container = "libmp3lame", "mp3"
    elif fmt == "flac":
        codec, container = "flac", "flac"
    else:
        codec, container = "pcm_s16le", "wav"
    with av.open(path, mode="w", format=container) as oc:
        st = oc.add_stream(codec, rate=int(samplerate))
        st.layout = layout
        fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm),
                                        format="s16p", layout=layout)
        fr.sample_rate = int(samplerate)
        fr.pts = 0
        for pkt in st.encode(fr):
            oc.mux(pkt)


def main():
    if len(sys.argv) < 5:
        print("用法: _stem_worker.py <输入> <输出目录> <two|four> <wav|flac|mp3>", flush=True)
        return 2

    src, outdir = sys.argv[1], sys.argv[2]
    mode = sys.argv[3].lower()
    fmt = sys.argv[4].lower()
    if mode not in ("two", "four"):
        mode = "two"
    if fmt not in ("wav", "flac", "mp3"):
        fmt = "wav"
    if not os.path.isfile(src):
        print("错误：找不到文件 %s" % src, flush=True)
        return 1
    os.makedirs(outdir, exist_ok=True)

    import torch
    from demucs.api import Separator

    stage("加载 Demucs 模型（htdemucs）")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    skip("设备 %s" % dev)
    sep = Separator(model="htdemucs", device=dev, progress=True)

    stage("正在分离（整首跑一遍，几分钟）")
    _origin, stems = sep.separate_audio_file(src)
    skip("拿到的轨：%s" % list(stems.keys()))
    if "vocals" not in stems:
        print("错误：Demucs 没给出 vocals 轨，实际有 %s" % list(stems.keys()), flush=True)
        return 1

    if mode == "two":
        stage("导出人声")
        export(stems["vocals"], os.path.join(outdir, "vocals." + fmt), sep.samplerate, fmt)
        stage("导出伴奏（鼓 + 贝斯 + 其他）")
        acc = None
        for k, v in stems.items():
            if k == "vocals":
                continue
            acc = v if acc is None else acc + v
        if acc is None:
            acc = torch.zeros_like(stems["vocals"])
        export(acc, os.path.join(outdir, "instrumental." + fmt), sep.samplerate, fmt)
        stage("分离完成")
        return 0

    for key in ("vocals", "drums", "bass", "other"):
        if key in stems:
            stage("导出 %s" % key)
            export(stems[key], os.path.join(outdir, key + "." + fmt), sep.samplerate, fmt)
    stage("分离完成")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("错误：%s" % e, flush=True)
        sys.exit(1)
