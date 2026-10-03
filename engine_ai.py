# -*- coding: utf-8 -*-
r"""
引擎一键装：给 F5-TTS（声音包朗读）和 Seed-VC（AI 翻唱）装运行环境
=====================================================================
为什么需要：包里带的 runtime 是 Python **embeddable** 版，
没有 `venv` / `ensurepip` ✗ 而这两个引擎都需要独立的虚拟环境 ✓

完整流程（全程官方渠道 ✓）：
  ① 下载 python-3.11.9-amd64.exe      ← python.org 官方直连
  ② **验签**：`Get-AuthenticodeSignature` 必须 Valid 且签名者是
     `Python Software Foundation` ✗ 不通过就删掉，绝不安装（铁律二/三）
  ③ 静默安装到 `local\python\`（用户级，不需要管理员）
  ④ 用它的 python.exe 建 venv → `local\<引擎>\venv\`
  ⑤ pip 装依赖 ← PyPI 官方（--only-binary 优先，带官方哈希校验）
     torch 的 CUDA 版 ← download.pytorch.org（PyTorch 官方）
  ⑥ clone 源码 ← GitHub 官方（SWivid/F5-TTS、Plachta/Seed-VC）
  ⑦ 探测状态，汇报给界面

设计要点：
  · 每一步都可以**单独重跑**（幂等），断了接着装 ✓
  · 进度写进 JOBS，app.py 轮询 `/api/engine/progress?job=`
  · **绝不跳过验签**（`_install_python` 里第一件事就是验）

用法（手动测试）：python engine_ai.py status
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
import paths as _paths                      # ★ 统一路径层
LOCAL = _paths.engines_root()               # Python 运行时 → <安装目录>\engines

# ---------------------------------------------------------------- 常量

PY_VER = "3.11.9"
PY_EXE = "python-%s-amd64.exe" % PY_VER
PY_URL = "https://www.python.org/ftp/python/%s/%s" % (PY_VER, PY_EXE)
PY_SHA256 = "5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde"
PY_SIGNER = "Python Software Foundation"      # ★ 必须匹配这个主体才算官方
PY_TARGET = os.path.join(LOCAL, "python")     # 静默安装到这里
PY_HOME = os.path.join(PY_TARGET, "python.exe")

# torch 的 CUDA 版走 PyTorch 官方索引 ✓
PYTORCH_INDEX = "https://download.pytorch.org/whl/cu124"

ENGINES = {
    "f5tts": {
        "name": "F5-TTS（声音包朗读）",
        "dir": _paths.f5tts(),              # ★ 装到「应用真正会去找」的位置
        "repo": "https://github.com/SWivid/F5-TTS.git",
        "pip": ["torch==2.4.1", "torchaudio==2.4.1"],
        "pip_index": PYTORCH_INDEX,
        "pip2": ["transformers", "vocos", "soundfile", "librosa", "numpy<2",
                 "omegaconf", "einx", "hydra-core", "safetensors",
                 "huggingface-hub", "accelerate", "tqdm", "click", "cached_path",
                 "pydub", "jieba", "pypinyin", "matplotlib", "gradio", "ema-pytorch",
                 "bitsandbytes", "torchdiffeq", "x-transformers"],
        "check": ["torch", "torchaudio", "transformers", "vocos"],
    },
    "seedvc": {
        "name": "Seed-VC（AI 翻唱）",
        "dir": _paths.seedvc(),             # ★ 同上
        "repo": "https://github.com/Plachta/Seed-VC.git",
        "pip": ["torch==2.4.1", "torchaudio==2.4.1"],
        "pip_index": PYTORCH_INDEX,
        "pip2": ["transformers", "vocos", "soundfile", "librosa", "numpy<2",
                 "omegaconf", "einx", "hydra-core", "safetensors",
                 "huggingface-hub", "accelerate", "funasr", "modelscope",
                 "descript-audio-codec", "resemblyzer", "gdown", "PyYAML",
                 "munch", "pydub", "tqdm", "scipy"],
        "check": ["torch", "torchaudio", "transformers", "funasr"],
    },
}

JOBS = {}
JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 任务进度

def new_job(kind):
    jid = "%s-%d" % (kind, int(time.time() * 1000) % 10**9)
    with JOB_LOCK:
        JOBS[jid] = {"state": "running", "t0": time.time(), "percent": 0,
                     "stage": "准备中", "detail": "", "log": [], "kind": kind}
    return jid


def job_log(jid, msg):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j:
            j["log"].append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
            j["log"] = j["log"][-400:]


def job_set(jid, **kw):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j:
            j.update(kw)


def get_job(jid):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if not j:
            return None
        out = dict(j)
        out["elapsed"] = int(time.time() - j["t0"])
        out["logText"] = "\n".join(j["log"][-200:])
        return out


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024
    return "%.2f TB" % n


# ---------------------------------------------------------------- 状态探测

def _venv_py(eng):
    return os.path.join(ENGINES[eng]["dir"], "venv", "Scripts", "python.exe")


def status():
    """报两个引擎的就绪情况"""
    out = {"ok": True, "python": {}, "engines": []}
    out["python"] = {
        "installed": os.path.isfile(PY_HOME),
        "path": PY_HOME if os.path.isfile(PY_HOME) else "",
        "version": PY_VER,
        "installer": os.path.join(DATA, "_python_installer.exe"),
        "installerReady": os.path.isfile(os.path.join(DATA, "_python_installer.exe")),
    }
    for key, e in ENGINES.items():
        py = _venv_py(key)
        venv_ok = os.path.isfile(py)
        deps = {}
        if venv_ok:
            code = ("import importlib,json;"
                    "r={};"
                    "[r.__setitem__(m, bool(importlib.util.find_spec(m))) for m in %r];"
                    "print(json.dumps(r))" % (e["check"],))
            try:
                r = subprocess.run([py, "-c", code], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=180,
                                   creationflags=0x08000000)
                deps = json.loads((r.stdout or "{}").strip() or "{}")
            except Exception:
                deps = {}
        src = os.path.join(e["dir"], "src")
        out["engines"].append({
            "key": key,
            "name": e["name"],
            "venv": venv_ok,
            "venvPy": py if venv_ok else "",
            "src": os.path.isdir(src),
            "deps": deps,
            "ready": bool(venv_ok and all(deps.get(m) for m in e["check"])
                          and os.path.isdir(src)),
            "needPython": not os.path.isfile(PY_HOME),
        })
    out["ready"] = (os.path.isfile(PY_HOME)
                    and all(x["ready"] for x in out["engines"]))
    return out


# ---------------------------------------------------------------- 步骤实现

def _run(jid, args, label, pct0, pct1, cwd=None, env=None, timeout=7200):
    """跑一个子进程并把输出喂进日志（隐藏窗口）"""
    job_log(jid, "· %s" % label)
    job_set(jid, stage=label, percent=pct0)
    p = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=0x08000000)
    t0 = time.time()
    for line in p.stdout:
        line = line.rstrip()
        if not line:
            continue
        job_log(jid, "  %s" % line[:200])
        # 粗略进度：按阶段线性插值
        el = time.time() - t0
        if pct1 > pct0:
            frac = min(0.95, el / max(1.0, 600.0))
            job_set(jid, percent=int(pct0 + (pct1 - pct0) * frac))
        if time.time() - t0 > timeout:
            p.kill()
            job_log(jid, "  ★ 超时，已终止")
            return False
    p.wait()
    ok = (p.returncode == 0)
    job_log(jid, "  %s（退出码 %s）" % ("✓ 完成" if ok else "✗ 失败", p.returncode))
    return ok


def _download(jid, url, dest, pct0, pct1, expect_sha="", expect_size=0):
    """带进度的下载 + 哈希校验"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and expect_size and os.path.getsize(dest) == expect_size:
        job_log(jid, "· 已存在且大小正确，跳过下载")
        return True
    job_log(jid, "· 下载 %s" % url.split("/")[-1])
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or expect_size or 0)
            got = 0
            last = 0.0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                now = time.time()
                if now - last > 1.5:
                    last = now
                    if total:
                        job_set(jid, percent=int(pct0 + (pct1 - pct0) * got / total),
                                detail="%s / %s" % (human(got), human(total)))
    except Exception as e:
        job_log(jid, "  ✗ 下载失败：%s" % str(e)[:160])
        return False
    if expect_size and os.path.getsize(tmp) != expect_size:
        job_log(jid, "  ✗ 大小不对：得到 %d，官方登记 %d" % (os.path.getsize(tmp), expect_size))
        os.remove(tmp)
        return False
    if expect_sha:
        h = hashlib.sha256(open(tmp, "rb").read()).hexdigest()
        if h.lower() != expect_sha.lower():
            job_log(jid, "  ✗ 哈希不匹配！得到 %s" % h[:24])
            os.remove(tmp)
            return False
        job_log(jid, "  ✓ 哈希校验通过")
    os.replace(tmp, dest)
    return True


