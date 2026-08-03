import json

for label, path in [
    ("修改用", r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data\lore.json"),
    ("稳定版", r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-稳定版\content_data\lore.json"),
]:
    with open(path, "r", encoding="utf-8") as f:
        lore = json.load(f)
    limited = sum(1 for e in lore if "限定文本" in e["title"])
    print(f"{label}: {len(lore)} 条, 限定文本 {limited} 条")
    for e in lore[-3:]:
        t = e["title"]
        print(f"  [{t[:100]}]")
    print()
