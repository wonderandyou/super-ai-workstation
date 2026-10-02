#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— 校园网一键登录（工具 1）
==============================================
校园网 · 深澜 Srun 认证
门户：http://10.30.4.3   自助服务：http://10.30.4.3:8800

原理：get_challenge 拿挑战码 → 深澜 xEncode（XXTEA 变体）加密账号密码 →
      算 sha1 签名 chksum → 调 srun_portal 登录。
      全程用你自己的账号，只是把「打开网页→输密码→点登录」自动化，不绕过任何认证。

注意两个深澜特有的坑（都在下面代码里标注了）：
  1. {SRBX1} 用的不是标准 base64，而是置换过字母表的版本
  2. xEncode 的 key 模式必须把原文长度追加到整数数组末尾
"""

import base64  # noqa: F401  (保留：便于排查时对照标准 base64)
import ctypes
import hashlib
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

DEFAULTS = {
    "campusPortal": "http://10.30.4.3",
    "campusUser": "",
    "campusPassword": "",
    "campusAcId": "4",
    "campusDomain": "@xsdianxin",
}
DOMAINS = [
    {"v": "@xsdianxin", "t": "学生 · 中国电信"},
    {"v": "@xsyidong", "t": "学生 · 中国移动"},
    {"v": "@xuesheng", "t": "学生 · 通用"},
    {"v": "@internet", "t": "教师"},
]

LOG = []


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    LOG.append(line)
    if len(LOG) > 200:
        del LOG[:60]
    return line


def cfg_from(cfg):
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = (cfg.get(k) or v)
    out["campusPortal"] = str(out["campusPortal"]).rstrip("/")
    return out


# --------------------------------------------------------------------------
#  深澜 xEncode（XXTEA 变体）+ 深澜自己的 base64 字母表
# --------------------------------------------------------------------------
# ⚠️ 坑 1：{SRBX1} 用的不是标准 base64，而是这个置换过字母表的版本。
#          用标准 base64 服务端会返回 auth_info_error。
SRUN_B64 = 'LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA'


def srun_b64(data):
    out = []
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        b = list(chunk) + [0] * (3 - len(chunk))
        out.append(SRUN_B64[b[0] >> 2])
        out.append(SRUN_B64[((b[0] & 3) << 4) | (b[1] >> 4)])
        out.append(SRUN_B64[((b[1] & 15) << 2) | (b[2] >> 6)] if len(chunk) > 1 else '=')
        out.append(SRUN_B64[b[2] & 63] if len(chunk) > 2 else '=')
    return ''.join(out)


def _ord_at(msg, idx):
    return ord(msg[idx]) if len(msg) > idx else 0


def _sencode(msg, key):
    """按 4 字节小端打包成 int 数组。
    ⚠️ 坑 2：key=True 时【必须把原文长度追加到末尾】，漏了会返回 auth_info_error。"""
    l = len(msg)
    out = []
    for i in range(0, l, 4):
        out.append(_ord_at(msg, i) | _ord_at(msg, i + 1) << 8 |
                   _ord_at(msg, i + 2) << 16 | _ord_at(msg, i + 3) << 24)
    if key:
        out.append(l)
    return out


def _lencode(msg, key):
    l = len(msg)
    ll = (l - 1) << 2
    if key:
        m = msg[l - 1]
        if m < ll - 3 or m > ll:
            return ''
        ll = m
    for i in range(0, l):
        msg[i] = (chr(msg[i] & 0xff) + chr(msg[i] >> 8 & 0xff) +
                  chr(msg[i] >> 16 & 0xff) + chr(msg[i] >> 24 & 0xff))
    return ''.join(msg)[0:ll] if key else ''.join(msg)


def xencode(msg, key):
    """深澜 xEncode（XXTEA 变体）"""
    if msg == '':
        return ''
    pwd = _sencode(msg, True)
    pwdk = _sencode(key, False)
    if len(pwdk) < 4:
        pwdk = pwdk + [0] * (4 - len(pwdk))
    n = len(pwd) - 1
    z, y = pwd[n], pwd[0]
    c = 0x86014019 | 0x183639A0
    m = e = p = d = 0
    q = int(6 + 52 / (n + 1))
    while q > 0:
        q -= 1
        d = (d + c) & 0xFFFFFFFF
        e = d >> 2 & 3
        p = 0
        while p < n:
            y = pwd[p + 1]
            m = z >> 5 ^ y << 2
            m = m + ((y >> 3 ^ z << 4) ^ (d ^ y))
            m = m + (pwdk[(p & 3) ^ e] ^ z)
            pwd[p] = (pwd[p] + m) & 0xFFFFFFFF
            z = pwd[p]
            p += 1
        y = pwd[0]
        m = z >> 5 ^ y << 2
        m = m + ((y >> 3 ^ z << 4) ^ (d ^ y))
        m = m + (pwdk[(p & 3) ^ e] ^ z)
        pwd[n] = (pwd[n] + m) & 0xFFFFFFFF
        z = pwd[n]
    return _lencode(pwd, False)


# --------------------------------------------------------------------------
#  网络小工具
# --------------------------------------------------------------------------
def http(url, data=None, timeout=10):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def jsonp(txt):
    i, j = txt.find('('), txt.rfind(')')
    if i >= 0 and j > i:
        try:
            return json.loads(txt[i + 1:j])
        except Exception:
            pass
    try:
        return json.loads(txt)
    except Exception:
        return {'raw': txt[:200]}


def local_ip(portal):
    host = urllib.parse.urlparse(portal).hostname or '10.30.4.3'
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 80))
        return s.getsockname()[0]
    except Exception:
        return ''
    finally:
        s.close()


def internet_ok(timeout=6):
    """用 Windows 自己的联网探测判断是否真能上网（未认证时会被门户劫持）"""
    try:
        return 'Microsoft Connect Test' in http(
            'http://www.msftconnecttest.com/connecttest.txt', timeout=timeout)
    except Exception:
        return False


def portal_status(cfg, ip):
    cb = 'cb%d' % int(time.time() * 1000)
    u = '%s/cgi-bin/rad_user_info?%s' % (cfg['campusPortal'], urllib.parse.urlencode(
        {'callback': cb, 'ip': ip, '_': int(time.time() * 1000)}))
    try:
        return jsonp(http(u))
    except Exception as e:
        return {'error': 'request_failed', 'msg': str(e)}


# --------------------------------------------------------------------------
#  登录
# --------------------------------------------------------------------------
def do_login(cfg, ip, n=200, type_=1):
    portal = cfg['campusPortal']
    uid = cfg['campusUser']
    pwd = cfg['campusPassword']
    acid = cfg['campusAcId']
    cb = 'jQuery%d' % int(time.time() * 1000)

    # 1) 挑战码
    u = '%s/cgi-bin/get_challenge?%s' % (portal, urllib.parse.urlencode(
        {'callback': cb, 'username': uid, 'ip': ip, '_': int(time.time() * 1000)}))
    ch = jsonp(http(u))
    token = ch.get('challenge')
    if not token:
        return False, ch

    # 2) 加密信息 + 签名
    info = {'username': uid, 'password': pwd, 'ip': ip, 'acid': acid,
            'enc_ver': 'srun_bx1'}
    info_enc = '{SRBX1}' + srun_b64(xencode(
        json.dumps(info, separators=(',', ':')), token).encode('latin-1'))
    hmd5 = hashlib.md5(pwd.encode()).hexdigest()
    chkstr = (token + uid + token + hmd5 + token + acid + token + ip + token +
              str(n) + token + str(type_) + token + info_enc)
    chksum = hashlib.sha1(chkstr.encode()).hexdigest()

    # 3) 提交登录
    params = {
        'callback': cb, 'action': 'login', 'username': uid,
        'password': '{MD5}' + hmd5, 'ac_id': acid, 'ip': ip,
        'chksum': chksum, 'info': info_enc, 'n': str(n), 'type': str(type_),
        'os': 'Windows 10', 'name': 'Windows', 'double_stack': '0',
        '_': int(time.time() * 1000),
    }
    res = jsonp(http('%s/cgi-bin/srun_portal' % portal, data=params, timeout=15))
    err = str(res.get('error', '')).lower()
    suc = str(res.get('suc_msg', '')).lower()
    ok = err in ('ok', 'login_ok') or 'already' in suc or 'success' in suc
    return ok, res


def full_user(cfg):
    """带运营商后缀的完整账号（少了后缀门户会报 E2806 Products is not found）"""
    u = cfg['campusUser']
    d = (cfg.get('campusDomain') or '').strip()
    if d and '@' not in u:
        return u + d
    return u


def status(cfg):
    c = cfg_from(cfg)
    ip = local_ip(c['campusPortal'])
    net = internet_ok()
    st = {}
    portal_ok = False
    if ip:
        st = portal_status(c, ip)
        # ⚠️ 深澜的 rad_user_info 成功时返回 error:"ok"（不是没有 error 字段）
        e = str(st.get('error', '')).lower()
        portal_ok = (e in ('ok', '')) or bool(st.get('user_name'))
    return {
        "ip": ip, "portal": c['campusPortal'], "acId": c['campusAcId'],
        "user": c['campusUser'], "domain": c['campusDomain'],
        "fullUser": full_user(c),
        "configured": bool(c['campusUser'] and c['campusPassword']),
        "passwordSet": bool(c['campusPassword']),
        "internet": net,
        "portalReachable": portal_ok,
        "portalState": ("在线：" + str(st.get('user_name'))) if st.get('user_name')
                       else (str(st.get('error')) if st.get('error') else "未知"),
        "onlineUser": st.get('user_name') or "",
        "onlineIp": st.get('online_ip') or "",
        "domains": DOMAINS,
        "selfService": c['campusPortal'].replace('http://', '') + ':8800',
        "verdict": ("online" if net else ("ready" if (c['campusUser'] and c['campusPassword'])
                                          else "need-setup")),
    }


def login(cfg, force=False):
    """执行一次登录；返回 {ok, steps:[...], internet, message}"""
    LOG.clear()
    c = cfg_from(cfg)
    out = {"steps": [], "ok": False, "message": "", "internetBefore": False,
           "internetAfter": False}

    if not c['campusUser'] or not c['campusPassword']:
        out["message"] = "还没填账号或密码"
        log("❌ 还没填账号或密码")
        out["steps"] = list(LOG)
        return out

    login_user = full_user(c)
    ip = local_ip(c['campusPortal'])
    log("——— 校园网一键登录 ———")
    log("本机 IP=%s   门户=%s   账号=%s" % (ip, c['campusPortal'], login_user))

    st = portal_status(c, ip)
    who = st.get('user_name') or st.get('error') or '未知'
    log("门户侧状态：%s%s" % (who, ("（在线 IP %s）" % st.get('online_ip')) if st.get('online_ip') else ""))

    net0 = internet_ok()
    out["internetBefore"] = net0
    if net0 and not force:
        log("✅ 现在本来就能上网，不用登录")
        out.update(ok=True, internetAfter=True, message="本来就能上网，无需登录")
        out["steps"] = list(LOG)
        return out

    for attempt in range(1, 4):
        log("第 %d 次尝试登录…" % attempt)
        # 临时把后缀拼进账号
        c2 = dict(c)
        c2['campusUser'] = login_user
        ok, res = do_login(c2, ip)
        log("门户返回：error=%s  suc_msg=%s  user_name=%s  online_ip=%s" % (
            res.get('error'), res.get('suc_msg'), res.get('user_name'), res.get('online_ip')))
        log("  （原始返回前 200 字：%s）" % json.dumps(res, ensure_ascii=False)[:200])
        if not ok and 'already' not in json.dumps(res, ensure_ascii=False).lower():
            log("⚠️ 门户说登录没成功，检查账号/密码/是否欠费")
        time.sleep(3)
        if internet_ok():
            log("🎉 登录成功，现在可以上网了")
            out.update(ok=True, internetAfter=True, message="登录成功")
            out["steps"] = list(LOG)
            return out
        if attempt < 3:
            time.sleep(4)

    log("❌ 3 次都没成功：确认账号密码；若提示欠费/锁定，去自助服务 %s:8800 看看"
        % c['campusPortal'].replace('http://', ''))
    out["internetAfter"] = internet_ok()
    out["message"] = "登录失败（3 次重试都没成功）"
    out["steps"] = list(LOG)
    return out


def probe(cfg):
    """只看状态，不登录"""
    return login(cfg, force=False) if False else status(cfg)


# ==========================================================================
#  开机自启（计划任务）
# ==========================================================================
TASK_NAME = "AI工作站-校园网自动登录"
OLD_TASK = "校园网自动登录-巡查"      # 用户之前的那个（每 5 分钟巡查）


def _run_ps(script, timeout=90):
    """跑一段 PowerShell，返回 (ok, 输出)"""
    import subprocess
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == "nt" else 0)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return p.returncode == 0, out
    except Exception as e:
        return False, str(e)


def _pythonw():
    """优先用 pythonw.exe：GUI 子系统，跑起来完全不闪黑框"""
    import sys
    exe = sys.executable or ""
    d = os.path.dirname(exe)
    for name in ("pythonw.exe", "python.exe"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return exe or "pythonw.exe"


def autostart_status():
    q = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
         "if ($t) { 'YES|' + $t.State + '|' + "
         "(($t.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ;; ') + '|' + "
         "(($t.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ',') } else { 'NO' }"
         % TASK_NAME)
    ok, out = _run_ps(q)
    installed = out.startswith("YES")
    info = {"installed": installed, "taskName": TASK_NAME, "raw": out}
    if installed:
        parts = out.split("|", 3)
        info["state"] = parts[1] if len(parts) > 1 else ""
        info["action"] = parts[2] if len(parts) > 2 else ""
        info["triggers"] = parts[3] if len(parts) > 3 else ""
    # 顺带看看那个老任务
    q2 = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
          "if ($t) { 'YES|' + $t.State } else { 'NO' }" % OLD_TASK)
    ok2, out2 = _run_ps(q2)
    info["oldTaskExists"] = out2.startswith("YES")
    info["oldTaskName"] = OLD_TASK
    info["oldTaskState"] = out2.split("|")[1] if "|" in out2 else ""
    return info


def _install_task_ps(runlevel="Limited"):
    py = _pythonw()
    script = os.path.join(ROOT, "campus.py")
    return r"""
