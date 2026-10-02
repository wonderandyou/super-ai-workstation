#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— DSH（DeepSeek Harness）安装与启动模块
=========================================================
职责：
  1. 检测 DSH 环境：Node / npm / dsh 包 / 启动器 / API Key / 服务是否在线
  2. 一键运行捆绑的 DSH 安装程序（复用 Install-DSH.ps1，静默 + 便携 Node，免管理员）
  3. 保存 DeepSeek API Key（写用户环境变量 DEEPSEEK_API_KEY，DSH 认这个）
  4. 启动 / 停止 dsh web，并检测后端服务是否在线
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
INSTALLER_DIR = os.path.join(ROOT, "dsh", "DSH-Installer")
INSTALL_PS1 = os.path.join(INSTALLER_DIR, "Install-DSH.ps1")
INSTALL_LOG = os.path.join(DATA, "dsh-install.log")
RUN_LOG = os.path.join(DATA, "dsh-run.log")

DEFAULT_PORT = 3080
APIKEY_URL = "https://platform.deepseek.com/api_keys"
PLATFORM_URL = "https://platform.deepseek.com/"

JOBS = {}
JOB_LOCK = threading.Lock()
DSH_PROC = None


def new_job(kind):
    jid = uuid.uuid4().hex[:12]
    with JOB_LOCK:
        JOBS[jid] = {"kind": kind, "state": "running", "t0": time.time(),
                     "stage": "准备中", "percent": 0.0, "detail": "",
                     "result": None, "error": None, "log": []}
    return jid


def job_set(jid, **kw):
    j = JOBS.get(jid)
    if j:
        j.update(kw)


def job_log(jid, msg):
    j = JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + msg)
    if len(j["log"]) > 500:
        del j["log"][:150]
    j["stage"] = msg


# --------------------------------------------------------------------------
#  小工具
# --------------------------------------------------------------------------
def run_cmd(args, timeout=40, cwd=None):
    """跑一条命令，返回 (code, stdout, stderr)"""
    try:
        flags = 0x08000000 if os.name == "nt" else 0
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, creationflags=flags,
                           encoding="utf-8", errors="replace", shell=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return -1, "", str(e)


def which_cmd(name):
    """在 PATH 里找某个命令（cmd 环境）"""
    code, out, _ = run_cmd(["cmd", "/c", "where", name], timeout=15)
    if code == 0 and out:
        return out.splitlines()[0].strip()
    return ""


def npm_prefix():
    """npm 全局目录"""
    for exe in ("npm.cmd", "npm"):
        code, out, _ = run_cmd(["cmd", "/c", exe, "prefix", "-g"], timeout=40)
        if code == 0 and out:
            ln = [x.strip() for x in out.splitlines() if x.strip()]
            if ln:
                return ln[-1]
    return ""


def port_open(port, host="127.0.0.1", timeout=0.8):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        s.close()


