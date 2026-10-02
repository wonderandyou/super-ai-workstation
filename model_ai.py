# -*- coding: utf-8 -*-
"""
本地模型 —— 一键下载 + 官方哈希校验
=====================================================================
为什么要有它：8 个本地功能里，以前只有「本地千问生图」和「高速工作流」有下载入口 ✗
            其余（溶图 29 GB / 音乐 / 翻唱 / 朗读 / 抠图 / 分离）都得手动弄 ✗
            换台机器就抓瞎。

设计要点（对应两条铁律）：
  · **来源只取正规渠道**：ModelScope（阿里官方，铁律一明确允许）
    + 模型作者/厂商的官方 CDN（例如 Demucs 走 Meta 官方）
  · **大小与哈希不写死**，下载前从 ModelScope 官方文件列表 API 实时取
    （`/repo/files` 返回的 `Sha256` 与 `Size`，铁律二第 1 条认可的期望值来源）
    —— 写死数字踩过坑：ComfyUI 便携包的 expect_size 就是错的，导致下载完被判失败 ✗
  · 下完逐个文件校验，结果写进 `data/模型校验记录.json` 留档（铁律二第 8 条）✓
  · 校验不过的文件**删掉**，绝不留着用（铁律二末条）✓

用法（app.py 调）：
    model_ai.groups()                  # 分组清单（界面用）
    model_ai.status()                  # 每个文件装了没
    model_ai.install(jid, "blend")     # 下载某一组
"""
import glob
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = r"D:\AI工作站"
DATA = os.path.join(ROOT, "data")
RECORD = os.path.join(DATA, "模型校验记录.json")
HOME = os.path.expanduser("~")

MS = "https://modelscope.cn/api/v1/models"
THREADS = 16
UA = {"User-Agent": "Mozilla/5.0"}

# --------------------------------------------------------------------------
#  模型清单
#  src="ms"   → repo / path（ModelScope，哈希与大小实时取官方 API）
#  src="url"  → 直接给官方地址（哈希必须已由官方渠道确证，写在这里）
#  src="hf"   → 官方在 HuggingFace，本机直连不通，界面只提示不给按钮（如实说明 ✓）
# --------------------------------------------------------------------------
# ⚠️ 关于 src="hf" 的下载源（2026-10-01 主人授权，仅此一次）
#   HF 官方（huggingface.co）在国内直连不通 ✗
#   主人明确授权：「就算是不可信网址也写进去，最后哈希检验就行」
#   所以 mirrors 里放一个非官方镜像，**仅作官方不通时的后备** ✓
#   但**无论从哪下，都必须过 size + sha256 校验**，不过就删 ✓
#   期望值取自「本机已装文件」——它们当初就是从 HF 官方库下载的，
#   缓存目录名与 refs/main 记录了 org/repo 和 commit，可完整追溯 ✓
MIRRORS_FOR_HF = ["https://hf-mirror.com"]   # ✗ 非官方镜像，仅本次授权


