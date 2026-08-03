# -*- coding: utf-8 -*-
"""快速诊断 wiki API 调用问题"""
import urllib.request, json, urllib.parse, ssl

# 测试1: 不含 SSL 验证
print('=== 测试1: 默认请求 ===')
name = '蒂蕾娜'
encoded = urllib.parse.quote(name)
url = f'https://wiki.biligame.com/ys/api.php?action=parse&page={encoded}&prop=text%7Cwikitext&format=json'
try:
    r = urllib.request.urlopen(url, timeout=15)
    print(f'  状态码: {r.status}')
    d = json.loads(r.read())
    print(f'  成功! wikitext: {len(d.get("parse",{}).get("wikitext",{}).get("*",""))} 字, html: {len(d.get("parse",{}).get("text",{}).get("*",""))} 字')
except Exception as e:
    print(f'  失败: {type(e).__name__}: {e}')

# 测试2: 带 SSL context
print('\n=== 测试2: 带 SSL context ===')
ctx = ssl.create_default_context()
try:
    r = urllib.request.urlopen(url, timeout=15, context=ctx)
    print(f'  状态码: {r.status}')
    d = json.loads(r.read())
    print(f'  成功!')
except Exception as e:
    print(f'  失败: {type(e).__name__}: {e}')

# 测试3: 单独请求 wikitext
print('\n=== 测试3: 仅 wikitext ===')
url3 = f'https://wiki.biligame.com/ys/api.php?action=parse&page={encoded}&prop=wikitext&format=json'
try:
    r = urllib.request.urlopen(url3, timeout=15)
    print(f'  状态码: {r.status}')
    d = json.loads(r.read())
    print(f'  成功!')
except Exception as e:
    print(f'  失败: {type(e).__name__}: {e}')

# 测试4: 单独请求 text (HTML)
print('\n=== 测试4: 仅 text ===')
url4 = f'https://wiki.biligame.com/ys/api.php?action=parse&page={encoded}&prop=text&format=json'
try:
    r = urllib.request.urlopen(url4, timeout=15)
    print(f'  状态码: {r.status}')
    print(f'  成功!')
except Exception as e:
    print(f'  失败: {type(e).__name__}: {e}')

# 测试5: Request 对象带 headers
print('\n=== 测试5: 带 headers ===')
req = urllib.request.Request(url, headers={
    'User-Agent': 'YuanShenStoryBot/1.0 (wiki data rebuild)',
    'Accept': 'application/json'
})
try:
    r = urllib.request.urlopen(req, timeout=15)
    print(f'  状态码: {r.status}')
    print(f'  成功!')
except Exception as e:
    print(f'  失败: {type(e).__name__}: {e}')
