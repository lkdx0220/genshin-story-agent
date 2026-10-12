# -*- coding: utf-8 -*-
"""wiki_source 导入引导（cast 链路迁移 0.2.0）。

解析顺序（迁移任务书 §五）：
  1. 环境变量 WIKI_SOURCE_SRC（指向 genshin-wiki-source 的 src 目录）
  2. 已安装包 wiki_source（python -m pip install -e <genshin-wiki-source>）
  3. 都没有 → ImportError，含安装指引

项目 scripts 不自带任何 B站/米游社网络实现；取数一律经 genshin-wiki-source。
"""

import os
import sys
from pathlib import Path

_INSTALL_PATH = r"C:\Users\24701\Desktop\skills开发\skills\genshin-wiki-source"
_INSTALL_HINT = (
    "genshin-wiki-source 未安装（cast 链路取数依赖它）。任选其一：\n"
    f'  1) python -m pip install -e "{_INSTALL_PATH}"\n'
    "  2) 设置环境变量 WIKI_SOURCE_SRC 指向其 src 目录"
)


def ensure_wiki_source() -> None:
    """确保 `import wiki_source` 可用；否则抛 ImportError（含安装指引）。"""
    try:
        import wiki_source  # noqa: F401

        return
    except ImportError:
        pass

    env = os.environ.get("WIKI_SOURCE_SRC")
    if env and (Path(env) / "wiki_source").is_dir():
        if env not in sys.path:
            sys.path.insert(0, str(Path(env).resolve()))
        import wiki_source  # noqa: F401

        return

    raise ImportError(_INSTALL_HINT)
