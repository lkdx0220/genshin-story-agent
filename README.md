# 原神剧情助手

基于 LangGraph 的原神游戏知识问答 Agent。零训练预算下，用通用大模型 + 28 个工具 + 混合检索实现游戏领域复杂推理问答。

## 架构

```
用户问题 → 别名检测 → 意图路由 → L1（快速回答）/ L2（深度推理）
                                    │
L2: Plan Agent → 工具调用（28 tools）→ 熔断截断 → Answer Agent
```

- **意图路由**：三级决策分层（规则层 → 轻量LLM → 强模型），平衡准确率与成本
- **混合检索**：BM25 关键词匹配 + 向量语义搜索，RRF 融合排序
- **工具封装**：28 个工具按功能域划分（查询/列表/搜索/内容加载）
- **熔断机制**：工具返回超阈值时截断直接生成回答，防止无限循环
- **别名消歧**：LLM 消歧 + 人工审核双通道，处理多义别名

## Wiki 数据同步

知识库数据来源于 B 站原神 Wiki。以下脚本用于增量更新内容数据：

```bash
# 抓取各区域「限定文本」板块（更新 lore.json）
python scripts/scrape_limited_texts.py

# 重建角色向量索引（更新 kb_vectors/）
python scripts/kb_build_index.py

# 预处理任务数据（生成 quests_processed.json 等）
python scripts/quest_preprocessor.py
```

抓取后的数据存于 `content_data/` 目录，运行时自动加载。

## 模型清单

本项目使用多个模型协同工作，请在阿里云百炼控制台确认已开通以下模型：

| 用途 | 模型 | 所属服务 | 说明 |
|------|------|----------|------|
| Plan Agent（L2 复杂规划） | qwen3.7-max | 阿里云百炼 | 多子问题拆解、工具选择 |
| Answer Agent（深度） | qwen3.7-max | 阿里云百炼 | 剧情/溯源/世界观类回答，medium reasoning |
| Answer Agent（中等） | qwen3.7-max | 阿里云百炼 | 搜索/书籍类回答，low reasoning |
| Plan Agent（L1 快速） | qwen-plus | 阿里云百炼 | 简单事实类快速规划 |
| Answer Agent（轻量） | qwen-plus | 阿里云百炼 | 角色查询类快速回答 |
| 意图路由 | qwen-plus | 阿里云百炼 | 实体锚定后的意图分类 |
| 别名消歧 | deepseek-v4-flash | DeepSeek | "水神→芙宁娜/芙卡洛斯"歧义判断 |
| L1/L2 路径分类 | deepseek-v4-flash | DeepSeek | 简单题/复杂题分流 |
| 向量 Embedding | text-embedding-v4 | 阿里云百炼 | 知识库语义检索 |
| 记忆 Embedding | paraphrase-multilingual-MiniLM-L12-v2 | 本地 | RAG 对话记忆（sentence-transformers） |

> **注意**：qwen3.7-max 需在百炼控制台单独申请开通，qwen-plus 默认开通。未开通的模型会报 `model not found` 错误。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
# 复制模板文件
copy .env.example .env
```

编辑 `.env`，填入你的 API Key：

```ini
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- **DASHSCOPE_API_KEY**（必填）：[阿里云百炼](https://dashscope.console.aliyun.com/) 获取，本项目使用 qwen3.7-max / qwen-plus
- **DEEPSEEK_API_KEY**（可选）：[DeepSeek 开放平台](https://platform.deepseek.com/) 获取，用于别名消歧（alias_judge_llm）和 L1/L2 路径分类（assess_llm），不填则降级使用千问

### 3. 运行

CLI 模式：
```bash
python genshin_story_agent.py
```

Web 服务：
```bash
python genshin_story_web_api.py
# 浏览器打开 http://localhost:5000/chat
```

### 4. 打包为 exe（可选）

```bash
pip install pyinstaller
python scripts/build_exe.py
```

生成的 `原神剧情助手.exe` 在项目根目录，可独立分发。打包前需确保 `.env` 已正确配置（API key 会编译进 exe）。

## 目录结构

```
app/                    # 模块化核心（参照 OpenManus 分层模式）
  agent/                # LangGraph 节点与工具执行器
  tools/                # 28 个 @tool 工具（query/list/search/content）
  config.py             # 配置常量
  llm.py                # LLM 实例池与重试
  data.py               # 知识库加载
  retrieval.py           # BM25 + 向量 + RRF 融合检索
  workflow.py            # StateGraph 拼装
content_data/           # 游戏内容数据（JSON）
genshin_knowledge_base/ # 知识库 Python 模块
prompts/                # 系统 Prompt
scripts/                # 构建/预处理脚本
wiki_data_tools/        # Wiki 数据爬取与重建工具
```

## 技术栈

- LangGraph / LangChain
- BM25 + 向量 RRF 混合检索
- Flask Web API
- PyInstaller 打包为 exe

## 评估

Golden Test 评测体系位于父目录 `golden_test/` 下，含 25 道标准化测试题及评测脚本。

## License

MIT
