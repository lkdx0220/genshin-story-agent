# 原神剧情助手

基于 LangGraph 的原神游戏知识问答 Agent。零训练预算下，用通用大模型 + 33 个已注册工具（32 个常规初始暴露） + 混合检索实现游戏领域复杂推理问答。

> **⚠️ 注意：本项目不是开箱即用的客户端应用，具有一定技术门槛。**
>
> 使用本项目需要：熟悉命令行操作、具备 Python 环境配置能力、自行安装一系列依赖包，还需要自行申请阿里云百炼（千问）和 DeepSeek 的 API Key 并配置环境变量。
>
> 如果你只是想找一个能回答原神问题的工具，而不想折腾命令行和 API 配置，本项目不适合你。

## 架构

```
用户问题 → 别名检测 → 意图路由 → L1（快速回答）/ L2（深度推理）
                                    │
L2: Plan Agent → 工具调用（33 tools，常规暴露 32）→ 熔断截断 → Answer Agent
```

- **意图路由**：三级决策分层（规则层 → 轻量LLM → 强模型），平衡准确率与成本
- **混合检索**：关键词/BM25 文本匹配（字符串命中 + SimpleBM25 + rerank）+ 向量语义搜索，RRF 融合排序；向量库仅保留 `kb_quests_vec`、`kb_lore`、`kb_books`、`kb_characters`、`kb_regions`，`kb_quests_bm25` 已剔除
- **工具封装**：33 个已注册工具（32 个常规初始暴露）按功能域划分（查询/列表/搜索/内容加载）
- **熔断机制**：工具返回超阈值时截断直接生成回答，防止无限循环
- **别名消歧**：LLM 消歧 + 人工审核双通道，处理多义别名
- **流式回答**：Answer LLM 流式生成，SSE `answer_delta` 逐段推送；前端实时 Markdown 渲染，流式与最终结果排版一致
- **Qwen 主备切换**：token-plan 主接口失败/401 时自动回退原 DashScope，`invoke` 与 `stream` 均生效；`.env` 优先于终端残留环境变量
- **端口自愈**：Web 服务启动时自动关闭旧实例、绑定 `127.0.0.1`，5000 被非本项目占用时自动换 5001~5050
- **隐藏搜索工具**：注册 33 个工具，常规初始暴露 32 个；`search_world` 仅在常规搜索碰壁后动态追加
- **Wiki 链接图**：`wiki_graph_search` / `wiki_graph_expand` / `wiki_graph_get` 三工具基于观测枢 `data-entry-id` 链接图，支持跨任务/地图文本/角色/物品多跳检索；全量图构建脚本见 `wiki_entry_graph.py` + `wiki_data_tools/_fetch_mihoyo_channel.py`
- **L3 全景连锁**：全景正则 + ≥2 个任务标题命中后，代码确定性加载任务全文、地图文本、实体提及索引反向边、说话人与图谱一跳扩展，拼成 `[全景全文读取]`，再交给 qwen3.8-max 按 task/entity/map/synthesis 分段并行生成

## Wiki 数据同步

知识库数据主要来源于 B 站原神 Wiki，并已接入米游社观测枢公开只读接口做任务/地图文本核对与补充。以下脚本用于增量更新内容数据：

```bash
# 抓取 B 站各区域限定文本（更新 lore.json）
python scripts/scrape_limited_texts.py

# 米游社观测枢任务/地图文本抓取与解析
python wiki_data_tools/_fetch_mihoyo_tasks.py --list-only --all-versions
python wiki_data_tools/_fetch_mihoyo_map_text.py --all-regions
python wiki_data_tools/_parse_mihoyo_tasks.py --raw content_data/mihoyo_tasks_raw.json
python wiki_data_tools/_parse_mihoyo_map_text.py --raw content_data/mihoyo_map_text_raw_full.json

# 重建全部向量索引（内容目录已修正为项目根 content_data）
python scripts/kb_build_index.py --force

# 预处理长任务切片（生成 quests_processed.json）
python scripts/quest_preprocessor.py

# 全量 wiki 链接图：首次全量抓取 + 构建
python wiki_data_tools/_fetch_mihoyo_channel.py --tier 1 --delay 0.6
python wiki_entry_graph.py --build

# 全量 wiki 链接图：后续增量更新（断点续传 + 自动重建）
python scripts/update_wiki_graph.py
```

