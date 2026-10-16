# compat 探针 canonical 副本

来源 review_root: C:/Users/24701/Documents/Codex/2026-10-14/l-t

| 原始文件 | 原始 SHA256 | 仓库形态 |
|---|---|---|
| b203-t1c2-review/test_independent_c2.py | 931b23b381a46aec583a0323b72a0f1ef57e0e0fc809be39f75d86d52fd393f1 | probe_independent_c2.py.orig（溯源，不运行） |
| b203-t1c2-r2-review/test_legacy_adapted_c2.py | 2e918f05876a74f6585c9aa1fd78b79a2bca8be15d299509c698865a308e4640 | probe_legacy_adapted_c2.py（运行，loader 指向 .orig） |
| b203-t1c2-r01-r04-review/test_c2_residual.py | 3309f62896156b2360a93415cf0683ca752403c61538d030ecc8930b36617ce6 | probe_c2_residual.py（运行，原样） |
| b203-t1c2-r2-review/test_optional_cleanup_parents.py | 327468100e36dc1b1cf714b53406af9444aed3a2b1cdf5373549e3699025c2b2 | probe_optional_cleanup_parents.py（运行，SOURCE 改仓库内相对路径） |
| b203-t1c1-review/test_independent_c1.py | be661ec62d02d71724b5a35035c8e67c0d55ed7077eceb6f04c6325463a39017 | probe_independent_c1.py（运行，原样） |
| b203-t1c1-c01-c06-review/test_residual_c1.py | 9bb915159303669d5e95e9385c6d89a93591413aea4d6e855e523da978617c4f | probe_residual_c1.py（运行，原样） |
| b203-t1c1-r01-r03-review/test_g10_closure.py | 3af5572c00ea1273bc307e89ba4209fdb31b2801208251b730839abb20104395 | probe_g10_closure.py（运行，原样） |

运行集：probe_legacy_adapted_c2(30) + probe_c2_residual(23) + probe_optional_cleanup_parents(3) + C1 probes(38) = 94。
probe_*.py 命名避免 tests/b203 目录自动收集；由 scripts/check_b203_contracts.py compat 显式运行。
