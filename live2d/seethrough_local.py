#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""本地跑 See-Through（必须在 C:\SeeThrough\venv 里执行）

    C:\SeeThrough\venv\Scripts\python.exe D:\AI工作站\live2d\seethrough_local.py ^
        --image "立绘.png" --out "输出目录" [--resolution 1024] [--seed 42] [--tblr] [--offload blockswap|group|none]

为什么要这个包装脚本：
  · 官方脚本默认从 **HuggingFace** 拉模型（本机连不上）→ 这里改成本地目录（模型来自 ModelScope）
  · 8 GB 显存跑 1280 会爆 → 默认走仓库自带的 **blockswap**（块级换入换出）
  · 给工作站输出可解析的进度行：  [ST] PCT <0-100> <文字>

模型（官方一手，ModelScope ljsabc 仓，已逐个校验 Sha256）：
  C:\SeeThrough\models\layerdiff   = ljsabc/seethroughv0.0.2_layerdiff3d
  C:\SeeThrough\models\marigold    = ljsabc/seethroughv0.0.1_marigold
"""
import argparse
import os
import os.path as osp
import shutil
import sys
import time

REPO = r"C:\SeeThrough\see-through"
MODELS = r"C:\SeeThrough\models"


def pct(n, text):
    print("[ST] PCT %d %s" % (n, text), flush=True)


def stage(text):
    print("[ST] STAGE %s" % text, flush=True)


def _tee_stdout(path):
    """把 print 同时写进日志文件（前端把它当子进程拉走时也能查进度）"""
    if not path:
        return None
    import builtins
    fh = open(path, "a", encoding="utf-8")
    orig = builtins.print

    def _print(*a, **kw):
        kw.setdefault("flush", True)
        try:
            orig(*a, **kw)
        except Exception:
            pass
        try:
            orig("[%s]" % time.strftime("%H:%M:%S"), *a, file=fh, **kw)
        except Exception:
            pass

    builtins.print = _print
    return fh


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True, help="PSD 落到哪个目录")
    ap.add_argument("--resolution", type=int, default=1024)
    ap.add_argument("--resolution-depth", type=int, default=768)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--tblr", action="store_true", help="拆分左右手臂与腿")
    ap.add_argument("--offload", default="nf4", choices=["nf4", "blockswap", "group", "none"],
                    help="省显存/省内存策略：nf4 = 4bit 量化（本机默认，最省）；"
                         "blockswap = 块级换入换出；group = 分组卸载；none = 全塞显存")
    ap.add_argument("--inpaint", default="auto", choices=["auto", "lama", "cv2"],
                    help="遮挡区域修复：lama 需要 lama_large_512px.ckpt（官方只在 HF，本机连不上）；"
                         "cv2 不需要任何权重；auto = 有本地权重就用 lama，否则退回 cv2")
    ap.add_argument("--work", default=None, help="中间产物目录（默认输出目录下的 _work）")
    args = ap.parse_args()

    _tee_stdout(os.environ.get("ST_LOCAL_LOG"))

    # ★ 修复引擎（inpaint）的选择
    #   仓库默认走 LaMa，而它是 torch.hub 从 HuggingFace 拉权重（本机连不上 ✗）
    #   这里：① 本地放了权重就用本地的（不改它一行代码）② 没放就退回 OpenCV 修复
    LOCAL_LAMA = osp.join(MODELS, "lama", "lama_large_512px.ckpt")
    have_lama = osp.isfile(LOCAL_LAMA)
    use_lama = (args.inpaint == "lama") or (args.inpaint == "auto" and have_lama)
    if use_lama and not have_lama:
        print("[ST] 警告：要 lama 但没找到 %s → 退回 cv2" % LOCAL_LAMA, flush=True)
        use_lama = False
    if use_lama:
        import torch.hub as _hub
        _orig_load = _hub.load_state_dict_from_url

        def _patched_load(url, *a, **kw):
            if "lama_large_512px" in str(url) and osp.isfile(LOCAL_LAMA):
                print("[ST] INFO 用本地 LaMa 权重：%s" % LOCAL_LAMA, flush=True)
                return torch.load(LOCAL_LAMA, map_location=kw.get("map_location", "cpu"),
                                  weights_only=False)
            return _orig_load(url, *a, **kw)

        _hub.load_state_dict_from_url = _patched_load
        print("[ST] INFO 遮挡修复：LaMa（本地权重）", flush=True)
    else:
        print("[ST] INFO 遮挡修复：OpenCV（无需下载权重）", flush=True)

    src = osp.abspath(args.image)
    out = osp.abspath(args.out)
    work = osp.abspath(args.work or osp.join(out, "_work"))
    os.makedirs(out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    if not osp.isfile(src):
        print("[ST] ERROR 找不到图片 %s" % src, flush=True)
        return 2

    # 仓库要求以「仓库根目录」为工作目录，assets 需要指向 common/assets
    assets = osp.join(REPO, "assets")
    if not osp.exists(assets):
        try:
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", assets, osp.join(REPO, "common", "assets")],
                           capture_output=True)
        except Exception:
            pass
    os.chdir(REPO)
    sys.path.insert(0, osp.join(REPO, "common"))
    sys.path.insert(0, REPO)
    sys.path.insert(0, osp.join(REPO, "inference", "scripts"))

    LD = osp.join(MODELS, "layerdiff")
    MG = osp.join(MODELS, "marigold")
    for p in (LD, MG):
        if not osp.isdir(p):
            print("[ST] ERROR 模型目录不存在：%s（先跑 C:\\SeeThrough\\fetch_models.py）" % p, flush=True)
            return 2

    pct(3, "导入依赖 …")
    import torch
    print("[ST] INFO torch %s cuda %s 可用=%s 显卡=%s" % (
        torch.__version__, torch.version.cuda, torch.cuda.is_available(),
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-"), flush=True)
    if not torch.cuda.is_available():
        print("[ST] ERROR 显卡不可用（CUDA 没起来）", flush=True)
        return 3

    from modules.layerdiffuse.vae import TransparentVAE
    from modules.layerdiffuse.layerdiff3d import UNetFrameConditionModel
    from utils.inference_utils import further_extr

    if not use_lama:
        # 把 cluster_inpaint_part 的默认 inpaint 改成 cv2（调用处不传这个参数，所以在这里兜）
        import utils.torchcv as _tcv
        import utils.inference_utils as _iu
        _orig_cip = _tcv.cluster_inpaint_part

        def _cip_cv2(*a, **kw):
            kw.setdefault("inpaint", "cv2")
            return _orig_cip(*a, **kw)

        _tcv.cluster_inpaint_part = _cip_cv2
        _iu.cluster_inpaint_part = _cip_cv2

    # ---------------- ① LayerDiff3D（出透明图层） ----------------
    stage("layerediff")
    pct(6, "加载 LayerDiff3D 模型（%s）…" % ("blockswap" if args.offload == "blockswap" else args.offload))
    t0 = time.time()
    # ★ 关键：low_cpu_mem_usage=True 让 safetensors 走内存映射，RSS 只有 1 GB 出头；
    #   不加的话会把 8.14 GB 全读进内存，本机 16 GB 直接被杀 ✗（踩过）
    _LD_KW = dict(torch_dtype=torch.bfloat16, low_cpu_mem_usage=True)

    if args.offload == "nf4":
        # ★ 本机默认：4bit 量化加载 unet —— 8.14 GB 压到 ~1 GB 内存 / ~0.6 GB 显存
        from transformers import BitsAndBytesConfig
        from modules.layerdiffuse.diffusers_kdiffusion_sdxl import (
            KDiffusionStableDiffusionXLPipeline as PipeCls)
        from inference_psd_quantized import run_layerdiff
        qc = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                bnb_4bit_compute_dtype=torch.bfloat16)
        pct(8, "以 4bit 量化加载 LayerDiff3D（省内存路线）…")
        unet = UNetFrameConditionModel.from_pretrained(
            LD, subfolder="unet", quantization_config=qc,
            low_cpu_mem_usage=True, device_map={"": 0})
        trans_vae = TransparentVAE.from_pretrained(LD, subfolder="trans_vae", **_LD_KW)
        pipe = PipeCls.from_pretrained(LD, trans_vae=trans_vae, unet=unet, scheduler=None, **_LD_KW)
        del trans_vae, unet
        import gc
        gc.collect()
        pipe.vae.to(dtype=torch.bfloat16, device="cuda")
        pipe.trans_vae.to(dtype=torch.bfloat16, device="cuda")
        # 量化组件交给 bnb 管设备，别手动 .to(cuda)
        if hasattr(pipe, "cache_tag_embeds"):
            pipe.cache_tag_embeds()   # 缓存 tag 词向量并卸载文本编码器，省显存
    else:
        trans_vae = TransparentVAE.from_pretrained(LD, subfolder="trans_vae", **_LD_KW)
        unet = UNetFrameConditionModel.from_pretrained(LD, subfolder="unet", **_LD_KW)

        if args.offload == "blockswap":
            from inference_psd_blockswap import (
                KDiffusionStableDiffusionXLPipelineBlockSwap as PipeCls, run_layerdiff)
            pipe = PipeCls.from_pretrained(LD, trans_vae=trans_vae, unet=unet, scheduler=None, **_LD_KW)
            del trans_vae, unet
            import gc
            gc.collect()
            # ★ 再确认一遍 bf16：块级换入时每块才不会被 fp32 撑爆显存
            for comp in ("vae", "trans_vae", "unet", "text_encoder", "text_encoder_2"):
                m = getattr(pipe, comp, None)
                if m is not None:
                    try:
                        m.to(dtype=torch.bfloat16)
                    except Exception:
                        pass
            pipe.enable_blockswap(device="cuda")
        else:
            from inference_psd_quantized import run_layerdiff
            from modules.layerdiffuse.diffusers_kdiffusion_sdxl import (
                KDiffusionStableDiffusionXLPipeline as PipeCls)
            pipe = PipeCls.from_pretrained(LD, trans_vae=trans_vae, unet=unet, **_LD_KW)
            del trans_vae, unet
            import gc
            gc.collect()
            if args.offload == "group":
                pipe.vae.to(dtype=torch.bfloat16, device="cuda")
                pipe.trans_vae.to(dtype=torch.bfloat16, device="cuda")
                pipe.unet.to(dtype=torch.bfloat16, device="cuda")
                pipe.text_encoder.to(dtype=torch.bfloat16, device="cuda")
                pipe.text_encoder_2.to(dtype=torch.bfloat16, device="cuda")
                pipe.enable_group_offload("cuda", num_blocks_per_group=1)

    print("[ST] INFO 模型加载耗时 %.1fs，显存峰值 %.2f GB" % (
        time.time() - t0, torch.cuda.max_memory_allocated() / 1e9), flush=True)
    pct(12, "LayerDiff3D 推理中（%d 步，%dpx）…" % (args.steps, args.resolution))
    t0 = time.time()
    run_layerdiff(pipe, src, work, args.seed, args.steps, args.resolution)
    print("[ST] INFO LayerDiff 耗时 %.1fs，显存峰值 %.2f GB" % (
        time.time() - t0, torch.cuda.max_memory_allocated() / 1e9), flush=True)
    del pipe
    torch.cuda.empty_cache()
    pct(55, "透明图层生成完毕")

    # ---------------- ② Marigold（估深度） ----------------
    stage("marigold")
    pct(58, "加载 Marigold 深度模型 …")
    from inference_psd_quantized import build_marigold_pipeline, run_marigold
    from modules.marigold import MarigoldDepthPipeline
    # build_marigold_pipeline 内部自己调 from_pretrained —— 这里给它注入同样的省内存参数
    _orig_unet_fp = UNetFrameConditionModel.from_pretrained.__func__
    _orig_mg_fp = MarigoldDepthPipeline.from_pretrained.__func__

    def _unet_fp(cls, *a, **kw):
        kw.setdefault("torch_dtype", torch.bfloat16)
        kw.setdefault("low_cpu_mem_usage", True)
        return _orig_unet_fp(cls, *a, **kw)

    def _mg_fp(cls, *a, **kw):
        kw.setdefault("torch_dtype", torch.bfloat16)
        kw.setdefault("low_cpu_mem_usage", True)
        return _orig_mg_fp(cls, *a, **kw)

    UNetFrameConditionModel.from_pretrained = classmethod(_unet_fp)
    MarigoldDepthPipeline.from_pretrained = classmethod(_mg_fp)

    mg_args = argparse.Namespace(quant_mode="none", cpu_offload=False, repo_id_depth=MG)
    t0 = time.time()
    mg = build_marigold_pipeline(mg_args)
    print("[ST] INFO Marigold 加载耗时 %.1fs" % (time.time() - t0), flush=True)
    pct(62, "深度推理中 …")
    t0 = time.time()
    run_marigold(mg, src, work, args.seed, args.resolution_depth)
    print("[ST] INFO Marigold 耗时 %.1fs，显存峰值 %.2f GB" % (
        time.time() - t0, torch.cuda.max_memory_allocated() / 1e9), flush=True)
    del mg
    torch.cuda.empty_cache()

    # ---------------- ③ 组装 PSD ----------------
    stage("psd")
    pct(85, "组装分层 PSD …")
    name = osp.splitext(osp.basename(src))[0]
    saved = osp.join(work, name)
    further_extr(saved, rotate=False, save_to_psd=True, tblr_split=args.tblr)
    psd = saved + ".psd"
    if not osp.isfile(psd):
        print("[ST] ERROR PSD 没生成：%s" % psd, flush=True)
        return 4
    dest = osp.join(out, osp.basename(psd))
    shutil.copy2(psd, dest)
    print("[ST] INFO 图层数（预览图）: %d" % len([f for f in os.listdir(saved) if f.endswith(".png")]), flush=True)
    pct(100, "完成：%s" % dest)
    print("[ST] DONE %s" % dest, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
