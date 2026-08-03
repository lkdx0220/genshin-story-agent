# -*- coding: utf-8 -*-
"""统一修复 quests.py 中所有任务类型的子任务顺序"""
import sys
import os
import shutil
import re
from collections import OrderedDict

BASE_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用'
KB_DIR = os.path.join(BASE_DIR, 'genshin_knowledge_base')
QUEST_FILE = os.path.join(KB_DIR, 'quests.py')

sys.path.insert(0, KB_DIR)
from quests import 任务知识库

def normalize_name(name):
    """标准化任务名：去除括号注释如（任务）"""
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()

# ====== 所有需要修复的 wiki 正确顺序 ======
# 格式: (系列任务全名, 正确的任务名顺序列表)

FIX_ORDERS = {
    # === 魔神任务 (33个) ===
    "第五章,你存在的时空": ["命运始动的钥匙", "「救世主」", "你所不在的时空"],
    "序章,为了没有眼泪的明天": ["阴影下的蒙德", "不期而遇", "那个绿色的家伙", "听凭风引", "温迪的计划", "温迪的新计划", "逃亡", "幕后谈话", "追逐暗影", "至宝的现状", "遗落之泪", "藏匿之泪", "被夺之泪", "澄净之泪", "与巨龙重逢"],
    "第二章,回响渊底的安魂曲": ["渊底不期的再会", "被守护者的灵柩", "因提瓦特的记忆", "黑蛇骑士的荣光"],
    "第四章,睡前故事": ["积存已久的委托", "不应存在的记忆", "以世界之格的诉说"],
    "空月之歌,尘与灯的挽歌": ["轰鸣与暗涌", "窥见记忆的暗面", "曾有人追猎月亮", "灰白的秩序熊熊燃烧"],
    "第三章,卡利贝尔": ["如命运般的相逢", "嘲弄命运的资格", "命运尽头的垂泪者", "既已写下的命运"],
    "第一章,辞行久远之躯": ["往生", "指月", "传香", "壶天", "市井", "归终", "邀约"],
    "间章,风起鹤归": ["琼台玉阁", "鸣海栖霞", "往事如尘", "此心安处"],
    "仿若无因飘落的轻雨": ["如同昔日的微茫月明", "真相流逝于雨后", "当一切回归于水"],
    "序章,巨龙与自由之歌": ["深渊法师", "障壁", "无人之家", "导光仪式", "在终曲之前", "为了青色的身影", "尾声，风停之后", "尾声的尾声"],
    "间章,悖理": ["第一及第二罪行", "众生的渴求", "园丁"],
    "第一章,浮世浮生千岩间": ["请仙", "惊变", "望舒", "叠山", "留云", "返尘"],
    "第二章,千手百眼，天下人间": ["剑与鱼与反抗者", "渴求神明注视之人", "邪眼", "眷属的践行", "定罪公文", "愚忠与愚勇", "御前决斗", "千手百眼", "愿望"],
    "第一章,迫近的客星": ["浮城", "玑衡", "孤芳", "离心", "回天", "送仙"],
    "第三章,穿越烟帷与暗林": ["林中遇变", "疗养观察", "痼疾", "缄默的求知者", "智慧之神的踪影", "失物匿于繁华", "近在咫尺的目标"],
    "第五章,黑石湮落白石下": ["抉择", "古名寻回之旅", "生命的回响", "坠入永夜", "过去与未来"],
    "第五章,荣花与炎日之途": ["纳塔！新的旅程", "归火圣夜巡礼", "温泉之乡"],
    "第四章,罪人舞步旋": ["相见亦是离别", "狩猎者，预见者", "审判日", "黑潮与白露的歌剧", "终幕礼"],
    "空月之歌,身土坏空，五蕴识转": ["花于何处醒来", "通向自我的歧途", "旧影重现"],
    "第二章,不动鸣神，恒常乐土": ["起航之日", "异乡人的忏悔录", "离岛逃离计划", "三个心愿", "无意义的等待的意义", "愿仁义之人被仁义以待", "剑道家的道路铺满剑道梦的碎片", "于狱中绽放之花"],
    "空月之歌,回望湮灭的月光": ["皆为预言", "蛇与蝎的亡命舞", "命运的回声"],
    "第三章,千朵玫瑰带来的黎明": ["终将到来的花神诞祭", "已然来临的花神诞祭", "流转存续的花神诞祭", "轮回意志的花神诞祭", "因果命运的花神诞祭", "空幻回响的花神诞祭", "终将结束的花神诞祭", "黎明"],
    "空月之歌,真实之月": ["最初的那抹月光", "月之将坠", "空月归乡"],
    "第三章,迷梦与空幻与欺骗": ["如凯旋的英雄一般", "来自某位「神明」的凝视"],
    "序章,捕风的异乡人": ["鸟瞰风物", "异常的权柄", "林间相会", "随风而来的骑士", "与轻风同行", "自由之都", "龙灾", "西风骑士团", "昔日的风", "骑士的现场教习", "书页里的电火花"],
    "间章,倾落伽蓝": ["夜中飞鸟坠于三段", "乱世轮舞", "幕切——倾奇之末", "如朝露一般"],
    "空月之歌,如果在冬夜，一个旅人": ["无月之夜", "你我交错的时空", "循着过往的足迹"],
    "空月之歌,归途": ["墟火", "阴燃", "逆焰"],
    "空月之歌,不存在的国土": ["匿于阴影", "特别行动", "如月长存"],
    "谕示胎动的终焉之刻": ["探入水底迷雾", "真相隐于影中", "守秘者与禁区", "灾厄的脚步", "片刻安宁"],
    "第三章,虚空鼓动，劫火高扬": ["行于黎明前夜幕里", "如临神之畔", "识藏日", "意识之舟所至之处", "请饮下祝胜之酒"],
    "第二章,无念无想，泡影断灭": ["在审判的雷鸣声中", "以反抗之人的名义"],
    "空月之歌,雪浪与苍林之舞": ["月亮升起的地方", "于月光下重逢", "在夜的阴影中"],

    # === 活动剧情 (2个) ===
    "奔霄颂玉轮": ["在岩间", "在人间", "白马闲游记"],
    "春曦画桃符": ["璃月港佳节兴，八奇现瘴疠隐", "往生堂三日无主，玉京台遣将调兵", "奇门术息灾平昏寿，护摩法净世定幽冥", "终回：八奇炼桃都"],

    # === 部族纪闻 (1个) ===
    "流淌着色彩的回忆,七彩之战的真相": ["追到纳塔的委托", "拜托了，大萨满女士", "七彩之遗", "记忆的颜色"],

    # === 世界任务 (4个) ===
    "夜莺之歌,多鲁德的长诗": ["古老的阵眼·其一", "古老的阵眼·其二", "古老的阵眼·其三"],
    "许伯利翁哀歌（系列任务）": ["狭间的叩问", "蛇心的叩问", "庙前的叩问"],
    "胜过白银与精金": ["无声剧场", "遗失宫殿", "忘忧灯塔"],
    "荒落之城的记述人": ["荒弃的智海", "祭验封绝之所", "昔时演算阵列之处", "昔时逆转力向之处"],
}


