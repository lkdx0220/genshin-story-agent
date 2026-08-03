#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
原神知识库包
数据来源: Bilibili 原神 Wiki (https://wiki.biligame.com/ys/)
"""

from .roles import 角色知识库
from .weapons import 武器知识库
from .artifacts import 圣遗物知识库
from .quests import 任务知识库
from .regions import 地区知识库
from .main_story import 主线剧情知识库
from .materials import 素材知识库

__all__ = [
    "角色知识库",
    "武器知识库",
    "圣遗物知识库",
    "任务知识库",
    "地区知识库",
    "主线剧情知识库",
    "素材知识库",
]
