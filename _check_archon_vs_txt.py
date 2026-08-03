# -*- coding: utf-8 -*-
"""用 原神魔神任务架构.txt 权威数据，全量对比 quests.py 中子任务顺序"""
import sys
import re
from collections import OrderedDict

sys.path.insert(0, r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\genshin_knowledge_base')
from quests import 任务知识库

def parse_txt(filepath):
    """解析架构文件，返回 {幕名: [(子任务名列表), 章节名]}"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_chapter = None
    current_act = None
    current_tasks = []
    result = OrderedDict()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 章节：如 "序章 捕风的异乡人" 或 "第一章 辞行久远之躯" 或 "空月之歌"
        chap_match = re.match(r'^([第序空间].*?章|空月之歌|间章)$', line)
        if chap_match:
            if current_act and current_tasks:
                result[current_act] = (current_tasks, current_chapter)
            current_chapter = line
            current_act = None
            current_tasks = []
            continue

        # 幕：如 "第一幕：捕风的异乡人" 或 "序奏：归途" 或 "幕间：万火归一"
        act_match = re.match(r'^(第[一二三四五六七八九十]+幕|序[奏幕]|[幕间]+)：(.+)$', line)
        if act_match:
            if current_act and current_tasks:
                result[current_act] = (current_tasks, current_chapter)
            current_act = act_match.group(2).strip()
            current_tasks = []
            continue

        # 子任务：如 "1 鸟瞰风物"
        task_match = re.match(r'^(\d+)\s+(.+)$', line)
        if task_match:
            current_tasks.append(task_match.group(2).strip())
            continue

        # 开场动画等跳过
        if '开场' in line or '流浪者的足迹' in line:
            continue

    # 最后一条
    if current_act and current_tasks:
        result[current_act] = (current_tasks, current_chapter)

    return result


def normalize_name(name):
    """标准化任务名"""
    return re.sub(r'[（(][^）)]*[）)]', '', name).strip()


def main():
    txt_path = r'c:\Users\24701\Desktop\原神剧情\原神魔神任务架构.txt'

    # 解析权威架构
    authority = parse_txt(txt_path)
    print(f"权威数据: {len(authority)} 个幕\n")

    # 分组 DB 魔神任务
    db_by_series = OrderedDict()
    for q in 任务知识库:
        if q.get('任务类型') != '魔神任务':
            continue
        s = q.get('系列任务', '')
        if not s:
            continue
        if s not in db_by_series:
            db_by_series[s] = []
        db_by_series[s].append(q.get('任务名称', ''))

    multi_db = {k: v for k, v in db_by_series.items() if len(v) > 1}
    print(f"DB 多子任务系列: {len(multi_db)} 个\n")

    correct = []
    wrong = []
    missing_in_authority = []
    missing_in_db = []

    # 对比
    for series_name, db_tasks in multi_db.items():
        # 解析系列名
        if ',' in series_name:
            parts = series_name.split(',', 1)
            chapter_prefix = parts[0].strip()
            act_name = parts[1].strip()
        else:
            chapter_prefix = ''
            act_name = series_name.strip()

        # 在权威数据中查找这个幕
        auth_name = None
        if act_name in authority:
            auth_name = act_name
        else:
            # 尝试模糊匹配
            for a_name in authority:
                n_auth = normalize_name(a_name)
                n_act = normalize_name(act_name)
                if n_auth == n_act:
                    auth_name = a_name
                    break
                # 部分匹配
                if n_auth in n_act or n_act in n_auth:
                    auth_name = a_name
                    break

        if auth_name is None:
            missing_in_authority.append((series_name, db_tasks))
            continue

        auth_tasks, auth_chapter = authority[auth_name]

        # 标准化对比
        db_norm = [normalize_name(t) for t in db_tasks]
        auth_norm = [normalize_name(t) for t in auth_tasks]

        if db_norm == auth_norm:
            correct.append(series_name)
            continue

        # 检查是否只是顺序不同（集合相同）
        if sorted(db_norm) != sorted(auth_norm):
            print(f"[内容不匹配] {series_name}")
            only_db = set(db_norm) - set(auth_norm)
            only_auth = set(auth_norm) - set(db_norm)
            if only_db:
                print(f"  仅DB: {only_db}")
            if only_auth:
                print(f"  仅权威: {only_auth}")
            missing_in_db.append((series_name, db_tasks, auth_tasks))
            continue

        # 顺序不同
        wrong.append((series_name, db_tasks, auth_tasks))

    # 检查权威数据中有但DB中没有的幕
    for act_name, (tasks, chapter) in authority.items():
        found = False
        for sn in multi_db:
            if act_name in sn or normalize_name(act_name) == normalize_name(sn.split(',')[-1].strip() if ',' in sn else sn):
                found = True
                break
        for sn, _ in missing_in_authority:
            break  # already accounted
        # not tracking here since it's complex

    # 输出
    print(f"\n{'='*60}")
    print(f"✅ 正确: {len(correct)}")
    print(f"❌ 顺序错误: {len(wrong)}")
    print(f"⚠️ 内容不匹配: {len(missing_in_db)}")
    print(f"⚠️ 权威中有但DB未找到: {len(missing_in_authority)}")

    if correct:
        print(f"\n--- 正确 ({len(correct)}) ---")
        for s in correct:
            print(f"  {s}")

    if wrong:
        print(f"\n--- 顺序错误 ({len(wrong)}) ---")
        for s, db, auth in wrong:
            print(f"\n  {s}")
            print(f"    权威: {' → '.join(auth)}")
            print(f"    DB:   {' → '.join(db)}")

    if missing_in_db:
        print(f"\n--- 内容不匹配 ({len(missing_in_db)}) ---")
        for s, db, auth in missing_in_db:
            print(f"  {s}: DB={db}, 权威={auth}")

    if missing_in_authority:
        print(f"\n--- DB有但权威数据中未找到 ({len(missing_in_authority)}) ---")
        for s, db in missing_in_authority:
            print(f"  {s}: {db}")

    # 保存错误数据
    out_path = r'C:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\_archon_from_txt.py'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('# 用 原神魔神任务架构.txt 对照结果\n')
        f.write('WRONG = [\n')
        for s, db, auth in wrong:
            f.write(f'    ("{s}", {repr(auth)}),\n')
        f.write(']\n\n')
        f.write('MISMATCH = [\n')
        for s, db, auth in missing_in_db:
            f.write(f'    ("{s}", {repr(db)}, {repr(auth)}),\n')
        f.write(']\n')
    print(f"\n已保存到 {out_path}")


if __name__ == '__main__':
    main()