抓取后的数据存于 `content_data/` 目录，运行时自动加载。

## 最近知识库更新（2026-09-10）

- 新增 `content_data/source_scope/`：BWiki 与米游社观测枢口径对齐结果（`official_catalog.json`、`classification.jsonl`、`bwiki_only.jsonl`、`review.jsonl`、`shared_for_replacement.jsonl`、`_summary.json`）。
- 对齐统计：本地 15618 条 → `shared_exact 11007`、`bwiki_only 2158`、`shared_cross_module 135`、`shared_base 1500`、`review 818`。
- 官方目录数量（节选）：task 1056、map_text 706、npc 2326、character 136、weapon 246、artifact 63、enemy 415、food 321、item 2037、book 93、organization 37、domain 76。
- 更新 `content_data/lore.json`、`content_data/npcs_*.json`、`content_data/quests_世界任务|其他任务|地图事件|彩蛋剧情|活动剧情.json`。
- 新增维护脚本：`scripts/preprocess_source_scope.py`、`scripts/apply_source_scope.py`、`scripts/import_crawler_corpus.py`、`scripts/merge_crawler_corpus.py`。
- 运行时向量库：合计 **18080** 条（kb_quests_vec 7416 / kb_lore 7515 / kb_books 353 / kb_characters 131 / kb_npcs 2657 / kb_regions 8）。
- Wiki 图 schema v3：**13511 节点 / 9435 链接**；实体提及索引 **4678 实体 / 39651 提及关系**。


## 模型清单

本项目使用多个模型协同工作，请确认以下模型可用（Qwen 主接口为 token-plan，失败自动回退阿里云百炼/DashScope）：

| 用途 | 模型 | 所属服务 | 说明 |
|------|------|----------|------|
| Plan Agent（L2 复杂规划） | qwen3.7-max | token-plan / 阿里云百炼（回退） | 多子问题拆解、工具选择 |
| Answer Agent（深度） | qwen3.7-max | token-plan / 阿里云百炼（回退） | 剧情/溯源/世界观类回答，medium reasoning |
| Answer Agent（中等） | qwen3.7-max | token-plan / 阿里云百炼（回退） | 搜索/书籍类回答，low reasoning |
| Plan Agent（L1 快速） | qwen3.7-plus | token-plan / 阿里云百炼（回退） | 简单事实类快速规划（兼顾工具调用稳定性） |
| Answer Agent（轻量） | qwen3.6-flash（主）/ qwen-plus（DashScope 回退） | token-plan / 阿里云百炼（回退） | 角色查询类快速回答 |
| 意图路由 | qwen3.7-plus（主）/ qwen-plus（DashScope 回退） | token-plan / 阿里云百炼（回退） | 实体锚定后的意图分类 |
| 别名消歧 | deepseek-flash | DeepSeek | "水神→芙宁娜/芙卡洛斯"歧义判断 |
| L1/L2 路径分类 | deepseek-flash | DeepSeek | 简单题/复杂题分流 |
| 向量 Embedding | text-embedding-v4 | 阿里云百炼 | 知识库语义检索 |
| L3 全景 Answer | qwen3.8-max | token-plan / 阿里云百炼（回退） | 全景题分段生成主模型，关闭 thinking |
| 记忆 Embedding | paraphrase-multilingual-MiniLM-L12-v2 | 本地 | RAG 对话记忆（sentence-transformers） |

> **注意**：当前 Qwen 主接口为 token-plan（OpenAI 兼容），失败自动回退原 DashScope；token-plan 不支持 qwen-plus，因此回退时轻量/路由模型才使用 qwen-plus。未开通的模型会报 `model not found` 错误。

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
# 可选：Qwen 主接口，默认 token-plan OpenAI 兼容端点；不填则使用原 DashScope
# DASHSCOPE_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
# 可选：主接口不可用时自动回退的旧 Key/旧接口
# DASHSCOPE_FALLBACK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# DASHSCOPE_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

