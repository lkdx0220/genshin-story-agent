#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神 Wiki 知识库构建脚本

工作流程:
  1. 从 Bilibili Wiki API 获取角色/武器/任务等分类下的所有页面
  2. 从 wikitext 中提取 {{模板}} 结构化数据
  3. 生成 genshin_knowledge_base.py

用法:
  python build_knowledge_base.py              # 构建全部
  python build_knowledge_base.py --characters # 仅构建角色
  python build_knowledge_base.py --limit 10   # 限制数量（测试用）
"""

import os, sys, re, json, time, argparse
from typing import Dict, List, Optional, Any
import requests

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

API = "https://wiki.biligame.com/ys/api.php"
OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genshin_knowledge_base", "roles.py")

# 速率控制
REQUEST_DELAY = 0.5
MAX_RETRIES = 3

# 创建带有正确 headers 的 session
_api_session = None

def _get_session():
    global _api_session
    if _api_session is None:
        _api_session = requests.Session()
        _api_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://wiki.biligame.com/ys/",
        })
    return _api_session


# ========== API 工具 ==========

def api_get(params: dict, retries: int = MAX_RETRIES) -> Optional[dict]:
    """调用 MediaWiki API，带重试"""
    session = _get_session()
    for attempt in range(retries):
        try:
            resp = session.get(API, params=params, timeout=30)
            if resp.status_code == 200 and resp.text.strip():
                return resp.json()
        except Exception as e:
            print(f"  [API] 第{attempt+1}次失败: {e}")
        time.sleep(2 ** attempt)
    return None


def get_category_pages(category: str, limit: Optional[int] = None) -> List[Dict]:
    """获取分类下所有页面列表，返回 [{pageid, title, ns}, ...]"""
    pages = []
    cmcontinue = None
    print(f"[分类] 获取 '{category}' 页面列表...")

    while True:
        params = {
            "action": "query", "list": "categorymembers",
            "cmtitle": f"Category:{category}", "cmlimit": 200, "format": "json"
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue

        data = api_get(params)
        if not data:
            print(f"  [错误] 无法获取分类 '{category}'")
            break

        pages.extend(data.get("query", {}).get("categorymembers", []))
        print(f"  已获取 {len(pages)} 页...", end="\r")

        if limit and len(pages) >= limit:
            pages = pages[:limit]
            break

        if "continue" in data and data["continue"].get("cmcontinue"):
            cmcontinue = data["continue"]["cmcontinue"]
            time.sleep(REQUEST_DELAY)
        else:
            break

    print(f"\n  完成: {len(pages)} 页")
    return pages


def get_page_wikitext(title: str) -> Optional[str]:
    """获取页面 wikitext 源码"""
    params = {
        "action": "query", "titles": title,
        "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "format": "json"
    }
    data = api_get(params)
    if not data:
        return None
    for pid, pinfo in data.get("query", {}).get("pages", {}).items():
        revs = pinfo.get("revisions", [])
        if revs:
            return revs[0].get("slots", {}).get("main", {}).get("*", "")
    return None


# ========== 数据清洗 ==========

def clean_value(v: str) -> str:
    """清洗模板值：去除HTML注释、多余空白"""
    v = re.sub(r'<!--.*?-->', '', v)  # 去除HTML注释
    v = v.strip()
    return v

def extract_star(star_str: str) -> int:
    """从稀有度字符串提取星级数字"""
    star_str = clean_value(star_str)
    if star_str == "未知" or not star_str:
        return 0
    m = re.search(r'(\d+)', star_str)
    return int(m.group(1)) if m else 0


# ========== 模板解析 ==========

def parse_template(content: str, template_name: str) -> Optional[Dict[str, str]]:
    """从 wikitext 中解析命名模板 {{template_name|key1=val1|...}}
    返回 {key: value} 字典。处理多行值。"""
    if not content:
        return None

    # 找到 {{template_name 开头，匹配到对应的 }}
    # 使用平衡括号匹配
    pattern = r'\{\{' + re.escape(template_name) + r'\s*\n(.*?)\n\s*\}\}'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        # 备选: 单行模板 {{template_name|...}}
        pattern2 = r'\{\{' + re.escape(template_name) + r'([^}]*)\}\}'
        match = re.search(pattern2, content, re.DOTALL)

    if not match:
        return None

    inner = match.group(1)
    result = {}

    # 按 |key=value 分割，处理多行值
    # 先找所有的 |key= 位置
    lines = inner.strip().split('\n')
    current_key = None
    current_value = []

    for line in lines:
        line = line.strip()
        if not line or line == '}}':
            continue
        # 检查是否是新键值对开头 |key=value
        m = re.match(r'^\|([^=]+)=(.*)', line)
        if m:
            # 保存上一个键值对
            if current_key:
                result[current_key.strip()] = '\n'.join(current_value).strip()
            current_key = m.group(1).strip()
            current_value = [m.group(2)]
        else:
            # 续行
            if current_key:
                current_value.append(line)

    # 最后一个键值对
    if current_key:
        result[current_key.strip()] = '\n'.join(current_value).strip()

    return result


def extract_text_section(content: str, heading: str) -> Optional[str]:
    """提取 == heading == 下的纯文本段落"""
    pattern = r'==\s*' + re.escape(heading) + r'\s*==\s*\n(.*?)(?:\n==|$)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        text = match.group(1).strip()
        # 去除 wiki 标记
        text = re.sub(r"'''(.+?)'''", r'\1', text)  # 粗体
        text = re.sub(r"''(.+?)''", r'\1', text)    # 斜体
        text = re.sub(r'\[\[([^\]|]+)\]\]', r'\1', text)  # 链接
        text = re.sub(r'\[\[[^\]|]+\|([^\]]+)\]\]', r'\1', text)  # 命名链接
        text = re.sub(r'<[^>]+>', '', text)  # HTML标签
        text = re.sub(r'\{\{[^}]+\}\}', '', text)  # 内联模板
        return text.strip()
    return None


# ========== 角色解析 ==========

def parse_character(page_title: str, wikitext: str) -> Optional[Dict]:
    """解析角色页面"""
    tmpl = parse_template(wikitext, "角色")
    if not tmpl:
        return None

    name = tmpl.get("名称", page_title)
    if not name:
        return None

    element = clean_value(tmpl.get("元素属性", ""))
    weapon = clean_value(tmpl.get("武器类型", ""))
    region = clean_value(tmpl.get("所属", ""))
    constellation = clean_value(tmpl.get("命之座", ""))
    star = extract_star(tmpl.get("稀有度", ""))
    title_str = clean_value(tmpl.get("称号", ""))
    intro = clean_value(tmpl.get("介绍", ""))
    gender = clean_value(tmpl.get("性别", ""))
    birthday = clean_value(tmpl.get("生日", ""))
    if not birthday:
        # 生日字段在 {{角色/信息}} 模板中，而非 {{角色}} 顶部 infobox
        info_tmpl = parse_template(wikitext, "角色/信息")
        if info_tmpl:
            birthday = clean_value(info_tmpl.get("生日", ""))
    tags = clean_value(tmpl.get("TAG", ""))
    release_ver = clean_value(tmpl.get("实装版本", ""))
    en_name = clean_value(tmpl.get("英文名称", ""))

    # 获取角色故事 - Wiki 模板名是 "角色/故事" 不是 "角色故事"
    story_tmpl = parse_template(wikitext, "角色/故事")
    story_text = ""
    stories = {}
    if story_tmpl:
        # 提取所有故事字段
        for field in ["角色详细", "角色故事1", "角色故事2", "角色故事3", "角色故事4", "角色故事5", "神之眼"]:
            val = clean_value(story_tmpl.get(field, ""))
            if val:
                val = re.sub(r'<[^>]+>', '', val).strip()[:1500]
                stories[field] = val
        story_text = stories.get("角色详细", "") or stories.get("角色故事1", "")

    if not intro and story_text:
        intro = story_text[:300]
    if not intro:
        intro = f"{name}，{title_str}。{element}元素{weapon}角色，所属{region}。" if element else ""

    return {
        "角色ID": f"CHR_{name}",
        "角色名称": name,
        "称号": title_str,
        "英文名": en_name,
        "神之眼": element,
        "武器类型": weapon,
        "所属": region,
        "命之座": constellation,
        "稀有度": star,
        "性别": gender,
        "生日": birthday,
        "TAG": [t.strip() for t in tags.split("、") if t.strip()] if tags else [],
        "实装版本": release_ver,
        "简介": intro[:300] if intro else "",
        "角色故事": stories if stories else {},
    }


# ========== 武器解析 ==========

def parse_weapon(page_title: str, wikitext: str) -> Optional[Dict]:
    """解析武器页面 —— 完整提取：故事、数值、技能"""
    tmpl = parse_template(wikitext, "武器图鉴")
    if not tmpl:
        tmpl = parse_template(wikitext, "武器")

    if not tmpl:
        return None

    name = tmpl.get("名称", page_title)
    if not name:
        return None

    weapon_type = clean_value(tmpl.get("类型", "") or tmpl.get("武器类型", ""))
    star = extract_star(tmpl.get("稀有度", ""))
    intro = clean_value(tmpl.get("介绍", ""))
    skill_name = clean_value(tmpl.get("技能名称", ""))
    sub_stat = clean_value(tmpl.get("副属性", ""))
    tags = clean_value(tmpl.get("TAG", ""))
    release_ver = clean_value(tmpl.get("实装版本", ""))

    # 武器故事（lore 文本）
    weapon_story = clean_value(tmpl.get("故事", ""))

    # 武器数值成长
    attack_growth = {}
    for key in ["初始攻击力", "20突破前攻击力", "20突破后攻击力",
                "40突破前攻击力", "40突破后攻击力", "50突破前攻击力", "50突破后攻击力",
                "60突破前攻击力", "60突破后攻击力", "70突破前攻击力", "70突破后攻击力",
                "80突破前攻击力", "80突破后攻击力", "90突破前攻击力"]:
        val = clean_value(tmpl.get(key, ""))
        if val:
            attack_growth[key] = val

    substat_growth = {}
    for key in ["初始副属性", "20级副属性", "40级副属性", "50级副属性",
                "60级副属性", "70级副属性", "80级副属性", "90级副属性"]:
        val = clean_value(tmpl.get(key, ""))
        if val:
            substat_growth[key] = val

    # 技能完整介绍
    skill_desc = clean_value(tmpl.get("技能介绍", ""))

    # 突破材料
    ascension_mats = {}
    for key in ["突破武器材料序列", "突破高级材料序列", "突破普通材料序列"]:
        val = clean_value(tmpl.get(key, ""))
        if val:
            ascension_mats[key] = val

    return {
        "武器ID": f"WPN_{name}",
        "武器名称": name,
        "武器类型": weapon_type,
        "稀有度": star,
        "副属性": sub_stat,
        "技能名称": skill_name,
        "技能介绍": skill_desc[:300] if skill_desc else "",
        "TAG": [t.strip() for t in tags.split("、") if t.strip()] if tags else [],
        "实装版本": release_ver,
        "简介": intro[:200] if intro else "",
        "武器故事": weapon_story,
        "攻击力成长": attack_growth,
        "副属性成长": substat_growth,
        "突破材料": ascension_mats,
    }


# ========== 圣遗物解析 ==========

def parse_artifact(page_title: str, wikitext: str) -> Optional[Dict]:
    """解析圣遗物套装页面 —— 完整提取：效果、五部位故事"""
    tmpl = parse_template(wikitext, "圣遗物套装")
    if not tmpl:
        tmpl = parse_template(wikitext, "圣遗物")

    if not tmpl:
        return None

    name = tmpl.get("名称", page_title)
    if not name:
        return None

    # 套装效果（Wiki用中文名"两件套效果"/"四件套效果"）
    two_pc = tmpl.get("两件套效果", "")
    four_pc = tmpl.get("四件套效果", "")

    # 五部位故事
    piece_stories = {}
    for piece_key in ["生之花故事", "死之羽故事", "时之沙故事", "空之杯故事", "理之冠故事"]:
        story = clean_value(tmpl.get(piece_key, ""))
        if story:
            piece_stories[piece_key] = story

    # 各部位名称
    piece_names = {}
    for piece_key in ["生之花名称", "死之羽名称", "时之沙名称", "空之杯名称", "理之冠名称"]:
        val = clean_value(tmpl.get(piece_key, ""))
        if val:
            piece_names[piece_key] = val

    # 获取途径
    obtain = clean_value(tmpl.get("获取途径", "") or tmpl.get("获取方式", ""))

    return {
        "圣遗物ID": f"ART_{name}",
        "圣遗物名称": name,
        "稀有度": tmpl.get("最低稀有度", "") + "-" + tmpl.get("最高稀有度", "") if tmpl.get("最高稀有度") else tmpl.get("稀有度", ""),
        "两件套效果": clean_value(two_pc),
        "四件套效果": clean_value(four_pc),
        "部位名称": piece_names,
        "部位故事": piece_stories,
        "获取途径": obtain,
        "简介": clean_value(tmpl.get("介绍", "")),
    }


# ========== 任务解析 ==========

def parse_quest(page_title: str, wikitext: str) -> Optional[Dict]:
    """解析任务页面"""
    # 尝试多种模板名
    for tmpl_name in ["任务信息", "魔神任务信息", "传说任务信息", "世界任务信息"]:
        tmpl = parse_template(wikitext, tmpl_name)
        if tmpl:
            break
    else:
        tmpl = None

    quest_type = ""
    if "魔神任务" in wikitext[:500]:
        quest_type = "魔神任务"
    elif "传说任务" in wikitext[:500]:
        quest_type = "传说任务"
    elif "世界任务" in wikitext[:500]:
        quest_type = "世界任务"

    return {
        "任务ID": f"QST_{page_title}",
        "任务名称": page_title,
        "任务类型": quest_type or (tmpl.get("类型", "") if tmpl else ""),
        "关联角色": tmpl.get("关联角色", "") if tmpl else "",
        "所属地区": tmpl.get("所属地区", "").strip() if tmpl else "",
        "简介": tmpl.get("描述", "")[:200] if tmpl else extract_text_section(wikitext, "任务描述") or "",
    }


# ========== 地区解析 ==========

def parse_region(page_title: str, wikitext: str) -> Optional[Dict]:
    """解析地区页面 - Wiki 无结构化地区模板，用硬编码映射 + 提取正文"""
    # 七国硬编码映射
    REGION_DATA = {
        "蒙德": {"元素": "风", "神明": "巴巴托斯（温迪）", "理念": "自由"},
        "璃月": {"元素": "岩", "神明": "摩拉克斯（钟离）", "理念": "契约"},
        "稻妻": {"元素": "雷", "神明": "巴尔泽布（雷电将军）", "理念": "永恒"},
        "须弥": {"元素": "草", "神明": "布耶尔（纳西妲）", "理念": "智慧"},
        "枫丹": {"元素": "水", "神明": "芙卡洛斯（芙宁娜）", "理念": "正义"},
        "纳塔": {"元素": "火", "神明": "玛薇卡（火神）", "理念": "战争"},
        "至冬": {"元素": "冰", "神明": "（冰之女皇）", "理念": "（未公布）"},
        "坎瑞亚": {"元素": "无", "神明": "无神之国度", "理念": "——"},
    }

    data = REGION_DATA.get(page_title, {"元素": "", "神明": "", "理念": ""})

    # 从正文提取简介（前300字符去掉模板）
    intro_text = re.sub(r'\{\{[^}]+\}\}', '', wikitext) if wikitext else ""
    intro_text = re.sub(r'<[^>]+>', '', intro_text)
    intro_text = re.sub(r'\n+', ' ', intro_text)
    # 跳过编年号、面包屑等前置内容
    intro_text = re.sub(r'^=+[^=]*=+', '', intro_text).strip()
    intro = intro_text[:300]

    return {
        "地区ID": f"REG_{page_title}",
        "地区名称": page_title,
        "元素": data["元素"],
        "神明": data["神明"],
        "理念": data["理念"],
        "主要地点": [],
        "剧情章节": "",
        "简介": intro,
    }


# ========== 批量获取与解析 ==========

def fetch_and_parse(category: str, parser_func, limit: Optional[int] = None,
                    description: str = "") -> List[Dict]:
    """批量获取分类下页面并解析"""
    pages = get_category_pages(category, limit=limit)
    if not pages:
        return []

    results = []
    total = len(pages)
    desc = f"[{description or category}]" 

    for i, page in enumerate(pages):
        title = page["title"]
        print(f"  {desc} [{i+1}/{total}] {title}...", end=" ")

        wikitext = get_page_wikitext(title)
        if wikitext:
            try:
                parsed = parser_func(title, wikitext)
                if parsed:
                    results.append(parsed)
                    print(f"OK ({len(results)}条)")
                else:
                    print("跳过(无数据)")
            except Exception as e:
                print(f"解析失败: {e}")
        else:
            print("获取失败")

        time.sleep(REQUEST_DELAY)

    print(f"  {desc} 完成: {len(results)}/{total} 条有效数据")
    return results


# ========== 知识库文件生成 ==========

def generate_knowledge_base(characters: List[Dict], weapons: List[Dict],
                            artifacts: List[Dict], quests: List[Dict],
                            regions: List[Dict], stories: List[Dict],
                            output_path: str, characters_only: bool = False):
    """生成知识库文件。
    当 characters_only=True 时，只输出角色知识库列表（用于 roles.py）。"""

    # 格式化 Python 列表
    def format_list(data: List[Dict], var_name: str) -> str:
        import pprint
        lines = [f"{var_name} = ["]
        for i, item in enumerate(data):
            # 用 repr 方式输出字典
            s = json.dumps(item, ensure_ascii=False, indent=2)
            # 缩进调整
            s = '\n'.join('    ' + line for line in s.split('\n'))
            lines.append(s + ("," if i < len(data) - 1 else ""))
        lines.append("]")
        return '\n'.join(lines)

    # 角色专属模式：只输出角色知识库
    if characters_only:
        code = f'''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
角色知识库
数据来源: https://wiki.biligame.com/ys/ (Bilibili原神Wiki)
自动生成于脚本: build_knowledge_base.py
"""
# ====== 角色知识库 ({len(characters)}位) ======

