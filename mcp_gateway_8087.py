# -*- coding: utf-8 -*-
"""
===============================================================================
  🔌 工具平面常驻统一网关 · Tool Plane 8087 (v1.1)
  • 端口矩阵：8081 网关(路由平面) · 8083 主脑 · 8085 视觉 · 8086 向量
    · 8087 工具平面(MCP插件 + 本地技能统一注册表)
  • 工具平面只干工具平面的事：注册、发现、执行、健康检查。
    主网关脚本 (qwen_tool_proxy.py) 只做动态挂载与路由，零工具硬编码
    （解耦：本文件独立运行、独立调试，报错只看 8087 日志）。
  • 分类规划（category 即能力域，网关按域路由、每次只暴露相关域）：
      memory    记忆召回    memory-recall     (local-skill)
      billing   费用账本    billing-stats     (local-skill)
      time      时间        clock             (local-skill)
      workspace 工作区      workspace-read    (local-skill)
                            + anytxt          (mcp-stdio)
      network   联网搜索    web-search 系      (mcp-stdio，按需替换)
      text      文本压缩    history-compact   (网关内联，不占表)
  • 类型字段 type：mcp-stdio = 独立子进程（慢/可崩，隔离保主链）；
    local-skill = 本进程函数（毫秒级，崩不了）。
  • 配置（全部外置，代码零写死）：
      mcp:   mcp_servers.local.json > mcp_servers.example.json
             > qidongqi/mcp-servers.json（旧版只读兼容）
      skill: skills_servers.local.json > skills_servers.example.json
  • 日志铁律：logs/8087_mcp_YYYYMMDD.log 单日单文件 append 累加，
    严禁拆分 out/err。
  • 安全：只绑 127.0.0.1；workspace-read 沙盒只读。
  • 独立调试（换机器直接跑，无需启动器）：
      python mcp_gateway_8087.py --selftest        # 只印注册表+健康，不绑端口
      python mcp_gateway_8087.py --port 8087       # 常驻
      curl http://127.0.0.1:8087/health
      curl http://127.0.0.1:8087/tools/list
===============================================================================
"""

import datetime
import json
import os
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

PORT = 8087
HOST = "127.0.0.1"
SELFTEST = False
_i = 0
while _i < len(sys.argv):
    _a = sys.argv[_i]
    if _a == "--selftest":
        SELFTEST = True
    elif _a.startswith("--port="):
        try:
            PORT = int(_a.split("=", 1)[1])
        except Exception:
            pass
    elif _a == "--port" and _i + 1 < len(sys.argv):
        try:
            PORT = int(sys.argv[_i + 1])
        except Exception:
            pass
    _i += 1


def _expand(s, ctx):
    if not isinstance(s, str):
        return s
    for k, v in ctx.items():
        s = s.replace("${" + k + "}", v)
    return s


def _ctx():
    return {
        "PYTHON": sys.executable,
        "BASE_DIR": BASE_DIR.replace("\\", "/"),
        "MODELS_DIR": os.path.join(BASE_DIR, "models").replace("\\", "/"),
    }


