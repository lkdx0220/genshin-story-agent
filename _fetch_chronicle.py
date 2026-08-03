# -*- coding: utf-8 -*-
"""抓取提瓦特编年史（总页 + 7 国子页面）写入 lore.json"""

import json
import os
import re
import sys
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, "content_data")

# 目标页面：wiki 页面标题（与 URL 中编码的一致）
# 注意：这些是独立页面，URL 如 /ys/璃月，不是 /ys/提瓦特编年史/璃月
PAGES = [
    ("提瓦特编年史（总）", "提瓦特编年史"),
    ("蒙德", "蒙德"),
    ("璃月", "璃月"),
    ("稻妻", "稻妻"),
    ("须弥", "须弥"),
    ("枫丹", "枫丹"),
    ("纳塔", "纳塔"),
    ("至冬", "至冬"),
]

WAIT_SEC = 3.0  # 请求间隔，防 IP 限流
CONTENT_MIN = 200  # 最低正文长度，少于此视为空页


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_page(title_raw: str) -> str:
    """用 wiki action=parse API (GET) 获取页面纯文本。"""
    try:
        import requests
        resp = requests.get(
            "https://wiki.biligame.com/ys/api.php",
            params={
                "action": "parse",
                "page": title_raw,
                "prop": "text",
                "format": "json",
                "disablelimitreport": "1",
                "disableeditsection": "1",
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://wiki.biligame.com/ys/",
            },
            timeout=30,
        )
        data = resp.json()
        html = data.get("parse", {}).get("text", {}).get("*", "")
    except Exception as e:
        log(f"  API 请求失败: {e}")
        return ""

    if not html:
        log(f"  API 返回空内容")
        return ""

    # 提取纯文本
    text = _html_to_text(html)
    return text


def _html_to_text(html: str) -> str:
    """将 wiki parse API 返回的 HTML 转为纯文本，保留段落结构。"""
    # 移除引用标注 <sup>...</sup>
    text = re.sub(r'<sup[^>]*>.*?</sup>', '', html, flags=re.DOTALL)
    # 移除脚本和样式
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL)
    # 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # 标题标签转纯文本标题
    text = re.sub(r'<h([2-4])[^>]*>(.*?)</h\1>', r'\n## \2\n', text, flags=re.DOTALL)
    # <br> -> 换行
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # <li> -> 列表项
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL)
    # <p> -> 段落
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n', text, flags=re.DOTALL)
    # 移除剩余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 解码 HTML 实体
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#039;', "'").replace('&nbsp;', ' ')
    text = text.replace('&#160;', ' ')
    # Unicode 转义
    text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
    # 合并空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 去掉首尾空白行
    text = text.strip()
    return text


def main():
    log("===== 抓取提瓦特编年史 =====")

    # 读取现有 lore.json
    lore_path = os.path.join(CONTENT_DIR, "lore.json")
    if os.path.exists(lore_path):
        with open(lore_path, "r", encoding="utf-8") as f:
            lore = json.load(f)
        log(f"现有 lore: {len(lore)} 条")
    else:
        lore = []
        log("lore.json 不存在，将新建")

    # 删除旧的"提瓦特编年史"条目（4 条极短片段）
    old_count = len(lore)
    lore = [entry for entry in lore if entry.get("title") != "提瓦特编年史"]
    removed = old_count - len(lore)
    if removed:
        log(f"移除旧条目: {removed} 条")

    success = 0
    for idx, (label, title) in enumerate(PAGES):
        log(f"\n[{idx+1}/{len(PAGES)}] {label}")
        text = fetch_page(title)

        if not text or len(text) < CONTENT_MIN:
            log(f"  SKIP: 内容不足 ({len(text)} 字)")
            continue

        log(f"  获取: {len(text)} 字")

        # 写入 lore.json
        entry = {
            "title": title,
            "source": "提瓦特编年史",
            "text": text,
        }
        # 检查是否已有同名条目，替换或追加
        replaced = False
        for i, old in enumerate(lore):
            if old.get("title") == title and old.get("source") == "提瓦特编年史":
                lore[i] = entry
                replaced = True
                break
        if not replaced:
            lore.append(entry)

        success += 1

        if idx < len(PAGES) - 1:
            time.sleep(WAIT_SEC)

    # 原子写入
    tmp = lore_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lore, f, ensure_ascii=False, indent=2)
    os.replace(tmp, lore_path)

    log(f"\n===== 完成 =====")
    log(f"成功: {success}/{len(PAGES)}")
    log(f"lore.json 总计: {len(lore)} 条")


if __name__ == "__main__":
    main()
