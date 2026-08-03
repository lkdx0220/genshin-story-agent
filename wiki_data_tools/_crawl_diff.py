#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Wiki 自动更新脚本 - 对比 wiki 与本地数据，输出差异报告。

核心流程：
  1. 按 wiki 分类拉取全部页面列表（list=categorymembers，带分页）
  2. 批量查询页面元数据（prop=info：touched、lastrevid、length）
  3. 与本地 _crawl_manifest.json 做对比
  4. lastrevid 变化 → 拉取 wikitext → 计算内容 SHA-256 Hash
  5. Hash 不同 → 标记为"实质更新"，Hash 相同 → 跳过
  6. 输出语义分级差异报告

用法：
  python _crawl_diff.py                    # 全部类别对比
  python _crawl_diff.py --category 角色     # 仅对比指定类别
  python _crawl_diff.py --category 角色 --auto-fetch  # 对比 + 自动抓取新增页
"""

import hashlib
import json
import os
import re
import sys
import time
import subprocess
import tempfile
import urllib.parse

# ====== 配置 ======

WIKI_API = "https://wiki.biligame.com/ys/api.php"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_FILE = os.path.join(SCRIPT_DIR, "_crawl_manifest.json")
DIFF_REPORT_FILE = os.path.join(SCRIPT_DIR, "_crawl_diff_report.json")
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "_crawl_diff_checkpoint.json")

REQUEST_INTERVAL = 10.0  # 请求间隔（秒），curl 指纹可绕过但频率仍需节制
BATCH_SIZE = 50          # prop=info 每批查询的页面数
CONTENT_DELAY = 3.0      # 拉取 wikitext 时的额外延迟（parse/revisions 限流更严格）

# wiki 分类 → 本地数据源映射
CATEGORY_MAP = {
    "角色": {
        "wiki_category": "角色",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "genshin_knowledge_base", "roles.py"),
        "data_type": "roles",
    },
    "武器": {
        "wiki_category": "武器",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "genshin_knowledge_base", "weapons.py"),
        "data_type": "weapons",
    },
    "任务": {
        "wiki_category": "任务",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "content_data", "quests_processed.json"),
        "data_type": "quests",
    },
    "书籍": {
        "wiki_category": "书籍",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "content_data", "books.json"),
        "data_type": "books",
    },
    "圣遗物": {
        "wiki_category": "圣遗物套装",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "genshin_knowledge_base", "artifacts.py"),
        "data_type": "artifacts",
    },
    "材料": {
        "wiki_category": "材料",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "genshin_knowledge_base", "materials.py"),
        "data_type": "materials",
    },
    "怪物": {
        "wiki_category": "怪物",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "content_data", "monsters.json"),
        "data_type": "monsters",
    },
    "食物": {
        "wiki_category": "食物",
        "output_file": os.path.join(os.path.dirname(SCRIPT_DIR), "content_data", "foods.json"),
        "data_type": "foods",
    },
}


# ====== 工具函数 ======

def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def safe_write(filepath, data):
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)


def api_get(params, retries=3):
    """调用 wiki API（使用 curl，TLS 指纹不被 CDN 封禁），带重试和 rate limit 保护。"""
    params["format"] = "json"
    url = WIKI_API + "?" + urllib.parse.urlencode(params)

    for attempt in range(retries):
        try:
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
                "--max-time", "30",
                url,
            ], capture_output=True, text=True, timeout=35)

            http_code = result.stdout.strip()
            if http_code == "200":
                with open(tmp_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.unlink(tmp_path)
                return data
            elif http_code == "567":
                wait = 10 * (2 ** attempt)
                log(f"  CDN 限流(567)，等待 {wait}s 后重试 (第 {attempt+1}/{retries} 次)...")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                time.sleep(wait)
            else:
                log(f"  HTTP {http_code}，第 {attempt+1}/{retries} 次重试...")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                time.sleep(REQUEST_INTERVAL * (attempt + 1))
        except subprocess.TimeoutExpired:
            log(f"  curl 超时，第 {attempt+1}/{retries} 次重试...")
            time.sleep(REQUEST_INTERVAL * (attempt + 1))
        except Exception as e:
            log(f"  API 请求失败: {e}，第 {attempt+1}/{retries} 次重试...")
            time.sleep(REQUEST_INTERVAL * (attempt + 1))
    return None


def _extract_content_for_hash(wikitext, data_type):
    """从 wikitext 中提取核心内容文本，用于计算 Hash。
    不同数据类型提取不同区域，忽略模板参数、分类标签、评论区等噪音。"""
    if not wikitext:
        return ""

    if data_type == "roles":
        # 角色页：提取角色信息模板 + 故事文本，忽略属性面板、评论区
        content_parts = []
        # 提取 {{角色信息|...}} 或类似 info 模板
        infobox = _extract_template(wikitext, "角色信息")
        if infobox:
            content_parts.append(infobox)
        # 提取故事段落（==角色故事== 之后的全部内容，直到下一个顶级段落或文末）
        story_match = re.search(r'==\s*角色故事\s*==\s*\n(.*?)(?=\n==[^=]|\Z)', wikitext, re.DOTALL)
        if story_match:
            content_parts.append(story_match.group(1))
        # 提取语音文本
        vo_match = re.search(r'==\s*角色语音\s*==\s*\n(.*?)(?=\n==[^=]|\Z)', wikitext, re.DOTALL)
        if vo_match:
            content_parts.append(vo_match.group(1))
        return "\n".join(content_parts)

    elif data_type == "quests":
        # 任务页：提取剧情正文，忽略任务信息模板参数（奖励、版本号等）
        # 通常任务页的剧情在 ==任务剧情== 或 ==对话== 标题下
        content_parts = []
        for heading in ["任务剧情", "对话", "剧情", "任务流程"]:
            m = re.search(rf'==\s*{heading}\s*==\s*\n(.*?)(?=\n==[^=]|\Z)', wikitext, re.DOTALL)
            if m:
                content_parts.append(m.group(1))
        if not content_parts:
            # 兜底：取任务信息模板 + 全部正文（去掉模板参数部分取核心描述）
            infobox = _extract_template(wikitext, "任务信息")
            if infobox:
                content_parts.append(infobox)
            # 取第一个 == 段落之后的内容
            first_section = re.search(r'==[^=].*?==\s*\n(.*)', wikitext, re.DOTALL)
            if first_section:
                content_parts.append(first_section.group(1))
        return "\n".join(content_parts)

    elif data_type in ("weapons", "artifacts", "materials", "books", "monsters"):
        # 通用：取 info 模板 + 正文（忽略分类标签）
        content_parts = []
        # 尝试提取信息模板
        template_names = {
            "weapons": "武器信息",
            "artifacts": "圣遗物信息",
            "materials": "材料信息",
            "books": "书籍信息",
            "monsters": "怪物信息",
        }
        tmpl_name = template_names.get(data_type, "")
        if tmpl_name:
            tmpl = _extract_template(wikitext, tmpl_name)
            if tmpl:
                content_parts.append(tmpl)
        # 提取正文段落
        sections = re.findall(r'==[^=].*?==\s*\n(.*?)(?=\n==[^=]|\Z)', wikitext, re.DOTALL)
        content_parts.extend(sections)
        return "\n".join(content_parts)

    else:
        # 未知类型：取全部 wikitext
        return wikitext


def _extract_template(text, template_name):
    """提取指定名称的 wiki 模板内容。"""
    pattern = r'\{\{' + re.escape(template_name)
    match = re.search(pattern, text)
    if not match:
        return ""
    pos = match.end()
    depth = 1
    while pos < len(text) and depth > 0:
        if text[pos:pos+2] == "{{":
            depth += 1
            pos += 2
        elif text[pos:pos+2] == "}}":
            depth -= 1
            pos += 2
        else:
            pos += 1
    if depth != 0:
        return ""
    return text[match.start():pos]


def compute_content_hash(wikitext, data_type):
    """计算核心内容的 SHA-256 Hash。"""
    core = _extract_content_for_hash(wikitext, data_type)
    return hashlib.sha256(core.encode("utf-8")).hexdigest()


# ====== 本地数据查询 ======

def _load_local_manifest():
    """加载本地 manifest。"""
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _build_local_index(category_key):
    """从本地数据源构建已知页面索引。
    返回 {page_title: {"lastrevid": ..., "content_hash": ..., ...}}"""
    cat_config = CATEGORY_MAP.get(category_key)
    if not cat_config:
        return {}

    manifest = _load_local_manifest()
    cat_manifest = manifest.get(category_key, {})
    return cat_manifest


# ====== Wiki 数据拉取 ======

def fetch_category_pages(category_name):
    """拉取指定分类下所有页面列表（带分页）。"""
    pages = []
    cmcontinue = None
    log(f"  拉取 Category:{category_name} 页面列表...")

    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category_name}",
            "cmlimit": 500,
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_get(params)
        if not data:
            log("  API 返回空，停止拉取")
            break

        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)

        if "continue" in data:
            cmcontinue = data["continue"]["cmcontinue"]
            time.sleep(REQUEST_INTERVAL)
        else:
            break

    log(f"  共 {len(pages)} 个页面")
    return pages


def fetch_pages_info(pages):
    """批量查询页面元数据（touched、lastrevid、length）。
    返回 {pageid: {title, touched, lastrevid, length}}"""
    info_map = {}
    total = len(pages)

    for i in range(0, total, BATCH_SIZE):
        batch = pages[i:i+BATCH_SIZE]
        pageids = "|".join(str(p["pageid"]) for p in batch)

        params = {
            "action": "query",
            "prop": "info",
            "pageids": pageids,
        }
        data = api_get(params)
        if not data:
            log(f"  prop=info 批次 {i//BATCH_SIZE + 1} 失败，跳过")
            continue

        result_pages = data.get("query", {}).get("pages", {})
        for pid_str, info in result_pages.items():
            pid = int(pid_str)
            if pid < 0:
                continue  # 无效页面
            info_map[pid] = {
                "title": info.get("title", ""),
                "touched": info.get("touched", ""),
                "lastrevid": info.get("lastrevid", 0),
                "length": info.get("length", 0),
            }

        log(f"  prop=info 批次 {i//BATCH_SIZE + 1}/{(total+BATCH_SIZE-1)//BATCH_SIZE}: {len(batch)} 页")
        if i + BATCH_SIZE < total:
            time.sleep(REQUEST_INTERVAL)

    log(f"  共获取 {len(info_map)} 个页面元数据")
    return info_map


def fetch_page_content(page_title):
    """拉取单页 wikitext 内容。使用 revisions API（限流比 parse 宽松）。
    注意：wiki CDN 对程序化请求有严格的 567 限流，单次运行大量拉取会被封。
    建议配合 --output-queue 模式：先输出待拉取页面列表到文件，
    再分批用 WebFetch 工具手动获取，最后用 --process-queue 解析。"""
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": page_title,
        "rvprop": "content",
    }
    data = api_get(params)
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for pid, page_info in pages.items():
        if int(pid) < 0:
            continue
        revisions = page_info.get("revisions", [])
        if revisions:
            # 版本较新的 MediaWiki 返回 slots 格式
            if "slots" in revisions[0]:
                return revisions[0].get("slots", {}).get("main", {}).get("*", "")
            # 旧格式直接返回 *
            return revisions[0].get("*", "")
    return None


# ====== 差异对比 ======

def classify_update(old_wikitext, new_wikitext, data_type):
    """分类更新类型：CONTENT_MAJOR / STATS_MINOR / META_ONLY。
    通过对比段落标题变化来判断。"""
    if not old_wikitext:
        return "CONTENT_MAJOR"  # 新增页面

    # 提取新旧版本的段落标题
    old_sections = set(re.findall(r'==\s*([^=]+?)\s*==', old_wikitext))
    new_sections = set(re.findall(r'==\s*([^=]+?)\s*==', new_wikitext))

    added = new_sections - old_sections
    removed = old_sections - new_sections

    # 剧情/故事/语音相关段落变化 → MAJOR
    content_headings = {"角色故事", "角色语音", "任务剧情", "对话", "剧情", "故事",
                         "角色详细", "神之眼", "任务流程", "相关剧情", "语音"}
    for h in (added | removed):
        for ch in content_headings:
            if ch in h:
                return "CONTENT_MAJOR"

    # 数值/属性相关段落变化 → MINOR
    stat_headings = {"属性", "突破", "天赋", "命之座", "推荐配队", "推荐装备",
                      "圣遗物推荐", "武器推荐", "面板推荐", "养成材料"}
    for h in (added | removed):
        for sh in stat_headings:
            if sh in h:
                return "STATS_MINOR"

    # 无实质段落变化，仅文本微调
    if not added and not removed:
        # 比较文本长度变化比例
        old_len = len(old_wikitext)
        new_len = len(new_wikitext)
        if old_len > 0:
            ratio = abs(new_len - old_len) / old_len
            if ratio < 0.01:
                return "META_ONLY"  # 变化 < 1%，很可能是错别字或分类标签
            elif ratio < 0.05:
                return "STATS_MINOR"
        return "CONTENT_MAJOR"

    return "CONTENT_MAJOR"  # 有段落增删，保守当 MAJOR 处理


# ====== 主流程 ======

def diff_category(category_key, auto_fetch=False):
    """对比单个分类的 wiki 数据与本地数据。"""
    cat_config = CATEGORY_MAP.get(category_key)
    if not cat_config:
        log(f"[错误] 未知分类: {category_key}")
        return None

    wiki_cat = cat_config["wiki_category"]
    data_type = cat_config["data_type"]
    local_index = _build_local_index(category_key)

    log(f"\n{'='*60}")
    log(f"  对比分类: {category_key} (Category:{wiki_cat})")
    log(f"  本地已知页面: {len(local_index)}")
    log(f"{'='*60}")

    # Step 1: 拉取 wiki 页面列表
    wiki_pages = fetch_category_pages(wiki_cat)
    if not wiki_pages:
        log("  [警告] 未拉取到任何页面，跳过")
        return None

    # Step 2: 获取所有页面元数据
    wiki_info = fetch_pages_info(wiki_pages)

    # Step 3: 快速对比（lastrevid 预筛）
    new_pages = []       # wiki 有，本地没有
    changed_pages = []   # lastrevid 变了，需要进一步 Hash 验证
    unchanged_pages = [] # lastrevid 没变
    deleted_pages = []   # 本地有，wiki 没有

    wiki_titles = {info["title"] for info in wiki_info.values()}

    for pid, info in wiki_info.items():
        title = info["title"]
        local_entry = local_index.get(title)
        if not local_entry:
            new_pages.append((pid, title, info))
        elif local_entry.get("lastrevid") != info["lastrevid"]:
            changed_pages.append((pid, title, info, local_entry))
        else:
            unchanged_pages.append((pid, title, info))

    for title in local_index:
        if title not in wiki_titles:
            deleted_pages.append((title, local_index[title]))

    log(f"  新增: {len(new_pages)} | 可能更新: {len(changed_pages)} | "
        f"未变: {len(unchanged_pages)} | 本地多出: {len(deleted_pages)}")

    # Step 4: 对 changed_pages 做内容 Hash 校验
    # 特殊处理：lastrevid=0 表示未建立基线，直接记录 Hash 不报更新
    real_updates = []
    false_updates = []
    baseline_pages = []  # 首次基线：只记录 Hash，不报更新

    for pid, title, info, local_entry in changed_pages:
        is_baseline = (local_entry.get("lastrevid", 0) == 0)
        if is_baseline:
            log(f"  基线建立: {title}")
        else:
            log(f"  Hash 校验: {title} (lastrevid {local_entry.get('lastrevid')} → {info['lastrevid']})")

        wikitext = fetch_page_content(title)
        if not wikitext:
            log(f"    拉取失败，跳过")
            continue

        new_hash = compute_content_hash(wikitext, data_type)
        old_hash = local_entry.get("content_hash", "")

        if is_baseline:
            baseline_pages.append({
                "pageid": pid,
                "title": title,
                "new_lastrevid": info["lastrevid"],
                "new_hash": new_hash,
            })
        elif new_hash != old_hash:
            update_type = classify_update(
                local_entry.get("_last_wikitext", ""), wikitext, data_type
            )
            real_updates.append({
                "pageid": pid,
                "title": title,
                "old_lastrevid": local_entry.get("lastrevid"),
                "new_lastrevid": info["lastrevid"],
                "old_hash": old_hash,
                "new_hash": new_hash,
                "update_type": update_type,
            })
            log(f"    → 实质更新 [{update_type}]")
        else:
            false_updates.append({"pageid": pid, "title": title})
            log(f"    → 假更新（Hash 未变）")

        time.sleep(CONTENT_DELAY)

    log(f"  实质更新: {len(real_updates)} | 首次基线: {len(baseline_pages)} | 假更新: {len(false_updates)}")

    # 构建差异报告
    report = {
        "category": category_key,
        "wiki_category": wiki_cat,
        "data_type": data_type,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_wiki_pages": len(wiki_info),
            "local_known_pages": len(local_index),
            "new": len(new_pages),
            "real_updates": len(real_updates),
            "baseline_established": len(baseline_pages),
            "false_updates": len(false_updates),
            "unchanged": len(unchanged_pages),
            "deleted_from_wiki": len(deleted_pages),
        },
        "new_pages": [{"pageid": pid, "title": t, "info": i} for pid, t, i in new_pages],
        "real_updates": real_updates,
        "baseline_pages": baseline_pages,
        "unchanged_count": len(unchanged_pages),
        "deleted_from_wiki": [{"title": t, "local": e} for t, e in deleted_pages],
    }

    return report


def save_manifest_from_report(report):
    """根据差异报告更新本地 manifest。"""
    manifest = _load_local_manifest()
    cat = report["category"]

    if cat not in manifest:
        manifest[cat] = {}

    cat_manifest = manifest[cat]

    # 基线页面：记录 lastrevid 和 hash（不报更新）
    for bp in report.get("baseline_pages", []):
        title = bp["title"]
        cat_manifest[title] = {
            "lastrevid": bp["new_lastrevid"],
            "content_hash": bp["new_hash"],
        }

    # 实质更新页面：更新 lastrevid 和 hash
    for upd in report["real_updates"]:
        title = upd["title"]
        if title in cat_manifest:
            cat_manifest[title]["lastrevid"] = upd["new_lastrevid"]
            cat_manifest[title]["content_hash"] = upd["new_hash"]

    manifest[cat] = cat_manifest
    safe_write(MANIFEST_FILE, manifest)
    log(f"  manifest 已更新 ({cat})")


def sync_manifest_lastrevid(light_report):
    """轻量同步：仅将基线页面的 lastrevid 回写到 manifest。
    用于 --skip-content 模式——不拉 wikitext，只更新版本戳。"""
    manifest = _load_local_manifest()
    cat = light_report["category"]

    if cat not in manifest:
        manifest[cat] = {}

    cat_manifest = manifest[cat]

    # 基线页面：lastrevid=0 → 写入 wiki 的 lastrevid
    for bp in light_report.get("baseline_pages", []):
        title = bp["title"]
        if title in cat_manifest:
            cat_manifest[title]["lastrevid"] = bp["new_lastrevid"]

    # 未变页面：确认 lastrevid 一致（可选，通常不变）
    # 此处不处理，因为未变页面的 lastrevid 本来就匹配

    manifest[cat] = cat_manifest
    safe_write(MANIFEST_FILE, manifest)
    baseline_count = len(light_report.get("baseline_pages", []))
    if baseline_count > 0:
        log(f"  manifest 已同步基线 ({cat}): {baseline_count} 条")


def init_manifest_from_local(category_key):
    """首次运行：从本地数据初始化 manifest（不拉 wiki）。
    为每个已知页面记录当前的 title → {lastrevid, content_hash}。
    lastrevid 填 0 表示"待首次同步"。"""
    cat_config = CATEGORY_MAP.get(category_key)
    if not cat_config:
        return

    manifest = _load_local_manifest()
    if category_key in manifest and manifest[category_key]:
        log(f"  {category_key} 已有 manifest，跳过初始化")
        return

    data_type = cat_config["data_type"]
    output_file = cat_config["output_file"]

    entries = {}
    if os.path.exists(output_file):
        if output_file.endswith(".json"):
            with open(output_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        elif output_file.endswith(".py"):
            # 导入 Python 模块
            mod_name = os.path.splitext(os.path.basename(output_file))[0]
            import importlib.util
            spec = importlib.util.spec_from_file_location(mod_name, output_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            # 按模块约定查找数据
            data = (getattr(mod, "角色知识库", None)
                    or getattr(mod, "武器知识库", None)
                    or getattr(mod, "圣遗物知识库", None)
                    or getattr(mod, "材料知识库", None)
                    or [])
        else:
            data = []

        # 各数据类型的 title 字段名
        _TITLE_KEYS = {
            "roles": "角色名称",
            "weapons": "武器名称",
            "artifacts": "圣遗物名称",
            "materials": "名称",
            "quests": "title",
            "books": "title",
            "monsters": "名称",
            "foods": "名称",
        }
        title_key = _TITLE_KEYS.get(data_type, "title")

        # 兼容两种数据格式：list of dict 或 dict of dict
        items_iter = data.items() if isinstance(data, dict) else enumerate(data)

        for _key, item in items_iter:
            if isinstance(item, str):
                # dict 格式下，value 可能是字符串（如 {title: value}）
                title = item if isinstance(data, dict) else item
            else:
                title = item.get(title_key, item.get("name", ""))

            if title:
                entries[title] = {
                    "lastrevid": 0,
                    "content_hash": "",
                }

    manifest[category_key] = entries
    safe_write(MANIFEST_FILE, manifest)
    log(f"  {category_key}: 初始化 {len(entries)} 条 (lastrevid=0)")


def diff_category_light(category_key):
    """轻量对比：仅 prop=info + lastrevid，不拉取 wikitext 内容。
    返回报告，后续通过 --process-queue 完成 Hash 对比。"""
    cat_config = CATEGORY_MAP.get(category_key)
    if not cat_config:
        log(f"[错误] 未知分类: {category_key}")
        return None

    wiki_cat = cat_config["wiki_category"]
    data_type = cat_config["data_type"]
    local_index = _build_local_index(category_key)

    log(f"\n{'='*60}")
    log(f"  轻量对比: {category_key} (Category:{wiki_cat})")
    log(f"  本地已知页面: {len(local_index)}")
    log(f"{'='*60}")

    wiki_pages = fetch_category_pages(wiki_cat)
    if not wiki_pages:
        log("  [警告] 未拉取到任何页面")
        return None

    wiki_info = fetch_pages_info(wiki_pages)

    new_pages = []
    changed_pages = []
    unchanged_pages = []
    deleted_pages = []
    wiki_titles = {info["title"] for info in wiki_info.values()}

    for pid, info in wiki_info.items():
        title = info["title"]
        local_entry = local_index.get(title)
        if not local_entry:
            new_pages.append((pid, title, info))
        elif local_entry.get("lastrevid", 0) != info["lastrevid"]:
            changed_pages.append((pid, title, info, local_entry))
        else:
            unchanged_pages.append((pid, title, info))

    for title in local_index:
        if title not in wiki_titles:
            deleted_pages.append((title, local_index[title]))

    # 分离基线页面（lastrevid=0）和真正变更
    baseline_pages = []
    real_changed = []
    for pid, title, info, local_entry in changed_pages:
        if local_entry.get("lastrevid", 0) == 0:
            baseline_pages.append({"pageid": pid, "title": title, "new_lastrevid": info["lastrevid"]})
        else:
            real_changed.append({"pageid": pid, "title": title, "new_lastrevid": info["lastrevid"],
                                 "old_lastrevid": local_entry["lastrevid"]})

    log(f"  新增: {len(new_pages)} | 可能更新: {len(real_changed)} | "
        f"待基线: {len(baseline_pages)} | 未变: {len(unchanged_pages)} | 本地多出: {len(deleted_pages)}")

    return {
        "category": category_key,
        "wiki_category": wiki_cat,
        "data_type": data_type,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_wiki_pages": len(wiki_info),
            "local_known_pages": len(local_index),
            "new": len(new_pages),
            "pending_updates": len(real_changed),
            "pending_baseline": len(baseline_pages),
            "unchanged": len(unchanged_pages),
            "deleted_from_wiki": len(deleted_pages),
        },
        "new_pages": [{"pageid": pid, "title": t, "info": i} for pid, t, i in new_pages],
        "baseline_pages": baseline_pages,
        "real_updates": [{"pageid": r["pageid"], "title": r["title"],
                          "old_lastrevid": r["old_lastrevid"],
                          "new_lastrevid": r["new_lastrevid"]} for r in real_changed],
        "unchanged_count": len(unchanged_pages),
        "deleted_from_wiki": [{"title": t, "local": e} for t, e in deleted_pages],
    }


# ====== 命令行入口 ======

QUEUE_OUTPUT_FILE = os.path.join(SCRIPT_DIR, "_crawl_content_queue.json")
QUEUE_RESULTS_FILE = os.path.join(SCRIPT_DIR, "_crawl_content_results.json")


def generate_content_queue(reports):
    """从差异报告生成待拉取页面队列（API URL 列表）。"""
    queue = []
    for report in reports:
        cat = report["category"]
        data_type = report["data_type"]
        # 新增页面
        for np in report.get("new_pages", []):
            queue.append({
                "category": cat,
                "data_type": data_type,
                "title": np["title"],
                "reason": "new",
                "api_url": f"https://wiki.biligame.com/ys/api.php?action=query&format=json&prop=revisions&titles={np['title']}&rvprop=content",
            })
        # 更新 + 基线页面
        for bp in report.get("baseline_pages", []):
            queue.append({
                "category": cat,
                "data_type": data_type,
                "title": bp["title"],
                "reason": "baseline",
                "api_url": f"https://wiki.biligame.com/ys/api.php?action=query&format=json&prop=revisions&titles={bp['title']}&rvprop=content",
            })
        for upd in report.get("real_updates", []):
            queue.append({
                "category": cat,
                "data_type": data_type,
                "title": upd["title"],
                "reason": "update",
                "api_url": f"https://wiki.biligame.com/ys/api.php?action=query&format=json&prop=revisions&titles={upd['title']}&rvprop=content",
            })
    return queue


def process_content_results(results_file):
    """处理已拉取的内容结果，完成 Hash 对比。
    results_file: JSON 列表，每项 {title, wikitext, reason}
    返回按 category 分组的对比结果。"""
    if not os.path.exists(results_file):
        log(f"[错误] 结果文件不存在: {results_file}")
        return

    with open(results_file, "r", encoding="utf-8") as f:
        results = json.load(f)

    log(f"加载 {len(results)} 条内容结果")

    # 按 category 分组
    by_category = {}
    for item in results:
        cat = item.get("category", "未知")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(item)

    all_reports = []
    manifest = _load_local_manifest()

    for cat, items in by_category.items():
        cat_config = CATEGORY_MAP.get(cat)
        if not cat_config:
            continue
        data_type = cat_config["data_type"]
        cat_manifest = manifest.get(cat, {})

        real_updates = []
        baseline_pages = []
        failed = []

        for item in items:
            title = item["title"]
            wikitext = item.get("wikitext", "")
            reason = item.get("reason", "unknown")

            if not wikitext:
                failed.append(title)
                continue

            new_hash = compute_content_hash(wikitext, data_type)
            local_entry = cat_manifest.get(title, {})
            old_hash = local_entry.get("content_hash", "")

            if reason == "baseline" or local_entry.get("lastrevid", 0) == 0:
                baseline_pages.append({
                    "title": title,
                    "new_hash": new_hash,
                    "new_lastrevid": local_entry.get("lastrevid", 0),
                })
                cat_manifest[title] = {
                    "lastrevid": local_entry.get("lastrevid", 0),
                    "content_hash": new_hash,
                }
            elif new_hash != old_hash:
                update_type = classify_update(
                    local_entry.get("_last_wikitext", ""), wikitext, data_type
                )
                real_updates.append({
                    "title": title,
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "update_type": update_type,
                })
                cat_manifest[title]["content_hash"] = new_hash

        log(f"  [{cat}] 实质更新: {len(real_updates)}, 基线: {len(baseline_pages)}, 失败: {len(failed)}")

        # 保存 manifest
        manifest[cat] = cat_manifest
        all_reports.append({
            "category": cat,
            "real_updates": real_updates,
            "baseline_pages": baseline_pages,
            "failed": failed,
        })

    safe_write(MANIFEST_FILE, manifest)
    safe_write(DIFF_REPORT_FILE, all_reports)

    # 汇总
    total_updated = sum(len(r["real_updates"]) for r in all_reports)
    total_baseline = sum(len(r["baseline_pages"]) for r in all_reports)
    total_failed = sum(len(r.get("failed", [])) for r in all_reports)

    log(f"\n{'='*60}")
    log(f"  处理完成")
    log(f"  实质更新: {total_updated} | 基线建立: {total_baseline} | 失败: {total_failed}")
    log(f"{'='*60}")

    if total_updated > 0:
        log(f"\n--- 实质更新详情 ---")
        for r in all_reports:
            for upd in r["real_updates"]:
                log(f"  [{r['category']}] {upd['title']} ({upd['update_type']})")


def diff_category_from_cache(category_key, metadata_file):
    """从预拉取的元数据缓存文件做对比（供 WebFetch 工作流使用）。
    metadata_file: JSON 文件，格式 {pageid: {title, lastrevid}}"""
    cat_config = CATEGORY_MAP.get(category_key)
    if not cat_config:
        log(f"[错误] 未知分类: {category_key}")
        return None

    if not os.path.exists(metadata_file):
        log(f"[错误] 元数据文件不存在: {metadata_file}")
        return None

    with open(metadata_file, "r", encoding="utf-8") as f:
        wiki_info = json.load(f)

    data_type = cat_config["data_type"]
    local_index = _build_local_index(category_key)

    log(f"\n{'='*60}")
    log(f"  缓存对比: {category_key}")
    log(f"  wiki 页面: {len(wiki_info)} | 本地已知: {len(local_index)}")
    log(f"{'='*60}")

    new_pages = []
    changed_pages = []
    unchanged_pages = []
    deleted_pages = []

    wiki_info_indexed = {}
    for pid_str, info in wiki_info.items():
        title = info.get("title", "")
        wiki_info_indexed[title] = {
            "pageid": int(pid_str),
            "title": title,
            "lastrevid": info.get("lastrevid", 0),
        }

    for title, info in wiki_info_indexed.items():
        local_entry = local_index.get(title)
        if not local_entry:
            new_pages.append(info)
        elif local_entry.get("lastrevid", 0) != info["lastrevid"]:
            changed_pages.append((info, local_entry))
        else:
            unchanged_pages.append(info)

    for title in local_index:
        if title not in wiki_info_indexed:
            deleted_pages.append((title, local_index[title]))

    # 分离基线和真正变更
    baseline_pages = []
    real_changed = []
    for info, local_entry in changed_pages:
        if local_entry.get("lastrevid", 0) == 0:
            baseline_pages.append(info)
        else:
            real_changed.append({"pageid": info["pageid"], "title": info["title"],
                                 "old_lastrevid": local_entry["lastrevid"],
                                 "new_lastrevid": info["lastrevid"]})

    log(f"  新增: {len(new_pages)} | 可能更新: {len(real_changed)} | "
        f"待基线: {len(baseline_pages)} | 未变: {len(unchanged_pages)} | 本地多出: {len(deleted_pages)}")

    return {
        "category": category_key,
        "wiki_category": cat_config.get("wiki_category", ""),
        "data_type": data_type,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_wiki_pages": len(wiki_info_indexed),
            "local_known_pages": len(local_index),
            "new": len(new_pages),
            "pending_updates": len(real_changed),
            "pending_baseline": len(baseline_pages),
            "unchanged": len(unchanged_pages),
            "deleted_from_wiki": len(deleted_pages),
        },
        "new_pages": new_pages,
        "baseline_pages": baseline_pages,
        "real_updates": real_changed,
        "unchanged_count": len(unchanged_pages),
        "deleted_from_wiki": [{"title": t, "local": e} for t, e in deleted_pages],
    }


def main():
    args = sys.argv[1:]
    target_categories = None
    auto_fetch = False
    init_mode = False
    skip_content = False
    process_queue_file = None
    load_metadata_file = None

    i = 0
    while i < len(args):
        if args[i] == "--category" and i + 1 < len(args):
            target_categories = [args[i+1]]
            i += 2
        elif args[i] == "--auto-fetch":
            auto_fetch = True
            i += 1
        elif args[i] == "--init":
            init_mode = True
            i += 1
        elif args[i] == "--skip-content":
            skip_content = True
            i += 1
        elif args[i] == "--process-queue" and i + 1 < len(args):
            process_queue_file = args[i+1]
            i += 2
        elif args[i] == "--load-metadata" and i + 1 < len(args):
            load_metadata_file = args[i+1]
            i += 2
        else:
            i += 1

    if process_queue_file:
        process_content_results(process_queue_file)
        return

    if load_metadata_file:
        # 从预拉取的缓存文件做对比
        cats = target_categories or list(CATEGORY_MAP.keys())
        all_reports = []
        for cat in cats:
            report = diff_category_from_cache(cat, load_metadata_file)
            if report:
                all_reports.append(report)

        queue = generate_content_queue(all_reports)
        safe_write(QUEUE_OUTPUT_FILE, queue)
        log(f"\n待拉取页面队列: {len(queue)} 条")
        log(f"队列文件: {QUEUE_OUTPUT_FILE}")
        if queue:
            reasons = {}
            for q in queue:
                reasons[q["reason"]] = reasons.get(q["reason"], 0) + 1
            for reason, count in reasons.items():
                log(f"  {reason}: {count} 条")

            log(f"\n--- 新增页面 ---")
            for q in queue:
                if q["reason"] == "new":
                    log(f"  [{q['category']}] {q['title']}")

            log(f"\n--- 实质更新页面 ---")
            for q in queue:
                if q["reason"] == "update":
                    log(f"  [{q['category']}] {q['title']}")
        return

    if init_mode:
        cats = target_categories or list(CATEGORY_MAP.keys())
        log("初始化 manifest（基于本地数据）...")
        for cat in cats:
            init_manifest_from_local(cat)
        log("初始化完成。")
        return

    if not target_categories:
        target_categories = list(CATEGORY_MAP.keys())

    log(f"开始对比，目标分类: {target_categories}")
    if skip_content:
        log("模式: 跳过内容拉取（--skip-content）")
    all_reports = []

    for cat in target_categories:
        if cat not in CATEGORY_MAP:
            log(f"[跳过] 未知分类: {cat}")
            continue

        if skip_content:
            # 轻量模式：只做 prop=info + lastrevid 对比，不拉 wikitext
            report = diff_category_light(cat)
        else:
            report = diff_category(cat, auto_fetch=auto_fetch)

        if report:
            all_reports.append(report)
            if skip_content:
                sync_manifest_lastrevid(report)
            else:
                save_manifest_from_report(report)

    if skip_content:
        # 生成待拉取队列
        queue = generate_content_queue(all_reports)
        safe_write(QUEUE_OUTPUT_FILE, queue)
        log(f"\n待拉取页面队列: {len(queue)} 条")
        log(f"队列文件: {QUEUE_OUTPUT_FILE}")
        log(f"请使用 WebFetch 分批拉取结果，保存至: {QUEUE_RESULTS_FILE}")
        log(f"完成后运行: python _crawl_diff.py --process-queue {QUEUE_RESULTS_FILE}")

        # 打印队列摘要
        if queue:
            log(f"\n--- 队列摘要 ---")
            reasons = {}
            for q in queue:
                reasons[q["reason"]] = reasons.get(q["reason"], 0) + 1
            for reason, count in reasons.items():
                log(f"  {reason}: {count} 条")
        return

    # 输出汇总
    log(f"\n{'='*60}")
    log(f"  差异报告汇总")
    log(f"{'='*60}")

    total_new = sum(r["summary"]["new"] for r in all_reports)
    total_updates = sum(r["summary"]["real_updates"] for r in all_reports)
    total_false = sum(r["summary"]["false_updates"] for r in all_reports)

    log(f"  新增页面: {total_new}")
    log(f"  实质更新: {total_updates}")
    log(f"  假更新（已过滤）: {total_false}")

    safe_write(DIFF_REPORT_FILE, all_reports)
    log(f"\n完整报告已保存: {DIFF_REPORT_FILE}")

    if total_updates > 0:
        log(f"\n--- 实质更新详情 ---")
        for r in all_reports:
            for upd in r["real_updates"]:
                log(f"  [{r['category']}] {upd['title']} ({upd['update_type']})")

    if total_new > 0:
        log(f"\n--- 新增页面（前 20 条）---")
        count = 0
        for r in all_reports:
            for np in r["new_pages"]:
                log(f"  [{r['category']}] {np['title']}")
                count += 1
                if count >= 20:
                    break
            if count >= 20:
                break


if __name__ == "__main__":
    main()
