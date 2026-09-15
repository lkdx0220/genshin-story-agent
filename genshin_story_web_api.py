#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神剧情助手 - Web API 服务端

基于 Flask 提供 RESTful API，供浏览器/手机访问。
"""

import os
import sys
import json
import hashlib
import hmac
import queue
import tempfile
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Any

# 本地默认走 HF 镜像并禁网（避免启动时探测 huggingface.co）；容器/云端部署用官方端点。
if os.getenv("CLOUD_DEPLOY") != "1":
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from flask import Flask, g, request, jsonify, render_template_string, Response
from flask_cors import CORS
import re

# 导入 Agent 工具
try:
    from genshin_story_agent import (
        tools, rag_memory, RAG_AVAILABLE,
        角色知识库, 地区知识库, 主线剧情知识库, 武器知识库, 任务知识库,
        _summarize_conversation, SUMMARY_TRIGGER, RECENT_TURNS,
    )
    AGENT_OK = True
    print("[Web] Agent 模块加载成功")
except ImportError as e:
    AGENT_OK = False
    tools = []
    print(f"[Web] Agent 加载失败: {e}")

app = Flask(__name__)
CORS(app)
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024

# ====== API 访问控制 ======
# 未配置任何口令时仅允许本机访问；配置后远程须携带 Bearer Token。
# 每把口令各自对应一个会话命名空间（见 _tenant_session_dir）：
# 不同 k 的会话列表互相不可见，访客也看不到本机 conversation_memory/ 里的历史。
API_TOKEN = os.getenv("API_TOKEN", "").strip()
API_TOKENS_EXTRA = os.getenv("API_TOKENS", "").strip()  # 形如 "alice:tokenA,bob:tokenB"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", None}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TENANT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _auto_tenant_name(token: str) -> str:
    """未显式命名的口令按哈希取稳定短名：重启后同一口令仍落在同一份会话列表"""
    return "tok_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def _build_token_map() -> Dict[str, str]:
    """口令 -> 会话命名空间。API_TOKENS 支持显式命名："alice:tokenA,bob:tokenB"。"""
    mapping: Dict[str, str] = {}
    for item in API_TOKENS_EXTRA.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, token = item.partition(":")
        name, token = name.strip(), token.strip()
        if not sep or not name or not token:
            print("[口令配置] 忽略一项（格式应为 名称:口令）")
            continue
        if not _TENANT_NAME_RE.fullmatch(name):
            print(f"[口令配置] 忽略一项（名称只允许字母数字下划线短横，1-32 位）: {name!r}")
            continue
        mapping[token] = name
    if API_TOKEN:
        mapping.setdefault(API_TOKEN, _auto_tenant_name(API_TOKEN))
    return mapping


TOKEN_MAP = _build_token_map()
if TOKEN_MAP:
    print(f"[口令配置] 启用 {len(TOKEN_MAP)} 把口令，各持独立会话列表")


def _is_local_request() -> bool:
    return (request.remote_addr or "") in _LOCAL_HOSTS or (request.remote_addr or "").startswith("127.")


def _match_token() -> str:
    """返回请求携带的合法口令原文；无匹配返回空串。

    逐个常数时间比较且不提前退出，避免按响应耗时逐字节试探口令。
    """
    provided = request.headers.get("Authorization", "")
    matched = ""
    for token in TOKEN_MAP:
        if hmac.compare_digest(provided, f"Bearer {token}"):
            matched = token
    return matched


def _has_valid_token() -> bool:
    """请求是否携带任一有效口令（未配置口令时恒为 False）。"""
    return bool(_match_token())


def _check_api_access():
    """保护所有 /api/* 路由。

    - 未配置口令：仅允许本机访问（本地启动器默认场景）。
    - 配置了口令：所有来源（含本机）都要求 Bearer Token。
      注意：经 Cloudflare Tunnel 等本地代理访问时，remote_addr 恒为 127.0.0.1；
      若仍保留「本机免 token」，隧道外的任何人都等同于本机，口令形同虚设。
    """
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        return None
    token = _match_token()
    g.tenant = TOKEN_MAP.get(token) if token else None  # 无口令或本地直连 → 根目录
    if request.path == "/api/health":
        return None  # 健康检查公开可读，供云平台/隧道探活
    if not TOKEN_MAP:
        if _is_local_request():
            return None
        return jsonify({
            "error": "该 API 仅限本机访问。若需远程访问，请在服务端配置 API_TOKEN。"
        }), 403
    if not token:
        return jsonify({"error": "无效或缺失 API_TOKEN"}), 403
    return None


app.before_request(_check_api_access)

SERVICE_VERSION = "web-1.0"


@app.route('/api/health', methods=['GET'])
def api_health():
    """健康检查：供云平台/隧道探活（公开可读，不含敏感信息）。"""
    kb_counts = {}
    try:
        # 统计接口在 KBVectorStore 实例上，运行时单例由 app/data.py 顶层初始化
        from app.data import _vector_store
        if _vector_store is not None:
            kb_counts = _vector_store.get_stats()
    except Exception as e:
        print(f"[health] 读取向量库统计失败: {type(e).__name__}: {e}")
        kb_counts = {}
    payload = {
        "status": "ok",
        "service": "genshin-story-agent-web",
        "version": SERVICE_VERSION,
        "agent_ok": AGENT_OK,
        "l3_enabled": os.getenv("L3_ENABLED", "1") == "1",
        "kb_counts": kb_counts,
    }
    # 部署细节（本地路径等）只对可信来源返回，避免公开端点泄漏服务端目录结构。
    # 配置了口令时（隧道/云端）必须校验 Token：此时 remote_addr 恒为 127.0.0.1，不能仅凭来源地址判断。
    if (not TOKEN_MAP and _is_local_request()) or _has_valid_token():
        payload["tenant"] = _current_tenant() or "local"  # 便于确认自己在哪个会话命名空间
        # 上报"实际生效"的向量目录与嵌入后端：环境变量常常没设（本地实例就没设），
        # kb_vector_store 会按默认值回退（kb_vectors_m3 + bge-m3），只读 env 会得到空串。
        try:
            import kb_vector_store as _kvs
            vector_dir = str(getattr(_kvs, "RUNTIME_VECTOR_DIR", "") or "")
            embedding_backend = str(getattr(_kvs, "RUNTIME_EMBEDDING_BACKEND", "") or "")
            from app.data import _vector_store
            if _vector_store is not None:
                vector_dir = str(getattr(_vector_store, "vector_dir", "") or vector_dir)
                embedding_backend = str(getattr(_vector_store, "embedding_backend", "") or embedding_backend)
        except Exception as e:
            print(f"[health] 读取向量库运行时配置失败: {type(e).__name__}: {e}")
            vector_dir = os.getenv("KB_VECTOR_DIR", "")
            embedding_backend = os.getenv("KB_EMBEDDING_BACKEND", "")
        payload["vector_dir"] = vector_dir
        payload["embedding_backend"] = embedding_backend
    return jsonify(payload)


# Agent 工作流单例：gunicorn 单 worker 下由各请求线程共享，避免每次请求重复编译图
_workflow_cache = None
_workflow_lock = threading.Lock()


def _get_workflow():
    global _workflow_cache
    if _workflow_cache is None:
        with _workflow_lock:
            if _workflow_cache is None:
                import genshin_story_agent as agent_module
                _workflow_cache = agent_module.create_agent_workflow()
    return _workflow_cache

# 取消信号映射：session_id → threading.Event
_cancel_events: Dict[str, threading.Event] = {}
# session_id → run_id 映射，供 /api/cancel 通过 session_id 定位运行实例
_session_cancel_keys: Dict[str, str] = {}

# ====== 内存级限流（防滥用/防恶意刷接口） ======
# 结构: 路径前缀 -> (窗口内允许次数, 窗口秒数)
_RATE_LIMITS = {
    "/chat": (30, 60),
    "/api/status": (60, 60),
    "/api/sessions": (30, 60),
    "/api/quest": (30, 60),
    "/api/search": (30, 60),
    "/api/chat": (10, 60),
}
_DEFAULT_RATE_LIMIT = (60, 60)


def _env_int(name: str, default: int) -> int:
    """读整型环境变量；缺失或非法时退回默认（部署时手滑不该让服务起不来）。"""
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


# 日配额：滑动窗口挡不住「慢速刷」（10 次/分钟累积起来一天也有一万多次），
# 这里给演示站兜底，主要防「链接被转发出去后无上限烧 API 配额」。
# 进程重启（HF 休眠唤醒、重新部署）会清零，属尽力而为的成本阻尼，不是安全边界。
_DAILY_LIMITS = {
    "/api/chat": _env_int("API_DAILY_CHAT_LIMIT", 120),
}
_rate_records: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()
_daily_records: Dict[str, list] = {}
_daily_lock = threading.Lock()


def _get_rate_limit(path: str):
    for prefix, cfg in _RATE_LIMITS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return cfg, prefix
    return _DEFAULT_RATE_LIMIT, path


def _get_daily_limit(path: str) -> int:
    for prefix, limit in _DAILY_LIMITS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return limit
    return 0


def _client_key() -> str:
    """限流用的客户端标识：优先取代理链最左侧的 X-Forwarded-For（HF / Cloudflare 会写入真实访客 IP），
    否则退回 remote_addr。注意：这只是成本阻尼标识，不是安全边界（XFF 可伪造）；
    真正的访问控制是 Bearer Token。
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.remote_addr or "unknown"


def _check_rate_limit():
    """Flask before_request 钩子：按客户端 + 路径前缀做滑动窗口限流，并对 /api/chat 计日配额。"""
    client = _client_key()
    (limit, window), key_prefix = _get_rate_limit(request.path)
    key = f"{client}:{key_prefix}"
    now = time.time()
    with _rate_lock:
        q = _rate_records[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return jsonify({
                "error": "请求过于频繁，请稍后重试",
                "retry_after": int(window),
            }), 429, {"Retry-After": str(int(window))}
        q.append(now)

    daily_limit = _get_daily_limit(request.path)
    if daily_limit:
        today = time.strftime("%Y-%m-%d")
        dkey = f"{client}:{request.path}"
        with _daily_lock:
            if len(_daily_records) > 2000:  # 顺手清掉隔天记录，避免长期运行内存增长
                for k in [k for k, v in _daily_records.items() if v[0] != today]:
                    _daily_records.pop(k, None)
            rec = _daily_records.get(dkey)
            if rec is None or rec[0] != today:
                rec = [today, 0]
                _daily_records[dkey] = rec
            if rec[1] >= daily_limit:
                retry_after = max(1, int(86400 - (now % 86400)))
                return jsonify({
                    "error": "已达到今日使用上限，请明天再试",
                    "retry_after": retry_after,
                }), 429, {"Retry-After": str(retry_after)}
            rec[1] += 1
    return None


app.before_request(_check_rate_limit)



def _find_tool(name: str):
    for t in tools:
        if t.name == name:
            return t
    return None


# ====== 聊天 UI ======
@app.route('/chat')
def chat_ui():
    """提供聊天客户端界面"""
    ui_path = os.path.join(os.path.dirname(__file__), 'chat_ui.html')
    if os.path.exists(ui_path):
        try:
            with open(ui_path, 'r', encoding='utf-8') as f:
                return f.read()
        except OSError as e:
            return f"<h1>读取 chat_ui.html 失败: {e}</h1>", 500
    return "<h1>chat_ui.html 未找到</h1>", 404


# ====== 首页 ======
@app.route('/')
def index():
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>原神剧情助手 API</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Microsoft YaHei',sans-serif;background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);min-height:100vh;padding:20px;color:#e0e0e0}
.container{max-width:700px;margin:0 auto;background:rgba(255,255,255,.06);border-radius:16px;padding:30px;border:1px solid rgba(255,255,255,.1)}
h1{text-align:center;color:#ffd700;margin-bottom:8px;font-size:26px}
.sub{text-align:center;color:#888;font-size:13px;margin-bottom:20px}
.status{text-align:center;padding:10px;background:rgba(76,175,80,.2);border-radius:8px;margin:15px 0;color:#66bb6a}
.api-item{background:rgba(255,255,255,.04);padding:12px 16px;margin:8px 0;border-radius:8px;border-left:3px solid #ffd700}
.api-title{font-weight:bold;color:#ffd700}
.api-url{color:#aaa;font-size:13px;margin-top:4px}
.api-desc{color:#888;font-size:12px;margin-top:4px}
pre{background:rgba(0,0,0,.3);padding:14px;border-radius:8px;overflow-x:auto;font-size:12px;color:#ccc}
</style>
</head>
<body>
<div class="container">
<h1>原神剧情助手 API</h1>
<p class="sub">提瓦特冒险向导 · 剧情检索服务</p>
<div class="status">API 运行中 | 知识库: """ + str(len(角色知识库) if AGENT_OK else 0) + """位角色</div>

<h3 style="color:#ffd700;margin-top:20px">API 端点</h3>
<div class="api-item"><div class="api-title">角色查询</div><div class="api-url">POST /api/character</div><div class="api-desc">{"character": "胡桃"}</div></div>
<div class="api-item"><div class="api-title">地区查询</div><div class="api-url">POST /api/region</div><div class="api-desc">{"region": "璃月"}</div></div>
<div class="api-item"><div class="api-title">剧情查询</div><div class="api-url">POST /api/story</div><div class="api-desc">{"story": "辞行久远之躯"}</div></div>
<div class="api-item"><div class="api-title">武器查询</div><div class="api-url">POST /api/weapon</div><div class="api-desc">{"weapon": "护摩之杖"}</div></div>
<div class="api-item"><div class="api-title">任务查询</div><div class="api-url">POST /api/quest</div><div class="api-desc">{"quest": "引蝶之章"}</div></div>
<div class="api-item"><div class="api-title">元素角色列表</div><div class="api-url">POST /api/element</div><div class="api-desc">{"element": "火"}</div></div>
<div class="api-item"><div class="api-title">全局搜索</div><div class="api-url">POST /api/search</div><div class="api-desc">{"query": "雷电将军"}</div></div>
<div class="api-item"><div class="api-title">智能对话</div><div class="api-url">POST /api/chat</div><div class="api-desc">{"message": "胡桃是谁？"}</div></div>

<p style="text-align:center;color:#666;margin-top:24px;font-size:12px">数据来源: Bilibili 原神 Wiki</p>
</div>
</body>
</html>"""
    return render_template_string(html)


@app.route('/api/status')
def status():
    return jsonify({
        "status": "running",
        "agent": AGENT_OK,
        "rag": RAG_AVAILABLE if AGENT_OK else False,
        "knowledge": {
            "characters": len(角色知识库) if AGENT_OK else 0,
            "regions": len(地区知识库) if AGENT_OK else 0,
            "stories": len(主线剧情知识库) if AGENT_OK else 0,
        },
        "timestamp": datetime.now().isoformat(),
    })


@app.route('/api/shutdown')
def api_shutdown():
    """远程关闭服务器（launcher 调用，清理残留进程）"""
    os._exit(0)
    return ""  # unreachable, 但 Flask 需要返回值


@app.route('/api/sessions')
def api_sessions():
    """列出磁盘上已保存的会话（只列当前口令命名空间内的）"""
    sessions = []
    session_dir = _tenant_session_dir(_current_tenant())
    if os.path.exists(session_dir):
        for fname in sorted(os.listdir(session_dir), reverse=True):
            if not fname.startswith("session_") or not fname.endswith(".json"):
                continue
            session_id = fname[8:-5]  # 去掉 "session_" 前缀和 ".json" 后缀
            if not _valid_session_id(session_id):
                continue  # 目录里手工放入的异常文件名不进列表（前端会把 id 拼进 HTML）
            fpath = os.path.join(session_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            pairs = data.get("pairs", [])
            # 提取标题（第一条用户消息的前30字）
            title = "新对话"
            last_message = ""
            if pairs:
                title = pairs[0].get("user", "")[:30]
                if pairs[-1].get("user"):
                    last_message = pairs[-1]["user"][:50]
            sessions.append({
                "id": session_id,
                "title": title,
                "count": len(pairs),
                "last_message": last_message,
            })
    return jsonify({"sessions": sessions})


@app.route('/api/cancel', methods=['POST'])
def api_cancel():
    """中断当前会话的 Agent 运行"""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    if not _valid_session_id(session_id):
        return jsonify({"success": False, "error": "非法的 session_id"}), 400
    session_key = _session_key(_current_tenant(), session_id)
    run_id = _session_cancel_keys.get(session_key)
    if not run_id:
        return jsonify({"success": False, "error": "未找到运行中的会话"}), 404
    cancel_event = _cancel_events.get(run_id)
    if cancel_event:
        cancel_event.set()
        print(f"[取消] session={session_id[:20]}... 已设置中断信号")
        return jsonify({"success": True, "cancelled": session_id})
    return jsonify({"success": False, "error": "未找到运行中的会话"}), 404


@app.route('/api/sessions/<session_id>', methods=['GET', 'DELETE'])
def api_session_detail(session_id):
    """获取或删除指定会话"""
    if not _valid_session_id(session_id):
        return jsonify({"success": False, "error": "非法的 session_id"}), 400
    tenant = _current_tenant()
    if request.method == 'DELETE':
        fpath = _session_file(session_id, tenant)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                return jsonify({"success": True, "deleted": session_id})
            except Exception as e:
                print(f"[会话删除失败] {type(e).__name__}: {e}")
                return jsonify({"success": False, "error": "删除会话失败，请查看服务端日志"}), 500
        return jsonify({"success": False, "error": "会话不存在"}), 404

    data = _load_session_from_disk(session_id, tenant)
    return jsonify(data)


@app.route('/api/character', methods=['POST'])
def api_character():
    tool = _find_tool("query_character")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "character" not in data:
        return jsonify({"error": "缺少 character 参数"}), 400
    result = tool.invoke(data["character"])
    return jsonify({"success": True, "result": result})


@app.route('/api/region', methods=['POST'])
def api_region():
    tool = _find_tool("query_region")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "region" not in data:
        return jsonify({"error": "缺少 region 参数"}), 400
    result = tool.invoke(data["region"])
    return jsonify({"success": True, "result": result})


@app.route('/api/story', methods=['POST'])
def api_story():
    tool = _find_tool("query_story")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "story" not in data:
        return jsonify({"error": "缺少 story 参数"}), 400
    result = tool.invoke(data["story"])
    return jsonify({"success": True, "result": result})


@app.route('/api/weapon', methods=['POST'])
def api_weapon():
    tool = _find_tool("query_weapon")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "weapon" not in data:
        return jsonify({"error": "缺少 weapon 参数"}), 400
    try:
        result = tool.invoke(data["weapon"])
    except Exception as e:
        return jsonify({"error": "查询武器信息失败"}), 500
    return jsonify({"success": True, "result": result})


@app.route('/api/quest', methods=['POST'])
def api_quest():
    tool = _find_tool("query_quest")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "quest" not in data:
        return jsonify({"error": "缺少 quest 参数"}), 400
    result = tool.invoke(data["quest"])
    return jsonify({"success": True, "result": result})


@app.route('/api/element', methods=['POST'])
def api_element():
    tool = _find_tool("list_characters_by_element")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "element" not in data:
        return jsonify({"error": "缺少 element 参数"}), 400
    result = tool.invoke(data["element"])
    return jsonify({"success": True, "result": result})


@app.route('/api/search', methods=['POST'])
def api_search():
    tool = _find_tool("hybrid_search")
    if not tool:
        return jsonify({"error": "模块未加载"}), 500
    data = request.get_json()
    if not data or "query" not in data:
        return jsonify({"error": "缺少 query 参数"}), 400
    result = tool.invoke(data["query"])
    return jsonify({"success": True, "result": result})


# 会话存储（简单内存字典，单机使用）
# 会话按口令分命名空间：每个 k 只看得见自己的列表；本地直连（无口令）落根目录。
_sessions: Dict[str, list] = {}
SESSION_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_memory")


def _valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(session_id or ""))


def _tenant_session_dir(tenant: str = None) -> str:
    """当前命名空间的会话目录。

    命名空间名来自 API_TOKENS 的显式命名或口令哈希，只含 [A-Za-z0-9_-]，
    因此不会越出 conversation_memory/；本地直连（tenant=None）用根目录。
    """
    path = os.path.join(SESSION_ROOT, tenant) if tenant else SESSION_ROOT
    os.makedirs(path, exist_ok=True)
    return path


def _current_tenant():
    """本次请求所属的会话命名空间；None = 本地直连。只能在请求上下文中调用。"""
    return getattr(g, "tenant", None)


def _session_key(tenant: str, session_id: str) -> str:
    """内存缓存的键：带命名空间前缀，避免不同口令的同名 session_id 互相串到。"""
    return f"{tenant or '_local'}:{session_id}"


def _session_file(session_id: str, tenant: str = None) -> str:
    """获取会话存储文件路径"""
    if not _valid_session_id(session_id):
        raise ValueError("非法的 session_id")
    return os.path.join(_tenant_session_dir(tenant), f"session_{session_id}.json")


def _load_session_from_disk(session_id: str, tenant: str = None) -> dict:
    """从 JSON 文件加载会话记录"""
    fpath = _session_file(session_id, tenant)
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pairs": [], "summary": ""}


def _save_session_to_disk(session_id: str, data: dict, tenant: str = None):
    """将会话记录写入 JSON 文件（随机临时文件 + 原子替换，避免并发写互相截断）"""
    if not _valid_session_id(session_id):
        raise ValueError("非法的 session_id")
    session_dir = _tenant_session_dir(tenant)
    fpath = os.path.join(session_dir, f"session_{session_id}.json")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="session_", suffix=".tmp", dir=session_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fpath)
    except Exception as e:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        print(f"[会话写入失败] {type(e).__name__}: {e}")


def _extract_tool_calls(state: Dict) -> list:
    """从 state 的 messages 中提取工具调用记录，返回前端友好格式"""
    messages = state.get("messages", [])
    if not messages:
        return []
    calls = []
    for msg in messages:
        msg_type = type(msg).__name__
        if msg_type == "AIMessage":
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    calls.append({
                        "tool": tc.get("name", "?"),
                        "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    })
    return calls




@app.route('/api/chat', methods=['POST'])
def api_chat():
    import genshin_story_agent as agent_module
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "缺少 message 参数"}), 400

    message = data["message"]
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "message 必须是非空字符串"}), 400
    if len(message) > 5000:
        return jsonify({"error": "message 过长，最多 5000 字"}), 400

    session_id = data.get("session_id", "default")
    if not _valid_session_id(session_id):
        return jsonify({"error": "非法的 session_id"}), 400

    # 加载历史（按口令命名空间隔离：不同 k 看不到彼此的会话列表）
    tenant = _current_tenant()
    session_key = _session_key(tenant, session_id)
    history = _sessions.get(session_key)
    if not history:
        history = _load_session_from_disk(session_id, tenant)
        _sessions[session_key] = history
    conv_pairs = history.get("pairs", [])
    conv_summary = history.get("summary", "")
    progress_queue = queue.Queue()

    def progress_hook(event):
        progress_queue.put(event)

    def run_agent():
        try:
            agent_module.set_progress_hook(progress_hook)
            agent_module._cancel_events[run_id] = cancel_event
            agent = _get_workflow()
            result = agent.invoke({
                "user_query": message,
                "rewritten_query": None,
                "alias_notes": None,
                "conversation_history": conv_pairs,
                "conversation_summary": conv_summary,
                "messages": [],
                "final_response": None,
                "iteration": 0,
                "plan_retry": 0,
                "execution_plan": None,
                "run_id": run_id,
            })
            answer = result.get("final_response", "无结果")
            tool_calls = _extract_tool_calls(result)
            progress_queue.put({
                "type": "done",
                "answer": answer,
                "tool_calls": tool_calls,
                "session_id": session_id,
                "message": message,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            # 只回传异常类型，完整堆栈留在服务端日志，避免向客户端泄漏内部路径
            progress_queue.put({"type": "error", "error": f"服务器内部错误（{type(e).__name__}），详情见服务端日志"})
        finally:
            agent_module.set_progress_hook(None)
            agent_module._cancel_events.pop(run_id, None)

    # 生成运行标识并注册取消信号（完整 UUID，避免短号被猜测后取消他人任务）
    import uuid
    run_id = str(uuid.uuid4())
    cancel_event = threading.Event()
    _cancel_events[run_id] = cancel_event
    # 会话键 → run_id 映射，供 /api/cancel 查找（键含命名空间，避免跨口令误取消）
    _session_cancel_keys[session_key] = run_id

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    def generate():
        nonlocal conv_pairs, conv_summary
        final_event = None
        while True:
            try:
                event = progress_queue.get(timeout=300)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "done":
                    answer_text = event.get("answer", "")
                    if "[回答已中断]" in answer_text:
                        # 路径A：Agent 内部检测到取消 → 发送 cancelled 事件给前端
                        yield f"data: {json.dumps({'type': 'cancelled', 'answer': answer_text}, ensure_ascii=False)}\n\n"
                        final_event = "cancelled"
                    else:
                        conv_pairs.append({"user": message, "assistant": answer_text})
                        if len(conv_pairs) > SUMMARY_TRIGGER:
                            turns_to_summarize = conv_pairs[:-RECENT_TURNS]
                            if turns_to_summarize:
                                conv_summary = _summarize_conversation(conv_summary, turns_to_summarize)
                                conv_pairs = conv_pairs[-RECENT_TURNS:]
                        final_event = "done"
                    break
                if event.get("type") == "error":
                    final_event = "error"
                    break
            except queue.Empty:
                if cancel_event.is_set():
                    # 路径B：队列超时 + 取消事件 → 发送 cancelled 给前端
                    yield f"data: {json.dumps({'type': 'cancelled', 'answer': '[回答已中断] 当前任务已被用户终止。'}, ensure_ascii=False)}\n\n"
                    final_event = "cancelled"
                    break
                yield f"data: {json.dumps({'type': 'timeout', 'error': '回答生成超时，请简化问题后重试'}, ensure_ascii=False)}\n\n"
                final_event = "timeout"
                break

        # 清理
        _cancel_events.pop(run_id, None)
        _session_cancel_keys.pop(session_key, None)

        # 保存会话（所有路径都保留用户消息）
        if final_event == "done":
            _sessions[session_key] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_key], tenant)
        elif final_event == "cancelled":
            # 中断：保存用户消息但不保存 AI 回复
            conv_pairs.append({"user": message, "assistant": ""})
            _sessions[session_key] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_key], tenant)
        else:
            conv_pairs.append({"user": message, "assistant": ""})
            _sessions[session_key] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_key], tenant)

    return Response(generate(), mimetype='text/event-stream')


if __name__ == '__main__':
    import socket
    import urllib.request

    def _port_free(port: int) -> bool:
        """检查 127.0.0.1 上端口是否空闲。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) != 0

    def _pick_port() -> int:
        """优先使用 5000；被本项目旧实例占用时先关闭旧实例，仍占用则找空闲端口。"""
        if _port_free(5000):
            return 5000

        print("[启动] 检测到 5000 端口被旧实例占用，尝试关闭旧实例...")
        try:
            urllib.request.urlopen("http://127.0.0.1:5000/api/shutdown", timeout=2)
        except Exception:
            # /api/shutdown 正常响应前就会退出进程，没有响应体是正常的
            pass

        for _ in range(5):
            time.sleep(1)
            if _port_free(5000):
                print("[启动] 旧实例已关闭，继续使用端口 5000")
                return 5000

        print("[启动] 端口 5000 仍被占用（可能不是本项目服务），自动寻找空闲端口...")
        for port in range(5001, 5051):
            if _port_free(port):
                print(f"[启动] 使用空闲端口 {port}")
                return port
        raise SystemExit("找不到可用端口，请先释放 5000 附近端口")

    port = _pick_port()
    print()
    print("=" * 50)
    print("  原神剧情助手 Web API")
    print("=" * 50)
    print(f"  地址: http://localhost:{port}")
    print("  接口: /api/character /api/region /api/story /api/chat 等")
    print("=" * 50)
    app.run(host='127.0.0.1', port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
