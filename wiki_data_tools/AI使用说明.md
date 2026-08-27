# Wiki 数据处理脚本使用说明（给 AI / 继任者）

> 本目录是原神剧情助手项目里所有“B站原神 Wiki 数据抓取/解析/重建”相关脚本。
> 请先读本文，再决定调用哪个脚本；**不要自己重写抓取/清理逻辑**。

---

## 0. 最重要的通用规则

1. **不要用 Python `requests` 直接请求 Wiki API。**
   - Wiki CDN 会对 Python requests 返回 HTTP 567。
   - 项目已验证：`curl.exe` 的 TLS 指纹不会被封。
   - 已有脚本基本都用 `curl.exe` 或 `http.client`/`urllib` 包装，请优先复用。

2. **处理 `{{折叠}}` / `{{黑幕}}` / 表格内容时，不要自己写正则丢弃。**
   - 见 `_fixup_quest_subpages.py` 的 `extract_dialogue_from_wikitext()`。
   - 它的核心思路是：去掉模板标记但保留内容。
   - 表格内容不能按行丢弃，要保留单元格文本。

3. **尽量不要改动 `content_data/` 下的数据文件结构。**
   - 如果确实要新增字段，先看现有同类条目的 schema。

---

## 1. 差异对比类

### `_crawl_diff.py`
- **作用**：对比 Wiki 分类页与本地知识库，输出新增/更新/删除差异。
- **当前支持分类**：角色、武器、任务、书籍、圣遗物、材料、怪物、食物。
- **常用命令**：
  - `python _crawl_diff.py --skip-content`：轻量对比，不拉正文。
  - `python _crawl_diff.py --category 角色`：只对比指定分类。
  - `python _crawl_diff.py --category 角色 --auto-fetch`：对比并抓取新增/更新页。
- **什么时候用**：
  - 想确认“Wiki 多了哪些词条、本地缺哪些”时。
  - 想判断某分类是否需要补数据时。
- **注意**：
  - 若要对 NPC/lore/采集物/概念/食谱做差异对比，需要先扩展 `CATEGORY_MAP` 和 `_extract_content_for_hash()`。

---

## 2. NPC 数据类

### `_build_npc_full.py`
- **作用**：合并 SMW 基础数据 + Wiki 详情，构建 `npcs_processed.json`，并写入 `kb_characters` 向量。
- **什么时候用**：NPC 基础数据或向量需要整体重建时。

### `_rebuild_npc_wiki_details.py`
- **作用**：从 Wiki API 全量重建 `npcs_wiki_details.json`（对话、相关剧情），支持断点续跑。
- **什么时候用**：NPC Wiki 详情缺失/过期，需要全量重建时。

### `_fetch_npc_wiki_v2.py`
- **作用**：生成批量 NPC URL，配合 WebFetch 人工抓取，绕 CDN 限流。
- **用法**：
  - `python _fetch_npc_wiki_v2.py`：生成下一批 URL。
  - `python _fetch_npc_wiki_v2.py --process`：解析抓取结果。
  - `python _fetch_npc_wiki_v2.py --status`：查看进度。
- **什么时候用**：当自动抓取被 567 限流，需要人工分批抓取时。

### `_parse_batch.py`
- **作用**：解析 WebFetch 批量抓取的 NPC wikitext，提取对话/origin/region/org_race，写入 `npcs_wiki_details.json`。
- **什么时候用**：配合 `_fetch_npc_wiki_v2.py` 的抓取结果做解析。

### `_fixup_failed_npcs.py`
- **作用**：补抓 `npcs_wiki_details.json` 中 `fetch_error` 的 NPC。
- **什么时候用**：批量重建后仍有失败 NPC 时。

### `_audit_npc_data.py`
- **作用**：审计 NPC 数据质量（ok/fetch_error/empty_page/no_status）。
- **什么时候用**：怀疑 NPC 数据不完整时，先跑它做体检。

### `_batch_embed.py`
- **作用**：循环运行 `_build_npc_full.py`，直到 NPC 向量全部嵌入完成。
- **什么时候用**：NPC 向量构建中断后需要继续跑完时。

### `_fetch_constellations.py`
- **作用**：批量抓取角色命之座 C1-C6 名称与效果。
- **什么时候用**：角色命之座数据缺失或需要更新时。

---

## 3. 任务/活动剧情类

### `_fixup_quest_subpages.py`
- **作用**：补抓活动剧情子页面，提取对话并追加到 `quests_活动活动.json`。
- **重要函数**：`extract_dialogue_from_wikitext()`
  - 处理 `{{折叠}}`、NPC 对话、表格等。
  - 是处理活动/任务正文的参考实现。
