#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神剧情助手 - Web API 服务端

基于 Flask 提供 RESTful API，供浏览器/手机访问。
"""

import os
import sys
import json
import queue
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, Any

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from flask import Flask, request, jsonify, render_template_string, Response
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
# 未配置 API_TOKEN 时仅允许本机访问；配置后远程须携带 Bearer Token。
API_TOKEN = os.getenv("API_TOKEN", "").strip()
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", None}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_local_request() -> bool:
    return (request.remote_addr or "") in _LOCAL_HOSTS or (request.remote_addr or "").startswith("127.")


def _check_api_access():
    """保护所有 /api/* 路由：本机免 token，远程需 Bearer Token。"""
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        return None
    if _is_local_request():
        return None
    if not API_TOKEN:
        return jsonify({
            "error": "该 API 仅限本机访问。若需远程访问，请在服务端配置 API_TOKEN。"
        }), 403
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        return jsonify({"error": "无效或缺失 API_TOKEN"}), 403
    return None


app.before_request(_check_api_access)

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
    "/api/chat": (10, 60),
}
_DEFAULT_RATE_LIMIT = (60, 60)
_rate_records: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _get_rate_limit(path: str):
    for prefix, cfg in _RATE_LIMITS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return cfg, prefix
    return _DEFAULT_RATE_LIMIT, path


def _check_rate_limit():
    """Flask before_request 钩子：按客户端 IP + 路径前缀做滑动窗口限流。"""
    (limit, window), key_prefix = _get_rate_limit(request.path)
    key = f"{request.remote_addr or 'unknown'}:{key_prefix}"
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
    _rl = _check_rate_limit()
    if _rl is not None:
        return _rl
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
    _rl = _check_rate_limit()
    if _rl is not None:
        return _rl
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
    """列出磁盘上所有已保存的会话"""
    sessions = []
    if os.path.exists(SESSION_DIR):
        for fname in sorted(os.listdir(SESSION_DIR), reverse=True):
            if not fname.startswith("session_") or not fname.endswith(".json"):
                continue
            fpath = os.path.join(SESSION_DIR, fname)
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
                "id": fname[8:-5],  # 去掉 "session_" 前缀和 ".json" 后缀
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
    run_id = _session_cancel_keys.get(session_id)
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
    _rl = _check_rate_limit()
    if _rl is not None:
        return _rl
    if not _valid_session_id(session_id):
        return jsonify({"success": False, "error": "非法的 session_id"}), 400
    if request.method == 'DELETE':
        fpath = _session_file(session_id)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
                return jsonify({"success": True, "deleted": session_id})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": False, "error": "会话不存在"}), 404

    data = _load_session_from_disk(session_id)
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
    _rl = _check_rate_limit()
    if _rl is not None:
        return _rl
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
_sessions: Dict[str, list] = {}
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_memory")


def _valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.fullmatch(session_id or ""))


def _session_file(session_id: str) -> str:
    """获取会话存储文件路径"""
    if not _valid_session_id(session_id):
        raise ValueError("非法的 session_id")
    os.makedirs(SESSION_DIR, exist_ok=True)
    return os.path.join(SESSION_DIR, f"session_{session_id}.json")


def _load_session_from_disk(session_id: str) -> dict:
    """从 JSON 文件加载会话记录"""
    fpath = _session_file(session_id)
    if os.path.exists(fpath):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pairs": [], "summary": ""}


def _save_session_to_disk(session_id: str, data: dict):
    """将会话记录写入 JSON 文件（原子写入）"""
    fpath = _session_file(session_id)
    tmp = fpath + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, fpath)
    except Exception:
        pass


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
    _rl = _check_rate_limit()
    if _rl is not None:
        return _rl
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

    # 加载历史
    history = _sessions.get(session_id)
    if not history:
        history = _load_session_from_disk(session_id)
        _sessions[session_id] = history
    conv_pairs = history.get("pairs", [])
    conv_summary = history.get("summary", "")
    progress_queue = queue.Queue()

    def progress_hook(event):
        progress_queue.put(event)

    def run_agent():
        try:
            agent_module.set_progress_hook(progress_hook)
            agent_module._cancel_events[run_id] = cancel_event
            agent = agent_module.create_agent_workflow()
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
            progress_queue.put({"type": "error", "error": str(e)})
        finally:
            agent_module.set_progress_hook(None)
            agent_module._cancel_events.pop(run_id, None)

    # 生成运行标识并注册取消信号
    import uuid
    run_id = str(uuid.uuid4())[:8]
    cancel_event = threading.Event()
    _cancel_events[run_id] = cancel_event
    # session_id → run_id 映射，供 /api/cancel 查找
    _session_cancel_keys[session_id] = run_id

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
        _session_cancel_keys.pop(session_id, None)

        # 保存会话（所有路径都保留用户消息）
        if final_event == "done":
            _sessions[session_id] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_id])
        elif final_event == "cancelled":
            # 中断：保存用户消息但不保存 AI 回复
            conv_pairs.append({"user": message, "assistant": ""})
            _sessions[session_id] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_id])
        else:
            conv_pairs.append({"user": message, "assistant": ""})
            _sessions[session_id] = {"pairs": conv_pairs, "summary": conv_summary}
            _save_session_to_disk(session_id, _sessions[session_id])

    return Response(generate(), mimetype='text/event-stream')


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("  原神剧情助手 Web API")
    print("=" * 50)
    print("  地址: http://localhost:5000")
    print("  接口: /api/character /api/region /api/story /api/chat 等")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=os.getenv("FLASK_DEBUG", "0") == "1")