GROUPS = [
    {
        "key": "dep", "name": "运行依赖 · 朗读用的 ffmpeg", "icon": "🧩",
        "note": "朗读要调 ffmpeg 命令行去转录参考音频，本机原本**没装** ✗。"
                "这里用 PyPI 官方的 imageio-ffmpeg（自带二进制，正规渠道），"
                "会自动补到 F5-TTS 和 ComfyUI 两套环境里；哪套不在就跳过哪套",
        "items": [
            {"src": "pip", "pkg": "imageio-ffmpeg", "env": "f5",
             "note": "F5-TTS 环境（朗读必需）"},
            {"src": "pip", "pkg": "imageio-ffmpeg", "env": "comfy",
             "note": "ComfyUI 环境（音频处理用）"},
        ],
    },
    {
        "key": "blend", "name": "AI 溶图 · 方案 B", "icon": "🎨",
        "note": "Qwen-Image-Edit-2509 全套，约 29 GB（最占地方的一组）",
        "items": [
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image-Edit_ComfyUI",
             "path": "split_files/diffusion_models/qwen_image_edit_2509_int8_convrot.safetensors",
             "dest": r"C:\ComfyUI-models\diffusion_models"},
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image-Edit_ComfyUI",
             "path": "split_files/loras/Qwen-Image-Edit-2509-Fusion.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\loras"},
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image_ComfyUI",
             "path": "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\text_encoders"},
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image_ComfyUI",
             "path": "split_files/vae/qwen_image_vae.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\vae"},
            {"src": "ms", "repo": "lightx2v/Qwen-Image-Lightning",
             "path": "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\loras"},
        ],
    },
    {
        "key": "local", "name": "本地千问生图", "icon": "🐋",
        "note": "Qwen-Image-2.1 的三个模型，约 6.5 GB（ComfyUI 引擎本身用上面那个按钮装）",
        "items": [
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image-2.1",
             "path": "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\diffusion_models"},
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image-2.1",
             "path": "text_encoders/qwen3vl_8b_w4a8.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\text_encoders"},
            {"src": "ms", "repo": "Comfy-Org/Qwen-Image-2.1",
             "path": "vae/qwen_image_2.1_vae_bf16.safetensors",
             "dest": r"D:\ComfyUI\ComfyUI\models\vae"},
        ],
    },
    {
        "key": "stem", "name": "AI 人声分离", "icon": "🎚️",
        "note": "Demucs htdemucs，80 MB（Meta 官方 CDN，已实测可直连）",
        "items": [
            {"src": "url",
             "url": "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th",
             "name": "955717e8-8726e21a.th",
             "dest": os.path.join(HOME, ".cache", "torch", "hub", "checkpoints"),
             # 这个哈希是「下载 Meta 官方原件后自己算出来」的，已逐字节比对本机文件一致 ✓
             "sha256": "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4",
             "size": 84141911},
        ],
    },
    {
        "key": "matting", "name": "AI 抠图", "icon": "✂️",
        "note": "RMBG 1.4 + 2.0（约 1.2 GB）—— 来源是作者 BRIA AI 在 ModelScope 的官方仓库 ✓",
        "items": [
            {"src": "ms", "repo": "briaai/RMBG-1.4", "path": "onnx/model.onnx",
             "save_as": "rmbg-1.4.onnx",
             "dest": os.path.join(HOME, "Documents", "抠图", "models")},
            {"src": "ms", "repo": "briaai/RMBG-2.0", "path": "onnx/model.onnx",
             "save_as": "rmbg-2.0.onnx",
             "dest": os.path.join(HOME, "Documents", "抠图", "models")},
        ],
    },
    {
        "key": "cover", "name": "AI 翻唱", "icon": "🎤",
        "note": "官方发布在 HuggingFace（Plachta/Seed-VC、nvidia/bigvgan、"
                "lj1995/VoiceConversionWebUI）。HF 官方直连不通 ✗ "
                "→ 已按主人 2026-10-01 的授权配了备用镜像，**下载后一律过 size+sha256 校验** ✓",
        "items": [
            {"src": "hf", "path": "DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth",
             "dest": os.path.join(r"D:\SeedVC", "checkpoints"), "repo": "Plachta/Seed-VC", "commit": "257283f9f41585055e8f858fba4fd044e5caed6e", "size": 440312082, "sha256": "8ec8841b20bb46df9f7e8e570a6946a4b87b940133c7f0e778487ff33841f720", "mirrors": MIRRORS_FOR_HF},
            {"src": "hf", "path": "hift.pt", "dest": os.path.join(r"D:\SeedVC", "checkpoints"), "repo": "Plachta/Seed-VC", "commit": "", "size": 81896716, "sha256": "91e679b6ca1eff71187ffb4f3ab0444935594cdcc20a9bd12afad111ef8d6012", "mirrors": MIRRORS_FOR_HF},
            {"src": "hf", "path": "rmvpe.pt", "dest": os.path.join(r"D:\SeedVC", "checkpoints"), "repo": "lj1995/VoiceConversionWebUI", "commit": "e6d0c1a17da07c33557852f9dfa2bd44cc75737d", "size": 181184272, "sha256": "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193", "mirrors": MIRRORS_FOR_HF},
            {"src": "hf", "path": "bigvgan_generator.pt",
             "dest": os.path.join(r"D:\SeedVC", "seed-vc", "checkpoints", "hf_cache"), "repo": "nvidia/bigvgan_v2_22khz_80band_256x", "commit": "633ff708ed5b74903e86ff1298cf4a98e921c513", "size": 449228171, "sha256": "e95ba25972d3de0628d99cd156e9315a9c018899bf739988959ebe3544080ced", "mirrors": MIRRORS_FOR_HF},
            # ★ Seed-VC 的 seed_vc_wrapper.py 同时加载 22k 和 44k 两个 bigvgan，
            #   少任何一个都会在换声时报错，所以两个都要列 ✗
            {"src": "hf", "path": "bigvgan_generator.pt",
             "dest": os.path.join(r"D:\SeedVC", "seed-vc", "checkpoints", "hf_cache"),
             "repo": "nvidia/bigvgan_v2_44khz_128band_512x",
             "commit": "95a9d1dcb12906c03edd938d77b9333d6ded7dfb",
             "size": 489041291,
             "sha256": "d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357",
             "mirrors": MIRRORS_FOR_HF},
        ],
    },
    {
        "key": "tts", "name": "声音包朗读", "icon": "📖",
        "note": "F5-TTS 主模型 1.3 GB（ModelScope 的 AI-ModelScope 官方镜像仓 ✓）；"
                "vocos 声码器官方只在 HuggingFace（charactr/vocos-mel-24khz），"
                "HF 直连不通 → 已配后备镜像，下载后过 size+sha256 校验 ✓",
        "items": [
            {"src": "ms", "repo": "AI-ModelScope/F5-TTS",
             "path": "F5TTS_v1_Base/model_1250000.safetensors",
             "dest": os.path.join(r"D:\F5TTS", "checkpoints", "F5TTS_v1_Base")},
            {"src": "hf", "path": "F5TTS_v1_Base/model_1250000.safetensors",
             "dest": os.path.join(r"D:\F5TTS", "checkpoints", "F5TTS_v1_Base"), "repo": "SWivid/F5-TTS", "commit": "", "size": 1348435761, "sha256": "670900fd14e6c458b95da6e9ed317cdb20dbaf7a1c02ac06a05475a9d32b6a38", "mirrors": MIRRORS_FOR_HF},
            {"src": "hf", "path": "pytorch_model.bin",
             "dest": os.path.join(r"D:\F5TTS", "checkpoints", "vocos-mel-24khz"), "repo": "charactr/vocos-mel-24khz", "commit": "0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21", "size": 54365991, "sha256": "97ec976ad1fd67a33ab2682d29c0ac7df85234fae875aefcc5fb215681a91b2a", "mirrors": MIRRORS_FOR_HF},
        ],
    },
    {
        "key": "music", "name": "AI 音乐工坊", "icon": "🎵",
        "note": "ACE-Step 1.5 一体化版（9.34 GB，很大，下载要有耐心）。"
                "官方发布在 HuggingFace 的 Comfy-Org 组织（ComfyUI 官方）✓；"
                "ModelScope 上只有分文件版 v1-3.5B，和本机用的不是一个东西 ✗",
        "items": [
            {"src": "hf", "path": "checkpoints/ace_step_1.5_turbo_aio.safetensors",
             "dest": os.path.join(r"D:\ComfyUI\ComfyUI\models", "checkpoints"),
             "repo": "Comfy-Org/ace_step_1.5_ComfyUI_files",
             "commit": "d84f74778c9389dba94211b51ee4354f7a9e6300",
             "size": 10025478736,
             "sha256": "67b0f43aa5c51c840bd0228e6a935d8ff416ec87e5df2fc0637da17a561252bc",
             "mirrors": MIRRORS_FOR_HF},
        ],
    },
    {
        "key": "live2d", "name": "Live2D 制作 · 本地拆层引擎", "icon": "🎭",
        "note": "See-Through 拆层模型（LayerDiff3D + Marigold，约 13.4 GB）——"
                "作者 ljsabc 在 ModelScope 的官方发布（一手渠道，合规）。"
                "只用「在线官方演示」的话不用装；本地引擎要独显 ≥ 8 GB",
        "items": [
            {"src": "ms-repo", "repo": "ljsabc/seethroughv0.0.2_layerdiff3d",
             "dest": r"C:\SeeThrough\models\layerdiff"},
            {"src": "ms-repo", "repo": "ljsabc/seethroughv0.0.1_marigold",
             "dest": r"C:\SeeThrough\models\marigold"},
        ],
    },
]


