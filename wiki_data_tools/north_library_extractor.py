#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""北陆图书馆/lore 专用正文提取工具。

为什么需要：
- 原 `_fixup_quest_subpages.extract_dialogue_from_wikitext()` 面向“任务对话”，
  会丢弃表格行、`{{#ask}}` 动态查询、图片说明，不适合北陆图书馆长文/表格。
- 北陆图书馆页面大量内容依赖 MediaWiki 渲染（{{折叠}}、{{#ask}} 表、wikitable），
  直接解析原始 wikitext 会漏。

方案：
- 用 action=parse 抓渲染后 HTML（Wiki 已展开模板/折叠/语义查询表）。
- 用 BeautifulSoup 提取正文：保留段落、列表、表格单元格文本；丢弃图片、脚本、导航噪音。
"""
import subprocess, tempfile, json, urllib.parse, os, sys, re
from bs4 import BeautifulSoup

API = 'https://wiki.biligame.com/ys/api.php'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'


def _curl(params):
    url = API + '?' + urllib.parse.urlencode(params)
    fd, tmp = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    try:
        r = subprocess.run(['curl.exe', '-s', '--compressed', '-w', '%{http_code}', '-o', tmp,
                            '-H', f'User-Agent: {UA}', '-H', 'Accept: application/json, text/plain, */*',
                            '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8',
                            '-H', 'Referer: https://wiki.biligame.com/ys/',
                            '--max-time', '90', url], capture_output=True, text=True, timeout=100)
        if r.stdout.strip() != '200':
            return None
        return json.load(open(tmp, encoding='utf-8'))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def fetch_rendered_html(title):
    """抓取渲染后的页面 HTML。"""
    data = _curl({'action': 'parse', 'page': title, 'prop': 'text', 'format': 'json'})
    if not data:
        return None
    return data.get('parse', {}).get('text', {}).get('*', '')


def _clean_text(s):
    s = re.sub(r'\s+', ' ', s or '').strip()
    return s


def extract_north_library_text(html):
    """从渲染后的 HTML 提取北陆图书馆正文（保留全文，只去噪音）。"""
    soup = BeautifulSoup(html or '', 'html.parser')
    # 删除脚本/样式/图片
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    for img in soup.find_all('img'):
        img.decompose()
    # 删除常见导航/页脚容器
    for sel in ['.mw-editsection', '.printfooter', '.catlinks', '.mw-jump', '.toc', '.navbox', 'center']:
        for tag in soup.select(sel):
            tag.decompose()
    # 全文提取
    text = soup.get_text('\n')
    lines = []
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            continue
        # 过滤明显噪音
        if any(k in s for k in ['首页', 'Ctrl+D', 'WIKI功能', '编辑', '拓展阅读', '目录', '返回', '分类:']):
            continue
        if len(s) < 2:
            continue
        lines.append(s)
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python north_library_extractor.py <页面标题>')
        sys.exit(0)
    title = sys.argv[1]
    html = fetch_rendered_html(title)
    if not html:
        print('抓取失败', title)
        sys.exit(1)
    print(extract_north_library_text(html))
