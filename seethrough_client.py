#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""See-Through 分层 PSD 客户端 —— 走作者官方在 ModelScope 的免费在线演示

演示地址：https://modelscope.cn/studios/ljsabc/See-Through
直连地址：https://ljsabc-see-through.ms.show

协议按 ModelScope 自家 Gradio 前端的真实行为实现（从它的前端 bundle 里挖出来的）：
  1. 所有请求带 header   x-studio-token: <studio_token>
  2. SSE 流：GET  /gradio_api/queue/data?session_hash=<H>&studio_token=<T>
  3. 提交：  POST /gradio_api/queue/join
             {data, event_data, fn_index, trigger_id, session_hash}
  4. 结果从 SSE 消息 process_completed 里取，文件用 /gradio_api/file=<path> 下载

★ xGPU 创空间要求登录：studio_token 从哪来
  - 环境变量 ST_STUDIO_TOKEN，或本目录 studio_token.txt（ms_token.py 会写）
  - 拿不到就会收到「请登录后再使用xGPU创空间」

只用标准库，不需要 pip 装东西。

用法：
    python seethrough_client.py <立绘图片> [-o 输出目录] [-r 1024] [-s 42] [--tblr]
"""
import argparse
import http.cookiejar
import json
import mimetypes
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("ST_BASE", "https://ljsabc-see-through.ms.show")
API = BASE + "/gradio_api"
HERE = os.path.dirname(os.path.abspath(__file__))


def cred_dir():
    """凭据目录：环境变量 > %LOCALAPPDATA%\\Live2D > 脚本目录。
    ★ 故意不放在 D:\\AI工作站 里 —— 免得被打进交付包 ✗"""
    d = os.environ.get("LIVE2D_CRED_DIR")
    if d:
        return d
    la = os.environ.get("LOCALAPPDATA")
    if la:
        return os.path.join(la, "Live2D")
    return HERE


def cred_file(name):
    p = os.path.join(cred_dir(), name)
    if os.path.isfile(p):
        return p
    return os.path.join(HERE, name)


TOKEN_FILE = cred_file("studio_token.txt")

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_JAR),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)


class LoginRequired(RuntimeError):
    """平台要求登录 xGPU 创空间。"""


def get_studio_token():
    tok = os.environ.get("ST_STUDIO_TOKEN")
    if tok:
        return tok
    if os.path.isfile(TOKEN_FILE):
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    return ""


def _req(url, data=None, headers=None, method=None, timeout=120, token=None):
    h = {"User-Agent": "see-through-client/1.0",
         "x-studio-token": get_studio_token() if token is None else token}
    if headers:
        h.update(headers)
    return _OPENER.open(urllib.request.Request(url, data=data, headers=h, method=method),
                        timeout=timeout)


def upload_image(path):
    """上传本地图片，返回服务端路径。"""
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    boundary = "----seethrough" + uuid.uuid4().hex
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="files"; filename="%s"\r\n' % name).encode("utf-8"),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        content,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    url = "%s/upload?upload_id=%s" % (API, uuid.uuid4().hex)
    r = _req(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
             timeout=300)
    out = json.loads(r.read().decode("utf-8"))
    if isinstance(out, list) and out:
        return out[0]
    raise RuntimeError("上传返回不符合预期: %r" % (out,))


def fn_index(api_name="inference"):
    cfg = json.loads(_req(BASE + "/config", timeout=60).read().decode("utf-8"))
    for dep in cfg.get("dependencies", []):
        if dep.get("api_name") == api_name.lstrip("/"):
            return dep.get("id")
    raise RuntimeError("在 /config 里找不到接口 %s" % api_name)


def run_inference(image_path, resolution=1024, seed=42, tblr_split=False,
                  log=lambda m: None, timeout=2400):
    """跑一次拆层。返回结果列表（每个元素是 gradio FileData / 图集数据）。"""
    token = get_studio_token()
    session_hash = uuid.uuid4().hex[:11]
    idx = fn_index("inference")

    log("上传立绘 ...")
    sp = upload_image(image_path)
    log("  服务端路径 %s" % sp)

    q = urllib.parse.urlencode({"session_hash": session_hash, "studio_token": token})
    stream_url = "%s/queue/data?%s" % (API, q)
    log("建立结果流 ...")

    payload = {
        "data": [{"path": sp, "orig_name": os.path.basename(image_path),
                  "meta": {"_type": "gradio.FileData"}},
                 float(resolution), float(seed), bool(tblr_split)],
        "event_data": None,
        "fn_index": idx,
        "trigger_id": int(time.time() * 1000) % 100000,
        "session_hash": session_hash,
    }

    box = {"done": False, "output": None, "error": None}
    t0 = time.time()
    log("会话 session_hash=%s" % session_hash)

    def reader():
        """结果流。两个必须处理的坑：
        ① 流比 join 先到 -> 服务端回 session_not_found 并断流
        ② 流挂着但可能没接到事件 -> 用 60 秒读超时当看门狗，静默就重连
        """
        attempts = 0
        while not box["done"] and attempts < 60:
            attempts += 1
            try:
                r = _req(stream_url, headers={"Accept": "text/event-stream"}, timeout=60)
                buf = b""
                while not box["done"]:
                    chunk = r.read1(65536) if hasattr(r, "read1") else r.read(1)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n\n" in buf:
                        raw, buf = buf.split(b"\n\n", 1)
                        for line in raw.decode("utf-8", "replace").splitlines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                msg = json.loads(line[5:].strip() or "{}")
                            except json.JSONDecodeError:
                                continue
                            m = msg.get("msg")
                            if m in ("heartbeat", "estimation"):
                                continue
                            if m == "process_completed":
                                out = msg.get("output") or {}
                                if isinstance(out, dict) and out.get("error"):
                                    box["error"] = str(out["error"])
                                elif msg.get("success"):
                                    box["output"] = out.get("data") if isinstance(out, dict) else out
                                else:
                                    box["error"] = json.dumps(msg, ensure_ascii=False)[:400]
                                box["done"] = True
                                return
                            if m == "close_stream":
                                return
                            if m == "unexpected_error":
                                if msg.get("session_not_found"):
                                    log("  流先到、会话还没建，1 秒后重连 ...")
                                else:
                                    log("  流报错: %s" % json.dumps(msg, ensure_ascii=False)[:200])
                                break
                            log("  [%4ds] %s" % (time.time() - t0, json.dumps(msg, ensure_ascii=False)[:160]))
                    else:
                        continue
                    break      # 内层中断 -> 重连
            except Exception as e:
                if not box["done"]:
                    log("  流静默/断开(%s)，重连第 %d 次" % (type(e).__name__, attempts))
            if not box["done"]:
                time.sleep(1.0)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(1.5)   # 先让流连上，再提交（前端同样顺序）

    log("提交推理 (resolution=%d, seed=%d, tblr=%s) ..." % (resolution, seed, tblr_split))
    join_code, join_body = None, ""
    try:
        r = _req(API + "/queue/join", data=json.dumps(payload).encode("utf-8"),
                 headers={"Content-Type": "application/json"}, timeout=120)
        join_code, join_body = r.status, r.read().decode("utf-8", "replace")
        log("  join -> %s %s" % (join_code, join_body[:120]))
    except urllib.error.HTTPError as e:
        join_code = e.code
        join_body = e.read().decode("utf-8", "replace")
        log("  join 被拒 -> HTTP %s %s" % (join_code, join_body[:200]))
        for _ in range(20):
            if box["done"]:
                break
            time.sleep(0.5)

    if join_code not in (200, None) and not box["done"]:
        raise RuntimeError("排队被拒：HTTP %s %s" % (join_code, join_body[:300]))

    last = 0.0
    while not box["done"]:
        if timeout and time.time() - t0 > timeout:
            raise RuntimeError("等超时了")
        if time.time() - last >= 15:
            last = time.time()
            log("  ... 等结果（%ds）" % int(time.time() - t0))
        time.sleep(0.5)

    if box["error"]:
        msg = box["error"]
        if "请登录" in msg or "log in" in msg.lower():
            raise LoginRequired(
                "官方演示要求登录 ModelScope 才能用（原文：%s）\n"
                "  → 先在浏览器登录 modelscope.cn 跑一次，或把访问令牌给我换登录态。" % msg)
        raise RuntimeError("服务端报错: %s" % msg)
    return box["output"] or []


def download(server_file, dest):
    url = "%s/file=%s" % (API, urllib.parse.quote(server_file))
    r = _req(url, timeout=600)
    with open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return os.path.getsize(dest)


def _collect_files(data, log):
    """从结果里挑出可下载的文件并下载。返回下载好的本地路径列表。"""
    files = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("path"):
                files.append(node)
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(data)
    return files


def _mk_logger():
    """日志：默认打 stdout；若设了 ST_LOG 就同时追加到文件（避免 shell 管道卡住）。"""
    path = os.environ.get("ST_LOG")
    fh = open(path, "a", encoding="utf-8") if path else None

    def log(msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        try:
            print(line, flush=True)
        except Exception:
            pass
        if fh:
            fh.write(line + "\n")
            fh.flush()
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("-r", "--resolution", type=int, default=1024)
    ap.add_argument("-s", "--seed", type=int, default=42)
    ap.add_argument("--tblr", action="store_true", help="拆分左右手臂与腿")
    args = ap.parse_args()

    src = os.path.abspath(args.image)
    stem = os.path.splitext(os.path.basename(src))[0]
    outdir = args.outdir or os.path.join(os.path.dirname(src), stem + "_seethrough")
    os.makedirs(outdir, exist_ok=True)
    log = _mk_logger()

    try:
        data = run_inference(src, args.resolution, args.seed, args.tblr, log=log)
    except LoginRequired as e:
        log("\n⛔ %s" % e)
        log("   拿到 ModelScope 访问令牌后跑： python %s" % os.path.join(HERE, "ms_token.py"))
        return 3

    log("完成，返回 %d 项" % len(data))
    saved = []
    for i, f in enumerate(_collect_files(data, log)):
        spath = f["path"]
        oname = f.get("orig_name") or os.path.basename(spath)
        if not os.path.splitext(oname)[1]:
            oname = os.path.basename(spath)
        dest = os.path.join(outdir, oname)
        if os.path.exists(dest) and oname.lower().endswith((".png", ".webp", ".jpg")):
            stem, ext = os.path.splitext(oname)
            dest = os.path.join(outdir, "%s_%02d%s" % (stem, i, ext))
        n = download(spath, dest)
        saved.append(dest)
        log("  %s (%.2f MB)" % (dest, n / 1048576.0))
    with open(os.path.join(outdir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"input": src, "resolution": args.resolution, "seed": args.seed,
                   "tblr_split": args.tblr, "outputs": saved, "raw": data},
                  fh, ensure_ascii=False, indent=1)
    log("完成 -> %s" % outdir)
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
