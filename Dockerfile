# 云端演示镜像（Hugging Face Spaces / 任意 Docker 平台）
#
# 设计要点：
#   1) 向量检索用 kb_vectors/（千问 text-embedding-v4 建的索引），查询时 HTTP 调 DashScope，
#      所以镜像里不需要 torch / sentence-transformers / chromadb（长期记忆在云端裁剪）。
#   2) 向量库与词条图不在 Git 仓库（.gitignore 排除 kb_vectors/），构建时由
#      download_kb_assets.py 从 GitHub Release 下载、校验 SHA256 后解压；
#      kb_vectors/ 里包含六集合与 wiki_entry_graph.json / wiki_entity_mention_index.json。
#   3) 图谱使用 schema4 覆盖包，避免只下载旧向量包时 L3 多源图谱缺条目。
#   4) 密钥不入镜像：API_TOKEN / DASHSCOPE_API_KEY / DASHSCOPE_FALLBACK_API_KEY / DEEPSEEK_API_KEY
#      等由平台的 Environment Variables 注入，本地 .env 已被 .dockerignore 排除。
#   5) L3_ENABLED=0：L3 全景题一次生成几万字、耗时长，演示站默认关闭，需要时改环境变量开。
#   6) 只装运行时真正 import 的包（见 requirements-web.txt 注释），镜像与冷启动都更小。

FROM python:3.11-slim

# 本机构建可用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 走国内镜像
ARG PIP_INDEX_URL=https://pypi.org/simple

# 知识库资产：默认从公开 Release 下载；离线/自建场景可用 --build-arg 覆盖为本地 HTTP 地址。
ARG KB_VECTORS_URL=https://github.com/lkdx0220/genshin-story-agent/releases/download/kb-2026.09.10/kb-vectors-text-embedding-v4-2026.09.10.zip
ARG KB_VECTORS_SHA256=96e7c78184ae1c6e1b5aac1cdf8a9a19714637c79d85e3d056bdd651392021ab
ARG KB_GRAPH_URL=https://github.com/lkdx0220/genshin-story-agent/releases/download/kb-2026.09.10/kb-graph-schema4-20260925.zip
ARG KB_GRAPH_SHA256=d2f291520556ac430b10e97d46f2e7835a28edc8f53a064182a8267d7e56d873

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

# 确保 urllib 能验证 GitHub HTTPS 证书
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 下载并校验知识库资产；图谱 schema4 包后解压，覆盖旧向量包内的 schema3 图谱。
COPY download_kb_assets.py ./
RUN python download_kb_assets.py /app \
        "${KB_VECTORS_URL}" "${KB_VECTORS_SHA256}" \
        "${KB_GRAPH_URL}" "${KB_GRAPH_SHA256}" \
    && rm download_kb_assets.py

COPY requirements-web.txt ./
RUN pip install -i "${PIP_INDEX_URL}" -r requirements-web.txt

# 代码与数据（文件清单由入口 import 闭包与运行时读盘记录确定，见 .dockerignore 注释）
COPY app/ app/
COPY prompts/ prompts/
COPY genshin_knowledge_base/ genshin_knowledge_base/
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
