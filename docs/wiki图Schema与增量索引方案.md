# Wiki 图 Schema 与增量索引方案

> 面向：负责知识库更新的同事
> 状态：设计稿，待施工
> 当前基线：`wiki_entry_graph.json` 13509 节点 / 41562 链接 / 74MB，`SCHEMA_VERSION=1`

---

## 1. 结论先行

1. **Schema 需要小幅升级，不需要重构。**
2. **增量索引建议分两级**：
   - 当前规模先做“变更检测 + 只抓变化详情 + 全量本地重建图”（网络成本为 0，构建本地很快）；
   - 等图规模显著变大再做“真正的增量写入”（patch graph）。
3. 增量机制的核心不是“重建得快不快”，而是**“知道哪些词条变了”**。

---

## 2. 当前 Schema 现状

```text
WikiEntry
  entry_id / title / entry_type / full_text / aliases / region / filters / links

WikiLink
  target_id / target_name / context

WikiEntryGraph
  entries: Dict[entry_id, WikiEntry]
  save/load（单 JSON，原子写）
  get / search / expand / backlinks / missing_targets
```

### 缺什么

| 缺口 | 影响 |
|---|---|
| 没有词条级 `status` | 无法标记新增/更新/删除/失效 |
| 没有 `source_channel` / `fetched_at` | 看不出词条来自哪个频道、何时抓的 |
| 没有 `content_hash` / `page_hash` | 无法做变更检测 |
| 没有 graph meta（构建时间、来源清单、脏链接清单） | 无法审计 |
| 脏链接（同 ID 多名字）被直接丢弃 | 没有留痕，出了问题查不到 |
| `missing_targets` 每次现算 | 现在够用，将来可快照 |

---

## 3. Schema 升级方案（v2）

### 3.1 WikiEntry 增加字段

```python
@dataclass
class WikiEntry:
    entry_id: str
    title: str
    entry_type: str
    full_text: str
    aliases: List[str]
    region: str
    filters: List[str]
    links: List[WikiLink]

    # v2 新增
    source_channel: int = 0          # 来源 channel_id，0=手工补抓
    fetched_at: str = ""             # 详情抓取时间
    content_hash: str = ""           # page JSON 的 sha1，变更检测用
    status: str = "ok"               # ok / new / updated / stale / dirty / error
    updated_at: str = ""             # 最近一次写图时间
```

### 3.2 WikiLink 增加字段（可选，向后兼容）

```python
@dataclass
class WikiLink:
    target_id: str
    target_name: str
    context: str = ""
    link_type: str = ""              # 从锚点/filters 推断：task/map_text/item/character/...
```

- `link_type` 先不强制填，给“出边类型过滤”留位置；
- 已有图升级时默认空串，不影响现有工具。

### 3.3 图级 meta

```python
{
  "schema_version": 2,
  "meta": {
    "built_at": "...",
    "entry_count": 13509,
    "link_count": 41562,
    "last_check_at": "...",
    "sources": [
      {"channel_id": 43, "graph_type": "task", "item_count": 1056, "fetched_at": "..."},
      ...
    ],
    "dirty_links": [
      {"source_entry_id": "509592", "target_id": "509226",
       "names": ["石兽如幽灵般端坐", "普洛克路斯忒斯的寝床"], "action": "dropped"}
    ],
    "missing_targets": {"1025": "大英雄的经验", ...}
  }
}
```

### 3.4 兼容策略

- `from_dict` 给所有 v2 字段提供默认值；
- v1 老文件能直接加载；
- 下一次全量构建自然生成 v2；
- `save()` 原子写保持不变。

---

## 4. 增量索引机制

### 4.1 原则

1. 网络抓取只对“变了”的词条；
2. 本地重建/写入可全量，因为 13509 条构建秒级；
3. 不自动删除词条，先标 `stale`，删除前全库检索；
4. 所有变更留痕可回滚；
5. 检测和抓取分离，便于定时任务/人工触发。

### 4.2 总体数据流

```text
观测枢 channel list
        |
        v
manifest 对比（卡片指纹 + page hash）
        |
        +--> new / updated / removed / unchanged
        |
        v
只抓 new+updated 的 entry_page
        |
        v
更新 channel_<id>.json（raw）
        |
        v
本地重建 graph（当前方案） 或 patch graph（远期）
        |
        v
写 graph meta / dirty / missing / stale
        |
        v
输出变更报告
```

### 4.3 变更检测

#### manifest 文件

```text
content_data/wiki_raw/_manifest.json
```

结构：

```json
{
  "version": 1,
  "last_check_at": "2026-09-08 12:00:00",
  "channels": {
    "43": {
      "graph_type": "task",
      "card_count": 1056,
      "items": {
        "509533": {
          "title": "至冬 在生命的寓所",
          "card_fingerprint": "sha1(...)",
          "page_hash": "sha1(...)",
          "fetched_at": "2026-09-07 23:17:00",
          "status": "ok"
        }
      }
    }
  }
}
```

#### 指纹定义

