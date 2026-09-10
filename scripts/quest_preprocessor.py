#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
长任务预处理脚本：为 >9000 字的任务生成双轨数据。
轨道 A：qwen-turbo 生成剧情大纲（摘要）
轨道 B：自然边界优先的 BM25 切片（约 3000 字窗口，保留原文，供 BM25 检索）
轨道 C：1500 字自然场景切片（供向量语义检索）

运行方式：python quest_preprocessor.py
一次运行即可，产物保存到 content_data/quests_processed.json
"""

import json
import os
import re
import sys
import time

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_ENDPOINT"] = ""
os.environ["LANGCHAIN_API_KEY"] = ""
os.environ["LANGCHAIN_PROJECT"] = ""

from dotenv import load_dotenv
load_dotenv()

from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# ====== 配置 ======
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or ""
if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY，请配置后再运行任务预处理。")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_DIR = os.path.join(_BASE_DIR, "content_data")
OUTPUT_FILE = os.path.join(CONTENT_DIR, "quests_processed.json")

# BM25 切片参数：自然边界 + 较大窗口 + 少量重叠
BM25_CHUNK_SIZE = 3000      # BM25 切片最大字数
BM25_CHUNK_OVERLAP = 200    # BM25 切片重叠字数
LENGTH_THRESHOLD = 9000
MAX_WORKERS = 3
LOG_FILE = os.path.join(_BASE_DIR, "_preprocess_log.txt")

# 向量切片参数
VEC_CHUNK_SIZE = 1500       # 向量切片最大字数
VEC_CHUNK_OVERLAP = 150     # 切片重叠字数

# ====== LLM ======
llm = ChatOpenAI(
    model="deepseek-flash",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    temperature=0.3,
    max_tokens=4096,
)


def summarize_quest(title, text):
    """为长任务生成剧情大纲"""
    prompt = (
        "你是原神剧情分析师。请为以下任务文本撰写一份不超过2000字的剧情大纲。\n"
        "要求：\n"
        "1. 按时间顺序概括关键事件\n"
        "2. 标出每次事件转折中涉及的核心角色及其动机\n"
        "3. 保留关键台词和重要设定揭示\n"
        "4. 不添加原文中没有的信息\n"
        "5. 格式简洁，用要点式呈现\n"
        f"\n任务名称：{title}\n原文：\n{text}"
    )
    for attempt in range(3):
        try:
            resp = llm.invoke([HumanMessage(content=prompt)])
            summary = resp.content.strip()
            if summary:
                return summary
        except Exception as e:
            time.sleep(2)
    return None


def chunk_text(text):
    """BM25 切片：自然边界优先，上限 BM25_CHUNK_SIZE 字，重叠 BM25_CHUNK_OVERLAP 字。"""
    return chunk_natural(text, max_size=BM25_CHUNK_SIZE, overlap=BM25_CHUNK_OVERLAP)


def chunk_natural(text, max_size=None, overlap=None):
    """按对话场景自然切分。

    默认向量参数：max_size=VEC_CHUNK_SIZE, overlap=VEC_CHUNK_OVERLAP。
    BM25 可传入更大窗口（BM25_CHUNK_SIZE/BM25_CHUNK_OVERLAP）。

    切片边界优先级（从高到低）：
    1. --- / *** 分隔符 → 强边界
    2. 空行 + 下一行【标题】→ 场景切换
    3. 空行 + 角色切换（说话者不同）→ 对话轮次边界
    4. 达到上限时：往前 200 字内找最近空行或角色切换点切；
       找不到则把当前角色长发言整体推到下一片
    5. 每片尾部带 overlap 字重叠
    """
    if max_size is None:
        max_size = VEC_CHUNK_SIZE
    if overlap is None:
        overlap = VEC_CHUNK_OVERLAP

    if not text or len(text.strip()) == 0:
        return []

    if len(text) <= max_size:
        return [text.strip()]

    # 角色发言检测：「角色名：」或「角色名:」
    speaker_pattern = re.compile(r'^(.+?)[：:]\s*')

    def _get_speaker(line):
        m = speaker_pattern.match(line.strip())
        return m.group(1).strip() if m else None

    lines = text.split(chr(10))

    # 步骤1：拆分为自然段落（segment）
    segments = []
    current_lines = []

    for line in lines:
        stripped = line.strip()

        # 强边界：--- / ***
        if bool(re.match(r'^[-*]{3,}$', stripped)):
            if current_lines:
                segments.append(chr(10).join(current_lines).strip())
                current_lines = []
            continue

        # 空行：段落分隔
        if not stripped:
            if current_lines:
                segments.append(chr(10).join(current_lines).strip())
                current_lines = []
            continue

        # 标题标记：独立段落（原则2）
        if stripped.startswith('【') and stripped.endswith('】'):
            if current_lines:
                segments.append(chr(10).join(current_lines).strip())
                current_lines = []
            segments.append(stripped)
            continue

        # 角色切换检测（原则3）：与前一行说话者不同，且紧跟前一行无空行
        if current_lines:
            prev_speaker = _get_speaker(current_lines[-1])
            curr_speaker = _get_speaker(line)
            if prev_speaker is not None and curr_speaker is not None and prev_speaker != curr_speaker:
                segments.append(chr(10).join(current_lines).strip())
                current_lines = []

        current_lines.append(line)

    if current_lines:
        segments.append(chr(10).join(current_lines).strip())

    # 步骤2：合并段落为切片
    chunks = []
    buffer_lines = []  # 改为按行追踪，方便回溯切分
    buffer_len = 0

    def _lines_len(lines_list):
        return sum(len(l) + 1 for l in lines_list) - 1 if lines_list else 0  # +1 for chr(10), -1 for last line

    def _find_backtrack_split(buffer_lines, max_lookback=200):
        """在 buffer_lines 末尾 max_lookback 字内找最近的自然切分点。
        返回 split_idx（从此行之后推到下一片），或 None。"""
        acc = 0
        for i in range(len(buffer_lines) - 1, 0, -1):
            acc += len(buffer_lines[i]) + 1  # +1 for chr(10)
            if acc > max_lookback:
                return None  # 回溯区域找不到

            prev_line = buffer_lines[i - 1].strip()
            curr_line = buffer_lines[i].strip()

            # 空行边界
            if not prev_line:
                return i

            # 角色切换边界
            prev_sp = _get_speaker(prev_line)
            curr_sp = _get_speaker(curr_line)
            if prev_sp is not None and curr_sp is not None and prev_sp != curr_sp:
                return i

        return None

    for seg_text in segments:
        seg_lines = seg_text.split(chr(10))
        seg_len = len(seg_text)

        if buffer_lines and buffer_len + seg_len + 1 > max_size:
            # 超限：尝试回溯找自然切分点（原则4）
            split_idx = _find_backtrack_split(buffer_lines)

            if split_idx is not None and split_idx > 0:
                # 找到自然切分点：buffer 前半保留，后半 + 新段落到下一片
                keep_lines = buffer_lines[:split_idx]
                push_lines = buffer_lines[split_idx:]
                chunks.append(chr(10).join(keep_lines).strip())
                buffer_lines = push_lines + seg_lines
                buffer_len = _lines_len(buffer_lines)
            else:
                # 找不到：当前段落到下一片（原则4 兜底）
                chunks.append(chr(10).join(buffer_lines).strip())
                buffer_lines = seg_lines
                buffer_len = seg_len
        else:
            if buffer_lines:
                buffer_lines.append('')
            buffer_lines.extend(seg_lines)
            buffer_len = buffer_len + seg_len + (1 if buffer_lines != seg_lines else 0)

    if buffer_lines:
        chunks.append(chr(10).join(buffer_lines).strip())

    # 单片段无需重叠
    if len(chunks) <= 1:
        return chunks

    # 添加重叠（原则5）：每片尾部带前一片末尾 overlap 字
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        overlap_chars = min(overlap, len(prev))
        overlap_text = prev[-overlap_chars:]
        result.append(overlap_text + chr(10) + chunks[i])

    return result

def load_all_quests():
    """读取所有 quests_*.json，返回列表"""
    all_quests = []
    for fn in sorted(os.listdir(CONTENT_DIR)):
        if not fn.startswith("quests_") or not fn.endswith(".json"):
            continue
        if fn == "quests_processed.json":
            continue
        filepath = os.path.join(CONTENT_DIR, fn)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            # 静默跳过，不写 stdout（避免终端问题）
            pass
            continue
        for entry in data:
            title = entry.get("title", "")
            text = entry.get("text", "")
            if title and len(text) > LENGTH_THRESHOLD:
                all_quests.append({
                    "title": title,
                    "text": text,
                    "category": entry.get("category", ""),
                    "source_file": fn,
                })
    return all_quests


def process_one(quest):
    """处理单条长任务：生成摘要 + BM25切片 + 向量切片"""
    title = quest["title"]
    text = quest["text"]
    total_chars = len(text)

    summary = summarize_quest(title, text)
    chunks_bm25 = chunk_text(text)        # 自然边界 + 3000字左右窗口（BM25用）
    chunks_vec = chunk_natural(text)      # 自然场景切片（向量用）

    return {
        "title": title,
        "total_chars": total_chars,
        "bm25_chunk_count": len(chunks_bm25),
        "vec_chunk_count": len(chunks_vec),
        "summary": summary,
        "chunks_bm25": chunks_bm25,
        "chunks_vec": chunks_vec,
        "category": quest["category"],
        "source_file": quest["source_file"],
    }


def main():
    log_lines = []
    def log(msg):
        log_lines.append(msg)
        with open(LOG_FILE, "a", encoding="utf-8") as lf:
            lf.write(msg + "\n")

    log("=" * 60)
    log("长任务预处理脚本（双轨）")
    log(f"阈值: >{LENGTH_THRESHOLD} 字")
    log(f"BM25切片: 自然边界优先, 上限 {BM25_CHUNK_SIZE} 字, 重叠 {BM25_CHUNK_OVERLAP} 字")
    log(f"向量切片: 自然场景切分, 上限 {VEC_CHUNK_SIZE} 字, 重叠 {VEC_CHUNK_OVERLAP} 字")
    log(f"并发: {MAX_WORKERS} 线程")
    log(f"CONTENT_DIR: {CONTENT_DIR}")
    log(f"OUTPUT_FILE: {OUTPUT_FILE}")
    log("=" * 60)

    # 1. 加载
    log("[1/3] 加载 quests_*.json ...")
    try:
        quests = load_all_quests()
    except Exception as e:
        log(f"  [致命错误] load_all_quests 异常: {e}")
        import traceback
        log(traceback.format_exc())
        print("\n".join(log_lines))
        return
    log(f"  => 共 {len(quests)} 条需要处理（>{LENGTH_THRESHOLD}字）")

    if not quests:
        log("  => 没有需要处理的条目，退出。")
        print("\n".join(log_lines))
        return

    # 断点续跑：加载已有的处理结果
    existing = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            log(f"  => 已有 {len(existing)} 条结果，跳过已处理的")
        except Exception:
            pass

    # 过滤已处理的
    quests = [q for q in quests if q["title"] not in existing]
    results = existing
    log(f"  => 还需处理 {len(quests)} 条")

    if not quests:
        log("  => 全部已处理，退出。")
        print("\n".join(log_lines))
        return

    # 按长度排序，先处理最短的（快速积累进度）
    quests.sort(key=lambda x: len(x["text"]))

    # 2. 并发处理（带定期保存）
    log(f"[2/3] 并发生成摘要 + 切片 ({MAX_WORKERS} 线程)...")
    completed = 0
    failed = 0

    def save_progress():
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {
                executor.submit(process_one, q): q for q in quests
            }
            for future in as_completed(future_map):
                quest = future_map[future]
                try:
                    result = future.result()
                    if result["summary"]:
                        results[result["title"]] = result
                        completed += 1
                    else:
                        failed += 1
                    log(f"  [{completed+failed}/{len(quests)}] {result['title']} "
                          f"({result['total_chars']}字, BM25:{result['bm25_chunk_count']}+Vec:{result['vec_chunk_count']})"
                          f"{' [摘要失败]' if not result['summary'] else ''}")
                except Exception as e:
                    failed += 1
                    log(f"  [{completed+failed}/{len(quests)}] {quest['title']}: 处理异常 {e}")

                # 每条都保存（终端进程容易被杀，需要频繁落盘）
                save_progress()

    except Exception as e:
        log(f"[错误] 主循环异常: {e}")
        import traceback
        log(traceback.format_exc())

    # 3. 最终保存
    log(f"[3/3] 保存到 {OUTPUT_FILE} ...")
    log(f"  => 成功 {completed} 条, 失败 {failed} 条")
    save_progress()

    # 统计
    total_bm25 = sum(r["bm25_chunk_count"] for r in results.values())
    total_vec = sum(r["vec_chunk_count"] for r in results.values())
    log(f"  => 总切片数: BM25={total_bm25}, Vec={total_vec}")
    log("=" * 60)
    log("预处理完成！")
    print("\n".join(log_lines))


if __name__ == "__main__":
    main()