{format_list(characters, "角色知识库")}
'''
    else:
        # 生成 Python 文件
        code = f'''#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原始剧情知识库
数据来源: https://wiki.biligame.com/ys/ (Bilibili原神Wiki)
自动生成于脚本: build_knowledge_base.py
"""
# ====== 角色知识库 ({len(characters)}位) ======

{format_list(characters, "角色知识库")}

# ====== 武器知识库 ({len(weapons)}把) ======

{format_list(weapons, "武器知识库")}

# ====== 圣遗物知识库 ({len(artifacts)}套) ======

{format_list(artifacts, "圣遗物知识库")}

# ====== 任务知识库 ({len(quests)}个) ======

{format_list(quests, "任务知识库")}

# ====== 地区知识库 ({len(regions)}个) ======

{format_list(regions, "地区知识库")}

# ====== 主线剧情知识库 ({len(stories)}章) ======

{format_list(stories, "主线剧情知识库")}

# ====== 素材/道具知识库 ======

素材知识库 = [
    {{
        "素材ID": "MAT001",
        "素材名称": "原石",
        "类型": "货币",
        "简介": "提瓦特大陆的通用货币，可用于祈愿获取角色与武器。",
    }},
    {{
        "素材ID": "MAT002",
        "素材名称": "摩拉",
        "类型": "货币",
        "简介": "提瓦特大陆流通的通用货币，用于角色升级、武器强化等。",
    }},
    {{
        "素材ID": "MAT003",
        "素材名称": "纠缠之缘",
        "类型": "祈愿道具",
        "简介": "用于限定角色/武器活动祈愿的消耗品。",
    }},
]
'''

    # 备份旧文件
    if os.path.exists(output_path):
        backup = output_path + ".bak"
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                old = f.read()
            with open(backup, "w", encoding="utf-8") as f:
                f.write(old)
            print(f"[备份] 旧知识库已备份到: {backup}")
        except:
            pass

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[生成] 知识库文件: {output_path}")


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description="原神 Wiki 知识库构建脚本")
    parser.add_argument("--limit", "-l", type=int, default=None, help="限制每类页面数量")
    parser.add_argument("--characters", "-c", action="store_true", help="仅构建角色")
    parser.add_argument("--weapons", "-w", action="store_true", help="仅构建武器")
    parser.add_argument("--artifacts", "-a", action="store_true", help="仅构建圣遗物")
    parser.add_argument("--quests", "-q", action="store_true", help="仅构建任务")
    parser.add_argument("--regions", "-r", action="store_true", help="仅构建地区")
    parser.add_argument("--output", "-o", type=str, default=OUTPUT, help="输出文件")
    args = parser.parse_args()

    all_mode = not any([args.characters, args.weapons, args.artifacts, args.quests, args.regions])

    print("=" * 60)
    print("  原神 Wiki 知识库构建脚本")
    print("=" * 60)

    characters, weapons, artifacts, quests, regions, stories = [], [], [], [], [], []

    if all_mode or args.characters:
        characters = fetch_and_parse("角色", parse_character, args.limit, "角色")

    if all_mode or args.weapons:
        weapons = fetch_and_parse("武器", parse_weapon, args.limit, "武器")

    if all_mode or args.artifacts:
        artifacts = fetch_and_parse("圣遗物套装", parse_artifact, args.limit, "圣遗物")

    if all_mode or args.quests:
        # 获取各类任务
        for quest_cat in ["魔神任务", "传说任务", "世界任务"]:
            quests.extend(fetch_and_parse(quest_cat, parse_quest, args.limit, quest_cat))
            if args.limit and len(quests) >= args.limit:
                quests = quests[:args.limit]
                break

    if all_mode or args.regions:
        # 地区：取固定的7个主要地区
        region_names = ["蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "至冬", "坎瑞亚", "天空岛"]
        for rn in region_names:
            if args.limit and len(regions) >= args.limit:
                break
            print(f"  [地区] 获取 '{rn}'...", end=" ")
            wikitext = get_page_wikitext(rn)
            if wikitext:
                parsed = parse_region(rn, wikitext)
                if parsed:
                    regions.append(parsed)
                    print("OK")
                else:
                    print("无模板(跳过)")
            else:
                print("无页面")
            time.sleep(REQUEST_DELAY)
        print(f"  地区完成: {len(regions)}个")

    # 剧情概要从文件中手工维护，不自动抓取
    stories = []  # 未来可从魔神任务页面解析

    # 生成
    generate_knowledge_base(characters, weapons, artifacts, quests, regions, stories, args.output,
                            characters_only=args.characters)

    print("\n" + "=" * 60)
    print(f"  构建完成!")
    print(f"  角色: {len(characters)} | 武器: {len(weapons)} | 圣遗物: {len(artifacts)}")
    print(f"  任务: {len(quests)} | 地区: {len(regions)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