- `card_fingerprint` = sha1(稳定序列化的 `{title, filters_text, alias_name, corner_mark, summary}`)；
- `page_hash` = sha1(稳定序列化的 `page` JSON)；
- 两者都要：卡片变了必抓；卡片没变但详情页可能变，也要能检测（低成本做法：详情页定期抽查/版本号变化时抓）。

#### 四类变更

```text
new      = 列表里有、manifest 没有
updated  = 指纹不同（card_fingerprint 或 page_hash）
removed  = manifest 有、列表里没有 -> 标 stale，不删
unchanged = 指纹相同 -> 跳过
```

### 4.4 增量抓取

- 复用 `_fetch_mihoyo_channel.py`，给其加 `--ids <file>`（或 `--ids "id1,id2"`）：
  - 只抓指定 content_id 详情；
  - 其余能力（curl、延迟、断点、写 raw）不变。
- 抓完立即重算 `page_hash` 写入 manifest。
- 抓取失败：保留旧 page，`status=error`，下次重试；不能因为一个失败中断整批。

### 4.5 增量写入

#### 阶段 A（推荐当前实施）

```text
raw 更新完成后 -> 重新执行本地 build_full_graph()
```

- 不重抓网络；
- 13509 条本地解析构建很快；
- 简单、可靠、与现有 `scripts/update_wiki_graph.py` 无缝衔接；
- 这已经能满足“变更检测 + 增量抓取”的核心收益。

#### 阶段 B（远期，图 > 10 万节点或构建 > 30s 再上）

- `WikiEntryGraph.patch(changed_entries, removed_ids)`：
  - 内存加载一次 graph；
  - 替换/新增 changed 词条；
  - removed 词条标 `stale`；
  - 重算受影响节点的 links / backlinks；
  - 原子保存。
- 前提是 v2 schema 和 manifest 已就位。

### 4.6 文件与职责

| 文件 | 改动 | 负责 |
|---|---|---|
| `wiki_graph_channels.py` | 无 | 稳定 |
| `wiki_entry_graph.py` | v2 schema、meta、patch 接口（阶段B） | 项目侧 |
| `_fetch_mihoyo_channel.py` | `--ids`、写 manifest、page_hash | 知识库侧 |
| `scripts/update_wiki_graph.py` | 改为 `--check / --apply / --report` | 知识库侧 |
| `content_data/wiki_raw/_manifest.json` | 新产物（建议 gitignore） | 知识库侧 |
| `kb_vectors/wiki_entry_graph.json` | 构建产物（已 gitignore） | 项目侧 |

### 4.7 命令形态

```bash
# 只检测，不抓取（输出 new/updated/removed 统计）
python scripts/update_wiki_graph.py --check

# 应用变更：抓变化详情 + 重建图
python scripts/update_wiki_graph.py --apply

# 输出上次变更报告
python scripts/update_wiki_graph.py --report
```

---

## 5. 边界情况

| 场景 | 处理 |
|---|---|
| 词条从列表消失 | 标 `stale`，不删；先全库检索是否合并到其他词条 |
| 详情抓取失败 | 保留旧数据，标 `error`，下次重试 |
| 脏链接（同 ID 多名字） | 进 `meta.dirty_links`，不悄悄丢 |
| 同一 ID 出现在多频道 | manifest 记录多来源，图内选高优先级频道 |
| 图文件损坏 | 每次 apply 前把旧 graph/manifest 复制 `.bak` |
| 回滚 | 恢复 `.bak` 后重新 build/patch |
| 详情更新但卡片未变 | page_hash 定期校验或版本更新时强制重抓该版本词条 |

---

## 6. 施工步骤

### M1：Schema v2（项目侧）

- 增加 v2 字段和默认值；
- graph meta；
- 保持 v1 兼容；
- 全量构建一次生成 v2 图。

### M2：manifest 与变更检测（知识库侧）

- `--check`：拉列表、算指纹、对比 manifest；
- 输出 new/updated/removed/unchanged；
- manifest 原子写。

### M3：增量抓取（知识库侧）

- `_fetch_mihoyo_channel.py --ids`；
- 抓 new/updated；
- 更新 raw 和 manifest 的 page_hash。

### M4：增量应用与报告（知识库侧）

- `scripts/update_wiki_graph.py --apply`：抓变化 -> build/patch -> 写 meta -> 报告；
- `--report`：打印 per-channel 变更、dirty/missing/stale 统计。

### M5：验证

1. `--check` 对当前无变化数据应输出 0 new / 0 updated / 0 removed；
2. 手动改一个 channel raw（模拟更新）验证能检出；
3. 模拟删除一个 ID，确认只标 stale 不丢；
4. 构建后 graph 统计与当前一致；
5. `wiki_graph_search/get/expand` 冒烟；
6. 可选：定向 Golden 6 题回归。

---

## 7. 当前要不要动？

- **Schema v2 可以现在做**：不破坏任何现有工具，为增量机制铺路；
- **变更检测 + 增量抓取 + 全量重建**：建议同事按 M2~M5 施工；
- **真增量写入（patch）**：等图规模翻 5~10 倍再上，现在收益低。
