# -*- coding: utf-8 -*-
"""构建实体别名/变体映射表：canonical -> variants。

用途（2026-10-12 知识库缺口补全任务 3）：
- 输出 `kb_vectors/entity_alias_map.json`，供下游 normalize_entity 直接读取；
- 覆盖官方/半官方变体、简繁、常见简称、错字/异体字；
- 每条映射带 source 字段，说明依据来自哪里。

数据源：
- `character_aliases.py:CHARACTER_ALIASES`：角色规范名 -> 别名列表（含「瓦蕾莎 / 瓦雷莎」等）；
- 可选人工补充证据：`content_data/character_voices.json`（角色语音里出现的官方写法）。

边界：
- 不猜测；无法给出依据的变体不写入；
- 单字变体不写入（中文单字误伤率高）；
- canonical 冲突的变体不写入 variant_to_canonical，并在 stats 中记录。
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BASE_DIR / "kb_vectors" / "entity_alias_map.json"


def _clean(name: str) -> str:
    return str(name or "").strip().replace("「", "").replace("」", "").strip()


def _load_voice_text() -> str:
    """角色语音全文（只读一次，供官方变体佐证）。"""
    path = BASE_DIR / "content_data" / "character_voices.json"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_payload() -> dict:
    """从 character_aliases.CHARACTER_ALIASES 构建别名映射 payload。"""
    import sys

    sys.path.insert(0, str(BASE_DIR))
    from character_aliases import CHARACTER_ALIASES

    voice_text = _load_voice_text()

    canonical_to_variants: Dict[str, List[str]] = {}
    records: List[dict] = []
    variant_to_canonical: Dict[str, str] = {}
    conflicts: Dict[str, Set[str]] = defaultdict(set)

    for canonical, aliases in CHARACTER_ALIASES.items():
        canonical = _clean(canonical)
        if not canonical:
            continue
        variants: List[str] = []
        for alias in aliases or []:
            clean = _clean(alias)
            if not clean or clean == canonical or len(clean) < 2:
                continue
            if clean not in variants:
                variants.append(clean)
        if not variants:
            continue
        variants.sort(key=lambda x: (-len(x), x))
        canonical_to_variants[canonical] = variants

        evidence = ["character_aliases.py:CHARACTER_ALIASES"]
        for variant in variants:
            if variant not in variant_to_canonical:
                variant_to_canonical[variant] = canonical
            elif variant_to_canonical[variant] != canonical:
                conflicts[variant].add(variant_to_canonical[variant])
                conflicts[variant].add(canonical)
            if voice_text and canonical in voice_text and variant in voice_text:
                evidence.append(f"content_data/character_voices.json:{canonical}/{variant}")
        records.append(
            {
                "canonical": canonical,
                "variants": variants,
                "source": sorted(set(evidence)),
            }
        )

    for variant in list(conflicts):
        if variant in variant_to_canonical:
            del variant_to_canonical[variant]

    return {
        "version": 1,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "character_aliases.py:CHARACTER_ALIASES + content_data/character_voices.json",
        "canonical_to_variants": canonical_to_variants,
        "variant_to_canonical": variant_to_canonical,
        "records": records,
        "stats": {
            "canonical_groups": len(canonical_to_variants),
            "variant_count": sum(len(v) for v in canonical_to_variants.values()),
            "variant_to_canonical": len(variant_to_canonical),
            "skipped_conflict_variants": sorted(conflicts),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 kb_vectors/entity_alias_map.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    payload = build_payload()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
    print(f"已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
