# -*- coding: utf-8 -*-
"""工具参数的取值词表（单一事实来源）。

约定：
- 取值集合**只在这里定义一次**：类型用 `Literal`（LLM 侧的 JSON Schema 会自带 `enum`），
  运行期需要的元组用 `typing.get_args` 从同一个 Literal 派生，禁止在别处再抄一份。
- 取值来源为知识库实际数据，不是文档转述：
  · 元素      ← 角色「神之眼」字段（7 个）
  · 角色地区  ← 角色「所属」字段（10 个，含 坎瑞亚 / 挪德卡莱）
  · 武器类型  ← 角色「武器类型」字段（5 个）
  · 稀有度    ← 角色「稀有度」字段取规范写法 4 / 5（数据里混入的 "5星" 属脏数据，不作为入参）
  · 特产地区  ← 采集物「类型」字段的「X区域特产」前缀（8 个，含 至冬）
- 好处：签名即文档（可读性），非法取值由 Schema 在调用前拦下（契约），
  错误文案与校验共用同一份元组（不会漂移）。
"""
from typing import Literal, get_args

# ====== 角色 ======
Element = Literal["火", "水", "风", "雷", "冰", "岩", "草"]
ELEMENTS = get_args(Element)

CharacterRegion = Literal[
    "蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "至冬", "坎瑞亚", "挪德卡莱", "其他",
]
CHARACTER_REGIONS = get_args(CharacterRegion)

WeaponType = Literal["单手剑", "双手剑", "长柄武器", "法器", "弓"]
WEAPON_TYPES = get_args(WeaponType)

Rarity = Literal["4", "5"]
RARITIES = get_args(Rarity)

# ====== 采集物（区域特产）======
CollectibleRegion = Literal["蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱", "至冬"]
COLLECTIBLE_REGIONS = get_args(CollectibleRegion)

# ====== 角色查询的可选小节 ======
# 空串 = 常规档案；"语音" = 语音档案。实现侧仍兼容 voice/语音档案/档案 等历史写法。
CharacterSection = Literal["", "语音"]
