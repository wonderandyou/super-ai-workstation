# -*- coding: utf-8 -*-
"""
翻唱 worker · 声音转换（Seed-VC）
=====================================================================
★ 必须用 Seed-VC 自己的解释器跑：
    D:\\SeedVC\\venv\\Scripts\\python.exe _cover_vc.py ...

为什么做薄封装而不是直接调 inference.py：
    inference.py 的 --output 要的是**目录**，文件名是它自己拼的
    （vc_{源名}_{目标名}_{长度}_ {步数}.wav），父进程不好接。
    这里包一层，跑完把它产出的最新 wav 拷成我们要的确切路径。

Seed-VC 的 CLI 参数（来自它的 inference.py）：
    --source            待转换的人声
    --target            参考音色（就是声音包）
    --output            输出目录
    --diffusion-steps   默认 30
    --inference-cfg-rate 默认 0.7
    --length-adjust     默认 1.0
    --f0-condition      翻唱建议开（跟原唱韵律走），否则音高会飘
    --semi-tone-shift   变调（半音）
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

SEEDVC_DIR = r"D:\SeedVC"
SEEDVC_SRC = os.path.join(SEEDVC_DIR, "seed-vc")
INFERENCE = os.path.join(SEEDVC_SRC, "inference.py")

# 铁律一：不设 HF_ENDPOINT 镜像 —— 模型只从官方源取（本机已有缓存，正常不触发下载）
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def stage(s):
    print("STAGE:" + s, flush=True)


def skip(s):
    print("SKIP:" + s, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--output", required=True, help="确切输出文件路径")
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=0.7)
    ap.add_argument("--length-adjust", type=float, default=1.0)
    ap.add_argument("--semi-tone", type=int, default=0)
    ap.add_argument("--f0", action="store_true", help="开 f0 条件（翻唱建议开）")
    a = ap.parse_args()

    if not os.path.isfile(INFERENCE):
        print("错误：找不到 %s" % INFERENCE, flush=True)
        return 1
    for p, what in ((a.source, "待转换人声"), (a.target, "声音包")):
        if not os.path.isfile(p):
            print("错误：找不到%s %s" % (what, p), flush=True)
            return 1

    # 输出目录（用临时名，跑完再拷成确切路径）
    outdir = os.path.join(os.path.dirname(a.output) or ".", "_vc_out")
    os.makedirs(outdir, exist_ok=True)
    for f in glob.glob(os.path.join(outdir, "*.wav")):
        try:
            os.remove(f)
        except Exception:
            pass

    args = [sys.executable, INFERENCE,
            "--source", a.source,
            "--target", a.target,
            "--output", outdir,
            "--diffusion-steps", str(a.steps),
            "--inference-cfg-rate", str(a.cfg),
            "--length-adjust", str(a.length_adjust),
            "--semi-tone-shift", str(a.semi_tone),
            "--f0-condition", "True" if a.f0 else "False",
            "--fp16", "True"]
    skip("命令 %s" % " ".join(os.path.basename(x) for x in args))

    stage("加载 Seed-VC 模型")
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = 0x08000000 if os.name == "nt" else 0
    pr = subprocess.Popen(args, env=env, cwd=SEEDVC_SRC, creationflags=flags,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace", bufsize=1)
    started = False
    errbuf = []
    keys = [
        ("Loading", "加载模型"),
        ("checkpoint", "加载模型"),
        ("whisper", "加载模型"),
        ("campplus", "加载说话人特征模型"),
        ("bigvgan", "加载声码器"),
        ("vocoder", "加载声码器"),
        ("f0", "提取音高"),
        ("pitch", "提取音高"),
        ("mel", "提取频谱"),
        ("convert", "声音转换中"),
        ("sampl", "声音转换中"),
        ("diffusion", "声音转换中"),
        ("sav", "写入结果"),
    ]
    t_last = time.time()
    for line in iter(pr.stdout.readline, ""):
        line = (line or "").rstrip()
        if not line:
            continue
        low = line.lower()
        if "traceback" in low or "error" in low or "no module" in low:
            errbuf.append(line)
            skip(line[:180])
            continue
        if _is_pct(line):
            continue
        # 关键阶段才更新进度（别把每行都当噪音丢了 —— 上一版就是这么卡在 40% 的）
        for k, label in keys:
            if k.lower() in low:
                if not started or label != last_stage[0]:
                    started = True
                    last_stage[0] = label
                    stage(label)
                break
        else:
            skip(line[:180])
        if time.time() - t_last > 20:
            t_last = time.time()
            stage(last_stage[0] or "声音转换中")
    pr.wait()
    if pr.returncode != 0:
        tail = "\n".join(errbuf[-8:]) or ("退出码 %s" % pr.returncode)
        print("错误：Seed-VC 失败 %s" % tail[:400], flush=True)
        return 1

    stage("整理输出")
    cands = sorted(glob.glob(os.path.join(outdir, "*.wav")),
                   key=os.path.getmtime, reverse=True)
    if not cands:
        print("错误：Seed-VC 跑完了但 %s 里没有 wav" % outdir, flush=True)
        return 1
    os.makedirs(os.path.dirname(a.output) or ".", exist_ok=True)
    if os.path.exists(a.output):
        os.remove(a.output)
    shutil.copy2(cands[0], a.output)
    skip("转换结果 %s  %.2f MB  用时 %.0f 秒" % (
        a.output, os.path.getsize(a.output) / 2 ** 20, time.time() - t0))
    stage("转换完成")
    return 0


last_stage = [""]      # 用一个元素的列表当可变容器，记录当前阶段名


def _is_pct(line):
    """进度条那种刷屏行（含 NN%|）识别出来丢掉"""
    import re
    return bool(re.match(r"^\s*\d+%\|", line))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("错误：%s" % e, flush=True)
        sys.exit(1)
