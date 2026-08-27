# -*- coding: utf-8 -*-
"""原神剧情助手 - LangGraph Agent 入口（薄壳）。

架构：别名归一化 → 路径分类(L1/L2) → [L1] 快速回答 / [L2] Agent 循环（LLM + 工具，最多 10 轮迭代）→ 回答
L1（简单事实）：合并 Plan/Answer，最多 2 轮工具调用
L2（复杂推理）：保留完整 Plan → Tool → Answer 流程

LLM: 通义千问 qwen3.7-max

注：本文件是薄壳，所有实现已拆分到 app/ 包：
  app/config.py        - 全局配置（环境变量、API key、路径常量）
  app/llm.py           - LLM 实例池与重试封装
  app/schema.py        - Agent 状态与系统 Prompt
  app/data.py          - 知识库加载与文本辅助函数
  app/retrieval.py     - 别名处理、BM25、混合检索、RRF 融合
  app/formatters.py    - 角色/地区/剧情格式化函数
  app/rag_memory.py    - RAG 对话记忆
  app/progress.py      - 进度事件钩子（Web API 用）
  app/tools/           - 28 个 @tool 工具，按类别分文件
  app/agent/           - LangGraph 节点函数与工具执行器
  app/workflow.py      - StateGraph 拼装

兼容性导出：保留 web_api.py 等历史调用方所需的符号。
"""
import sys
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

# ====== 通过 app.config 完成环境初始化（HF 镜像、LangSmith、UTF-8、.env）======
from app.config import (
    CONTENT_DIR, PROMPTS_DIR,
    QWEN_API_KEY, QWEN_BASE_URL,
    RECENT_TURNS, SUMMARY_TRIGGER,
    MAX_AGENT_ITERATIONS, MAX_PLAN_RETRIES, MAX_FAST_ITERATIONS,
)

# ====== 知识库（供 web_api.py 等导入）======
from app.data import (
    角色知识库, 地区知识库, 主线剧情知识库, 武器知识库,
    任务知识库, 素材知识库, 圣遗物知识库,
    ACT_TO_QUESTS, ACTIVITY_LEGENDARY_ALIAS, FORCE_ACTIVITY_TITLES, TERM_ALIASES,
    _npcs_data, _vector_store, _load_content_json, _load_processed_data,
    _quests_processed, _normalize_for_match, _match_all_in,
    _build_quest_header, _extract_scenes, _extract_characters,
)

# ====== LLM 实例 ======
from app.llm import (
    llm, plan_llm, plan_llm_l2,
    answer_llm_light, answer_llm_medium, answer_llm_deep,
    INTENT_LLM_MAP, _select_answer_llm,
    alias_judge_llm, assess_llm,
    llm_invoke_with_retry,
)

# ====== 系统 Prompt & 状态 ======
from app.schema import (
    GenshinAdvisorState,
    AGENT_SYSTEM_PROMPT_PLAN, AGENT_SYSTEM_PROMPT_ANSWER, AGENT_SYSTEM_PROMPT_FAST,
    HELP_TEXT, _load_prompt,
)

# ====== RAG 记忆 ======
from app.rag_memory import rag_memory, RAG_AVAILABLE

# ====== 检索层 ======
from app.retrieval import (
    _sanitize_query, _is_compound_hit, _judge_alias_sandbox,
    _expand_query_with_aliases, _rerank,
    _keyword_search_docs, _rrf_fusion, _get_doc_key,
    _build_title_registry, TITLE_REGISTRY, SECTION_TO_TITLE,
    hybrid_search, kb_vector_search, SimpleBM25,
)

# ====== 格式化函数 ======
from app.formatters import (
    _format_role_info, _format_npc_info, _format_region_info, _format_story_info,
)

# ====== 工具列表 ======
from app.tools import (
    tools, tools_by_name, MELTDOWN_TRIGGER_TOOLS,
    query_character, query_region, query_story, query_weapon, query_quest,
    list_characters_by_element, list_characters_by_region,
    list_characters_by_weapon, list_characters_by_rarity,
    list_all_quest_series, list_all_books, list_all_lore_entries,
    list_all_weapons_and_artifacts, list_all_game_items,
    search_activity, find_first_mention, search_all, search_lore,
    load_book_content, load_quest_content, get_book_metadata,
    count_character_lines,
    query_monster, query_artifact, query_material, query_collectible,
    list_collectibles_by_region, query_recipe, query_food,
)

# ====== Agent 节点 ======
from app.agent import (
    rewrite_query, assess_query, route_after_assess,
    fast_agent, route_after_fast,
    plan_agent, route_after_plan,
    route_after_tools, answer_agent, tool_executor,
    _summarize_conversation, _build_fallback_answer, _is_not_found,
    ASSESS_PROMPT,
)