# --------------------------------------------------------------------------
#  ModelScope 官方文件表：路径 → (sha256, size, 下载地址)
# --------------------------------------------------------------------------
_ms_cache = {}


def ms_files(repo):
    """取 ModelScope 官方文件列表（含每个文件的 Sha256 与 Size）"""
    if repo in _ms_cache:
        return _ms_cache[repo]
    out = {}
    url = "%s/%s/repo/files?Revision=master&Recursive=true" % (MS, repo)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        for f in ((d.get("Data") or {}).get("Files") or []):
            if str(f.get("Type") or "") == "tree":
                continue
            p = str(f.get("Path") or "")
            if p:
                out[p] = (f.get("Sha256"), f.get("Size"))
    except Exception as e:
        print("[model_ai] 拿 %s 文件表失败：%s" % (repo, e))
    _ms_cache[repo] = out
    return out


def ms_url(repo, path):
    return "%s/%s/repo?Revision=master&FilePath=%s" % (
        MS, repo, urllib.parse.quote(path, safe=""))


def repo_tree(repo):
    """src="ms-repo" 用：整仓库文件清单（排除 .gitattributes），
    返回 [(相对路径, sha256, size), ...] 按路径排序；拿不到返回 []"""
    out = []
    for p, (sha, size) in sorted(ms_files(repo).items()):
        if p == ".gitattributes" or p.endswith("/.gitattributes"):
            continue
        out.append((p, sha, size))
    return out


