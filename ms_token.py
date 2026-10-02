#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ModelScope 登录态注入：把 SDK 访问令牌换成创空间的 studio_token

背景（实测）：See-Through 官方演示是 **xGPU 创空间**，平台会返回
    「请登录后再使用xGPU创空间。| Please log in to use xGPU Studio.」
匿名一律拒。ModelScope 前端是这么拿身份的：
    GET /api/v1/studios/token        -> Data.Token（登录用户拿到真 token，匿名拿到空串）
然后所有对创空间的请求都带上 header  x-studio-token: <token>
    SSE 流：/gradio_api/queue/data?session_hash=..&studio_token=<token>

本脚本试几种把 SDK 令牌（ms- 开头）换成 studio_token 的方式，成功就写
    <本目录>/studio_token.txt

用法：
    python ms_token.py                 # 从 ms_api_key.txt 读令牌
    python ms_token.py ms-xxxxxxxx     # 直接给令牌
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0"


def cred_dir():
    """凭据目录：环境变量 > %LOCALAPPDATA%\\Live2D > 脚本目录（别放进交付包里 ✗）"""
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


KEY_FILE = cred_file("ms_api_key.txt")
TOKEN_FILE = os.path.join(cred_dir(), "studio_token.txt")


def read_key():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    if os.path.isfile(KEY_FILE):
        return open(KEY_FILE, encoding="utf-8").read().strip()
    return os.environ.get("MODELSCOPE_API_KEY", "").strip()


def get(url, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", "%s: %s" % (type(e).__name__, e)


def main():
    key = read_key()
    if not key:
        print("没找到令牌。把 ms- 开头的访问令牌写进 %s，或用参数传。" % KEY_FILE)
        return 2
    print("令牌前缀:", key[:12] + "..." if len(key) > 12 else key)

    attempts = [
        ("Bearer 认证 /api/v1/studios/token",
         "https://modelscope.cn/api/v1/studios/token",
         {"Authorization": "Bearer " + key}),
        ("X-Modelscope-Token 头",
         "https://modelscope.cn/api/v1/studios/token",
         {"X-Modelscope-Token": key}),
        ("OpenAPI 版 /openapi/v1/studios/token",
         "https://modelscope.cn/openapi/v1/studios/token",
         {"Authorization": "Bearer " + key}),
        ("OpenAPI 用户自检",
         "https://modelscope.cn/openapi/v1/users/me",
         {"Authorization": "Bearer " + key}),
        ("老接口用户自检",
         "https://modelscope.cn/api/v1/users/me",
         {"Authorization": "Bearer " + key}),
    ]
    token = ""
    for name, url, hdr in attempts:
        code, body = get(url, hdr)
        print("\n== %s\n   %s -> %s" % (name, code, url))
        print("   ", body[:400])
        if isinstance(code, int) and code == 200:
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                continue
            data = d.get("Data") or d.get("data") or {}
            if isinstance(data, dict):
                t = data.get("Token") or data.get("token")
                if t:
                    token = t
                    print("   ★ 拿到 studio_token:", t[:16] + "...")
                    break
    if token:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
        print("\n已写入", TOKEN_FILE)
        return 0
    print("\n这几种方式都没换到 studio_token。")
    print("说明 SDK 令牌和网页登录态是两套东西 —— 那就走「浏览器里跑一次演示」这条路。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
