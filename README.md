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

## 快速开始

```bash
pip install -r requirements.txt
python genshin_story_agent.py
```

Web 服务：
```bash
python genshin_story_web_api.py
# 浏览器打开 http://localhost:5000/chat
```

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