$ErrorActionPreference = 'Stop'
$user = "$env:USERDOMAIN\$env:USERNAME"
$A = New-ScheduledTaskAction -Execute '%(py)s' -Argument '"%(script)s" --quiet' -WorkingDirectory '%(wd)s'
$T = New-ScheduledTaskTrigger -AtLogOn -User $user
$T.Delay = 'PT30S'
try {
    $rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
    $T.Repetition = $rep
} catch { }
$S = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
     -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden `
     -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$P = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel %(rl)s
Register-ScheduledTask -TaskName '%(name)s' -Action $A -Trigger $T -Settings $S -Principal $P -Force | Out-Null
$t = Get-ScheduledTask -TaskName '%(name)s'
'OK|' + $t.State
""" % {"py": py.replace("'", "''"), "script": script.replace("'", "''"),
       "wd": ROOT.replace("'", "''"), "name": TASK_NAME, "rl": runlevel}


def install_autostart():
    """
    注册计划任务：登录后 30 秒跑一次，之后每 5 分钟巡查一次（断网自动重连）。

    ⚠️ 坑：注册「最高权限」(-RunLevel Highest) 的任务**必须管理员**，普通用户会拿到
       HRESULT 0x80070005 (Access denied)。而校园网登录只是发 HTTP 请求，
       根本不需要提权，所以这里用 Limited —— 免管理员、直接成功。
    """
    ok, out = _run_ps(_install_task_ps("Limited"), timeout=120)
    if ok and "OK|" in out:
        return {"ok": True,
                "message": "开机自启已开启（登录后 30 秒自动登录，之后每 5 分钟巡查）",
                "pythonw": _pythonw(), "task": TASK_NAME, "runLevel": "Limited"}

    # 万一还是被拒，退一步用 UAC 提权重试一次
    if "denied" in out.lower() or "0x80070005" in out or "拒绝访问" in out:
        ok2, out2 = _run_ps_elevated(_install_task_ps("Limited"), timeout=180)
        if ok2:
            return {"ok": True,
                    "message": "开机自启已开启（经管理员授权创建）",
                    "pythonw": _pythonw(), "task": TASK_NAME, "runLevel": "Limited"}
        return {"ok": False, "error": "创建计划任务被拒绝，且提权失败：" + (out2[-300:] or out[-300:])}

    return {"ok": False, "error": out[-500:] or "注册计划任务失败"}


def _run_ps_elevated(inner_script, timeout=180):
    """把一段 PowerShell 用 UAC 提权跑一次（结果写临时文件回读）"""
    import subprocess
    import tempfile
    out_file = os.path.join(tempfile.gettempdir(), "aiws_task_%s.txt" % uuid.uuid4().hex[:8])
    ps1 = os.path.join(tempfile.gettempdir(), "aiws_task_%s.ps1" % uuid.uuid4().hex[:8])
    try:
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write("$ErrorActionPreference='Stop'\n")
            f.write("try {\n" + inner_script + "\n} catch { 'ERR|' + $_.Exception.Message }\n")
            f.write("} | Out-File -FilePath '%s' -Encoding utf8\n" % out_file)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe",
            '-NoProfile -ExecutionPolicy Bypass -File "%s"' % ps1, None, 0)
        if rc <= 32:
            return False, "用户取消了管理员授权" if rc == 5 else "无法提权（代码 %d）" % rc
        waited = 0.0
        while waited < timeout:
            time.sleep(0.5)
            waited += 0.5
            if os.path.isfile(out_file):
                txt = open(out_file, encoding="utf-8-sig", errors="replace").read()
                return ("OK|" in txt), txt
        return False, "等待提权进程超时"
    except Exception as e:
        return False, str(e)
    finally:
        for p in (ps1, out_file):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass


def remove_autostart():
    q = ("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false -ErrorAction Stop; 'OK'"
         % TASK_NAME)
    ok, out = _run_ps(q, timeout=60)
    if ok and "OK" in out:
        return {"ok": True, "message": "已关闭开机自启"}
    if "cannot find" in out.lower() or "找不到" in out or "No MSFT" in out:
        return {"ok": True, "message": "本来就是关闭状态"}
    return {"ok": False, "error": out[-400:] or "删除计划任务失败"}


def remove_old_task():
    q = ("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false -ErrorAction Stop; 'OK'"
         % OLD_TASK)
    ok, out = _run_ps(q, timeout=60)
    if ok and "OK" in out:
        return {"ok": True, "message": "已删除旧任务「%s」" % OLD_TASK}
    return {"ok": False, "error": out[-400:] or "删除失败"}


if __name__ == "__main__":
    import io
    import sys as _sys
    cfgp = os.path.join(ROOT, "data", "config.json")
    cfg = json.load(io.open(cfgp, encoding="utf-8")) if os.path.isfile(cfgp) else {}
    args = _sys.argv[1:]

    if "--force" in args:
        r = login(cfg, force=True)
        print("\n".join(r["steps"]))
    elif "--quiet" in args:
        # 计划任务用的静默模式：本来能上网就什么都不做（不打印、不写日志）
        try:
            if internet_ok():
                _sys.exit(0)
            r = login(cfg, force=False)
            _sys.exit(0 if r.get("ok") else 1)
        except Exception:
            _sys.exit(2)
    else:
        print(json.dumps(status(cfg), ensure_ascii=False, indent=2))