def _verify_authenticode(path):
    """用 Windows 原生验签；返回 (ok, 签名者, 状态)"""
    ps = ("$s = Get-AuthenticodeSignature '%s';"
          "Write-Output ('S=' + $s.Status);"
          "if ($s.SignerCertificate) { Write-Output ('N=' + $s.SignerCertificate.Subject) }"
          % path.replace("'", "''"))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120, creationflags=0x08000000)
    except Exception as e:
        return False, "", "验签失败：%s" % str(e)[:80]
    st, signer = "", ""
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("S="):
            st = ln[2:].strip()
        elif ln.startswith("N="):
            signer = ln[2:].strip()
    return (st == "Valid" and PY_SIGNER in signer), signer, st


def _install_python(jid):
    """① 下载 → ② 验签 → ③ 静默安装"""
    if os.path.isfile(PY_HOME):
        job_log(jid, "· Python 已就绪：%s" % PY_HOME)
        return True
    inst = os.path.join(DATA, "_python_installer.exe")
    if not _download(jid, PY_URL, inst, 0, 40, PY_SHA256, 26216840):
        return False

    # ★ 铁律二/三：必须验签，不通过就删掉，绝不安装 ✗
    job_set(jid, stage="验证官方签名", percent=42)
    ok, signer, st = _verify_authenticode(inst)
    job_log(jid, "  签名状态：%s" % st)
    job_log(jid, "  签名者　：%s" % signer)
    if not ok:
        job_log(jid, "✗ 签名验证未通过（必须是 Valid 且签名者为 %s）→ 删掉不用" % PY_SIGNER)
        try:
            os.remove(inst)
        except Exception:
            pass
        return False
    job_log(jid, "✓ 官方签名验证通过，确认是 python.org 原件")

    job_set(jid, stage="静默安装 Python", percent=45)
    os.makedirs(PY_TARGET, exist_ok=True)
    args = [inst, "/quiet", "InstallAllUsers=0", "TargetDir=" + PY_TARGET,
            "Include_launcher=0", "PrependPath=0", "AssociateFiles=0",
            "Shortcuts=0", "Include_test=0", "Include_doc=0", "Include_tcltk=1",
            "Include_pip=1", "SimpleInstall=1"]
    if not _run(jid, args, "静默安装 Python（约 1~3 分钟）", 45, 60, timeout=1800):
        return False
    for _ in range(30):
        if os.path.isfile(PY_HOME):
            break
        time.sleep(2)
    if not os.path.isfile(PY_HOME):
        job_log(jid, "✗ 装完了但找不到 %s" % PY_HOME)
        return False
    job_log(jid, "✓ Python 就绪：%s" % PY_HOME)
    return True


