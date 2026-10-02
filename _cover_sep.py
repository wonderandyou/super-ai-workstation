# -*- coding: utf-8 -*-
"""
翻唱 worker · 分离 / 混音
=====================================================================
★ 必须用 ComfyUI 那个解释器跑（Demucs 和 PyAV 装在那套里）：
    D:\\ComfyUI\\python_embeded\\python.exe _cover_sep.py ...

用法：
    _cover_sep.py separate <歌曲> <人声输出.wav> <伴奏输出.wav>
    _cover_sep.py mix <人声> <伴奏> <成品.flac> [人声增益] [伴奏增益]

进度用 `STAGE:xxx` 打给父进程，`SKIP:` 前缀的行父进程会忽略。
"""
import os
import sys

# 让 Demucs 走国内镜像下模型
# 铁律一：不设 HF_ENDPOINT 镜像 —— 模型只从官方源取（本机已有缓存，正常不触发下载）
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def stage(s):
    print("STAGE:" + s, flush=True)


def skip(s):
    print("SKIP:" + s, flush=True)


# --------------------------------------------------------------------------
#  分离
# --------------------------------------------------------------------------
def do_separate(src, vocals_out, inst_out):
    import torch
    from demucs.api import Separator, save_audio

    stage("加载 Demucs 模型")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    skip("Demucs 设备 %s" % dev)

    sep = Separator(model="htdemucs", device=dev, progress=True)
    stage("正在分离人声 / 伴奏（整首跑一遍，几分钟）")
    origin, stems = sep.separate_audio_file(src)

    if "vocals" not in stems:
        print("错误：Demucs 没给出 vocals 轨，实际有 %s" % list(stems.keys()), flush=True)
        return 1

    for d in (os.path.dirname(vocals_out), os.path.dirname(inst_out)):
        if d:
            os.makedirs(d, exist_ok=True)

    stage("导出人声")
    save_audio(stems["vocals"], vocals_out, samplerate=sep.samplerate)

    # 伴奏 = 其余所有轨相加（drums + bass + other）
    stage("导出伴奏")
    import torch as _t
    acc = None
    for k, v in stems.items():
        if k == "vocals":
            continue
        acc = v if acc is None else acc + v
    if acc is None:
        # 万一没有其他轨，就做一个等长的静音
        acc = _t.zeros_like(stems["vocals"])
    save_audio(acc, inst_out, samplerate=sep.samplerate)

    skip("人声 %s" % vocals_out)
    skip("伴奏 %s" % inst_out)
    stage("分离完成")
    return 0


# --------------------------------------------------------------------------
#  混音
# --------------------------------------------------------------------------
def _read(path):
    """读成 (float32 [ch, N], 采样率)"""
    import av
    import numpy as np

    with av.open(path) as c:
        st = c.streams.audio[0]
        rate = st.codec_context.sample_rate
        chunks = []
        for fr in c.decode(st):
            a = fr.to_ndarray()
            ch = len(fr.layout.channels)
            if a.ndim == 1:
                a = a.reshape(1, -1)
            # ★ 打包格式下 to_ndarray 给的是 (1, N*声道)，必须按布局 reshape
            if a.shape[0] == 1 and ch > 1:
                a = a.reshape(ch, -1)
            chunks.append(a)
    if not chunks:
        raise RuntimeError("读不到音频：%s" % path)
    data = np.concatenate(chunks, axis=1).astype(np.float32) / 32768.0
    return data, rate


def _write_flac(path, data, rate):
    import av
    import numpy as np

    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767.0).astype(np.int16)
    ch = data.shape[0]
    layout = "mono" if ch == 1 else ("stereo" if ch == 2 else None)
    if layout is None:
        print("错误：不支持 %d 声道" % ch, flush=True)
        return False
    ext = os.path.splitext(path)[1].lower()
    fmt = "flac" if ext == ".flac" else "wav"
    codec = "flac" if ext == ".flac" else "pcm_s16le"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with av.open(path, mode="w", format=fmt) as oc:
        st = oc.add_stream(codec, rate=rate)
        st.layout = layout
        fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm), format="s16p",
                                        layout=layout)
        fr.sample_rate = rate
        fr.pts = 0
        for pkt in st.encode(fr):
            oc.mux(pkt)
    return True


def do_mix(vocal_path, inst_path, out_path, vg=1.0, ig=0.9):
    import numpy as np

    stage("读入转换后的人声和伴奏")
    v, vr = _read(vocal_path)
    i, ir = _read(inst_path)

    # 采样率不一致就按最近邻重采样（正常情况两边都是 44100，不会走到这）
    if vr != ir:
        skip("采样率不同 %d vs %d，按比例重采样" % (vr, ir))
        ratio = ir / float(vr)
        idx = (np.arange(int(v.shape[1] * ratio)) / ratio).astype(np.int64)
        idx = np.clip(idx, 0, v.shape[1] - 1)
        v = v[:, idx]
        vr = ir

    # 声道数对齐
    ch = max(v.shape[0], i.shape[0])
    if v.shape[0] == 1 and ch > 1:
        v = np.repeat(v, ch, axis=0)
    if i.shape[0] == 1 and ch > 1:
        i = np.repeat(i, ch, axis=0)

    # 长度对齐（取短的，避免尾部多出一截）
    n = min(v.shape[1], i.shape[1])
    if abs(v.shape[1] - i.shape[1]) > vr * 0.05:
        skip("长度差 %.2f 秒，按短的截齐" % (abs(v.shape[1] - i.shape[1]) / vr))
    v = v[:, :n]
    i = i[:, :n]

    stage("混合")
    mix = v * float(vg) + i * float(ig)

    # 防削波
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.98:
        mix = mix * (0.98 / peak)
        skip("峰值 %.2f，压了 %.2f 倍避免削波" % (peak, 0.98 / peak))

    stage("写入成品")
    if not _write_flac(out_path, mix, vr):
        return 1
    skip("成品 %s  %.2f MB  %.2f 秒" % (out_path, os.path.getsize(out_path) / 2 ** 20,
                                        n / float(vr)))
    stage("混音完成")
    return 0


# --------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("用法: _cover_sep.py separate|mix ...", flush=True)
        return 2
    mode = sys.argv[1]
    if mode == "separate":
        if len(sys.argv) < 5:
            print("用法: separate <歌曲> <人声> <伴奏>", flush=True)
            return 2
        return do_separate(sys.argv[2], sys.argv[3], sys.argv[4])
    if mode == "mix":
        if len(sys.argv) < 5:
            print("用法: mix <人声> <伴奏> <成品> [人声增益] [伴奏增益]", flush=True)
            return 2
        vg = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
        ig = float(sys.argv[6]) if len(sys.argv) > 6 else 0.9
        return do_mix(sys.argv[2], sys.argv[3], sys.argv[4], vg, ig)
    print("未知模式：%s" % mode, flush=True)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("错误：%s" % e, flush=True)
        sys.exit(1)
