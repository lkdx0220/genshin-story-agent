import json

a_path = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data\books.json"
b_path = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-稳定版\content_data\books.json"

with open(a_path, "r", encoding="utf-8") as f:
    a = json.load(f)
with open(b_path, "r", encoding="utf-8") as f:
    b = json.load(f)

a_titles = {e.get("title", "") for e in a}
b_titles = {e.get("title", "") for e in b}
only_b = b_titles - a_titles

print(f"稳定版独有 {len(only_b)} 条:\n")
for t in sorted(only_b):
    # 找出对应条目
    for e in b:
        if e.get("title") == t:
            text = e.get("text", "")
            # 去掉导航头只看正文
            if "\n\n" in text:
                body = text.split("\n\n", 1)[1]
            else:
                body = text
            preview = body[:150].replace("\n", " ")
            print(f"  [{t}]")
            print(f"    {preview}...")
            print()
            break
