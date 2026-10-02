# -*- coding: utf-8 -*-
"""
翻唱用声音包太长的处理 —— 临时裁到 8 秒再用
=====================================================================
★ 真凶（用数据查出来的）：
  Seed-VC 的 inference.py:274 / 362
      max_context_window = sr // hop_length * 30          # 30 秒窗口
      max_source_window  = max_context_window - mel2.size(2)
                                                    ↑ 减去【参考音频】长度
  → **声音包越长，每次能处理的源音频越短** ✗
  实测：273 秒的人声，转换输出只有 **136 秒**（正好一半）✗
      剩下的没被转换 → 混音后**一半还是原唱** ✓✓✓
      这就是主人听到的「选了男声却还有原唱女声」✓

  Seed-VC 官方推荐参考音频 **3~10 秒**；这台机器上的声音包是 40~50 秒 ✗

做法：不改编曲也不动制作器（老声音包也要能用），
      在**用之前**把声音包临时裁到 8 秒（取中间段，避开静音头尾）✓

★ 用 ComfyUI 那套 Python 跑（有 soundfile）：
    D:\\ComfyUI\\python_embeded\\python.exe _cover_slimref.py <原> <裁后> [秒数]

用法（人手动跑也方便）：python _cover_slimref.py <原.wav> <裁后.wav> [8]
"""
import os
import sys

MAX_SEC = 8.0


def main():
    if len(sys.argv) < 3:
        print("用法：_cover_slimref.py <原> <裁后> [秒数]")
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    want = float(sys.argv[3]) if len(sys.argv) > 3 else MAX_SEC
    if not os.path.isfile(src):
        print("错误：找不到 %s" % src, flush=True)
        return 1

    import numpy as np
    import soundfile as sf

    x, rate = sf.read(src, dtype="float32", always_2d=True)
    n = len(x)
    dur = n / float(rate)
    if dur <= want:
        print("SKIP:声音包 %.1f 秒，本来就够短，直接用 ✓" % dur, flush=True)
        return 2                     # 2 = 没动（调用方按原文件用）

    # 取中间那 want 秒 —— 避开开头的呼吸声和结尾的拖尾
    mid = n // 2
    half = int(want * rate / 2)
    a = max(0, mid - half)
    b = min(n, a + int(want * rate))
    seg = x[a:b]
    # 两头各 30ms 淡入淡出，免得裁出"咔"一声
    f = int(rate * 0.03)
    if f > 1 and len(seg) > 2 * f:
        ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)[:, None]
        seg[:f] = seg[:f] * ramp
        seg[-f:] = seg[-f:] * ramp[::-1]
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    sf.write(dst, seg, rate)
    print("SKIP:声音包 %.1f 秒太长 → 裁成 %.1f 秒（取中间段）✓" % (dur, len(seg) / float(rate)),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