- **DASHSCOPE_API_KEY**（必填）：Qwen 模型 Key，默认从 [阿里云百炼](https://dashscope.console.aliyun.com/) 获取；当前项目主接口使用 token-plan，失败时自动回退原 DashScope。本项目使用 qwen3.7-max / qwen3.7-plus / qwen3.6-flash，回退时可用 qwen-plus
- **DASHSCOPE_BASE_URL**（可选）：Qwen 主接口地址，默认 `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
- **DASHSCOPE_FALLBACK_API_KEY / DASHSCOPE_FALLBACK_BASE_URL**（可选）：主接口失败/配额用光时自动切回原 DashScope 的旧 Key 与旧地址
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

生成的 `原神剧情助手.exe` 在项目根目录，可独立分发。打包不会把 `.env` 编译进 exe；运行时请把 `.env` 放在 exe 同目录。若缺少 API Key，启动时会明确报错。

## 自用版解锁：主观评价与假设推演（可选）

当前最新版本已经默认支持**基于多条独立证据的身份/归属/代称推断**，例如从多个文献特征中推断“有翼者 / 派凯蒙宁”等称呼指向同一实体；Agent 会区分“直接证据”和“合理推断”，不会把推测写成官方定论。

下面这一项是**可选**的：如果你希望 Agent 还能像普通 AI 助手一样进行**主观评价、角色点评、假设推演**（例如“评价一下胡桃”“把角色A放到角色B的位置会不会更好”），可以按下面的改动开启。

> 说明：开启后，事实型问题仍然严格基于工具返回；只有用户明确要求评价/看法/假设时，Agent 才会输出分析和推断，并且会区分“游戏内事实”和“我的分析/主观看法”。

### 1. `prompts/system/agent_system_v4_answer.txt`

在【事实溯源】之后插入：

```text
===== 主观评价与假设推演（用户明确要求时适用）=====
当用户明确要求评价、看法、比较、假设推演时（如“你觉得XX怎么样”“评价一下XX”“如果把A放到B的位置会不会更好”“你更喜欢谁”），允许以下行为：
1. 以工具返回的剧情、行为、设定为论据，进行合理的分析、评价、推测和假设推演。
2. 必须区分“游戏内事实/原文依据”和“我的分析/推测/主观看法”。
3. 不得把推测写成“游戏里就是这样/官方设定如此”；不得伪造具体台词、任务名、数字等事实。
4. 推理可以使用性格、动机、叙事逻辑、常理等合理依据，但应避免无证据的极端脑补。
5. 如果用户只问事实（“发生了什么事/原话/是谁”），仍按严格事实模式回答，不主动添加主观评价。
```

### 2. `prompts/system/agent_fast_answer.txt`

把开头：

```text
你的知识有且仅有工具返回的内容。严禁使用任何训练数据或常识进行补充、猜测。
```

改为：

```text
事实型问题的直接事实必须来自工具返回；对于用户明确要求评价、看法、假设推演的问题，允许以工具事实为锚点进行合理分析和推断，但必须区分“游戏内事实/原文依据”与“我的分析/主观看法”。
```

### 3. `app/agent/nodes.py`

在 `_SYNTHESIS_MARKERS` 中追加：

```python
"评价", "怎么评价", "如何看待", "你觉得", "你感觉",
"如果", "假设", "会不会", "更喜欢", "哪个更好", "谁更适合",
"分析一下", "会更好", "会怎样", "会如何",
```

修改后重启 Web/CLI 即可生效；如果你使用的是已经打包好的 `原神剧情助手.exe`，需要重新执行 `python scripts/build_exe.py` 打包，因为 exe 内是旧代码快照。

## 目录结构

```
app/                    # 模块化核心（参照 OpenManus 分层模式）
  agent/                # LangGraph 节点与工具执行器
  tools/                # 33 个 @tool 工具（初始暴露 32 个；query/list/search/content）
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

## 数据规模（2026-09）

- NPC：`npcs_processed.json` 2640 条
- 世界观/地图文本：`lore.json` 7493 条（其中地图文本 7260 条）
- 任务：世界任务 762、活动剧情 657、传说任务 238、魔神任务 224，长任务预处理 268 条
- 其他内容：材料 812、食物 714、怪物 550、食谱 453、书籍 105、采集物 61
- 向量库：`kb_quests_vec` 7200、`kb_lore` 7625、`kb_books` 353、`kb_characters` 131、`kb_regions` 8，合计 15317；`kb_quests_bm25` 已移除

## 评估

Golden Test 评测体系位于父目录 `golden_test/` 下，含 25 道标准化测试题及评测脚本。

## License

MIT