# ====== 工作流 ======
from app.workflow import create_agent_workflow

# ====== 进度钩子（兼容 web_api.py 的 agent_module._progress_hook = ... 写法）======
# _cancel_events 是 dict，引用共享，对它进行 [k]=v / .pop(k) 操作会反映到 app.progress
from app.progress import _cancel_events, _emit_progress, set_progress_hook

# 兼容 web_api.py 的 `agent_module._progress_hook = hook` 写法：
# 由于 _progress_hook 是不可变变量，直接赋值不会同步到 app.progress；
# 改为通过 set_progress_hook() 函数注入。web_api.py 需相应改为 set_progress_hook()。
set_progress_hook  # 显式导出供 web_api.py 使用


# ====== 主函数 ======
def main():
    print("\n" + "=" * 55)
    print("       原神剧情检索助手 Agent")
    print("=" * 55)
    print(f"  LLM: 通义千问 qwen3.7-max")
    print(f"  架构: 别名归一化 → 路径分类(L1/L2) → 快速/完整 双路径")
    print(f"  L1 快速: 合并 Plan/Answer，最多 {MAX_FAST_ITERATIONS} 轮工具调用")
    print(f"  L2 完整: Plan → Tool → Answer，最多 {MAX_AGENT_ITERATIONS} 轮迭代")
    print(f"  知识库: 角色{len(角色知识库)}位 | 武器{len(武器知识库)}把 | 圣遗物{len(圣遗物知识库)}套")
    print(f"  剧情: {len(主线剧情知识库)}章 | 地区{len(地区知识库)}个 | 任务{len(任务知识库)}个")
    print(f"  内容数据: 怪物/材料/采集物/食谱/书籍/任务剧情")
    print(f"  RAG记忆: {'已启用' if rag_memory else '未启用'}")
    print("=" * 55)
    print("\n输入 '帮助' 查看用法，输入 '退出' 结束对话\n")

    agent = create_agent_workflow()
    conversation_history = []
    conversation_summary = ""

    while True:
        try:
            user_input = input("\n【旅行者】").strip()
            if not user_input:
                continue

            if user_input.lower() in ["退出", "exit", "q"]:
                print("\n祝你在提瓦特的旅途愉快！")
                break

            if user_input.lower() in ["帮助", "help"]:
                print(HELP_TEXT)
                continue

            if user_input.lower() in ["记忆", "memory"]:
                if rag_memory:
                    stats = rag_memory.get_stats()
                    print(f"\n  记忆总数: {stats.get('count', 0)} 条")
                else:
                    print("\n  RAG 记忆系统未启用（需安装 chromadb + sentence-transformers）")
                continue

            print("\n  ... 正在查询...")

            start = datetime.now()
            result = agent.invoke({
                "user_query": user_input,
                "rewritten_query": None,
                "alias_notes": None,
                "conversation_history": conversation_history,
                "conversation_summary": conversation_summary,
                "messages": [],
                "final_response": None,
                "iteration": 0,
            })
            elapsed = (datetime.now() - start).total_seconds()

            response = result.get("final_response", "未生成回答")

            print(f"\n{'─' * 50}")
            print(f"  [用时 {elapsed:.1f}s]")
            print(f"{'─' * 50}")
            print(response)
            print(f"{'─' * 50}")

            conversation_history.append({"user": user_input, "assistant": response})

            # 多轮记忆管理：超过阈值时触发摘要
            unsummarized = len(conversation_history)
            if unsummarized > SUMMARY_TRIGGER:
                # 保留最近 RECENT_TURNS 轮，其余做摘要
                turns_to_summarize = conversation_history[:-RECENT_TURNS]
                if turns_to_summarize:
                    print(f"\n  [记忆] 压缩 {len(turns_to_summarize)} 轮对话...")
                    conversation_summary = _summarize_conversation(conversation_summary, turns_to_summarize)
                    conversation_history = conversation_history[-RECENT_TURNS:]
                    print(f"  [记忆] 摘要完成，当前保留最近 {len(conversation_history)} 轮")

            # 保存到 RAG 长期记忆
            if rag_memory:
                try:
                    rag_memory.save_conversation(user_input, response)
                except Exception as e:
                    print(f"\n[记忆] 保存失败: {e}")

        except KeyboardInterrupt:
            print("\n\n祝你在提瓦特的旅途愉快！")
            break
        except Exception as e:
            print(f"\n[系统错误] {e}")


if __name__ == "__main__":
    main()
