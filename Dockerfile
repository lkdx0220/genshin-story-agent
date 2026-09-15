# 云端演示镜像（Hugging Face Spaces / 任意 Docker 平台）
#
# 设计要点：
#   1) 向量检索用 kb_vectors/（千问 text-embedding-v4 建的索引），查询时 HTTP 调 DashScope，
#      所以镜像里不需要 torch / sentence-transformers / chromadb（长期记忆在云端裁剪）。
#   2) 词条图（wiki_entry_graph.json / wiki_entity_mention_index.json）就放在 kb_vectors/ 里，
#      KB_GRAPH_DIR 指过去即可，构建脚本与运行时读的是同一个目录（见 wiki_entry_graph.py）。
#   3) 密钥不入镜像：API_TOKEN / DASHSCOPE_API_KEY / DASHSCOPE_FALLBACK_API_KEY / DEEPSEEK_API_KEY
#      等由平台的 Environment Variables 注入，本地 .env 已被 .dockerignore 排除。
#   4) L3_ENABLED=0：L3 全景题一次生成几万字、耗时长，演示站默认关闭，需要时改环境变量开。
#   5) 只装运行时真正 import 的包（见 requirements-web.txt 注释），镜像与冷启动都更小。

FROM python:3.11-slim

# 本机构建可用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 走国内镜像
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CLOUD_DEPLOY=1 \
    L3_ENABLED=0 \
    KB_VECTOR_DIR=/app/kb_vectors \
    KB_EMBEDDING_BACKEND=text-embedding-v4 \
    KB_GRAPH_DIR=/app/kb_vectors \
    PORT=7860

WORKDIR /app

COPY requirements-web.txt ./
RUN pip install -i "${PIP_INDEX_URL}" -r requirements-web.txt

# 代码与数据（文件清单由入口 import 闭包与运行时读盘记录确定，见 .dockerignore 注释）
COPY app/ app/
COPY prompts/ prompts/
COPY genshin_knowledge_base/ genshin_knowledge_base/
COPY kb_vectors/ kb_vectors/
COPY content_data/ content_data/
COPY chat_ui.html ./
COPY genshin_story_web_api.py genshin_story_agent.py intent_router.py character_aliases.py \
     kb_vector_store.py memory_manager.py wiki_entry_graph.py wiki_graph_channels.py ./

# 会话 JSON 落盘目录：容器里必须可写（平台磁盘是临时的，重启即丢，符合「云端不留长期记忆」）
RUN mkdir -p conversation_memory && chmod 777 conversation_memory

EXPOSE 7860

# 单 worker：工作流单例、限流表、取消信号都是进程内状态；并发靠线程池。
# timeout 放长：L3 全景题单次可能跑几分钟，默认 30s 会被杀掉。
CMD ["sh", "-c", "exec gunicorn -k gthread --workers 1 --threads 8 --timeout 1200 --graceful-timeout 30 --bind 0.0.0.0:${PORT:-7860} genshin_story_web_api:app"]
