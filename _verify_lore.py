#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
万国诸卷拾遗全量校验脚本 v2

改进：用告示内容指纹匹配，而非告示标题匹配

功能：
  1. 从 wiki API 获取 7 个国家的"万国诸卷拾遗/X"页面 HTML
  2. 解析 HTML，提取每个告示的 (子地区, 公告板, 告示标题, 告示内容指纹)
  3. 加载 lore.json，提取所有"万国诸卷拾遗/X/Y"条目的 text
  4. 用内容指纹（前30字去标点）在 lore.json 中匹配
  5. 输出差异报告

用法: python _verify_lore.py
"""
import urllib.request
import urllib.parse
import json
import re
import os
import time
from html import unescape

BASE = os.path.dirname(os.path.abspath(__file__))
LORE_PATH = os.path.join(BASE, "content_data", "lore.json")
REPORT_PATH = os.path.join(BASE, "_lore_verify_report.json")
CACHE_DIR = os.path.join(BASE, "_wiki_cache")

COUNTRIES = ["蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱"]

WIKI_API = "https://wiki.biligame.com/ys/api.php?action=parse&page={page}&prop=text&format=json&disablelimitreport=1&disableeditsection=1&disabletoc=1"


def fetch_wiki_page(page_name, use_cache=True, max_retries=3):
    """调用 wiki API 获取页面 HTML，带缓存和重试"""
    cache_file = os.path.join(CACHE_DIR, page_name.replace("/", "_") + ".html")

    # 优先读缓存
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return f.read(), None

    url = WIKI_API.format(page=urllib.parse.quote(page_name))

    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LoreVerifier/2.0"
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if "error" in data:
                return None, f"API error: {data['error'].get('info', 'unknown')}"
            html = data.get("parse", {}).get("text", {}).get("*", "")

            # 保存缓存
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(html)

            return html, None
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                print(f"    [重试 {attempt + 1}/{max_retries}] {e}，等待 {wait}s...")
                time.sleep(wait)
            else:
                return None, str(e)

    return None, "max retries exceeded"


def strip_html(text):
    """去掉 HTML 标签，保留纯文本"""
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    return text.strip()


def make_fingerprint(text, n=30):
    """
    生成指纹：只保留中文字符，取前 n 个

    lore.json 的 text 可能含 wiki 标记（如 {{#info:[[花初]]、[[鉴秋]]}}），
    而 wiki HTML 已解析为纯文本。只保留中文字符可消除格式差异。
    """
    cleaned = re.sub(r'[^\u4e00-\u9fff]', '', text)
    return cleaned[:n]


def parse_wiki_html(html):
    """
    解析 wiki HTML，提取每个告示的内容指纹

    HTML 结构：
      <h3> 子地区（如"云来海"）
      <h4> 公告板（如"吃虎岩·公告板"）
      <h5> 告示标题（如"琉璃亭广告"）
      告示内容在 h5 后的 <p>/<li>/<dd> 中

    返回:
      dict: {子地区: [(公告板, 告示标题, 指纹), ...]}
    """
    result = {}
    current_region = None
    current_board = None

    # 用正则按 h2-h5 分段
    # 模式有2个捕获组：组1=完整标题标签，组2=标签名
    # re.split 结果：[前缀, 完整标签1, 标签名1, 内容1, 完整标签2, 标签名2, 内容2, ...]
    parts = re.split(r'(<(h[2-5])[^>]*>.*?</\2>)', html, flags=re.DOTALL)

    i = 1  # parts[0] 是第一个标题前的内容，跳过
    while i < len(parts):
        full_tag = parts[i]
        tag_name = parts[i + 1] if i + 1 < len(parts) else ""
        content = parts[i + 2] if i + 2 < len(parts) else ""

        tag_match = re.match(r'<(h[2-5])', full_tag)
        if not tag_match:
            i += 3
            continue

        tag = tag_match.group(1)
        # 提取标题文本
        headline_m = re.search(r'class="mw-headline"[^>]*>(.*?)</span>', full_tag, re.DOTALL)
        heading_text = strip_html(headline_m.group(1)) if headline_m else ""

        if tag == "h3":
            current_region = heading_text
            result.setdefault(current_region, [])
            current_board = None
        elif tag == "h4":
            current_board = heading_text
            if current_region:
                result[current_region].append((current_board, None, None))
        elif tag == "h5":
            # 提取 h5 后的文本内容作为告示内容
            raw_text = strip_html(content)[:300]
            fp = make_fingerprint(raw_text) if raw_text else ""
            if current_region is not None:
                result[current_region].append((current_board, heading_text, fp))

        i += 3

    return result


def load_lore_entries():
    """
    加载 lore.json，提取所有"万国诸卷拾遗/X/Y"条目

    返回:
      dict: {(国家, 子地区): [text1, text2, ...]}
      同时返回每个 text 的指纹集合
    """
    with open(LORE_PATH, "r", encoding="utf-8") as f:
        lore = json.load(f)

    entries = {}
    fingerprints = {}  # {(国家, 子地区): set(指纹1, 指纹2, ...)}
    for entry in lore:
        title = entry.get("title", "")
        if not title.startswith("万国诸卷拾遗/"):
            continue
        parts = title.split(" / ")
        if len(parts) < 2:
            continue
        country = parts[0].replace("万国诸卷拾遗/", "")
        region = " / ".join(parts[1:])
        key = (country, region)
        text = entry.get("text", "")
        entries.setdefault(key, []).append(text)

        # 生成指纹（前30字去标点）
        fp = make_fingerprint(text)
        if fp:
            fingerprints.setdefault(key, set()).add(fp)

    return entries, fingerprints


def check_fingerprint_in_lore(fp, lore_fps):
    """
    检查 wiki 告示的指纹是否在 lore 的指纹集合中出现

    模糊匹配：如果 wiki 指纹的 前15字 是 lore 某个指纹的子串，
    或者 lore 某个指纹的 前15字 是 wiki 指纹的子串，认为匹配
    """
    if not fp:
        return True  # 无指纹的告示跳过（可能是公告板层级）

    short_fp = fp[:15]  # 前15字作为短指纹
    for lore_fp in lore_fps:
        if short_fp in lore_fp or short_fp[:10] in lore_fp:
            return True
        # 反向检查
        if lore_fp[:15] in fp:
            return True

    return False


def verify_country(country, wiki_structure, lore_entries, lore_fps):
    """校验单个国家"""
    wiki_regions = {}
    missing = []
    for region, items in wiki_structure.items():
        wiki_regions[region] = len([x for x in items if x[2] is not None])  # 只算有指纹的
        lore_key = (country, region)
        lore_texts = lore_entries.get(lore_key, [])
        lore_fp_set = lore_fps.get(lore_key, set())

        for board, title, fp in items:
            if fp is None:
                continue  # 跳过公告板层级
            if not check_fingerprint_in_lore(fp, lore_fp_set):
                missing.append((region, board, title, fp))

    lore_regions_for_country = {
        k[1]: len(v) for k, v in lore_entries.items() if k[0] == country
    }
    extra_lore = []
    for region in lore_regions_for_country:
        if region not in wiki_structure:
            extra_lore.append((region, lore_regions_for_country[region]))

    return {
        "country": country,
        "wiki_regions": wiki_regions,
        "lore_regions": lore_regions_for_country,
        "missing_count": len(missing),
        "missing": missing,
        "extra_lore_count": len(extra_lore),
        "extra_lore": extra_lore,
    }


def main():
    print("=" * 70)
    print("万国诸卷拾遗全量校验 v2（内容指纹匹配）")
    print("=" * 70)

    print("\n[1] 加载 lore.json...")
    lore_entries, lore_fps = load_lore_entries()
    total_lore = sum(len(v) for v in lore_entries.values())
    print(f"    lore.json 中万国诸卷拾遗条目: {total_lore} 条")
    print(f"    覆盖 (国家, 子地区) 组合: {len(lore_entries)} 个")

    all_results = []
    for idx, country in enumerate(COUNTRIES):
        print(f"\n[{idx + 2}] 校验 {country}...")
        page_name = f"万国诸卷拾遗/{country}"
        html, err = fetch_wiki_page(page_name)
        if err:
            print(f"    [错误] 获取 wiki 页面失败: {err}")
            all_results.append({
                "country": country, "error": err,
                "wiki_regions": {}, "lore_regions": {},
                "missing_count": 0, "missing": [],
                "extra_lore_count": 0, "extra_lore": [],
            })
            continue

        print(f"    wiki HTML 大小: {len(html)} 字符")
        wiki_structure = parse_wiki_html(html)
        total_wiki_items = sum(len(v) for v in wiki_structure.values())
        total_with_fp = sum(len([x for x in items if x[2] is not None]) for items in wiki_structure.values())
        print(f"    wiki 解析出子地区: {len(wiki_structure)} 个")
        print(f"    wiki 告示条目总数: {total_wiki_items}（含指纹: {total_with_fp}）")

        result = verify_country(country, wiki_structure, lore_entries, lore_fps)
        all_results.append(result)

        if result["missing_count"] > 0:
            print(f"    [警告] 漏收 {result['missing_count']} 条告示:")
            for region, board, title, fp in result["missing"][:15]:
                board_str = f"[{board}] " if board else ""
                print(f"      - {region} / {board_str}{title}")
                print(f"        指纹: {fp}")
            if result["missing_count"] > 15:
                print(f"      ... 还有 {result['missing_count'] - 15} 条")
        else:
            print(f"    [OK] 无漏收")

        if result["extra_lore_count"] > 0:
            print(f"    [提示] lore 有但 wiki 没有的子地区: {len(result['extra_lore'])} 个")

        time.sleep(2)

    print(f"\n[x] 保存报告到 {REPORT_PATH}...")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("校验汇总")
    print("=" * 70)
    print(f"{'国家':<10} {'wiki告示':>10} {'lore条目':>10} {'漏收':>10} {'lore多余':>10}")
    print("-" * 55)
    total_missing = 0
    for r in all_results:
        if "error" in r:
            print(f"{r['country']:<10} {'ERROR':>10}")
            continue
        wiki_total = sum(r["wiki_regions"].values())
        lore_total = sum(r["lore_regions"].values())
        print(f"{r['country']:<10} {wiki_total:>10} {lore_total:>10} {r['missing_count']:>10} {r['extra_lore_count']:>10}")
        total_missing += r["missing_count"]
    print("-" * 55)
    print(f"{'总计漏收':<10} {'':>10} {'':>10} {total_missing:>10}")
    print(f"\n详细报告: {REPORT_PATH}")


if __name__ == "__main__":
    main()
