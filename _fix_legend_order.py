# -*- coding: utf-8 -*-
"""修复 quests.py 中传说任务子任务顺序"""
import sys
import os
import shutil
from collections import OrderedDict

BASE_DIR = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用'
KB_DIR = os.path.join(BASE_DIR, 'genshin_knowledge_base')
QUEST_FILE = os.path.join(KB_DIR, 'quests.py')

sys.path.insert(0, KB_DIR)
from quests import 任务知识库

# wiki 正确顺序映射：系列任务全名 -> 正确顺序的任务名称列表
# 系列任务全名格式: "章名,幕名"（与 DB 中完全一致）
WIKI_ORDER_MAP = {
    "丝切铗之章,当他们谈起今夜": ["缄默与风雅", "织物中的锋芒", "黑与白的交织"],
    "香氛瓶之章,花债血偿": ["如梦如真的香氛", "危险的轨迹", "逝去之人的回忆"],
    "引蝶之章,奈何蝶飞去": ["往生不可追", "秘境来客", "蝶飞何处", "寻蝶遇险", "无妄之渊", "黄泉之路", "奈何蝶飞去"],
    "貘枕之章,食梦者的忧郁": ["捕梦者", "幕间物语", "眠之庭"],
    "白垩之章,旅行者观察报告": ["实验对象一：旅行者", "实验对象二：可莉", "实验对象三：阿贝多"],
    "赤龙之章,名为故事的魔法": ["故事的起点", "被祝福的旅途", "名为故事的魔法"],
    "磷星之章,星与夜的低语": ["星之泪", "心相", "无垠"],
    "夜枭之章,暗夜英雄的不在场证明": ["暗夜英雄的传说", "暗夜英雄的不在场证明", "暗夜英雄的危机"],
    "四叶草之章,真正的宝物": ["四叶草的低语", "四叶草的祝福", "真正的宝物"],
    "迅捷剑之章,夜色无声": ["夜晚的追猎", "月下的审判", "夜色无声"],
    "孔雀羽之章,海盗秘宝": ["海盗秘宝", "孔雀羽之章"],
    "天隼之章,乌合的虚像": ["乌合的虚像", "沙海的追寻", "学者的归来"],
    "野蔷薇之章,共渡潮落": ["往日的繁星", "水中的暗涌", "共渡潮落"],
    "仙狐之章,鸣神御祓祈愿祭": ["轻小说之乱", "新的篇章", "愿此刻永恒"],
    "小狼之章,卢皮卡的意义": ["随风而来的旅行者", "森林中的第一课", "朋友的意义", "卢皮卡的意义"],
    "黑斑猫之章,被遗忘的怪盗": ["被遗忘的怪盗", "幕后的魔术师", "真实的谎言", "最后的魔术"],
    "海精之章,谎言的温度": ["谎言的温度", "小小的奇迹", "海精之章"],
    "净炼火之章,炉火熄灭之际": ["炉火熄灭之际", "余烬之中", "新王的诞生"],
    "琉金之章,如梦如电的隽永": ["琉金之章", "花火之约", "如梦如电的隽永"],
    "琉金之章,拾星之旅": ["拾星之旅", "流星坠落之处"],
    "蝎尾鬃狮之章,「狮之血」": ["黑染红绸", "赤沙旧事", "伪貌真心"],
    "锦织之章,江湖不问出处": ["寻书巧遇江湖事", "知己知彼定良策", "山雨欲来风满楼", "淡泊名利侠客行"],
    "睡莲之章,致智慧者": ["街头巷尾的温暖韵律", "当剧目不再上演", "团聚于此的意义"],
    "天狼之章,致予远征之人": ["凯旋的远征者", "命运的远征者", "孤独的远征者"],
    "小兔之章,风、勇气和翅膀": ["风之翼随风而起", "飞行的许可", "蒙德城的飞行者", "那家伙叫「怪鸟」", "侦察骑士的作风"],
    "潮涌之章,往日留痕": ["似曾相识的威胁", "翻涌不歇的回忆", "旧事新解"],
    "天牛之章,赤金魂": ["鬼之恶", "鬼之义", "鬼之傲"],
    "智慧主之章,余温": ["复苏之梦", "坠落之梦", "告别之梦"],
    "闲鹤之章,千里月明": ["闲话家常", "寻秘四方", "酣然一梦"],
    "不败阳焰之章,正如那烈日": ["和平之后", "全新的巡礼", "聆听，归来者"],
    "雪鹤之章,鹤与白兔的诉说": ["私人委托", "丝织之愿", "他乡之食", "恒久之约", "愿与君同"],
    "郭狐之章,没有答案的课题": ["污染之始", "深入腹地", "机械之心", "花园回忆"],
    "眠龙之章,兵戈梦去，春草如茵": ["海祇见闻", "流言疑云", "她的秘密", "庆功晚宴", "新的开始"],
    "神守柏之章,梧桐一叶落": ["旧枝新芽", "真实亦是谎言", "棋定风寂"],
    "仙麟之章,云之海，人之海": ["云之海的仙兽", "人之海的秘书", "跨越时间的托付"],
    "长杓之章,蒙德食遇之旅": ["狩猎中的小厨师", "料理之约", "一锤定音的食材", "意外的收获", "远古的馈赠", "料理对决"],
    "枫红之章,陌野不识故人": ["平野现紫烟", "乱象识真面", "穷途望归路"],
    "幽客之章,棋生断处": ["七星选拔", "知人知面", "落子定局"],
    "歌仙之章,若你困于无风之地": ["童心的隐秘", "南风与冒险", "狮牙之牙", "若你困于无风之地"],
    "映天之章,在此世的星空之外": ["占星术与五十年之约", "向蒙德进发", "扑朔迷离的命运"],
    "幼狮之章,骑士团长的一日假期": ["西风吹拂的日常", "委托人玛格丽特的思念", "委托人查尔斯的烦恼", "委托人莎拉的忧愁", "骑士团长的一日假期"],
    "狡兔之章,骑士之铭": ["骑士之勋绩", "骑士之追缉", "骑士之决意"],
    "悬壶之章,「医心」": ["寻医", "问药", "求方"],
    "鲸天之章,独眼小宝总动员": ["远渡重洋的邂逅", "托克的璃月见闻录", "童话里的守梦人"],
    "守狱犬之章,于怨嗟之地重生": ["重生的契机", "以「檐帽会」为傲", "罪有应得"],
    "香糕塔之章,珍上至珍": ["以声名塑形", "以炽焰炙烤", "以冰霜粉饰"],
}


