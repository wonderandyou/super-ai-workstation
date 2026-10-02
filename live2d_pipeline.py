#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一条龙：立绘 → 分层 PSD → Live2D 模型（moc3 / cmo3）

三步：
  1. See-Through（作者官方 ModelScope 免费演示）把立绘拆成带深度的分层 PSD
  2. 把 PSD 导进本机 PSD2Live（走它自己的 MCP 接口，127.0.0.1:23871）
  3. PSD2Live 导出 moc3 / cmo3 / model3.json

用法：
    python live2d_pipeline.py <立绘.png> [--name 角色名] [-r 1024] [-s 42] [--tblr]
                             [--psd 已有PSD]      # 跳过第 1 步，直接用现成 PSD
                             [--dry]              # 只做第 1 步，不碰 PSD2Live

产物：
    桌面\\Live2D素材\\<名字>\\   ← 分层 PSD + 预览
    桌面\\Live2D成品\\<名字>\\   ← moc3 / cmo3 / model3.json ...
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "_seethrough"))
sys.path.insert(0, HERE)

import seethrough_client as st           # noqa: E402
from psd2live_mcp import McpClient, tool_result_text  # noqa: E402

DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
MAT_DIR = os.path.join(DESKTOP, "Live2D素材")
OUT_DIR = os.path.join(DESKTOP, "Live2D成品")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def step_seethrough(image, outdir, resolution, seed, tblr):
    os.makedirs(outdir, exist_ok=True)
    log("① 送到 See-Through 官方演示拆层 ...")
    data = st.run_inference(image, resolution, seed, tblr,
                            log=lambda m: log("   " + m.strip()))
    log("   演示返回 %d 项，开始下载 ..." % len(data))
    psd = None
    for item in st._collect_files(data, log):
        spath = item["path"]
        oname = item.get("orig_name") or os.path.basename(spath)
        if not os.path.splitext(oname)[1]:
            oname = os.path.basename(spath)
        dest = os.path.join(outdir, oname)
        n = st.download(spath, dest)
        log("   下载 %s (%.2f MB)" % (oname, n / 1048576.0))
        if oname.lower().endswith(".psd"):
            psd = dest
    if not psd:
        raise RuntimeError("官方演示没返回 PSD，检查输出目录: %s" % outdir)
    return psd


PSD2LIVE_EXE = os.environ.get(
    "PSD2LIVE_EXE",
    r"D:\AI工作站\downloads\PSD2Live-1.6.0\PSD2Live\PSD2Live.exe")
PSD2LIVE_PORT = 23871


def _workspace_loaded(cli):
    try:
        txt = tool_result_text(cli.tool("inspect", {"scope": "project"}))
        return '"loaded":true' in txt.replace(" ", ""), txt
    except Exception as e:
        return False, str(e)


def restart_engine(log):
    """关掉再拉起 PSD2Live（等于界面里的「新建」；未保存的工程会丢）。"""
    import subprocess
    log("   重启 PSD2Live 引擎（清空工程）...")
    for p in psutil_like_procs():
        try:
            subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True, timeout=20)
            log("   已结束进程 %s" % p)
        except Exception as e:
            log("   结束 %s 失败: %s" % (p, e))
    time.sleep(3)
    if not os.path.isfile(PSD2LIVE_EXE):
        raise RuntimeError("找不到 PSD2Live：%s（可用环境变量 PSD2LIVE_EXE 指定）" % PSD2LIVE_EXE)
    subprocess.Popen([PSD2LIVE_EXE], cwd=os.path.dirname(PSD2LIVE_EXE),
                     creationflags=0x08000000)
    # 等端口起来
    import socket
    for i in range(60):
        time.sleep(1)
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", PSD2LIVE_PORT))
            s.close()
            log("   引擎已就绪（%ds）" % (i + 1))
            time.sleep(3)      # 再给它一点时间把工程初始化好
            return
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    raise RuntimeError("PSD2Live 重启后 %ds 还没起来" % 60)


