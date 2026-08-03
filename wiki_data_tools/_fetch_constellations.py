#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量爬取角色命之座详情（C1-C6 名称 + 效果）
使用批量 revisions API（18 个角色/批），减少 API 调用次数避免 CDN 限流。
"""

import os, sys, re, json, time, random, importlib.util, subprocess, tempfile, urllib.parse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "_constellations.json")
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "_constellations_checkpoint.json")

WIKI_API = "https://wiki.biligame.com/ys/api.php"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

BATCH_SIZE = 18             # 每批 18 个角色
BATCH_COOLDOWN = 60.0       # 批次间冷却 1 分钟（curl 不受 CDN 限流）
BATCH_JITTER = 30.0         # 冷却随机抖动 ±30s
BATCH_TIMEOUT = 90          # 批量请求超时

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_character_names():
    """从 roles.py 加载所有角色名称。"""
    roles_path = os.path.join(PROJECT_DIR, "genshin_knowledge_base", "roles.py")
    spec = importlib.util.spec_from_file_location("roles", roles_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = getattr(mod, "角色知识库", [])
    return [r["角色名称"] for r in data if r.get("角色名称")]


def fetch_wikitext_batch(names):
    """使用 curl 批量拉取多个页面的 wikitext（curl 的 TLS 指纹不被 CDN 封禁）。
    返回 {page_name: wikitext_or_None}。"""
    titles = "|".join(names)
    url = WIKI_API + "?" + urllib.parse.urlencode({
        "action": "query",
        "prop": "revisions",
        "titles": titles,
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
    })

    for attempt in range(3):
        try:
            # curl 输出：-s 静默，-w 输出 HTTP 状态码，-o 保存响应体
            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            os.close(fd)
            result = subprocess.run([
                "curl.exe", "-s", "--compressed",
                "-w", "%{http_code}",
                "-o", tmp_path,
                "-H", f"User-Agent: {USER_AGENT}",
                "-H", "Accept: application/json, text/plain, */*",
                "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                "-H", "Referer: https://wiki.biligame.com/ys/",
                "--max-time", str(BATCH_TIMEOUT),
                url,
            ], capture_output=True, text=True, timeout=BATCH_TIMEOUT + 10)

            http_code = result.stdout.strip()
            if http_code == "200":
                with open(tmp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.unlink(tmp_path)
                pages = data.get("query", {}).get("pages", {})
                results = {}
                for pid, page_info in pages.items():
                    if int(pid) < 0:
                        continue
                    title = page_info.get("title", "")
                    revisions = page_info.get("revisions", [])
                    if revisions:
                        content = revisions[0].get("slots", {}).get("main", {}).get("*", "")
                        results[title] = content if content else None
                    else:
                        results[title] = None
                return results
            elif http_code == "567":
                wait = 30 * (2 ** attempt)
                log(f"  CDN 567 限流，等待 {wait}s 后重试 (第 {attempt+1}/3 次)...")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                time.sleep(wait)
            else:
                log(f"  HTTP {http_code}，第 {attempt+1}/3 次重试...")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                time.sleep(15 * (attempt + 1))
        except subprocess.TimeoutExpired:
            log(f"  curl 超时，第 {attempt+1}/3 次重试...")
            time.sleep(15 * (attempt + 1))
        except Exception as e:
            log(f"  curl 请求失败: {e}，第 {attempt+1}/3 次重试...")
            time.sleep(10 * (attempt + 1))
    return None


# ====== 解析函数 ======

def _find_template_end(text, start_pos):
    """从 start_pos（'{{' 开头）用括号计数找到匹配的 '}}' 位置。"""
    depth = 0
    i = start_pos
    while i < len(text):
        if text[i:i+2] == '{{':
            depth += 1
            i += 2
        elif text[i:i+2] == '}}':
            depth -= 1
            if depth == 0:
                return i + 2
            i += 2
        else:
            i += 1
    return -1


def _template_display_text(inner):
    """从模板内部提取显示文本（最后一个 | 之后的非嵌套内容）。
    例如 {{颜色|描述|刹那之花}} → '刹那之花'
    无参数模板返回 None。"""
    # 括号计数找最后一个深度为 0 的 |
    last_pipe = -1
    depth = 0
    for j, ch in enumerate(inner):
        if ch == '{' and j+1 < len(inner) and inner[j+1] == '{':
            depth += 1
        elif ch == '}' and j+1 < len(inner) and inner[j+1] == '}':
            depth -= 1
        elif ch == '|' and depth == 0:
            last_pipe = j
    if last_pipe < 0:
        return None  # 无参数模板，移除
    text = inner[last_pipe+1:].strip()
    # 递归清理嵌套模板
    return _clean_wiki_markup(text) if text else None


def _clean_wiki_markup(text):
    """清理 wiki 格式标记和内联模板，保留模板内的显示文本。"""
    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] == '{{':
            end = _find_template_end(text, i)
            if end > 0:
                inner = text[i+2:end-2]  # 去掉 {{ 和 }}
                display = _template_display_text(inner)
                if display:
                    result.append(display)
                i = end
                continue
        result.append(text[i])
        i += 1
    cleaned = ''.join(result)
    cleaned = re.sub(r'<br\s*/?>', '\n', cleaned)
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = re.sub(r"'''", '', cleaned)
    cleaned = re.sub(r"''", '', cleaned)
    cleaned = re.sub(r'\[\[([^\]|]+)\]\]', r'\1', cleaned)
    cleaned = re.sub(r'\[\[[^\]]+\|([^\]]+)\]\]', r'\1', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r'  +', ' ', cleaned)
    return cleaned.strip()


def parse_constellations(wikitext):
    """从 wikitext 中提取命之座数据。返回 list of {name, effect}。"""
    start_pos = wikitext.find('{{角色/命之座')
    if start_pos == -1:
        return None
    end_pos = _find_template_end(wikitext, start_pos)
    if end_pos < 0:
        return None
    block = wikitext[start_pos:end_pos]

    first_nl = block.find('\n')
    if first_nl < 0:
        inner = block[len('{{角色/命之座'):-2]
    else:
        inner = block[first_nl+1:-2]

    result = []
    for i in range(1, 7):
        name_field = f'命之座{i}'
        effect_field = f'命之座{i}效果'
# 提取名称（单行，= 之后到行尾，名称不能包含 | 或换行）
        name_pattern = rf'\|{name_field}\s*=\s*([^|\n]+)'
        name_match = re.search(name_pattern, inner)
        name = name_match.group(1).strip() if name_match else ""

        effect_pattern = rf'\|{effect_field}\s*=\s*(.+?)(?=\n\s*\|命之座|\Z)'
        effect_match = re.search(effect_pattern, inner, re.DOTALL)
        effect = effect_match.group(1).strip() if effect_match else ""

        effect = _clean_wiki_markup(effect)
        result.append({"name": name, "effect": effect})

    if all(not r["name"] for r in result):
        return None
    return result


# ====== 检查点 ======

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(data):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ====== 主流程 ======

def main():
    log("加载角色列表...")
    all_names = load_character_names()
    log(f"共 {len(all_names)} 个角色")

    results = load_checkpoint()
    done = set(results.keys())

    # 过滤出待处理的角色
    pending = [n for n in all_names if n not in done]
    log(f"已完成: {len(done)}，待处理: {len(pending)}")

    if not pending:
        log("全部完成！")
        return

    # 分批
    batches = [pending[i:i+BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    total_batches = len(batches)

    for batch_idx, batch in enumerate(batches):
        log(f"\n--- 批次 {batch_idx+1}/{total_batches} ({len(batch)} 个角色) ---")

        batch_result = fetch_wikitext_batch(batch)

        if batch_result is None:
            log(f"  批次 {batch_idx+1} 全部失败（3 次重试均未成功），跳过")
            save_checkpoint(results)
            # 对失败的批次，可能 CDN 在封禁中，等更久
            log(f"  等待 5 分钟冷却...")
            time.sleep(300)
            continue

        batch_ok = 0
        for name in batch:
            wikitext = batch_result.get(name)
            if wikitext:
                constellations = parse_constellations(wikitext)
                if constellations:
                    results[name] = constellations
                    batch_ok += 1
                    log(f"  {name}: {sum(1 for c in constellations if c['name'])}/6 有效")
                else:
                    log(f"  {name}: 未找到命之座数据")
            else:
                log(f"  {name}: wikitext 为空或页面不存在")

        save_checkpoint(results)
        log(f"  批次 {batch_idx+1} 完成: {batch_ok}/{len(batch)} 成功")

        # 批次间冷却
        if batch_idx < total_batches - 1:
            delay = BATCH_COOLDOWN + random.uniform(0, BATCH_JITTER)
            log(f"  等待 {delay:.0f}s 后进入下一批...")
            time.sleep(delay)

    # 最终输出
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    successful = sum(1 for v in results.values() if v and any(c["name"] for c in v))
    log(f"\n完成！成功 {successful}/{len(all_names)} 个角色")
    log(f"结果保存至: {OUTPUT_FILE}")

    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


if __name__ == "__main__":
    main()
