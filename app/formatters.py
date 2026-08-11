# -*- coding: utf-8 -*-
"""格式化工具函数：把知识库 dict 渲染成 LLM 友好的文本块。

被 app/tools/query.py 等工具调用，输出格式直接影响 LLM 的回答质量。
"""
from typing import Dict


def _format_role_info(role: Dict) -> str:
    """格式化角色信息。剧情数据始终显示，游戏机制数据（稀有度/武器类型）按需显示。"""
    lines = [f"\n{'='*50}"]
    lines.append(f"【{role['角色名称']}】{role.get('称号', '')}")
    lines.append(f"所属: {role.get('所属', '未知')}")
    if role.get('性别'):
        lines.append(f"性别: {role['性别']}")
    if role.get('稀有度'):
        lines.append(f"稀有度: {role['稀有度']}星")
    if role.get('武器类型'):
        lines.append(f"武器类型: {role['武器类型']}")
    if role.get('神之眼'):
        lines.append(f"神之眼: {role['神之眼']}")
    if role.get('命之座'):
        lines.append(f"命之座: {role['命之座']}")
    if role.get('实装版本'):
        lines.append(f"实装版本: {role['实装版本']}")
    if role.get('生日'):
        lines.append(f"生日: {role['生日']}")
    if role.get('身份'):
        lines.append(f"身份: {', '.join(role['身份']) if isinstance(role['身份'], list) else role['身份']}")
    if role.get('TAG'):
        tags = role['TAG'] if isinstance(role['TAG'], list) else role['TAG'].split('、')
        lines.append(f"特性: {'、'.join(tags[:6])}{'...' if len(tags) > 6 else ''}")
    lines.append(f"\n简介: {role.get('简介', '暂无')}")
    stories = role.get('角色故事', {})
    if stories:
        lines.append(f"\n角色故事（共{len(stories)}段）:")
        for sk, sv in stories.items():
            preview = sv[:200].replace('\n', ' ')
            lines.append(f"  [{sk}]: {preview}...")
    if role.get('相关剧情'):
        lines.append(f"\n相关剧情:")
        for p in role['相关剧情']:
            lines.append(f"  · {p}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_npc_info(name: str, npc: Dict) -> str:
    """格式化 NPC 信息"""
    lines = [f"\n{'='*50}"]
    lines.append(f"【{name}】（NPC）")
    if npc.get('occupation'):
        lines.append(f"职业: {npc['occupation']}")
    if npc.get('sex'):
        lines.append(f"性别: {npc['sex']}")
    if npc.get('org'):
        lines.append(f"所属组织: {npc['org']}")
    if npc.get('race'):
        lines.append(f"种族: {npc['race']}")
    if npc.get('origin'):
        lines.append(f"出身: {npc['origin']}")
    if npc.get('region'):
        lines.append(f"地区: {npc['region']}")
    if npc.get('version'):
        lines.append(f"实装版本: {npc['version']}")
    if npc.get('system'):
        lines.append(f"系统: {npc['system']}")
    if npc.get('appear_time'):
        lines.append(f"出现时间: {npc['appear_time']}")

    dialogue = npc.get('dialogue', '')
    if dialogue:
        lines.append(f"\n对话与语音（前600字预览）:")
        lines.append(dialogue[:600])

    related = npc.get('related_quests', [])
    if related:
        lines.append(f"\n相关任务:")
        for q in related:
            lines.append(f"  · {q}")

    lines.append(f"\n[系统标记] 此条目为 NPC（非可玩角色）。若用户询问其成长/履历/身份转变/出场经历，请使用 NPC 离散成长检索协议：仅以 NPC 全名检索 hybrid_search，禁止添加「成长/变化/脉络」等抽象词，拿到出场列表后按版本顺序逐篇加载全文。")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_region_info(region: Dict) -> str:
    lines = [f"\n{'='*50}"]
    lines.append(f"【{region['地区名称']}】")
    lines.append(f"元素: {region.get('元素')}  |  神明: {region.get('神明')}")
    lines.append(f"理念: {region.get('理念')}  |  剧情章节: {region.get('剧情章节')}")
    lines.append(f"主要地点: {', '.join(region.get('主要地点', []))}")
    lines.append(f"\n简介: {region.get('简介', '暂无')}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)


def _format_story_info(arc: Dict) -> str:
    lines = [f"\n{'='*50}"]
    lines.append(f"【{arc['章节名称']}】")
    lines.append(f"所属地区: {arc.get('所属地区')}")
    lines.append(f"主要角色: {', '.join(arc.get('主要角色', []))}")
    lines.append(f"\n剧情概要: {arc.get('剧情概要', '暂无')}")
    if arc.get('关键事件'):
        lines.append(f"\n关键事件:")
        for i, event in enumerate(arc['关键事件'], 1):
            lines.append(f"  {i}. {event}")
    lines.append(f"{'='*50}")
    return "\n".join(lines)