def main():
    # 1. 分组所有需要修复的系列条目
    series_groups = OrderedDict()
    for i, q in enumerate(任务知识库):
        s = q.get('系列任务', '')
        if not s:
            continue
        if s not in series_groups:
            series_groups[s] = []
        series_groups[s].append((i, q))

    # 2. 修复
    fixed_count = 0
    skipped_count = 0
    ok_count = 0

    for series_name, wiki_order in FIX_ORDERS.items():
        if series_name not in series_groups:
            print(f"[跳过] 未找到系列: {series_name}")
            skipped_count += 1
            continue

        entries = series_groups[series_name]
        current_names = [e[1].get('任务名称', '') for e in entries]
        
        # 标准化对比
        current_norm = [normalize_name(n) for n in current_names]
        wiki_norm = [normalize_name(n) for n in wiki_order]

        if current_norm == wiki_norm:
            ok_count += 1
            continue

        if sorted(current_norm) != sorted(wiki_norm):
            print(f"[警告] {series_name}: 任务名集合不匹配")
            print(f"  Wiki: {sorted(wiki_norm)}")
            print(f"  DB:   {sorted(current_norm)}")
            skipped_count += 1
            continue

        # 建立映射
        name_map = {}
        for idx, entry in entries:
            name_map[normalize_name(entry.get('任务名称', ''))] = (idx, entry)

        # 按 wiki 顺序重排
        new_ordered = []
        all_found = True
        for name in wiki_order:
            nn = normalize_name(name)
            if nn in name_map:
                new_ordered.append(name_map[nn])
            else:
                print(f"[警告] {series_name}: 找不到 '{name}' (norm: {nn})")
                all_found = False
                break
        
        if not all_found:
            skipped_count += 1
            continue

        # 在原列表中替换
        indices = sorted([e[0] for e in entries])
        for pos, (_, entry) in zip(indices, new_ordered):
            任务知识库[pos] = entry

        task_type = new_ordered[0][1].get('任务类型', '')
        print(f"[修复] [{task_type}] {series_name}")
        print(f"  旧: {' -> '.join(current_names)}")
        print(f"  新: {' -> '.join(wiki_order)}")
        fixed_count += 1

    print(f"\n===== 修复完成: {fixed_count} 个, 跳过: {skipped_count} 个, 已正确: {ok_count} 个 =====")

    # 3. 写回文件
    bak_file = QUEST_FILE + '.bak3'
    if not os.path.exists(bak_file):
        shutil.copy2(QUEST_FILE, bak_file)
        print(f"[备份] {bak_file}")

    lines = ['任务知识库 = [']
    for i, entry in enumerate(任务知识库):
        if i > 0:
            lines.append('    {')
        else:
            lines.append('    {')

        field_order = ['任务名称', '任务类型', '简介', '关联角色', '系列任务', '所属角色', '是否伪传说任务']
        output_fields = []
        for field in field_order:
            if field in entry:
                output_fields.append((field, entry[field]))
        seen = set(field_order)
        for field, val in entry.items():
            if field not in seen:
                output_fields.append((field, val))
                seen.add(field)

        for j, (field, val) in enumerate(output_fields):
            is_last = (j == len(output_fields) - 1)
            comma = '' if is_last else ','
            if isinstance(val, str):
                escaped = val.replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'        "{field}": "{escaped}"{comma}')
            else:
                lines.append(f'        "{field}": {repr(val)}{comma}')

        if i < len(任务知识库) - 1:
            lines.append('    },')
        else:
            lines.append('    }')

    lines.append(']')
    content = '\n'.join(lines) + '\n'

    with open(QUEST_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[完成] 已写入 {QUEST_FILE} ({len(任务知识库)} 条, {os.path.getsize(QUEST_FILE)} 字节)")


if __name__ == '__main__':
    main()