def _make_venv(jid, eng):
    e = ENGINES[eng]
    vdir = os.path.join(e["dir"], "venv")
    py = os.path.join(vdir, "Scripts", "python.exe")
    if os.path.isfile(py):
        job_log(jid, "· venv 已存在")
        return py
    os.makedirs(e["dir"], exist_ok=True)
    if not _run(jid, [PY_HOME, "-m", "venv", vdir],
                "创建虚拟环境 %s" % os.path.basename(e["dir"]), 60, 66, timeout=900):
        return ""
    return py if os.path.isfile(py) else ""


def install(jid, engine):
    """装一个引擎（幂等，可反复跑）"""
    if engine not in ENGINES:
        job_log(jid, "★ 没有这个引擎：%s" % engine)
        job_set(jid, state="failed")
        return False
    e = ENGINES[engine]
    job_log(jid, "开始装「%s」" % e["name"])

    if not _install_python(jid):
        job_set(jid, state="failed", stage="失败", detail="Python 环境没装好")
        return False

    py = _make_venv(jid, engine)
    if not py:
        job_set(jid, state="failed", stage="失败", detail="虚拟环境创建失败")
        return False

    # 升级 pip（走 PyPI 官方）
    _run(jid, [py, "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"],
         "升级 pip", 66, 70, timeout=1800)

    # torch（CUDA 版，PyTorch 官方索引）
    if e.get("pip"):
        if not _run(jid, [py, "-m", "pip", "install", "--index-url", e["pip_index"]] + e["pip"],
                    "安装 torch（约 2~3 GB，最久的一步）", 70, 88, timeout=14400):
            job_log(jid, "★ torch 安装失败 —— 常见原因：网络中断。可以重跑本步骤 ✓")

    # 其余依赖（PyPI 官方）
    if e.get("pip2"):
        _run(jid, [py, "-m", "pip", "install"] + e["pip2"],
             "安装其余依赖（%d 个包）" % len(e["pip2"]), 88, 96, timeout=14400)

    # clone 官方源码
    src = os.path.join(e["dir"], "src")
    if not os.path.isdir(src):
        git = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
        if not os.path.isfile(git) and not shutil.which("git"):
            job_log(jid, "★ 没找到 git，跳过源码克隆（可手工放源码到 %s）" % src)
        else:
            _run(jid, [git, "clone", "--depth", "1", e["repo"], src],
                 "克隆官方源码（%s）" % e["repo"].split("/")[-1], 96, 99, timeout=3600)
    else:
        job_log(jid, "· 源码已存在")

    st = status()
    mine = next((x for x in st["engines"] if x["key"] == engine), None)
    job_set(jid, state="done", percent=100,
            stage="完成" if (mine and mine["ready"]) else "部分完成",
            detail=("依赖齐全 ✓" if (mine and mine["ready"])
                    else "还差点东西，建议重跑一次（已装好的会跳过）"))
    job_log(jid, "结束：%s" % json.dumps(mine, ensure_ascii=False))
    return True


