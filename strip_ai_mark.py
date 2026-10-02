# -*- coding: utf-8 -*-
r"""
自动去掉视频里的「AI生成」水印
=====================================================================
主人：做个脚本以后自动把生成的视频里的 ai 标裁掉

实测：智谱 CogVideoX-Flash 的水印「AI生成」在**右下角**，
      1792×1024 下约 x 1648~1763 / y 963~1000
      → 右边距 ~29px(1.6%W)  下边距 ~24px(2.3%H)  尺寸 ~115×37(6.4%W × 3.6%H)

两种做法（都保留音频、都用 libx264 重编码）：
  · delogo  —— 用周围像素把那一小块"糊"掉，**尺寸不变**（推荐 ✓）
  · crop    —— 直接把右下角连边裁掉，**画面变小但不变形** ✓

用法：
    python strip_ai_mark.py <视频> [--mode delogo|crop] [--out 输出路径]
    python strip_ai_mark.py --batch <目录>       # 批量处理整个目录
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

FF = (r"D:\ComfyUI\python_embeded\Lib\site-packages\imageio_ffmpeg\binaries"
      r"\ffmpeg-win-x86_64-v7.1.exe")
FPROBE_HINT = os.path.join(os.path.dirname(FF), "ffprobe.exe")

# 水印相对右下角的比例（带余量，实测值 ×1.3 左右）
PAD_RIGHT = 0.022     # 右边距占宽度
PAD_BOTTOM = 0.026    # 下边距占高度
BOX_W = 0.088         # 水印框宽（实测 6.4% → 给余量）
BOX_H = 0.060         # 水印框高（实测 3.6% → 给余量）


def ffmpeg_exe():
    if os.path.isfile(FF):
        return FF
    return "ffmpeg"


def probe_size(path):
    """用 ffmpeg -i 读分辨率"""
    r = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    txt = r.stderr or ""
    for ln in txt.splitlines():
        if "Video:" in ln and "x" in ln:
            for tok in ln.split(","):
                t = tok.strip().split(" ")[0]
                m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", t)
                if m:
                    return int(m.group(1)), int(m.group(2))
    return 0, 0


def compute_box(w, h):
    """算出水印框（x, y, bw, bh），按比例自适应任意分辨率"""
    bw = max(24, int(round(w * BOX_W)))
    bh = max(16, int(round(h * BOX_H)))
    x = w - int(round(w * PAD_RIGHT)) - bw
    y = h - int(round(h * PAD_BOTTOM)) - bh
    x = max(0, min(x, w - bw))
    y = max(0, min(y, h - bh))
    return x, y, bw, bh


def build_vf(mode, w, h, extra_pad=0):
    x, y, bw, bh = compute_box(w, h)
    if mode == "delogo":
        # delogo 的 w/h 必须是偶数且 >0
        bw -= bw % 2
        bh -= bh % 2
        return "delogo=x=%d:y=%d:w=%d:h=%d" % (x, y, bw, bh), (x, y, bw, bh)

    # 裁掉右边 + 下边（不拉伸）
    cw = w - (w - x) - 2
    ch = h - (h - y) - 2
    cw -= cw % 2
    ch -= ch % 2
    if mode == "crop":
        return "crop=w=%d:h=%d:x=0:y=0" % (cw, ch), (x, y, bw, bh)
    if mode == "crop-scale":
        # ★ 推荐：先裁掉水印，再缩回原尺寸 —— 比例不变、无糊痕、几乎无损 ✓
        return ("crop=w=%d:h=%d:x=0:y=0,scale=%d:%d:flags=lanczos"
                % (cw, ch, w, h)), (x, y, bw, bh)
    if mode == "crop-pad":
        # 裁掉水印后，用黑边补回原尺寸（适合不想缩放的场合）
        return ("crop=w=%d:h=%d:x=0:y=0,pad=%d:%d:0:0:color=black"
                % (cw, ch, w, h)), (x, y, bw, bh)
    raise RuntimeError("不认识的模式：%s" % mode)


def strip(path, out=None, mode="delogo", crf=18, preset="medium"):
    if not os.path.isfile(path):
        raise RuntimeError("文件不存在：%s" % path)
    w, h = probe_size(path)
    if not w:
        raise RuntimeError("读不到分辨率（ffmpeg 不支持这个格式？）")
    vf, box = build_vf(mode, w, h)
    if out is None:
        base, ext = os.path.splitext(path)
        out = base + "_无标" + (ext or ".mp4")
    args = [ffmpeg_exe(), "-y", "-i", path, "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", out]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError("ffmpeg 失败：%s" % (r.stderr or "")[-400:])
    nw, nh = probe_size(out)
    return {"in": path, "out": out, "mode": mode, "srcSize": "%dx%d" % (w, h),
            "outSize": "%dx%d" % (nw, nh), "box": box,
            "inMB": round(os.path.getsize(path) / 2 ** 20, 2),
            "outMB": round(os.path.getsize(out) / 2 ** 20, 2)}


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("src", nargs="?", help="视频文件，或 --batch 时的目录")
    ap.add_argument("--batch", action="store_true", help="把目录里所有 mp4 都处理")
    ap.add_argument("--mode", default="crop-scale",
                    choices=("crop-scale", "crop", "crop-pad", "delogo"),
                    help="crop-scale=裁掉后缩回原尺寸（推荐）｜crop=裁掉变小｜"
                         "crop-pad=裁掉后补黑边｜delogo=糊掉（会留痕）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--crf", type=int, default=18)
    a = ap.parse_args()

    if not a.src:
        # 默认：处理桌面\AI视频 里最新的一条
        d = os.path.join(os.path.expanduser("~"), "Desktop", "AI视频")
        vids = sorted(glob.glob(os.path.join(d, "*.mp4")), key=os.path.getmtime, reverse=True)
        vids = [v for v in vids if "_无标" not in v]
        if not vids:
            print("  用法：python strip_ai_mark.py <视频> [--mode delogo|crop]")
            return 1
        a.src = vids[0]
        print("  没给参数 → 自动选最新的一条：%s" % os.path.basename(a.src))

    if a.batch or os.path.isdir(a.src):
        vids = sorted(glob.glob(os.path.join(a.src, "*.mp4")))
        vids = [v for v in vids if "_无标" not in v]
        print("  批量处理 %d 个文件（模式 %s）" % (len(vids), a.mode))
        for v in vids:
            try:
                r = strip(v, mode=a.mode, crf=a.crf)
                print("   ✓ %s → %s（%s→%s，%.2f→%.2f MB）" % (
                    os.path.basename(r["in"]), os.path.basename(r["out"]),
                    r["srcSize"], r["outSize"], r["inMB"], r["outMB"]))
            except Exception as e:
                print("   ✗ %s：%s" % (os.path.basename(v), str(e)[:150]))
        return 0

    print("=" * 92)
    print("去水印（模式 %s）" % a.mode)
    print("=" * 92)
    r = strip(a.src, out=a.out, mode=a.mode, crf=a.crf)
    print("  输入：%s（%s，%.2f MB）" % (os.path.basename(r["in"]), r["srcSize"], r["inMB"]))
    print("  水印框：x=%d y=%d w=%d h=%d" % r["box"])
    print("  输出：%s（%s，%.2f MB）" % (os.path.basename(r["out"]), r["outSize"], r["outMB"]))
    print("  %s" % json.dumps({k: r[k] for k in ("mode", "srcSize", "outSize")}, ensure_ascii=False))
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
