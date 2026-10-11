#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""初步补齐 cast：从剧情台词里提取“谁说话了”。

适用范围（按用户 2026-10-11 确认）：
- 传说任务（全部缺失 cast 的条目）
- 活动剧情（全部缺失 cast 的条目）
- 额外对话 / 彩蛋（处理到这一步为止）
- 魔神任务特殊：我们不会孤军奋战走说话人提取；
  鸟瞰风物 / 异常的权柄 / 不期而遇 / 被夺之泪确认为“仅旅行者/派蒙”，写 cast_absent。

规则：
- 只写 metadata.出场人物 / cast_source / cast_absent，不碰 text；
- 过滤旅行者、派蒙、空、荧以及旁白/泛称；
- 去重但保留首次出现顺序；
- 幂等，可重复运行。
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
CONTENT_DIR = BASE_DIR / "content_data"
REPORT_PATH = BASE_DIR / "reports" / "cast_fix_speakers_report.json"
BACKUP_SUFFIX = ".bak-before-speaker-cast-20261011"

# 支持「仆人」、凯瑟琳（挪德卡莱）这类名字
_SPEAKER_RE = re.compile(r"(?:^|\n)\s*\*?\s*([^\n：:]{1,40}?)\s*[：:]")
_BAD_SUBSTRINGS = (
    "选项", "描述", "任务", "奖励", "版本", "提示", "教程", "旁白",
    "神秘声音", "？？？", "众人", "士兵", "观众", "居民", "小女孩", "小男孩",
    "少女", "少年", "声音", "？？", "??",
    "们", "的", "某", "广播", "纸张", "笔记", "备忘录", "记录", "日志",
    "信件", "信函", "留言", "告示", "公告", "海报", "传单", "书籍",
    "书柜", "本子", "牌子", "屏幕", "终端", "机关", "机械", "系统",
    "童谣", "歌谣", "文章", "小说", "剧本", "广告", "纸条", "便签",
    "手册", "档案", "资料", "导游", "增益", "效果", "属性", "状态", "机制",
    "MediaWiki", "神像", "雕像", "过场动画", "动画", "CG", "幻影", "残影",
    "影子", "回响", "司法统计", "失物招领", "市民通告", "订阅", "公告板",
    "统计", "通告", "招领", "须知", "规则", "说明", "菜单", "读心", "摆设",
)
_PROTAGONISTS = {"旅行者", "派蒙", "空", "荧", "哥哥", "妹妹"}
_SPECIAL_ARCHON_NO_CAST = {"鸟瞰风物", "异常的权柄", "不期而遇", "被夺之泪"}
_SPECIAL_ARCHON_SPEAKER = {"我们不会孤军奋战"}


def is_valid_cast(raw) -> bool:
    value = str(raw or "").strip()
    return bool(value and "=" not in value and "|" not in value)


def is_extra(title: str, category: str) -> bool:
    return category == "彩蛋剧情" or "额外对话" in title or "彩蛋" in title


def clean_speaker(name: str) -> str:
    name = str(name or "").strip().lstrip("*").strip()
    name = re.sub(r"[（(](?:心声|画外音|内心独白|内心|气泡文字)[）)]$", "", name).strip()
    name = name.strip("、,，;；/ ")
    return name


def is_valid_speaker(name: str) -> bool:
    if not name or len(name) > 20:
        return False
    if name in _PROTAGONISTS:
        return False
    quoted = name.startswith(("「", "『", "【")) and name.endswith(("」", "』", "】"))
    if not quoted:
        low = name.lower()
        if any(token in low for token in ("file", "width", "div", "span", "null", "none")):
            return False
        for bad in _BAD_SUBSTRINGS:
            if bad in name:
                return False
    # 必须是中文/·/括号组成的短名，避免抓到成段文本
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·「」『』【】（）()\-\s]{1,20}", name):
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", name):
        return False
    return True


def extract_speakers(text: str):
    speakers = []
    seen = set()
    for match in _SPEAKER_RE.finditer(text or ""):
        raw_name = clean_speaker(match.group(1))
        # 支持 “派蒙&旅行者”“A、B” 这类同行多人
        for name in re.split(r"[&、,，;；/]", raw_name):
            name = clean_speaker(name)
            if not is_valid_speaker(name) or name in seen:
                continue
            seen.add(name)
            speakers.append(name)
    return speakers


def main():
    parser = argparse.ArgumentParser(description="从台词提取 cast")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = {}
    for path in sorted(CONTENT_DIR.glob("quests_*.json")):
        if "processed" in path.name or ".bak" in path.name or "before" in path.name:
            continue
        try:
            files[path] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

    stats = Counter()
    samples = {"updated": [], "absent": []}
    dirty_files = set()

    for path, data in files.items():
        for item in (data if isinstance(data, list) else list(data.values())):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            existing_source = str(metadata.get("cast_source") or "").strip()
            if is_valid_cast(metadata.get("出场人物")) and existing_source != "speaker_lines":
                continue
            category = str(item.get("category") or "")
            title = str(item.get("title") or "")
            is_special_no_cast = title in _SPECIAL_ARCHON_NO_CAST
            is_special_speaker = title in _SPECIAL_ARCHON_SPEAKER
            # 所有缺 cast 的剧情条目都走说话人提取；
            # 已有官方 cast 的条目在上面已跳过，不会被覆盖。

            if is_special_no_cast:
                metadata.pop("出场人物", None)
                metadata["cast_absent"] = True
                metadata["cast_source"] = "protagonist_only"
                stats["protagonist_only"] += 1
                if len(samples["absent"]) < 10:
                    samples["absent"].append(title)
                dirty_files.add(path)
                continue

            speakers = extract_speakers(item.get("text") or "")
            if speakers:
                metadata["出场人物"] = "、".join(speakers)
                metadata["cast_source"] = "speaker_lines"
                metadata.pop("cast_absent", None)
                stats["updated"] += 1
                if len(samples["updated"]) < 10:
                    samples["updated"].append({"title": title, "category": category, "speakers": speakers[:8]})
            else:
                metadata.pop("出场人物", None)
                metadata["cast_absent"] = True
                metadata["cast_source"] = "protagonist_only"
                stats["protagonist_only"] += 1
                if len(samples["absent"]) < 10:
                    samples["absent"].append(title)
            dirty_files.add(path)

    if not args.dry_run:
        for path in sorted(dirty_files):
            backup = Path(str(path) + BACKUP_SUFFIX)
            if not backup.exists():
                backup.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")
            path.write_text(json.dumps(files[path], ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"stats": dict(stats), "samples": samples, "dry_run": args.dry_run}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