- **什么时候用**：活动任务子页面缺失时。

### `_fix_quest_series.py`
- **作用**：从 Wiki SMW API 获取世界任务系列关系，修复系列归属缺失。
- **什么时候用**：任务缺少“系列任务”字段或系列关系错误时。

---

## 4. 向量索引/知识库重建类

### `_rebuild_character_index.py`
- **作用**：只重建 `kb_characters` 向量索引（角色/NPC）。
- **什么时候用**：角色/NPC 数据更新后，只需重建该集合时。

### `_rebuild_collectibles.py`
- **作用**：从 `materials.json` 重建 `collectibles.json`，补全类型/分布地区。
- **什么时候用**：采集物/野生生物数据需要从材料数据重建时。

### `_build_archon.py`
- **作用**：只索引 `quests_魔神任务.json`，支持断点续跑。
- **什么时候用**：只更新魔神任务向量时。

---

## 5. 批量抓取/工具类

### `_batch_fetch_post.py`
- **作用**：用 POST 方式批量请求 Wiki API，避免 URL 过长导致 567。
- **什么时候用**：需要一次抓取大量页面标题时。

### `_debug_fetch.py`
- **作用**：快速诊断 Wiki API 请求是否成功、限流等。
- **什么时候用**：抓取失败时先跑它看是网络/限流/页面缺失。

### `_gen_batch_urls.py` / `_gen_remaining_urls.py`
- **作用**：为 NPC 批量抓取生成 URL 列表。
- **什么时候用**：配合人工 WebFetch 抓取流程。

### `_gen_html.py`
- **作用**：生成对比测试 HTML 片段。
- **注意**：强依赖本机临时路径，属于一次性工具，一般不要当通用流程用。

---

## 6. `scripts/` 目录（项目根级）

虽然不在本目录，但常与 Wiki 数据联动：

| 脚本 | 作用 |
|---|---|
| `scripts/scrape_limited_texts.py` | 抓取各地区「万国诸卷拾遗」限定文本，追加到 `lore.json` |
| `scripts/quest_preprocessor.py` | 长任务预处理，生成 `quests_processed.json` |
| `scripts/kb_build_index.py` | 全量/增量重建知识库向量索引 |
| `scripts/build_knowledge_base.py` | 从 Wiki 重建 `genshin_knowledge_base/*.py` 结构化知识库 |
| `scripts/build_exe.py` | 打包 exe |

---

## 7. 给 AI 的工作建议

- 遇到“Wiki 数据缺失/过时”：
  1. 先看 `_crawl_diff.py` 能否覆盖该分类；
  2. 不能覆盖就先扩展 `CATEGORY_MAP`，不要另写一套 diff；
  3. 抓取正文统一用 curl/现有脚本；
  4. 解析折叠/表格优先用 `extract_dialogue_from_wikitext()` 的思路；
  5. 补数据前先对比本地已有内容，避免重复。

---

## 8. 北陆图书馆/lore 专用提取工具（新增）

### `wiki_data_tools/north_library_extractor.py`
- **作用**：北陆图书馆/lore 专用正文提取。
- **为什么需要**：
  - 原 `extract_dialogue_from_wikitext()` 面向任务对话，会丢弃表格、`{{#ask}}` 动态查询、图片说明；
  - 北陆图书馆大量内容是 wikitable、`{{折叠}}`、`{{#ask}}` 生成的动态表。
- **原理**：
  - 用 `action=parse` 抓**渲染后 HTML**（MediaWiki 已展开模板/折叠/动态查询）；
  - 用 BeautifulSoup 提取段落、列表、表格单元格文本；
  - 丢弃图片、script/style、导航/页脚噪音。
- **用法**：
  - Python：`from north_library_extractor import fetch_rendered_html, extract_north_library_text`
  - CLI：`python north_library_extractor.py <页面标题>`
- **什么时候用**：处理北陆图书馆、lore、含表格/折叠/动态查询的 Wiki 页面时，优先用它。

---

## 9. 重要：合并/新增前必须全库检索

- 当发现“旧版本地有、新版本地没有”的内容时，**不能直接判定为删除**。
- 必须先在**整个本地知识库**里做全盘检索：
  - 精确匹配；
  - 再做最长公共子串/模糊匹配；
- 因为 Wiki 改版后内容可能被**转移到其他词条**，例如：
  - `霜月` → 并入 `龙族文明`
  - `挪德卡莱` → 并入 `至冬`
- 合并两个词条时：
  1. 先对比两份文本的异同；
  2. 对旧版独有内容做全库检索；
  3. 如果已存在于其他词条/正文，**不要重复保留**；
  4. 只有全库都找不到的内容才保留/新增。
