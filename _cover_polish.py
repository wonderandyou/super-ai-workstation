# -*- coding: utf-8 -*-
"""
翻唱后处理 · 去爆破音 + 柔化齿音
=====================================================================
★ 用 ComfyUI 那套 Python 跑（有 numpy / soundfile / scipy）：
    D:\\ComfyUI\\python_embeded\\python.exe _cover_polish.py <入.wav> <出.wav> [强度0~1]

为什么需要它：Seed-VC 换完音色后**没有任何后处理** ✗
  · 爆破音（p/t/k）本质是**低频气流冲击** —— 换声模型会把它放大 ✓
  · 换声还会带来齿音/嘶声（人声 6~8kHz 本来就容易有凸起）✓

处方（每一条都只动该动的）：
  ① 70Hz 高通           —— 砍掉爆破的低频冲击（最有效的一条）✓
  ② 6kHz 以上柔化        —— 压齿音，和朗读用的是同一套曲线 ✓
  ③ 4~5.5kHz 轻收        —— 防"嘶" ✓
  ④ 真峰值限幅           —— 防削顶（FLAC/WAV 定点超了就破音）✓

强度 0 = 不处理，1 = 全量。默认 0.6（温和）。
"""
import os
import sys

import numpy as np
import soundfile as sf


def _hp70(x, rate, k):
    """① 一阶高通，砍 70Hz 以下（爆破音的气流冲击在这）"""
    if k <= 0:
        return x
    fc = 70.0
    a = np.exp(-2.0 * np.pi * fc / rate)
    y = np.empty_like(x)
    prev_x = 0.0
    prev_y = 0.0
    for i in range(len(x)):
        v = a * (prev_y + x[i] - prev_x)
        y[i] = v
        prev_x = x[i]
        prev_y = v
    return x + (y - x) * k          # 按强度在"原信号 / 高通后"之间插值


def _hp70_fast(x, rate, k):
    """上面那个逐样本循环太慢，用 scipy 的 SOS 高通（快而且稳）"""
    if k <= 0:
        return x
    try:
        from scipy.signal import butter, sosfilt
        sos = butter(2, 70.0 / (rate / 2.0), btype="highpass", output="sos")
        y = sosfilt(sos, x)
        return x + (y - x) * k
    except Exception:
        return _hp70(x, rate, k)


def _tame_hiss(x, rate, k):
    """②③ 压 6kHz 以上的齿音（FFT 域乘增益曲线）"""
    if k <= 0:
        return x
    n = len(x)
    if n < 256:
        return x
    fq = np.fft.rfftfreq(n, 1.0 / rate)
    safe = np.maximum(fq, 1.0)
    g = np.zeros_like(fq)
    g -= 7.0 * k / (1.0 + (6000.0 / safe) ** 6)
    g -= 4.0 * k / (1.0 + (8000.0 / safe) ** 6)
    g -= 2.0 * k * np.exp(-0.5 * ((np.log2(safe / 4700.0)) / 0.30) ** 2)
    spec = np.fft.rfft(x)
    return np.fft.irfft(spec * (10.0 ** (g / 20.0)), n=n)


def _limit(x, ceiling=0.99):
    """④ 真峰值限幅"""
    pk = float(np.abs(x).max())
    if pk > ceiling:
        x = x * (ceiling / pk)
    return x


def main():
    if len(sys.argv) < 3:
        print("用法：_cover_polish.py <入.wav> <出.wav> [强度0~1]")
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    k = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
    k = max(0.0, min(1.0, k))
    if not os.path.isfile(src):
        print("错误：找不到 %s" % src, flush=True)
        return 1

    x, rate = sf.read(src, dtype="float32", always_2d=True)
    n_ch = x.shape[1]
    print("STAGE:去爆破音（%d 声道 / %d Hz / %.1f 秒，强度 %.2f）"
          % (n_ch, rate, len(x) / float(rate), k), flush=True)

    out = np.empty_like(x)
    for c in range(n_ch):
        y = _hp70_fast(x[:, c], rate, k)
        y = _tame_hiss(y, rate, k)
        out[:, c] = _limit(y)

    before = float(np.abs(x).max())
    after = float(np.abs(out).max())
    # 低频能量对比（看爆破有没有降下来）
    def low_energy(a):
        fq = np.fft.rfftfreq(len(a), 1.0 / rate)
        sp = np.abs(np.fft.rfft(a))
        m = fq < 120.0
        return float(np.sqrt((sp[m] ** 2).mean())) if m.any() else 0.0
    e0 = low_energy(x[:, 0])
    e1 = low_energy(out[:, 0])

    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    sf.write(dst, out, rate)
    print("SKIP:峰值 %.3f → %.3f ｜ 120Hz 以下能量 %.0f → %.0f（降了 %.0f%%）"
          % (before, after, e0, e1, (1 - e1 / e0) * 100 if e0 else 0), flush=True)
    print("STAGE:后处理完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