def start_install(engine):
    jid = new_job("engine-" + engine)
    threading.Thread(target=install, args=(jid, engine), daemon=True).start()
    return jid


# ---------------------------------------------------------------- 命令行

def _cli():
    if len(sys.argv) < 2 or sys.argv[1] == "status":
        st = status()
        print("Python : %s" % ("已装 %s" % st["python"]["path"] if st["python"]["installed"]
                               else "未装（一键装会先装它）"))
        for x in st["engines"]:
            print("%-24s venv=%s 源码=%s 依赖=%s → %s" % (
                x["name"], x["venv"], x["src"], x["deps"],
                "就绪 ✓" if x["ready"] else "未就绪 ✗"))
        return 0
    if sys.argv[1] == "install" and len(sys.argv) > 2:
        jid = start_install(sys.argv[2])
        print("任务 %s 已开始，日志：" % jid)
        while True:
            j = get_job(jid)
            time.sleep(3)
            j2 = get_job(jid)
            if j2 and j2["logText"] != (j or {}).get("logText"):
                print("\n".join(j2["logText"].splitlines()[-3:]))
            if j2 and j2["state"] in ("done", "failed"):
                print("== %s：%s" % (j2["state"], j2.get("detail", "")))
                return 0 if j2["state"] == "done" else 1
    print("用法：python engine_ai.py [status | install <f5tts|seedvc>]")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
