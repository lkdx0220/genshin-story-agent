# -*- coding: utf-8 -*-
"""pytest 全局配置。

- 把仓库根与 scripts/ 加入 sys.path，便于直接导入被测模块
- 受限环境（只读缓存/杀软拦截）下，可用环境变量把缓存与临时目录指到可写位置：
  TEMP/TMP、RUFF_CACHE_DIR、PYTHONPYCACHEPREFIX、pytest --basetemp
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
