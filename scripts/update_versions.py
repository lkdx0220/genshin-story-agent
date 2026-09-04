#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 wiki API 数据中提取版本号，更新 quests_活动剧情.json 的 metadata.所属版本"""

import json
import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WIKI_FILE = r"C:\Users\24701\AppData\Local\Temp\trae\toolcall-output\fa71ec72-b60d-4802-bb40-9c378fde21c0.txt"
QUESTS_FILE = os.path.join(BASE_DIR, "content_data", "quests_活动剧情.json")

# 版本号映射：6.x -> 月之x
VERSION_MAP_6X = {
    "6.0": "月之一", "6.1": "月之二", "6.2": "月之三", "6.3": "月之四",
    "6.4": "月之五", "6.5": "月之六", "6.6": "月之七", "6.7": "月之八",
}


def convert_version(ver):
    if ver in VERSION_MAP_6X:
        return VERSION_MAP_6X[ver]
    return ver


def strip_quotes(name):
    """去掉书名号「」"""
    name = name.strip()
    if name.startswith("\u300c") and name.endswith("\u300d"):
        return name[1:-1]
    return name


def decode_unicode_escapes(text):
    """解码 unicode 转义序列"""
    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] == '\\u' and i + 6 <= len(text):
            try:
                result.append(chr(int(text[i+2:i+6], 16)))
                i += 6
                continue
            except ValueError:
                pass
        result.append(text[i])
        i += 1
    return ''.join(result)


def parse_wiki_data(filepath):
    """
    解析 wiki API JSON 返回结果，提取 {活动名称: 版本号} 字典。
    支持截断的 JSON（用正则从原始文本提取，绕过 JSON 解析）。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    mapping = {}

    # wiki API 返回的 JSON 中，中文 key 使用 unicode 转义
    # Python3 re 中 \u 是 Unicode 转义，需双写 \\u 来匹配字面量
    ver_escaped_pat = r'"\\u6240\\u5c5e\\u7248\\u672c":(\[[^\]]*\])'
    fulltext_pat = r'"fulltext":"((?:[^"\\]|\\.)*)"'

    ver_matches = list(re.finditer(ver_escaped_pat, content))
    ft_matches = list(re.finditer(fulltext_pat, content))

    # 配对策略：每个 "所属版本" 和其之后第一个 "fulltext" 属同一条目
    ft_idx = 0
    for ver_match in ver_matches:
        ver_end = ver_match.end()
        while ft_idx < len(ft_matches) and ft_matches[ft_idx].start() < ver_end:
            ft_idx += 1
        if ft_idx >= len(ft_matches):
            break

        ft_match = ft_matches[ft_idx]
        ver_array_str = ver_match.group(1)

        try:
            ver_array = json.loads(ver_array_str)
        except json.JSONDecodeError:
            continue

        if not ver_array:
            continue

        ver = convert_version(ver_array[0])
        fulltext_raw = ft_match.group(1)
        fulltext = decode_unicode_escapes(fulltext_raw)
        name = strip_quotes(fulltext)

        if name:
            mapping[name] = ver

    return mapping


def fix_truncated_json_and_parse(filepath):
    """尝试修复截断的 JSON 然后解析"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到最后一个完整的 displaytitle 条目截断
    last_displaytitle = content.rfind('"displaytitle":""')
    if last_displaytitle >= 0:
        after = content[last_displaytitle + len('"displaytitle":""'):]
        idx = after.find("}")
        if idx >= 0:
            cut_pos = last_displaytitle + len('"displaytitle":""') + idx + 1
            content = content[:cut_pos] + '}}}'

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}

    results = data["query"]["results"]
    mapping = {}

    for key, value in results.items():
        fulltext = value.get("fulltext", "")
        versions = value.get("printouts", {}).get("所属版本", [])
        if not versions:
            continue
        ver = convert_version(versions[0])
        name = strip_quotes(fulltext)
        mapping[name] = ver

    return mapping


def main():
    print("=" * 60)
    print("步骤1：解析 wiki API 数据")

    # 两种方式提取，合并结果（互补）
    mapping_a = fix_truncated_json_and_parse(WIKI_FILE)
    mapping_b = parse_wiki_data(WIKI_FILE)
    name_to_version = {**mapping_a, **mapping_b}

    print(f"  提取到 {len(name_to_version)} 条有效版本映射")

    # 显示一些样本
    if name_to_version:
        sample = list(name_to_version.items())[:10]
        print("  样本:")
        for name, ver in sample:
            print(f"    {name} -> {ver}")

    print("\n" + "=" * 60)
    print("步骤2：更新 quests_活动剧情.json")

    with open(QUESTS_FILE, "r", encoding="utf-8") as f:
        quests = json.load(f)

    total = len(quests)
    matched = 0
    unmatched = []

    for item in quests:
        title = item.get("title", "")
        name = strip_quotes(title)

        if name in name_to_version:
            item["metadata"]["所属版本"] = name_to_version[name]
            matched += 1
        else:
            unmatched.append(title)

    with open(QUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(quests, f, ensure_ascii=False, indent=2)

    print(f"  已写回: {QUESTS_FILE}")

    print("\n" + "=" * 60)
    print("步骤3：统计结果")
    print(f"  总记录数: {total}")
    print(f"  成功匹配: {matched}")
    print(f"  未匹配:   {len(unmatched)}")
    if unmatched:
        print("\n  未匹配的记录:")
        for t in unmatched:
            print(f"    - {t}")


if __name__ == "__main__":
    main()
