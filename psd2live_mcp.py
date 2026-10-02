#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PSD2Live 本地 MCP 客户端（标准库实现，可直接 import 或当命令行用）

- 端点：http://127.0.0.1:23871/mcp（Streamable HTTP）
- token：环境变量 PSD2LIVE_MCP_TOKEN，否则读 Windows 注册表
    HKCU\\Software\\JavaSoft\\Prefs\\io\\github\\psd2live\\agent
    -> agent_mcp_bearer_token（Java Preferences 转义：// -> /，/X -> X 大写）

命令行用法：
    python psd2live_mcp.py list                     # 列出 26 个工具
    python psd2live_mcp.py show <工具名>             # 看某个工具的入参结构
    python psd2live_mcp.py call <工具名> <参数.json>  # 调用工具（参数写在 json 文件里）
    python psd2live_mcp.py inspect project          # 常用快捷方式
"""
import json
import os
import sys
import urllib.error
import urllib.request

try:
    import winreg
except ImportError:  # 非 Windows
    winreg = None

ENDPOINT = os.environ.get("PSD2LIVE_MCP_ENDPOINT", "http://127.0.0.1:23871/mcp")
PROTOCOL = "2025-06-18"


def _decode_java_pref(value):
    out, i = [], 0
    while i < len(value):
        if value[i] == "/" and i + 1 < len(value):
            i += 1
            out.append("/" if value[i] == "/" else value[i].upper())
        else:
            out.append(value[i])
        i += 1
    return "".join(out)


def get_token():
    tok = os.environ.get("PSD2LIVE_MCP_TOKEN")
    if tok:
        return tok
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\JavaSoft\Prefs\io\github\psd2live\agent") as k:
            val, _ = winreg.QueryValueEx(k, "agent_mcp_bearer_token")
        return _decode_java_pref(val)
    except OSError:
        return None


def _parse_body(body):
    """响应可能是纯 JSON，也可能是 SSE（data: 行）。"""
    body = body.strip()
    if not body:
        return []
    if body.startswith("{") or body.startswith("["):
        try:
            obj = json.loads(body)
            return obj if isinstance(obj, list) else [obj]
        except json.JSONDecodeError:
            pass
    out, buf = [], []
    for line in body.splitlines() + [""]:
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        elif not line and buf:
            raw = "\n".join(buf)
            buf = []
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                out.append({"_raw": raw})
    return out


class McpClient:
    """一次会话内连续调用多个工具。"""

    def __init__(self, token=None, endpoint=ENDPOINT, timeout=600):
        self.endpoint = endpoint
        self.token = token or get_token()
        self.timeout = timeout
        self.session = None
        self.protocol = PROTOCOL
        self._id = 0
        if not self.token:
            raise RuntimeError("没找到 PSD2Live MCP token（先启动一次 PSD2Live，或设 PSD2LIVE_MCP_TOKEN）")

    def _post(self, msg):
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.endpoint, data=json.dumps(msg).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError("MCP HTTP %s: %s" % (e.code, detail[:400]))
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        if hdrs.get("mcp-session-id"):
            self.session = hdrs["mcp-session-id"]
        return _parse_body(r.read().decode("utf-8", "replace"))

    def connect(self):
        self._id += 1
        msgs = self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize",
                           "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                      "clientInfo": {"name": "dsh-live2d", "version": "1.0"}}})
        result = msgs[0].get("result", {}) if msgs else {}
        self.protocol = result.get("protocolVersion", PROTOCOL)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call(self, method, params=None):
        if self.session is None:
            self.connect()
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        msgs = self._post(msg)
        for m in msgs:
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError("MCP 错误: %s" % json.dumps(m["error"], ensure_ascii=False)[:400])
                return m.get("result", {})
        return msgs[0].get("result", {}) if msgs else {}

    def tool(self, name, arguments=None):
        return self.call("tools/call", {"name": name, "arguments": arguments or {}})


def tool_result_text(result):
    """把 tools/call 的返回压成可读文本。"""
    if isinstance(result, dict) and "content" in result:
        parts = []
        for c in result["content"]:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cli = McpClient()
    cmd = args[0]
    if cmd == "list":
        res = cli.call("tools/list")
        for t in res.get("tools", []):
            print("%-14s %s" % (t["name"], t["description"].split("\n")[0][:100]))
        return 0
    if cmd == "show":
        res = cli.call("tools/list")
        for t in res.get("tools", []):
            if t["name"] == args[1]:
                print(json.dumps(t["inputSchema"], ensure_ascii=False, indent=1))
                return 0
        print("没有这个工具:", args[1])
        return 1
    if cmd == "call":
        name = args[1]
        payload = {}
        if len(args) > 2:
            with open(args[2], encoding="utf-8") as f:
                payload = json.load(f)
        res = cli.tool(name, payload)
        print(tool_result_text(res))
        return 0
    if cmd == "inspect":
        res = cli.tool("inspect", {"scope": args[1] if len(args) > 1 else "project"})
        print(tool_result_text(res))
        return 0
    print("未知命令:", cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
