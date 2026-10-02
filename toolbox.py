#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大型 AI 工作站 —— 电脑工具百宝箱
=====================================
两个工具：

1) 内存释放
   执行前记一次可用内存，跑完再记一次，差值就是本次释放量（实测，不估算）。
   调用的是 Windows 标准 API：
     · EmptyWorkingSet            逐个进程回收工作集（psapi）
     · SetSystemFileCacheSize     收缩系统文件缓存（kernel32）
     · NtSetSystemInformation     清空待机列表 / 系统级工作集（ntdll，需要提权）
   ⚠️ 说明：这类操作会让「可用内存」数字变好看，但被换出的页面之后还会被读回，
      对日常使用提升有限。真正的用处是「准备跑吃内存的大程序之前先腾地方」。

2) 多线程自定义下载器
   标准 HTTP Range 分块 + 多线程并发 + 断点续传 + 实时速度/ETA。
"""

import os
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

DL_JOBS = {}
DL_LOCK = threading.Lock()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ==========================================================================
#  一、内存释放
# ==========================================================================
def _mem():
    """返回 (总内存, 可用内存)，单位字节"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys, st.ullAvailPhys, st.dwMemoryLoad
    except Exception:
        return 0, 0, 0


def memory_status():
    total, avail, load = _mem()
    return {
        "total": total, "available": avail, "used": max(0, total - avail),
        "loadPercent": load,
        "totalText": human(total), "availableText": human(avail),
        "usedText": human(max(0, total - avail)),
    }


def _enable_privilege(name):
    """启用一个特权（清空待机列表需要 SeProfileSingleProcessPrivilege）"""
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    # —— 必须声明 argtypes，否则 64 位下 byref(HANDLE) 会被传错 ——
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                             ctypes.POINTER(LUID)]
    advapi.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    advapi.AdjustTokenPrivileges.restype = wintypes.BOOL

    TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x0020, 0x0008
    SE_PRIVILEGE_ENABLED = 0x0002
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel32.GetCurrentProcess(),
                                   TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                   ctypes.byref(token)):
        return False, "OpenProcessToken 失败（错误 %d）" % ctypes.get_last_error()
    try:
        luid = LUID()
        if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False, "找不到特权 " + name
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        if not advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            return False, "AdjustTokenPrivileges 失败（错误 %d）" % ctypes.get_last_error()
        if ctypes.get_last_error() == 1300:   # ERROR_NOT_ALL_ASSIGNED
            return False, "权限不足 —— 需要以管理员身份运行才能清空待机列表"
        return True, "ok"
    finally:
        kernel32.CloseHandle(token)


def _empty_all_working_sets():
    """对每个能打开的进程调 EmptyWorkingSet（psapi）"""
    import ctypes
    from ctypes import wintypes
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_SET_QUOTA = 0x0100
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    done = 0
    failed = 0
    buf = (wintypes.DWORD * 4096)()
    need = wintypes.DWORD()
    if not psapi.EnumProcesses(buf, ctypes.sizeof(buf), ctypes.byref(need)):
        return 0, 0
    n = need.value // ctypes.sizeof(wintypes.DWORD)
    mypid = kernel32.GetCurrentProcessId()
    for i in range(n):
        pid = buf[i]
        if not pid or pid == mypid:
            continue
        h = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if not h:
            failed += 1
            continue
        try:
            if psapi.EmptyWorkingSet(h):
                done += 1
            else:
                failed += 1
        finally:
            kernel32.CloseHandle(h)
    return done, failed


