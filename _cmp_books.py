import json

a_path = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-修改用\content_data\books.json"
b_path = r"c:\Users\24701\Desktop\原神剧情\CASE-原神剧情助手-稳定版\content_data\books.json"

with open(a_path, "r", encoding="utf-8") as f:
    a = json.load(f)
with open(b_path, "r", encoding="utf-8") as f:
    b = json.load(f)

print(f"修改用 books: {len(a)} 条")
print(f"稳定版 books: {len(b)} 条")

if isinstance(a, list) and isinstance(b, list):
    print("\n修改用最后3条:")
    for e in a[-3:]:
        print(f"  {e.get('title','?')[:80]}")
    print("\n稳定版最后3条:")
    for e in b[-3:]:
        print(f"  {e.get('title','?')[:80]}")

    a_titles = {e.get("title", "") for e in a}
    b_titles = {e.get("title", "") for e in b}
    only_a = a_titles - b_titles
    only_b = b_titles - a_titles
    print(f"\n仅在修改用: {len(only_a)} 条")
    print(f"仅在稳定版: {len(only_b)} 条")
    if only_b:
        print("稳定版独有的前10条:")
        for t in sorted(only_b)[:10]:
            print(f"  {t[:80]}")
    if only_a:
        print("修改用独有的前10条:")
        for t in sorted(only_a)[:10]:
            print(f"  {t[:80]}")