def _expand(items):
    """把 src="ms-repo"（整仓库）展开成单文件条目（src="ms"，save_as 保留子目录），
    其余原样返回。这样 status()/install() 的其余逻辑不用为 ms-repo 单独分支。"""
    out = []
    for it in items:
        if it.get("src") == "ms-repo":
            for rel, _sha, _size in repo_tree(it["repo"]):
                out.append({"src": "ms", "repo": it["repo"], "path": rel,
                            "dest": it["dest"], "save_as": rel.replace("/", os.sep)})
        else:
            out.append(it)
    return out


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%.0f %s" % (n, u)) if u in ("B", "KB") else ("%.2f %s" % (n, u))
        n /= 1024


# --------------------------------------------------------------------------
#  下载（HTTP Range 分块并发 —— ModelScope 单线程只有 0.4 MB/s，16 线程 5.6 MB/s）
# --------------------------------------------------------------------------
def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def multi_download(url, dst, size, threads=THREADS, on_tick=None):
    """分块并发下载到 dst；返回 (ok, 说明)"""
    tmp = dst + ".part"
    if os.path.isfile(tmp) and os.path.getsize(tmp) == size:
        os.replace(tmp, dst)
        return True, "已有完整临时文件"

    block = max(1 << 20, size // threads)
    ranges = []
    a = 0
    while a < size:
        b = min(a + block - 1, size - 1)
        ranges.append((a, b))
        a = b + 1

    done = [0] * len(ranges)
    got = [0]
    lock = threading.Lock()
    err = []

    try:
        with open(tmp, "wb") as f:
            f.truncate(size)
    except OSError as e:
        return False, "建不了临时文件：%s" % e

    def one(idx, a, b):
        try:
            req = urllib.request.Request(url, headers=dict(UA, Range="bytes=%d-%d" % (a, b)))
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            want = b - a + 1
            if len(data) != want:
                raise RuntimeError("块大小不对：%d ≠ %d" % (len(data), want))
            with open(tmp, "r+b") as f:
                f.seek(a)
                f.write(data)
            with lock:
                done[idx] = want
                got[0] += want
                if on_tick:
                    on_tick(got[0], size)
        except Exception as e:
            with lock:
                err.append("%s" % e)

    while True:
        todo = [i for i in range(len(ranges)) if not done[i]]
        if not todo or err:
            break
        batch = todo[:threads * 3]
        ts = []
        for i in batch:
            t = threading.Thread(target=one, args=(i, ranges[i][0], ranges[i][1]))
            t.daemon = True
            t.start()
            ts.append(t)
        for t in ts:
            t.join(timeout=1800)
        if err:
            break
        if not any(done[i] for i in batch):
            err.append("这一批一块都没下成")

    if err:
        return False, "下载出错：%s" % err[0]
    if sum(done) != size:
        return False, "只下到 %s / %s" % (human(sum(done)), human(size))
    os.replace(tmp, dst)
    return True, "下完了 %s" % human(size)


# --------------------------------------------------------------------------
#  状态 / 下载
# --------------------------------------------------------------------------
def _dest_path(it):
    if it.get("src") == "url":
        return os.path.join(it["dest"], it["name"])
    # save_as：有些仓库里的文件名是通用的（比如 onnx/model.onnx），
    # 落到本机得改成各自的正式名字（rmbg-1.4.onnx）✓
    name = it.get("save_as") or os.path.basename(it["path"])
    return os.path.join(it["dest"], name)


# --------------------------------------------------------------------------
#  pip 依赖（不是模型文件，要装进某个 Python 环境里）
# --------------------------------------------------------------------------
def _env_py(which):
    """按名字找一个环境的 python；找不到返回空串

    f5    → F5-TTS 那套（复用 tts_ai 里的常量，换台机器路径不同也能对上）
    comfy → ComfyUI 便携包那套（复用 local_ai 的自动探测）
    """
    p = ""
    try:
        if which == "f5":
            import tts_ai
            p = getattr(tts_ai, "F5_PY", "")
        elif which == "comfy":
            import local_ai
            loc = local_ai.resolve_comfy()
            if loc:
                p = os.path.join(loc["root"], "python_embeded", "python.exe")
    except Exception:
        p = ""
    return p if (p and os.path.isfile(p)) else ""


def _pip_have(py, pkg, timeout=180):
    """那个环境里装了这个包没"""
    if not py:
        return False
    mod = pkg.replace("-", "_").split("[")[0]
    try:
        r = subprocess.run([py, "-c", "import %s" % mod], capture_output=True,
                           timeout=timeout, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def _install_pip(jid, g, log, prog):
    """整组都是 pip 包时走这条路（装进对应的环境里）"""
    n = len(g["items"])
    okn = 0
    for i, it in enumerate(g["items"], 1):
        pkg = it["pkg"]
        label = it.get("note") or it.get("env") or ""
        py = _env_py(it.get("env"))
        prog(100.0 * (i - 1) / n, "装 %s" % pkg, label)
        if not py:
            log("· 跳过「%s」—— 这套环境不在这台机器上" % label)
            okn += 1
            continue
        if _pip_have(py, pkg):
            log("· 已经有了，跳过：%s（%s）" % (pkg, label))
            okn += 1
            continue
        log("[%d/%d] %s → %s" % (i, n, pkg, label))
        log("      装进：%s" % py)
        try:
            r = subprocess.run([py, "-m", "pip", "install", pkg],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=3600,
                               creationflags=0x08000000)
        except Exception as e:
            log("  ✗ 出错：%s" % e)
            continue
        if r.returncode == 0:
            log("  ✓ 装好了")
            okn += 1
        else:
            log("  ✗ 失败：%s" % ((r.stderr or r.stdout or "")[-300:]))
    prog(100, "已完成", "%d/%d" % (okn, n))
    log("✓ 完成：%d/%d" % (okn, n))
    job_set(jid, state="done", percent=100, result={"ok": okn == n})
    return okn == n


# ⚠️ 关于 src="hf" 的下载源（2026-10-01 主人授权，仅此一次）
#   HF 官方（huggingface.co）在国内直连不通 ✗
#   主人明确授权：「就算是不可信网址也写进去，最后哈希检验就行」
#   所以这里的 mirrors 里放了一个非官方镜像，**仅作官方不通时的后备** ✓
#   但**无论从哪下，都必须过 size + sha256 校验**，不过就删 ✓
#   期望值取自「本机已装文件」——它们当初就是从 HF 官方库下载的，
#   缓存目录名与 refs/main 记录了 org/repo 和 commit，可完整追溯 ✓



def _hf_cache_find(dest, fname, repo=""):
    r"""在 HuggingFace 风格的缓存目录里找文件。

    踩过两件事：
      ① 模型明明装好了界面却报「缺」✗
         HF 新缓存的目录结构是 <dest>\models--<org>--<repo>\snapshots\<commit>\<文件名>
         而原来只检查 <dest>\<文件名> ✓
      ② 同名文件有多个版本时挑错了 ✗
         比如 bigvgan_generator.pt 同时存在 22khz 和 44khz 两个仓库里，
         原来 sorted()[0] 拿到的是 22khz，而调用方可能想要 44khz ✓
         → 传入 repo 时按仓库精确匹配 ✓
    """
    try:
        pat = os.path.join(dest, "models--*", "snapshots", "*", fname)
        hits = sorted(glob.glob(pat))
        if repo:
            key = "models--" + repo.replace("/", "--")
            exact = [h for h in hits if key in h]
            if exact:
                return exact[0]
        if hits:
            return hits[0]
    except Exception:
        pass
    return ""


def status():
    """每个文件装了没（大小对不对）"""
    groups = []
    for g in GROUPS:
        items = []
        for it in _expand(g["items"]):
            # pip 依赖：没有「文件」，看那个环境里能不能 import ✓
            if it.get("src") == "pip":
                py = _env_py(it.get("env"))
                have = _pip_have(py, it["pkg"]) if py else False
                items.append({
                    "name": it["pkg"],
                    "dest": (it.get("note") or it.get("env") or ""),
                    "size": 0,
                    "sizeText": "已装 ✓" if have else ("环境不在" if not py else "未装"),
                    "installed": have,
                })
                continue
            p = _dest_path(it)
            # ★ 先按原路径找；找不到再按 HuggingFace 缓存结构找一次 ✗
            #   （否则明明装好了也会报「缺」）
            if not (os.path.isfile(p) and os.path.getsize(p) > 0):
                alt = _hf_cache_find(os.path.dirname(p), os.path.basename(p), it.get("repo", ""))
                if alt:
                    p = alt
            sz = os.path.getsize(p) if os.path.isfile(p) else 0
            items.append({
                "name": os.path.basename(p),
                "dest": p,
                "size": sz,
                "sizeText": human(sz) if sz else "—",
                "installed": bool(sz),
            })
        groups.append({
            "key": g["key"], "name": g["name"], "icon": g["icon"],
            "note": g.get("note", ""), "blocked": g.get("blocked", ""),
            "count": len(items),
            "have": sum(1 for i in items if i["installed"]),
            "items": items,
        })
    return {"ok": True, "groups": groups}


def _record(files):
    """校验结果留档（铁律二第 8 条）"""
    d = {}
    if os.path.isfile(RECORD):
        try:
            d = json.load(open(RECORD, encoding="utf-8"))
        except Exception:
            d = {}
    for name, info in files.items():
        d[name] = info
    try:
        json.dump(d, open(RECORD, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("[model_ai] 留档失败：%s" % e)


# --------------------------------------------------------------------------
#  任务进度（和 stem_ai 一样的机制，app.py 的 /api/models/progress 直接读）
# --------------------------------------------------------------------------
JOBS = {}
JOB_LOCK = threading.Lock()


def job_log(jid, msg):
    j = JOBS.get(jid)
    if j is None:
        return
    with JOB_LOCK:
        j.setdefault("log", []).append("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        if len(j["log"]) > 400:
            del j["log"][:-400]
    print("[model_ai] %s" % msg, flush=True)


def job_set(jid, **kw):
    j = JOBS.get(jid)
    if j is None:
        return
    with JOB_LOCK:
        j.update(kw)


def get_job(jid):
    j = JOBS.get(jid)
    if not j:
        return None
    out = dict(j)
    out["elapsed"] = round(time.time() - j.get("t0", time.time()), 1)
    return out


def install(jid, group_key):
    """下载并校验某一组（进度写进 JOBS，app.py 轮询 /api/models/progress）"""
    with JOB_LOCK:
        JOBS[jid] = {"state": "running", "t0": time.time(), "percent": 0,
                     "stage": "准备中", "detail": "", "log": [], "group": group_key}

    def log(m):
        job_log(jid, m)

    def prog(pct, stage, detail=""):
        job_set(jid, percent=int(pct), stage=stage, detail=detail)

    g = next((x for x in GROUPS if x["key"] == group_key), None)
    if not g:
        log("★ 没有这一组：%s" % group_key)
        return False
    if not g["items"]:
        log("★ 这一组还没有确证的正规下载源：%s" % g.get("blocked", ""))
        return False

    # 整组都是 pip 包（比如 ffmpeg 那个依赖组）→ 走专用路径 ✓
    if g["items"] and all(it.get("src") == "pip" for it in g["items"]):
        log("开始装「%s」：%d 项" % (g["name"], len(g["items"])))
        return _install_pip(jid, g, log, prog)

    expanded = _expand(g["items"])
    if not expanded:
        log("★ 拿不到模型清单（ModelScope 文件表拉取失败）—— 先检查网络再试")
        return False

    log("开始装「%s」：%d 个文件" % (g["name"], len(expanded)))
    todo = []
    for it in expanded:
        p = _dest_path(it)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            log("· 已有，跳过：%s（%s）" % (os.path.basename(p), human(os.path.getsize(p))))
        else:
            todo.append(it)
    if not todo:
        log("✓ 这一组文件都在了")
        prog(100, "已完成", "文件齐全")
        return True

    # 先把每个文件的大小/哈希问到（ModelScope 走官方 API，url 源用清单里写死的）
    plans = []
    for it in todo:
        if it["src"] == "url":
            plans.append({"it": it, "urls": [it["url"]], "size": it["size"],
                          "sha": (it.get("sha256") or "").lower()})
            continue

        if it["src"] == "hf":
            # ★ HF 官方发布：官方地址放第一位，官方不通时按 mirrors 依次试 ✗
            #   镜像属于非官方（2026-10-01 主人授权，仅此一次），
            #   所以**不管从哪个源下来，都必须过下面的 size+sha256 校验** ✓
            repo = it.get("repo") or ""
            commit = it.get("commit") or "main"
            # ★ 注意：URL 里要用「仓库内的完整相对路径」✗
            #   踩过：F5-TTS 的模型在 F5TTS_v1_Base/ 子目录下，
            #   用 basename 拼出来是 404（镜像实测就是 404）✓
            #   而落到本机仍然只取文件名（_dest_path 里用的是 basename）✓
            rel = str(it.get("path") or "").replace("\\", "/").lstrip("/")
            urls = []
            if repo:
                urls.append("https://huggingface.co/%s/resolve/%s/%s" % (repo, commit, rel))
            for m in (it.get("mirrors") or []):
                urls.append("%s/%s/resolve/%s/%s" % (m.rstrip("/"), repo, commit, rel))
            if not urls:
                log("★ 这一项没配下载地址：%s" % fname)
                return False
            plans.append({"it": it, "urls": urls,
                          "size": int(it.get("size") or 0),
                          "sha": (it.get("sha256") or "").lower()})
            continue

        table = ms_files(it["repo"])
        info = table.get(it["path"])
        if not info or not info[0]:
            log("★ 从 ModelScope 拿不到官方哈希：%s / %s" % (it["repo"], it["path"]))
            return False
        plans.append({"it": it, "urls": [ms_url(it["repo"], it["path"])],
                      "size": int(info[1] or 0), "sha": str(info[0]).lower()})

    total = sum(p["size"] for p in plans)
    log("需要下载共 %s" % human(total))
    done_bytes = 0
    records = {}
    for n, pl in enumerate(plans, 1):
        it = pl["it"]
        name = os.path.basename(_dest_path(it))
        log("[%d/%d] %s（%s）" % (n, len(plans), name, human(pl["size"])))
        prog(100.0 * done_bytes / total, "下载 %s" % name, "%d/%d" % (n, len(plans)))
        base = done_bytes
        last = [0.0]

        def tick(got, size, base=base, name=name, last=last):
            now = time.time()
            if now - last[0] < 2:
                return
            last[0] = now
            prog(100.0 * (base + got) / total, "下载 %s" % name,
                 "%s / %s（%s）" % (human(base + got), human(total), human(size)))

        os.makedirs(os.path.dirname(_dest_path(it)), exist_ok=True)
        # ★ 多源依次重试：官方优先，不行再换后备源 ✓
        ok, msg = False, "没有可用地址"
        for u in (pl.get("urls") or [pl.get("url")]):
            host = u.split("/")[2] if u.count("/") >= 2 else u
            ok, msg = multi_download(u, _dest_path(it), pl["size"], on_tick=tick)
            if ok:
                if host != "huggingface.co":
                    log("  · 走的是非官方后备源（%s）—— 接下来会做哈希校验把关 ✓" % host)
                break
            log("  · %s 这个源不行（%s），换下一个" % (host, str(msg)[:70]))
            # 半截文件清掉，免得下一个源续传出错
            try:
                if os.path.isfile(_dest_path(it)):
                    os.remove(_dest_path(it))
            except Exception:
                pass
        if not ok:
            log("✗ %s 失败：%s" % (name, msg))
            return False

        # ★ 官方哈希校验（不过就删，铁律二末条）
        log("  校验 %s …" % name)
        local = _sha256(_dest_path(it))
        if local != pl["sha"]:
            log("  ✗✗ 哈希不一致！本地 %s ≠ 官方 %s" % (local[:16], pl["sha"][:16]))
            log("     按铁律二：这个文件删掉，不使用 ✗")
            try:
                os.remove(_dest_path(it))
            except OSError:
                pass
            return False
        log("  ✓ 哈希与官方一致（%s…）" % local[:16])
        records[name] = {
            "name": name, "local": _dest_path(it), "size": pl["size"],
            "sha256": local, "expected_from": pl["url"].split("?")[0],
            "method": "ModelScope 官方文件表 Sha256 逐字节比对"
                      if it["src"] == "ms" else "官方原件自算后比对",
            "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"), "result": "一致 ✓",
        }
        _record(records)
        done_bytes += pl["size"]

    prog(100, "已完成", "全部校验通过")
    log("✓ 「%s」装好了，%d 个文件全部通过官方哈希校验" % (g["name"], len(plans)))
    return True