def _trim_file_cache():
    """收缩系统文件缓存"""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = getattr(kernel32, "SetSystemFileCacheSize", None)
    if fn is None:
        return False, "系统不支持 SetSystemFileCacheSize"
    SIZE_T = ctypes.c_size_t
    fn.argtypes = [SIZE_T, SIZE_T, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    # 传 -1 表示「不限制」，随后系统会顺带回收缓存；这是各家内存清理工具的常规做法
    if fn(SIZE_T(-1), SIZE_T(-1), 0):
        return True, "ok"
    err = ctypes.get_last_error()
    if err == 1314:      # ERROR_PRIVILEGE_NOT_HELD
        return False, "需要管理员权限"
    return False, "SetSystemFileCacheSize 失败（错误 %d）" % err


def _purge_standby(command=4):
    """
    清空待机列表（ntdll!NtSetSystemInformation）
      command: 2=清空系统工作集  3=刷新已修改页表  4=清空待机列表
               5=清空低优先级待机列表
    """
    import ctypes
    from ctypes import wintypes
    ok, why = _enable_privilege("SeProfileSingleProcessPrivilege")
    if not ok:
        return False, why
    ntdll = ctypes.WinDLL("ntdll")
    fn = getattr(ntdll, "NtSetSystemInformation", None)
    if fn is None:
        return False, "系统不支持 NtSetSystemInformation"
    SystemMemoryListInformation = 0x50
    cmd = ctypes.c_int(command)
    fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    fn.restype = ctypes.c_long
    st = fn(SystemMemoryListInformation, ctypes.byref(cmd), ctypes.sizeof(cmd))
    if st == 0:
        return True, "ok"
    return False, "NtSetSystemInformation 返回 0x%X（%s）" % (
        st & 0xFFFFFFFF, "权限不足" if st in (-1073741790, -1073741727) else "调用失败")


def release_memory(deep=True):
    """执行一次内存释放，返回前后对比 + 各步骤结果"""
    before = _mem()
    steps = []

    t0 = time.time()
    done, failed = _empty_all_working_sets()
    steps.append({"name": "回收各进程工作集", "ok": done > 0,
                  "detail": "成功 %d 个进程，跳过 %d 个" % (done, failed),
                  "api": "EmptyWorkingSet"})

    ok, why = _trim_file_cache()
    steps.append({"name": "收缩系统文件缓存", "ok": ok, "detail": why,
                  "api": "SetSystemFileCacheSize"})

    if deep:
        for cmd, label in ((4, "清空待机列表"), (5, "清空低优先级待机列表")):
            ok, why = _purge_standby(cmd)
            steps.append({"name": label, "ok": ok, "detail": why,
                          "api": "NtSetSystemInformation"})

    time.sleep(1.2)                      # 等系统把数字算准
    after = _mem()
    freed = max(0, after[1] - before[1])
    return {
        "beforeAvailable": before[1], "afterAvailable": after[1],
        "beforeText": human(before[1]), "afterText": human(after[1]),
        "freed": freed, "freedText": human(freed),
        "loadBefore": before[2], "loadAfter": after[2],
        "elapsed": round(time.time() - t0, 2),
        "steps": steps,
        "isAdmin": is_admin(),
    }


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def release_memory_elevated(timeout=120):
    """
    深度清理需要 SeProfileSingleProcessPrivilege（只有管理员有）。
    这里用 ShellExecuteW "runas" 拉起一个提权的自己，由子进程执行并把结果写到临时 JSON。
    """
    import ctypes
    import json
    import subprocess
    import sys
    import tempfile

    out = os.path.join(tempfile.gettempdir(),
                       "aiws_mem_%s.json" % uuid.uuid4().hex[:8])
    py = sys.executable or "python.exe"
    script = os.path.join(ROOT, "toolbox.py")
    args = '"%s" --deep-out "%s"' % (script, out)

    # SW_HIDE = 0，提权后不弹黑框
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", py, args, ROOT, 0)
    if rc <= 32:
        # 5 = 用户拒绝；其它为启动失败
        return {"ok": False,
                "error": "用户取消了管理员授权（UAC）" if rc == 5 else
                         "无法启动提权进程（代码 %d）" % rc}

    waited = 0.0
    while waited < timeout:
        time.sleep(0.5)
        waited += 0.5
        if os.path.isfile(out):
            try:
                with open(out, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(out)
                return {"ok": True, "result": data, "elevated": True}
            except Exception:
                continue
    return {"ok": False, "error": "等待提权清理超时（%.0f 秒）" % timeout}



# ==========================================================================
#  二、多线程下载器
# ==========================================================================
def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return ("%.0f %s" % (n, u)) if u == "B" else ("%.2f %s" % (n, u))
        n /= 1024
    return "%.2f PB" % n


def probe_url(url, timeout=25):
    """HEAD（不支持就 GET 读一点）拿到大小和是否支持分块"""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": UA, "Accept": "*/*"})
    size, ranges, name = 0, False, ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = r.headers
            size = int(h.get("Content-Length") or 0)
            ranges = (h.get("Accept-Ranges", "").lower() == "bytes")
            name = guess_name(url, h.get("Content-Disposition", ""))
        if size:
            return {"ok": True, "size": size, "ranges": ranges, "name": name,
                    "sizeText": human(size)}
    except Exception:
        pass
    # 回退：GET 读 1 字节
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                try:
                    size = int(cr.split("/")[-1])
                except Exception:
                    size = 0
            ranges = bool(cr)
            name = guess_name(url, r.headers.get("Content-Disposition", ""))
        return {"ok": True, "size": size, "ranges": ranges, "name": name,
                "sizeText": human(size) if size else "未知"}
    except Exception as e:
        return {"ok": False, "error": "无法访问该地址：%s" % e}


def guess_name(url, disposition=""):
    import re
    from urllib.parse import unquote, urlparse
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition or "", re.I)
    if m:
        return unquote(m.group(1)).strip()
    p = urlparse(url).path
    base = unquote(os.path.basename(p)) or "download.bin"
    return base


def new_dl(url, path, threads, size, ranges):
    jid = uuid.uuid4().hex[:12]
    parts = max(1, min(int(threads or 8), 32)) if ranges else 1
    chunk = (size // parts) if size else 0
    segs = []
    for i in range(parts):
        s = i * chunk
        e = (size - 1) if i == parts - 1 else (s + chunk - 1)
        if size and s > e:
            break
        segs.append({"i": i, "start": s, "end": e, "done": 0,
                     "tmp": path + ".part%d" % i, "state": "等待"})
    with DL_LOCK:
        DL_JOBS[jid] = {
            "id": jid,
            "kind": "download", "url": url, "path": path, "size": size,
            "threads": len(segs), "segs": segs, "state": "running",
            "t0": time.time(), "bytes": 0, "error": None, "result": None,
            "ranges": ranges, "log": [],
        }
    return jid


def dl_log(jid, m):
    j = DL_JOBS.get(jid)
    if not j:
        return
    j["log"].append(time.strftime("%H:%M:%S") + "  " + m)
    if len(j["log"]) > 200:
        del j["log"][:60]


def dl_progress(jid):
    j = DL_JOBS.get(jid)
    if not j:
        return None
    el = max(0.001, time.time() - j["t0"])
    total = j["size"] or sum(s["done"] for s in j["segs"])
    done = sum(s["done"] for s in j["segs"])
    speed = done / el
    eta = ((total - done) / speed) if (speed > 0 and total > done) else 0
    return {
        "state": j["state"], "percent": round(100.0 * done / total, 1) if total else 0,
        "done": done, "total": total, "doneText": human(done),
        "totalText": human(total) if total else "未知",
        "speed": speed, "speedText": human(speed) + "/s",
        "eta": int(eta), "etaText": ("%d 分 %d 秒" % (eta // 60, eta % 60)) if eta else "—",
        "elapsed": round(el, 1), "threads": j["threads"],
        "segs": [{"i": s["i"], "done": s["done"],
                  "size": max(1, s["end"] - s["start"] + 1),
                  "state": s["state"],
                  "percent": round(100.0 * s["done"] / max(1, s["end"] - s["start"] + 1), 1)}
                 for s in j["segs"]],
        "error": j["error"], "result": j["result"], "log": (j["log"] or [])[-40:],
        "path": j["path"], "url": j["url"],
    }


def _fetch_range(j, seg, url):
    start = seg["start"] + seg["done"]
    end = seg["end"]
    if start > end:
        seg["state"] = "完成"
        return
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if j["ranges"]:
        hdrs["Range"] = "bytes=%d-%d" % (start, end)
    seg["state"] = "下载中"
    mode = "ab" if seg["done"] else "wb"

    # 每块最多重试 4 次（网络抖动很常见，尤其单线程长连接）
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=60) as r, open(seg["tmp"], mode) as f:
                while True:
                    b = r.read(1 << 18)
                    if not b:
                        break
                    f.write(b)
                    seg["done"] += len(b)
                    if j["state"] == "cancel":
                        seg["state"] = "已取消"
                        return
                    mode = "ab"          # 首块写完后续都是追加
            seg["state"] = "完成"
            return
        except Exception as e:
            last_err = e
            if j["state"] == "cancel":
                seg["state"] = "已取消"
                return
            if attempt < 3:
                dl_log(j["id"], "分块 %d 第 %d 次失败，2 秒后重试：%s"
                       % (seg["i"] + 1, attempt + 1, e))
                time.sleep(2)
                # 重试要从「已下字节」接着下
                start = seg["start"] + seg["done"]
                if start > end:
                    seg["state"] = "完成"
                    return
                hdrs["Range"] = "bytes=%d-%d" % (start, end)
                mode = "ab" if seg["done"] else "wb"
    seg["state"] = "失败"
    raise RuntimeError("分块 %d 重试 4 次仍失败：%s" % (seg["i"] + 1, last_err))


def download_worker(jid):
    j = DL_JOBS.get(jid)
    if not j:
        return
    try:
        os.makedirs(os.path.dirname(j["path"]) or ".", exist_ok=True)
        dl_log(jid, "开始下载　线程数 %d　目标 %s" % (j["threads"], j["path"]))
        errs = []

        def run(seg):
            try:
                _fetch_range(j, seg, j["url"])
            except Exception as e:
                errs.append(str(e))

        ths = [threading.Thread(target=run, args=(s,), daemon=True) for s in j["segs"]]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

        if errs:
            raise RuntimeError("；".join(errs[:3]))

        # 合并分块
        dl_log(jid, "分块下载完成，正在合并…")
        tmp_final = j["path"] + ".merging"
        with open(tmp_final, "wb") as out:
            for s in j["segs"]:
                if os.path.isfile(s["tmp"]):
                    with open(s["tmp"], "rb") as f:
                        while True:
                            b = f.read(1 << 20)
                            if not b:
                                break
                            out.write(b)
        os.replace(tmp_final, j["path"])
        for s in j["segs"]:
            try:
                os.remove(s["tmp"])
            except Exception:
                pass

        size = os.path.getsize(j["path"])
        j["size"] = size
        j["state"] = "done"
        j["result"] = {"path": j["path"], "name": os.path.basename(j["path"]),
                       "size": size, "sizeText": human(size),
                       "seconds": round(time.time() - j["t0"], 1)}
        dl_log(jid, "下载完成　%s　用时 %.1f 秒" % (human(size), time.time() - j["t0"]))
    except Exception as e:
        j["state"] = "error"
        j["error"] = str(e)
        dl_log(jid, "失败：" + str(e))


def dl_cancel(jid):
    j = DL_JOBS.get(jid)
    if j and j["state"] == "running":
        j["state"] = "cancel"
        return True
    return False


def dl_pause(jid):
    """暂停：等当前块写完就停（简单实现：标记后线程在下一次循环退出）"""
    j = DL_JOBS.get(jid)
    if j and j["state"] == "running":
        j["state"] = "paused"
        return True
    return False


# ==========================================================================
#  三、会话备份（DSH 上下文自动落文本）
# ==========================================================================
#  为什么是这个做法：
#    DSH 内核自己就会把过长的上下文压缩成摘要（工具结果修剪 / 图片卸载 /
#    自动压缩 / /compact 都在内核默认清单里），**这部分不用我们管**。
#    而且官方的压缩事件是「会话日志事件」，不是插件能挂的事件 ——
#    所以「写插件监听压缩」这条路走不通。
#    真正缺的只有一件：落一份人能读的文本。这里就是那个看门狗的开关面板。
# ==========================================================================
BACKUP_DIR = r"D:\DSH-备份\会话文本"
WATCHDOG_PY = os.path.join(ROOT, "_session_watchdog.py")
BACKUP_TASK = "DSH-会话看门狗"
BACKUP_INTERVAL_MIN = 10
_BK_LOCK = threading.Lock()


def _short_sess(sess):
    return (sess.replace("session-", "").replace("session", "")[:8]) or sess[:8]


def _read_json_soft(path):
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def backup_status():
    """会话备份现状：计划任务 / 各会话进度 / 备份文件 / 日志尾部"""
    import subprocess

    st = _read_json_soft(os.path.join(BACKUP_DIR, "_看门狗状态.json"))

    sessions = []
    for path, rec in (st or {}).items():
        try:
            sess = os.path.basename(os.path.dirname(path))
        except Exception:
            sess = path
        bt = rec.get("backupTime") or 0
        sessions.append({
            "name": _short_sess(sess),
            "now": rec.get("size", 0), "nowText": human(rec.get("size", 0)),
            "backed": rec.get("backupSize", 0), "backedText": human(rec.get("backupSize", 0)),
            "count": rec.get("backups", 0),
            "lastTime": time.strftime("%m-%d %H:%M", time.localtime(bt)) if bt else "",
            "file": os.path.basename(rec.get("backupFile") or ""),
        })
    sessions.sort(key=lambda x: x["now"], reverse=True)

    files, total = [], 0
    try:
        for fn in os.listdir(BACKUP_DIR):
            if not fn.lower().endswith(".md"):
                continue
            fp = os.path.join(BACKUP_DIR, fn)
            try:
                sz = os.path.getsize(fp)
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            total += sz
            files.append({"name": fn, "size": sz, "sizeText": human(sz),
                          "time": time.strftime("%m-%d %H:%M", time.localtime(mt))})
    except Exception:
        pass
    files.sort(key=lambda x: x["name"], reverse=True)

    # 计划任务在不在、下次什么时候跑（schtasks 输出是系统 ANSI 编码，中文机器是 GBK）
    task = {"exists": False, "status": "", "next": "", "intervalMin": BACKUP_INTERVAL_MIN}
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", BACKUP_TASK, "/fo", "LIST"],
                           capture_output=True, timeout=25)
        txt = (r.stdout or b"").decode("gbk", "replace")
        if r.returncode == 0:
            task["exists"] = True
            for line in txt.splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip(), v.strip()
                if k in ("状态", "Status"):
                    task["status"] = v
                elif k in ("下次运行时间", "Next Run Time"):
                    task["next"] = v
    except Exception as e:
        task["error"] = str(e)

    log_tail = []
    try:
        with open(os.path.join(BACKUP_DIR, "_看门狗.log"), "r",
                  encoding="utf-8", errors="replace") as f:
            log_tail = f.read().splitlines()[-12:]
    except Exception:
        pass

    return {
        "ok": True,
        "dir": BACKUP_DIR,
        "sessions": sessions,
        "files": files[:10],
        "fileCount": len(files),
        "totalSize": total, "totalText": human(total),
        "task": task,
        "log": log_tail,
        "watchdog": WATCHDOG_PY,
        "watchdogExists": os.path.isfile(WATCHDOG_PY),
        "taskName": BACKUP_TASK,
    }


def backup_run():
    """立刻跑一轮看门狗（子进程；同一时间只允许一个）"""
    import subprocess
    import sys

    if not os.path.isfile(WATCHDOG_PY):
        return {"ok": False, "error": "找不到看门狗脚本：%s" % WATCHDOG_PY}
    if not _BK_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "上一轮还在跑，等它跑完再点"}
    try:
        t0 = time.time()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"        # 不然子进程输出是 GBK，解出来乱码
        r = subprocess.run([sys.executable, WATCHDOG_PY, "--once"],
                           capture_output=True, timeout=900, env=env)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        made = sum(1 for l in lines if "✓ 落盘" in l)
        return {"ok": True, "made": made, "lines": lines[-14:],
                "stderr": err.strip()[:400],
                "seconds": round(time.time() - t0, 1)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "跑超时了（15 分钟）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _BK_LOCK.release()


# ==========================================================================
#  ★ 提权子进程入口 —— 必须放在**文件最末尾** ✗
#  踩过：以前放在中间（第 296 行），而 human() 定义在第 321 行，
#  直接 `python toolbox.py` 跑到这里时 human 还没定义
#  → release_memory() 里一调就 NameError → 写回降级对象
#  → 前端显示「?」「undefined」✓（非提权路径 import 模块，所以是好的）
# ==========================================================================
if __name__ == "__main__":
    import json
    import sys

    _out = ""
    if "--deep-out" in sys.argv:
        try:
            _out = sys.argv[sys.argv.index("--deep-out") + 1]
        except Exception:
            _out = ""
    try:
        _res = release_memory(deep=True)
    except Exception as _e:
        # ★ 降级也要给全字段，别让前端拿到 undefined ✗
        try:
            _b = _mem()
            _bt, _at, _lb, _la = human(_b[1]), human(_b[1]), _b[2], _b[2]
        except Exception:
            _bt = _at = "?"
            _lb = _la = 0
        _res = {"error": str(_e), "freed": 0, "freedText": "0 B",
                "beforeText": _bt, "afterText": _at,
                "loadBefore": _lb, "loadAfter": _la,
                "elapsed": 0, "steps": [], "isAdmin": is_admin()}
    if _out:
        try:
            with open(_out, "w", encoding="utf-8") as _f:
                json.dump(_res, _f, ensure_ascii=False)
        except Exception:
            pass