def psutil_like_procs():
    """列出 PSD2Live 相关进程号（不装 psutil，用 tasklist）。"""
    import subprocess
    out = ""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 2 and "PSD2Live" in parts[0]:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def step_import(psd, name, allow_restart=False):
    log("② 把 PSD 导进本机 PSD2Live ...")
    cli = McpClient()
    info = cli.connect()
    log("   已连上 PSD2Live %s (MCP %s)" % (info.get("serverInfo", {}).get("version", "?"),
                                            info.get("protocolVersion", "?")))
    loaded, txt = _workspace_loaded(cli)
    log("   导入前工程状态: %s" % txt[:200])

    if loaded:
        if not allow_restart:
            raise RuntimeError(
                "PSD2Live 里已经开着一个工程，MCP 不允许覆盖导入 ✗\n"
                "  · 想保留 → 先在它界面里导出/关掉\n"
                "  · 想让我直接重开引擎（未保存的改动会丢）→ 命令后面加 --restart")
        cli = None
        restart_engine(log)
        cli = McpClient()
        cli.connect()
        loaded, txt = _workspace_loaded(cli)
        log("   重启后工程状态: %s" % txt[:200])
        if loaded:
            raise RuntimeError("重启后工程还是非空，先手动处理一下 PSD2Live")

    # ⚠️ asset 的 request 是 additionalProperties:false，psd 变体只认 mode/path
    res = cli.tool("asset", {"request": {"mode": "psd", "path": psd}})
    text = tool_result_text(res)
    log("   导入返回: %s" % text[:800])
    if '"error"' in text:
        raise RuntimeError("导入失败：%s" % text[:300])
    return cli, res


def step_export(cli, name, outdir):
    log("③ 导出 Live2D 模型 ...")
    os.makedirs(outdir, exist_ok=True)
    state = find_state(cli)
    if not state:
        raise RuntimeError("没拿到 state，无法导出")
    res = cli.tool("export", {"state": state, "output_directory": outdir})
    text = tool_result_text(res)
    log("   导出返回: %s" % text[:1000])
    if '"error"' in text:
        raise RuntimeError("导出失败：%s" % text[:300])
    return res


def find_state(cli):
    """从 inspect 的返回里挖出当前 state（revision 令牌）。"""
    for scope in ("project", "settings"):
        try:
            res = cli.tool("inspect", {"scope": scope})
        except Exception as e:
            log("   inspect %s 失败: %s" % (scope, e))
            continue
        state = _dig_state(res)
        if state:
            return state
    return None


def _dig_state(obj):
    if isinstance(obj, dict):
        for k in ("state", "revision", "revisionId", "head"):
            v = obj.get(k)
            if isinstance(v, str) and len(v) > 8:
                return v
        for v in obj.values():
            got = _dig_state(v)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _dig_state(v)
            if got:
                return got
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", help="立绘图片")
    ap.add_argument("--name", default=None, help="作品名（默认取图片名）")
    ap.add_argument("--psd", default=None, help="已有分层 PSD，跳过 See-Through 那一步")
    ap.add_argument("-r", "--resolution", type=int, default=1024)
    ap.add_argument("-s", "--seed", type=int, default=42)
    ap.add_argument("--tblr", action="store_true", help="拆分左右手臂与腿")
    ap.add_argument("--dry", action="store_true", help="只出 PSD，不动 PSD2Live")
    ap.add_argument("--restart", action="store_true",
                    help="PSD2Live 里已有工程时，直接重启它清空（未保存的改动会丢）")
    args = ap.parse_args()

    if not args.image and not args.psd:
        ap.error("要么给立绘图片，要么给 --psd")

    name = args.name or os.path.splitext(os.path.basename(args.image or args.psd))[0]
    mat = os.path.join(MAT_DIR, name)
    out = os.path.join(OUT_DIR, name)

    if args.psd:
        psd = os.path.abspath(args.psd)
        if not os.path.isfile(psd):
            raise SystemExit("找不到 PSD: %s" % psd)
        log("用现成分层 PSD: %s" % psd)
    else:
        src = os.path.abspath(args.image)
        if not os.path.isfile(src):
            raise SystemExit("找不到图片: %s" % src)
        psd = step_seethrough(src, mat, args.resolution, args.seed, args.tblr)

    if args.dry:
        log("--dry：到此为止，PSD 在 %s" % psd)
        return 0

    cli, _ = step_import(psd, name, allow_restart=args.restart)
    step_export(cli, name, out)

    log("完成 ✓  PSD: %s   模型: %s" % (psd, out))
    for f in sorted(os.listdir(out)):
        p = os.path.join(out, f)
        if os.path.isfile(p):
            log("   %-40s %8.1f KB" % (f, os.path.getsize(p) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
