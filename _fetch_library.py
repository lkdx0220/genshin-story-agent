# -*- coding: utf-8 -*-
"""抓取北陆图书馆全部子页面 + 补抓缺失的编年史正文，写入 lore.json"""

import json
import os
import re
import sys
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, "content_data")
TODO_PATH = os.path.join(SCRIPT_DIR, "_fetch_library_todo.json")
LORE_PATH = os.path.join(CONTENT_DIR, "lore.json")

WAIT_SEC = 3.0       # 请求间隔，防 IP 限流
CONTENT_MIN = 50      # 最低正文长度


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _html_to_text(html):
    """将 wiki parse API 返回的 HTML 转为纯文本（复用 _fetch_chronicle.py 逻辑）"""
    text = re.sub(r'<sup[^>]*>.*?</sup>', '', html, flags=re.DOTALL)
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<h([2-4])[^>]*>(.*?)</h\1>', r'\n## \2\n', text, flags=re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL)
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#039;', "'").replace('&nbsp;', ' ')
    text = text.replace('&#160;', ' ')
    text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_page(title):
    """用 wiki API 获取页面纯文本，返回 (text, success)"""
    try:
        resp = requests.get(
            "https://wiki.biligame.com/ys/api.php",
            params={
                "action": "parse",
                "page": title,
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
        return "", False

    if not html:
        log(f"  API 返回空内容")
        return "", False

    text = _html_to_text(html)
    return text, True


def add_to_lore(lore, title, text, source, entry_type):
    """添加或替换 lore.json 中的条目"""
    entry = {
        "title": title,
        "source": source,
        "text": text,
        "entry_type": entry_type,
    }
    replaced = False
    for i, old in enumerate(lore):
        if old.get("title") == title and old.get("source") == source:
            lore[i] = entry
            replaced = True
            break
    if not replaced:
        lore.append(entry)
    return lore


def main():
    log("===== 抓取北陆图书馆 =====")

    # 加载跟踪文件
    todo = load_json(TODO_PATH)
    lore = load_json(LORE_PATH) if os.path.exists(LORE_PATH) else []

    total_pending = sum(
        len([p for p in section["pages"] if p["status"] == "pending"])
        + (1 if "正文(编年史)缺失" in section.get("note", "") else 0)
        for section_name, section in todo.items()
        if section_name not in ("description", "total_sections", "total_subpages", "fetched", "last_updated")
    )
    log(f"待抓取: {total_pending} 项 (含缺正文的国家)")
    log("")

    processed = 0

    for section_key, section in todo.items():
        if section_key in ("description", "total_sections", "total_subpages", "fetched", "last_updated"):
            continue

        label = section["label"]
        need_body = "正文(编年史)缺失" in section.get("note", "")
        pending_pages = [p for p in section["pages"] if p["status"] == "pending"]

        if not need_body and not pending_pages:
            continue

        log(f"\n## {label} ({len(pending_pages)} 子页面{'+正文' if need_body else ''})")

        # 1) 先补正文
        if need_body:
            body_title = label
            log(f"  [正文] {body_title}")
            text, ok = fetch_page(body_title)
            if ok and len(text) >= CONTENT_MIN:
                lore = add_to_lore(lore, body_title, text, "提瓦特编年史", "世界观设定")
                log(f"    -> {len(text)} 字")
                processed += 1
            else:
                log(f"    -> 失败或无内容")
            time.sleep(WAIT_SEC)

        # 2) 子页面
        for page in pending_pages:
            title = page["title"]
            note = page.get("note", "")
            log(f"  [{note or '子页面'}] {title}")

            text, ok = fetch_page(title)
            if ok and len(text) >= CONTENT_MIN:
                lore = add_to_lore(lore, title, text, "北陆图书馆", "世界观设定")
                page["status"] = "fetched"
                log(f"    -> {len(text)} 字")
                processed += 1
            elif ok:
                page["status"] = "skipped"
                log(f"    -> 内容过少 ({len(text)} 字)，跳过")
            else:
                page["status"] = "failed"
                log(f"    -> 失败")

            # 每页更新跟踪文件
            section["fetched"] = len([p for p in section["pages"] if p["status"] == "fetched"])
            todo["fetched"] = sum(
                s["fetched"] for k, s in todo.items()
                if isinstance(s, dict) and "fetched" in s
            )
            save_json(TODO_PATH, todo)

            time.sleep(WAIT_SEC)

    # 最终保存 lore.json
    save_json(LORE_PATH, lore)
    log(f"\n===== 完成 =====")
    log(f"本次处理: {processed} 条")
    log(f"lore.json 总计: {len(lore)} 条")


if __name__ == "__main__":
    main()