def http_probe(port, timeout=3):
    """访问 DSH 首页；返回 (online, statusCode, note)。任何 HTTP 响应都算在线。"""
    for path in ("/", "/api/health", "/health"):
        try:
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (int(port), path),
                                         method="GET",
                                         headers={"User-Agent": "AIWorkstation"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return True, r.status, path
        except urllib.error.HTTPError as e:
            # 401/403 = 服务在跑，只是要令牌
            return True, e.code, path
        except Exception:
            continue
    return (port_open(port), None, "")


AUTH_PAT = r"http://(?:127\.0\.0\.1|localhost):(\d+)/\?token=([A-Za-z0-9_\-]+)"


def find_auth_url(port=None):
    """
    从运行日志里找出带令牌的授权地址。
    DSH 的首页**必须**用这个链接打开 —— 手输 http://127.0.0.1:3080/ 会 401。
    """
    import re as _re
    port = int(port or DEFAULT_PORT)
    pat = _re.compile(r"http://(?:127\.0\.0\.1|localhost):%d/\?token=[A-Za-z0-9_\-]+" % port)
    cands = [RUN_LOG]
    try:
        d = os.path.expanduser("~/.dsh/logs")
        if os.path.isdir(d):
            files = sorted((os.path.join(d, x) for x in os.listdir(d) if os.path.isfile(os.path.join(d, x))),
                           key=os.path.getmtime, reverse=True)[:12]
            cands += files
    except Exception:
        pass
    for p in cands:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                m = pat.findall(f.read())
            if m:
                return m[-1]
        except Exception:
            continue
    return ""


def get_env_key():
    """读用户级 DEEPSEEK_API_KEY"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v, _ = winreg.QueryValueEx(k, "DEEPSEEK_API_KEY")
            return (v or "").strip()
    except Exception:
        return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def set_env_key(key):
    """写用户级 DEEPSEEK_API_KEY（广播 WM_SETTINGCHANGE 让新开的程序立刻生效）"""
    key = (key or "").strip()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DEEPSEEK_API_KEY", 0, winreg.REG_SZ, key)
    except Exception as e:
        return False, "写入注册表失败：%s" % e
    try:
        import ctypes
        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 2000, None)
    except Exception:
        pass
    os.environ["DEEPSEEK_API_KEY"] = key
    return True, "已保存"


def mask(key):
    key = (key or "").strip()
    if not key:
        return "(未设置)"
    if len(key) <= 8:
        return key[0] + "*" * (len(key) - 1)
    return key[:6] + "*" * 8 + key[-4:]


# --------------------------------------------------------------------------
#  环境状态
# --------------------------------------------------------------------------
def status(port=None):
    port = int(port or DEFAULT_PORT)
    st = {
        "node": None, "npm": None, "nodeVersion": "", "npmVersion": "",
        "prefix": "", "dshInstalled": False, "dshVersion": "", "dshCmd": "",
        "launcher": "", "launcherInPkg": "",
        "apiKeySet": False, "apiKeyMask": "(未设置)",
        "port": port, "online": False, "httpCode": None, "probePath": "",
        "installer": os.path.isfile(INSTALL_PS1),
        "installerDir": INSTALLER_DIR,
        "apikeyUrl": APIKEY_URL, "platformUrl": PLATFORM_URL,
        "desktopLnk": os.path.isfile(os.path.join(
            os.path.expanduser("~"), "Desktop", "DSH Web.lnk")),
    }

    st["node"] = which_cmd("node.exe") or which_cmd("node")
    if st["node"]:
        _, v, _ = run_cmd([st["node"], "-v"], timeout=20)
        st["nodeVersion"] = v
    st["npm"] = which_cmd("npm.cmd") or which_cmd("npm")
    if st["npm"]:
        _, v, _ = run_cmd(["cmd", "/c", st["npm"], "-v"], timeout=30)
        st["npmVersion"] = v

    pref = npm_prefix()
    st["prefix"] = pref
    if pref:
        pkg = os.path.join(pref, "node_modules", "@deepseek-ai", "dsh", "package.json")
        if os.path.isfile(pkg):
            try:
                with open(pkg, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                st["dshInstalled"] = True
                st["dshVersion"] = meta.get("version", "")
            except Exception:
                st["dshInstalled"] = True
        st["launcher"] = os.path.join(pref, "dsh-web.cmd") if \
            os.path.isfile(os.path.join(pref, "dsh-web.cmd")) else ""
        inPkg = os.path.join(pref, "node_modules", "@deepseek-ai", "dsh", "dsh-web.cmd")
        st["launcherInPkg"] = inPkg if os.path.isfile(inPkg) else ""

    dsh = which_cmd("dsh.cmd") or which_cmd("dsh")
    if not dsh and pref:
        c = os.path.join(pref, "dsh.cmd")
        if os.path.isfile(c):
            dsh = c
    st["dshCmd"] = dsh
    if dsh and not st["dshInstalled"]:
        st["dshInstalled"] = True

    k = get_env_key()
    st["apiKeySet"] = bool(k)
    st["apiKeyMask"] = mask(k)

    online, code, path = http_probe(port)
    st["online"] = bool(online)
    st["httpCode"] = code
    st["probePath"] = path
    st["authUrl"] = find_auth_url(port) if online else ""

    # 结论
    if not st["node"]:
        st["verdict"] = "need-node"
        st["reason"] = "还没装 Node.js —— 点「一键安装 DSH」会自动装（便携版，不需要管理员）"
    elif not st["dshInstalled"]:
        st["verdict"] = "need-dsh"
        st["reason"] = "Node.js 有了，但 DSH 还没装 —— 点「一键安装 DSH」"
    elif not st["apiKeySet"]:
        st["verdict"] = "need-key"
        st["reason"] = "DSH 装好了，但还没填 DeepSeek API Key"
    elif st["online"]:
        st["verdict"] = "online"
        st["reason"] = "服务在线（端口 %d）" % port
    else:
        st["verdict"] = "ready"
        st["reason"] = "一切就绪 —— 点「启动 DSH」即可"
    return st


# --------------------------------------------------------------------------
#  安装
# --------------------------------------------------------------------------
def install_worker(jid, apikey=""):
    """跑捆绑的 Install-DSH.ps1（静默 + 便携 Node，免管理员）"""
    try:
        if not os.path.isfile(INSTALL_PS1):
            raise RuntimeError("找不到安装脚本：%s" % INSTALL_PS1)
        os.makedirs(DATA, exist_ok=True)
        with open(INSTALL_LOG, "w", encoding="utf-8") as f:
            f.write("===== DSH 安装开始 %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", INSTALL_PS1, "-Silent", "-NodeInstallMode", "Zip"]
        apikey = (apikey or "").strip()
        if apikey:
            args += ["-ApiKey", apikey, "-SaveApiKey"]
            job_log(jid, "已带上 API Key（会写入用户环境变量 DEEPSEEK_API_KEY）")

        job_log(jid, "[1/2] 正在安装（免管理员·便携 Node 模式）…")
        job_set(jid, stage="安装中", percent=5, detail="这一步可能要几分钟")

        flags = 0x08000000 if os.name == "nt" else 0
        logf = open(INSTALL_LOG, "a", encoding="utf-8", errors="replace", buffering=1)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=INSTALLER_DIR, creationflags=flags,
                                encoding="utf-8", errors="replace", bufsize=1)

        t0 = time.time()
        for line in iter(proc.stdout.readline, ""):
            line = (line or "").rstrip()
            if not line:
                continue
            logf.write(line + "\n")
            job_log(jid, line)
            el = time.time() - t0
            job_set(jid, percent=round(min(95, 5 + 90 * (1 - pow(2.718, -el / 120.0))), 1))
        proc.wait()
        logf.close()

        if proc.returncode == 0:
            job_log(jid, "安装脚本返回 0 —— 成功")
            job_set(jid, state="done", percent=100, stage="完成",
                    result={"version": status().get("dshVersion", "")})
        else:
            raise RuntimeError("安装脚本返回 %d，详见日志" % proc.returncode)
    except Exception as e:
        job_set(jid, state="error", error=str(e))
        job_log(jid, "失败：" + str(e))


# --------------------------------------------------------------------------
#  启动 / 停止 dsh web
# --------------------------------------------------------------------------
def start_dsh(port=None):
    global DSH_PROC
    port = int(port or DEFAULT_PORT)
    online, code, _ = http_probe(port)
    if online:
        return {"ok": True, "already": True,
                "message": "DSH 服务已经在运行（端口 %d，HTTP %s）" % (port, code)}
    # 端口被占但还没响应 HTTP —— 多半是上一个实例正在启动中，别再起一个
    if port_open(port):
        return {"ok": True, "already": True, "starting": True,
                "message": "端口 %d 已被占用（DSH 可能正在启动中），请稍等再点「重新检测」" % port}

    st = status(port)
    if not st["dshInstalled"]:
        return {"ok": False, "error": "还没安装 DSH —— 先点上面的「一键安装 DSH」"}

    # 优先用 installer 注入的启动器；否则直接调 dsh.cmd
    cmd = st["launcher"] or st["dshCmd"]
    if not cmd:
        return {"ok": False, "error": "找不到 dsh 启动器，请重新安装 DSH"}

    os.makedirs(DATA, exist_ok=True)
    logf = open(RUN_LOG, "a", encoding="utf-8", errors="replace", buffering=1)
    logf.write("\n===== dsh web 启动 %s  端口 %d =====\n"
               % (time.strftime("%Y-%m-%d %H:%M:%S"), port))
    env = dict(os.environ)
    k = get_env_key()
    if k:
        env["DEEPSEEK_API_KEY"] = k
    flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0

    # ⚠️ dsh-web.cmd 的逻辑是：只要传了参数，它就认为参数「已给全」，
    #    会丢掉内置的 `web` 子命令（导致 "error: --profile <name> is required"）。
    #    所以：默认端口时【不带参数】调它；要指定端口时直接调 dsh.cmd web --port N。
    if st["launcher"] and port == DEFAULT_PORT:
        args = ["cmd", "/c", st["launcher"]]
    else:
        target = st["dshCmd"] or st["launcher"]
        args = ["cmd", "/c", target, "web", "--port", str(port)]

    try:
        DSH_PROC = subprocess.Popen(
            args,
            cwd=os.path.dirname(st["launcher"] or st["dshCmd"]) or ROOT,
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=env, creationflags=flags)
    except Exception as e:
        return {"ok": False, "error": "启动失败：%s" % e}
    return {"ok": True, "pid": DSH_PROC.pid, "message": "已启动，正在等待服务就绪…"}


def stop_dsh(port=None):
    global DSH_PROC
    port = int(port or DEFAULT_PORT)
    killed = []
    if DSH_PROC and DSH_PROC.poll() is None:
        try:
            DSH_PROC.terminate()
            killed.append(DSH_PROC.pid)
        except Exception:
            pass
    # 兜底：按端口找进程
    try:
        code, out, _ = run_cmd(["cmd", "/c", "netstat -ano -p tcp"], timeout=25)
        pids = set()
        for ln in (out or "").splitlines():
            if (":%d " % port) in ln and "LISTENING" in ln.upper():
                m = re.search(r"(\d+)\s*$", ln.strip())
                if m:
                    pids.add(m.group(1))
        for pid in pids:
            run_cmd(["taskkill", "/F", "/PID", pid], timeout=15)
            killed.append(int(pid))
    except Exception:
        pass
    return {"ok": True, "killed": killed}