def main():
    # 1. 分组所有传说任务条目（按系列任务全名）
    series_groups = OrderedDict()
    for i, q in enumerate(任务知识库):
        if q.get('任务类型') != '传说任务':
            continue
        s = q.get('系列任务', '')
        if not s:
            continue
        if s not in series_groups:
            series_groups[s] = []
        series_groups[s].append((i, q))

    # 2. 对于每个需要修复的系列，在原列表中重排条目
    fixed_count = 0
    skipped = 0

    for series_name, wiki_order in WIKI_ORDER_MAP.items():
        if series_name not in series_groups:
            print(f"[跳过] 未在 DB 中找到系列: {series_name}")
            skipped += 1
            continue

        entries = series_groups[series_name]  # [(index, entry), ...]
        current_names = [e[1].get('任务名称', '') for e in entries]

        if current_names == wiki_order:
            print(f"[正确] {series_name}: 已经是正确顺序")
            continue

        if sorted(current_names) != sorted(wiki_order):
            print(f"[警告] {series_name}: 任务名称集合不匹配!")
            print(f"  Wiki: {sorted(wiki_order)}")
            print(f"  DB:   {sorted(current_names)}")
            # 显示差异
            wiki_set = set(wiki_order)
            db_set = set(current_names)
            only_wiki = wiki_set - db_set
            only_db = db_set - wiki_set
            if only_wiki:
                print(f"  仅在 Wiki: {only_wiki}")
            if only_db:
                print(f"  仅在 DB:   {only_db}")
            skipped += 1
            continue

        # 建立任务名称 -> 条目信息的映射
        name_to_entry = {}
        for idx, entry in entries:
            name_to_entry[entry.get('任务名称', '')] = (idx, entry)

        # 按 wiki 顺序重排
        new_ordered = []
        for name in wiki_order:
            new_ordered.append(name_to_entry[name])

        # 在原列表中替换这些条目
        indices = sorted([e[0] for e in entries])
        for pos, (_, entry) in zip(indices, new_ordered):
            任务知识库[pos] = entry

        role = new_ordered[0][1].get('所属角色', '')
        print(f"[修复] {series_name} [{role}]")
        print(f"  旧: {' -> '.join(current_names)}")
        print(f"  新: {' -> '.join(wiki_order)}")
        fixed_count += 1

    print(f"\n共修复: {fixed_count} 个系列, 跳过: {skipped} 个")

    # 3. 写回文件
    bak_file = QUEST_FILE + '.bak2'
    if not os.path.exists(bak_file):
        shutil.copy2(QUEST_FILE, bak_file)
        print(f"[备份] {bak_file}")

    lines = ['任务知识库 = [']
    for i, entry in enumerate(任务知识库):
        if i > 0:
            lines.append('    {')
        else:
            lines.append('    {')

        # 按字段顺序输出：确保与原始格式一致
        field_order = ['任务名称', '任务类型', '简介', '关联角色', '系列任务', '所属角色', '是否伪传说任务']

        output_fields = []
        for field in field_order:
            if field in entry:
                output_fields.append((field, entry[field]))

        # 额外字段
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

    print(f"[完成] 已写入 {QUEST_FILE} ({len(任务知识库)} 条)")
    print(f"文件大小: {os.path.getsize(QUEST_FILE)} 字节")


if __name__ == '__main__':
    main()