def load_mcp_servers():
    """MCP 插件表：local 覆盖 example，同名按 name 合并；旧版 key 格式只读兼容。"""
    ctx = _ctx()
    merged, order = {}, []
    for fname in ("mcp_servers.example.json", "mcp_servers.local.json"):
        fpath = os.path.join(BASE_DIR, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for srv in json.load(f).get("servers", []):
                    name = srv.get("name", "")
                    if not name:
                        continue
                    item = dict(srv)
                    item["type"] = "mcp-stdio"
                    item["command"] = _expand(srv.get("command", ""), ctx)
                    item["args"] = [_expand(a, ctx) for a in (srv.get("args") or [])]
                    item["source"] = fname
                    if name not in merged:
                        order.append(name)
                    merged[name] = item
        except Exception as e:
            log_line(f"[WARN] 读取 {fname} 失败: {e}")
    if not merged:
        legacy = os.path.join(BASE_DIR, "qidongqi", "mcp-servers.json")
        if os.path.exists(legacy):
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    for name, srv in json.load(f).get("mcpServers", {}).items():
                        merged[name] = {
                            "name": name, "type": "mcp-stdio", "enabled": True,
                            "category": "legacy",
                            "command": _expand(srv.get("command", ""), ctx),
                            "args": [_expand(a, ctx) for a in (srv.get("args") or [])],
                            "env": srv.get("env", {}), "timeout": 30,
                            "desc": "旧版兼容导入", "source": "legacy",
                        }
                        order.append(name)
            except Exception as e:
                log_line(f"[WARN] 读取旧版 mcp-servers.json 失败: {e}")
    return [merged[n] for n in order]


def load_skills():
    """本地技能表：local 覆盖 example，同名按 name 合并。"""
    merged, order = {}, []
    for fname in ("skills_servers.example.json", "skills_servers.local.json"):
        fpath = os.path.join(BASE_DIR, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for sk in json.load(f).get("skills", []):
                    name = sk.get("name", "")
                    if not name:
                        continue
                    item = dict(sk)
                    item["type"] = "local-skill"
                    item["source"] = fname
                    if name not in merged:
                        order.append(name)
                    merged[name] = item
        except Exception as e:
            log_line(f"[WARN] 读取 {fname} 失败: {e}")
    return [merged[n] for n in order]


def load_registry():
    """统一注册表：MCP 插件 + 本地技能，同名 skill 优先（技能短路插件）。"""
    reg, seen = [], set()
    for sk in load_skills():
        reg.append(sk)
        seen.add(sk["name"])
    for srv in load_mcp_servers():
        if srv["name"] not in seen:
            reg.append(srv)
    return reg


def command_available(cmd):
    if not cmd:
        return False
    if os.path.isabs(cmd) or os.sep in cmd or "/" in cmd:
        return os.path.exists(cmd)
    return shutil.which(cmd) is not None


LOG_FP = None


def log_line(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    try:
        if LOG_FP:
            LOG_FP.write(line + "\n")
            LOG_FP.flush()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# local-skill 执行器（本进程内，毫秒级；失败一律结构化返回，不抛异常）
# ---------------------------------------------------------------------------

def _skill_clock(_args):
    now = datetime.datetime.now().astimezone()
    return {"ok": True, "iso": now.isoformat(timespec="seconds"),
            "weekday": now.strftime("%A"), "tz": str(now.tzinfo)}


def _skill_billing_stats(_args):
    fpath = os.path.join(BASE_DIR, "token_billing_stats.json")
    if not os.path.exists(fpath):
        return {"ok": False, "error": "no_billing_file",
                "hint": "账本尚未生成（网关跑起来后自动落盘），非故障"}
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        keys = list(data.keys()) if isinstance(data, dict) else []
        return {"ok": True, "size_bytes": os.path.getsize(fpath),
                "top_keys": keys[:10], "has_data": True}
    except Exception as e:
        return {"ok": False, "error": "billing_read_failed", "reason": str(e)}


def _skill_workspace_read(args):
    rel = (args.get("path") or args.get("file") or "").strip()
    if not rel:
        return {"ok": False, "error": "missing_path", "hint": "传参 path（相对根目录）"}
    # 沙盒：禁绝对路径、禁 ..、禁隐藏段/.git/__pycache__/node_modules
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    banned = {".git", "__pycache__", "node_modules"}
    if (os.path.isabs(rel) or ".." in parts or any(p.startswith(".") or p in banned for p in parts)):
        return {"ok": False, "error": "path_denied", "hint": "沙盒只读：根目录内非隐藏文件"}
    fpath = os.path.join(BASE_DIR, *parts)
    if not os.path.isfile(fpath):
        return {"ok": False, "error": "not_found", "path": "/".join(parts)}
    if os.path.getsize(fpath) > 65536:
        return {"ok": False, "error": "too_large", "hint": "单文件≤64KB，超限请分段读"}
    try:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            return {"ok": True, "path": "/".join(parts), "content": f.read()}
    except Exception as e:
        return {"ok": False, "error": "read_failed", "reason": str(e)}


def _skill_memory_recall(args):
    query = (args.get("query") or args.get("q") or "").strip()
    if not query:
        return {"ok": False, "error": "missing_query", "hint": "传参 query"}
    try:
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)
        import personal_memory as pm
        limit = int(args.get("limit", 3))
        hits = pm.recall_memory(query, limit=max(1, min(5, limit)))
        return {"ok": True, "query": query, "hits": hits}
    except Exception as e:
        return {"ok": False, "error": "recall_unavailable",
                "reason": f"{type(e).__name__}: {e}",
                "hint": "多为 8086 向量引擎未起或依赖缺失，非工具平面故障"}


SKILL_HANDLERS = {
    "clock": _skill_clock,
    "billing_stats": _skill_billing_stats,
    "workspace_read": _skill_workspace_read,
    "memory_recall": _skill_memory_recall,
}


class ToolPlaneHandler(BaseHTTPRequestHandler):
    server_version = "ToolPlane8087/1.1"

    def log_message(self, fmt, *args):
        log_line(f"[HTTP] {self.address_string()} {fmt % args}")

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(body.__len__()))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            reg = load_registry()
            on = [s for s in reg if s.get("enabled")]
            cats = {}
            for s in on:
                cats[s.get("category", "misc")] = cats.get(s.get("category", "misc"), 0) + 1
            self._send(200, {"status": "ok", "port": PORT,
                             "servers_total": len(reg), "servers_enabled": len(on),
                             "by_category": cats,
                             "servers": [s["name"] for s in on]})
        elif self.path in ("/tools/list", "/tools", "/list"):
            reg = load_registry()
            out = []
            for s in reg:
                item = {"name": s["name"], "type": s.get("type", "?"),
                        "category": s.get("category", "misc"),
                        "enabled": bool(s.get("enabled")),
                        "desc": s.get("desc", ""), "source": s.get("source", "")}
                if s.get("type") == "mcp-stdio":
                    item["command_available"] = command_available(s.get("command", ""))
                else:
                    item["handler_ready"] = s.get("handler") in SKILL_HANDLERS
                out.append(item)
            self._send(200, {"servers": out})
        elif self.path in ("/", "/dashboard"):
            reg = load_registry()
            self._send(200, {"service": "tool-plane-8087", "status": "ok",
                             "endpoints": ["/health", "/tools/list", "/tools/call"],
                             "servers": [s["name"] for s in reg if s.get("enabled")]})
        else:
            self._send(404, {"error": "not_found", "path": self.path})

    def do_POST(self):
        if self.path in ("/tools/call", "/call"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception:
                payload = {}
            name = payload.get("server") or payload.get("name") or payload.get("tool") or ""
            reg = {s["name"]: s for s in load_registry()}
            srv = reg.get(name)
            if not srv:
                self._send(404, {"error": "server_not_found", "server": name,
                                 "available": sorted(reg)})
                return
            if not srv.get("enabled"):
                self._send(403, {"error": "server_disabled", "server": name})
                return
            if srv.get("type") == "local-skill":
                fn = SKILL_HANDLERS.get(srv.get("handler", ""))
                if not fn:
                    self._send(501, {"error": "handler_missing", "server": name})
                    return
                try:
                    res = fn(payload.get("args", {}) if isinstance(payload.get("args"), dict) else payload)
                except Exception as e:
                    res = {"ok": False, "error": "handler_crashed", "reason": str(e)}
                self._send(200 if res.get("ok") else 503, {"server": name, "result": res})
            else:
                # mcp-stdio 的 JSON-RPC 直通在下一阶段实现， honest 501
                self._send(501, {"error": "not_implemented_yet", "server": name,
                                 "hint": "mcp-stdio 直通下一阶段；本地技能已可用"})
        else:
            self._send(404, {"error": "not_found", "path": self.path})


def run_selftest():
    """独立调试入口：不绑端口，只印注册表 + 健康 + 本地技能实测，换机器排错先跑它。"""
    print("== Tool Plane 8087 selftest ==")
    print(f"BASE_DIR={BASE_DIR}")
    reg = load_registry()
    on = [s for s in reg if s.get("enabled")]
    print(f"registry: total={len(reg)} enabled={len(on)}")
    ok = True
    for s in reg:
        if s.get("type") == "mcp-stdio":
            avail = command_available(s.get("command", ""))
            flag = "OK " if (avail or not s.get("enabled")) else "MISS"
            if s.get("enabled") and not avail:
                ok = False
            print(f"  [{flag}] mcp-stdio  {s['name']:14s} cat={s.get('category','misc'):9s} "
                  f"en={int(bool(s.get('enabled')))} cmd={s.get('command','')[:60]} src={s.get('source','')}")
        else:
            ready = s.get("handler") in SKILL_HANDLERS
            flag = "OK " if (ready or not s.get("enabled")) else "MISS"
            if s.get("enabled") and not ready:
                ok = False
            print(f"  [{flag}] local-skill {s['name']:14s} cat={s.get('category','misc'):9s} "
                  f"en={int(bool(s.get('enabled')))} handler={s.get('handler','')} src={s.get('source','')}")
    # 本地技能实测（clock 必过；billing/memory 按环境明示 503 也算正常）
    for name, args in (("clock", {}), ("billing_stats", {}),):
        fn = SKILL_HANDLERS[{"clock": "clock", "billing_stats": "billing_stats"}[name]]
        try:
            res = fn(args)
            print(f"  [..] skill {name}: ok={res.get('ok')} "
                  f"{str(res)[:120]}")
        except Exception as e:
            ok = False
            print(f"  [FAIL] skill {name}: {e}")
    print("SELFTEST " + ("PASS" if ok else "FAIL(有 MISS，多为本机缺命令，按行处理)"))
    return 0 if ok else 1


def main():
    global LOG_FP
    if SELFTEST:
        sys.exit(run_selftest())
    today = time.strftime("%Y%m%d")
    log_file = os.path.join(LOGS_DIR, f"8087_mcp_{today}.log")
    try:
        LOG_FP = open(log_file, "a", encoding="utf-8", buffering=1)
        LOG_FP.write(f"\n--- [8087 ToolPlane Session at {time.strftime('%Y-%m-%d %H:%M:%S')}] ---\n")
        LOG_FP.flush()
    except Exception:
        LOG_FP = None
    reg = load_registry()
    log_line(f"🔌 工具平面 8087 启动：{len(reg)} 项（{[s['name'] for s in reg]}），日志 {os.path.basename(log_file)}")
    httpd = ThreadingHTTPServer((HOST, PORT), ToolPlaneHandler)
    log_line(f"✅ 监听 http://{HOST}:{PORT}（仅本机）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
        if LOG_FP:
            try:
                LOG_FP.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
