# -*- coding: utf-8 -*-
"""
补漏脚本：补抓活动剧情子页面

流程：
1. 读取 quests_活动活动.json，找出 Type A 活动（主页面无角色对话，有子页面链接）
2. 对每个 Type A 活动，请求 wiki API 获取主页面 HTML
3. 从 HTML 中解析「活动剧情」栏的实际子页面链接
4. 与已有数据交叉比对，找出缺失的子页面
5. 逐个抓取缺失子页面的 wikitext，提取对话内容
6. 将新条目追加到 quests_活动活动.json

用法：python wiki_data_tools/_fixup_quest_subpages.py
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONTENT_DIR = os.path.join(PROJECT_DIR, 'content_data')

# 输入/输出
QUESTS_FILE = os.path.join(CONTENT_DIR, 'quests_活动活动.json')
CHECKPOINT_FILE = os.path.join(CONTENT_DIR, '_quest_subpage_fixup_cp.json')

# 请求配置
REQUEST_DELAY = 1.5     # 单个请求间延迟 (秒)
BATCH_SIZE = 10         # 每批处理数 (降低避免触发 Cloudflare)
BATCH_DELAY = 5.0       # 批次间延迟 (秒)
MAX_RETRIES = 3         # 最大重试次数
RETRY_WAITS = [10, 20, 30]
MAX_CONSEC_FAILS = 5     # 连续失败上限（超过则判定 IP 被封，自动退出）

# API 配置
API_URL = 'https://wiki.biligame.com/ys/api.php'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def log(msg):
    """带时间戳的日志输出"""
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)


def fetch_wiki_page(page_title):
    """获取 wiki 页面的 wikitext 和 HTML，返回 (wikitext, html, pageid, error_msg)"""
    encoded = urllib.parse.quote(page_title)
    url = f'{API_URL}?action=parse&page={encoded}&prop=text%7Cwikitext&format=json'

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return None, None, None, f'HTTP {e.code}'
    except urllib.error.URLError as e:
        return None, None, None, f'网络错误: {e.reason}'
    except Exception as e:
        return None, None, None, f'{type(e).__name__}: {e}'

    if 'error' in d:
        error_code = d.get('error', {}).get('code', '')
        error_info = d.get('error', {}).get('info', '')
        if error_code == 'missingtitle':
            return '', '', None, 'missingtitle'
        return None, None, None, f'API错误: {error_code} - {error_info}'

    parse_data = d.get('parse', {})
    pageid = parse_data.get('pageid', None)
    wt = parse_data.get('wikitext', {}).get('*', '')
    html = parse_data.get('text', {}).get('*', '')
    return wt, html, pageid, 'ok'


def parse_subpage_links_from_html(html):
    """从活动主页面 HTML 中提取「活动剧情」栏的子页面链接

    wiki 页面结构:
    <h2>活动剧情</h2>
    <ul><li><a href="/ys/子页面名" title="子页面名">子页面名</a></li></ul>
    """
    if not html:
        return []

    # 找到「活动剧情」标题（wiki API 返回的 HTML 中，id 在 mw-headline span 上）
    pattern = r'<span[^>]*class="mw-headline"[^>]*id="活动剧情"'
    m = re.search(pattern, html)
    if not m:
        # 回退：找「剧情」的 headline
        pattern2 = r'<span[^>]*class="mw-headline"[^>]*id="[^"]*剧情'
        m = re.search(pattern2, html)
    if not m:
        return []

    # 从活动剧情标题位置往后找最近的 <ul>
    section_start = m.start()
    ul_match = re.search(r'<ul>', html[section_start:section_start + 5000])
    if not ul_match:
        return []

    ul_start = section_start + ul_match.start()
    ul_end_match = re.search(r'</ul>', html[ul_start:ul_start + 5000])
    if not ul_end_match:
        return []

    ul_html = html[ul_start:ul_start + ul_end_match.end()]

    # 提取所有 <a> 标签的 title 属性（即子页面名）
    links = re.findall(r'<a\s+[^>]*title="([^"]+)"[^>]*>', ul_html)
    # 过滤掉非页面标题的属性值（如 "编辑" "查看"）
    sub_pages = []
    for title in links:
        title = title.strip()
        if not title:
            continue
        # 过滤掉 wiki 编辑/查看等链接
        if re.match(r'^(编辑|查看|阅读|页面|特殊|帮助|分类|文件|MediaWiki)', title):
            continue
        # 过滤掉当前页面自身
        if title.startswith('「') and title.endswith('」'):
            continue
        sub_pages.append(title)

    return sub_pages


def extract_dialogue_from_wikitext(wikitext):
    """从活动子页面的 wikitext 提取任务剧情对话

    活动子页面的格式：
    *角色名：对话内容
    
    也支持 {{NPC任务}} 或 {{任务剧情}} 模板中的内容
    """
    if not wikitext:
        return ''

    # 去除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', wikitext, flags=re.DOTALL)
    # 去除模板调用（保留内容）
    # {{折叠|...}} -> 内容
    # 先展开模板中的内容
    text = re.sub(r'\{\{[^}]*?\|', '', text)
    text = text.replace('{{', '').replace('}}', '')
    # 去除 <noinclude> 块
    text = re.sub(r'<noinclude>.*?</noinclude>', '', text, flags=re.DOTALL)

    # wiki 链接展开: [[页面名]] -> 页面名, [[页面名|显示]] -> 显示
    text = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', text)

    # 去除粗体标记
    text = text.replace("'''", '')

    # <br> -> 换行
    text = text.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')

    # 提取所有有用行（保留对话行和描述行）
    lines = text.split('\n')
    result = []
    in_table = False
    in_template = False
    brace_depth = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append('')
            continue

        # 跟踪 {{ }} 深度
        brace_depth += stripped.count('{{') - stripped.count('}}')
        if brace_depth > 0:
            in_template = True
            continue
        else:
            in_template = False

        # 跳过表格行
        if stripped.startswith('{|') or stripped.startswith('|}'):
            in_table = not in_table
            continue
        if in_table:
            continue
        if stripped.startswith('|') or stripped.startswith('!'):
            continue

        # 跳过模板定义行
        if re.match(r'\|[^=]+\s*=\s*', stripped):
            continue

        # 跳过明显的元数据行
        if any(stripped.startswith(kw) for kw in ['<', '[[分类:', '[[Category:', '{{']):
            continue

        result.append(stripped)

    text = '\n'.join(result)
    # 合并多余空行
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def build_quest_entry(title, pageid, text, parent_title):
    """构建与 quests_活动活动.json 格式一致的活动条目"""
    return {
        'source': '活动活动',
        'category': '活动活动',
        'title': title,
        'pageid': pageid or 0,
        'metadata': {
            '任务名称': title,
            '所属版本': '',      # 从主活动继承
            '任务地区': '',
            '任务类型': '活动活动',
            '出场人物': '',
            '相关活动': parent_title,
            '系列任务': ''
        },
        'text': text
    }


def main():
    log('=' * 60)
    log(' 活动剧情子页面补漏脚本')
    log('=' * 60)

    # 1. 读取数据
    if not os.path.exists(QUESTS_FILE):
        log(f'错误: 找不到 {QUESTS_FILE}')
        sys.exit(1)

    with open(QUESTS_FILE, 'r', encoding='utf-8') as f:
        quests = json.load(f)
    log(f'已读取 {len(quests)} 条活动数据')

    # 收集已有标题
    existing_titles = set()
    for q in quests:
        t = q.get('title', '').strip()
        if t and t != '{{PAGENAME}}':
            existing_titles.add(t)

    # 2. 找出 Type A 活动（主页面无角色对话，且子页面未全部作为独立条目存在）
    type_a_activities = []
    for q in quests:
        title = q.get('title', '')
        text = q.get('text', '')

        if '「' not in title:
            continue

        lines = text.strip().split('\n')
        has_dialogue = False
        subpage_candidates = []

        for line in lines[:20]:  # 只检查前 20 行
            stripped = line.strip()
            # 子页面列表（候选名）
            if re.match(r'^\*[^*]', stripped) and '：' not in stripped and '|' not in stripped:
                sp = stripped.lstrip('*').strip()
                if sp and len(sp) <= 30 and not sp.startswith('{'):
                    subpage_candidates.append(sp)
            # 角色对话
            if re.match(r'^\*[^*]', stripped) and ('：' in stripped or ':' in stripped):
                has_dialogue = True
            if re.match(r'^[-—]*\s*\S+[：:]', stripped):
                has_dialogue = True

        if not subpage_candidates or has_dialogue:
            continue

        # 检查候选子页面是否已全部作为独立条目存在
        all_captured = all(sp in existing_titles for sp in subpage_candidates)
        if all_captured:
            continue  # 子页面都已收录，跳过

        type_a_activities.append(title)

    log(f'Type A 活动 (完全缺失): {len(type_a_activities)} 个')
    for a in type_a_activities:
        log(f'  - {a}')

    # 3. 读取 checkpoint
    processed_activities = set()
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        processed_activities = set(cp.get('processed_activities', []))
        log(f'checkpoint: 已处理 {len(processed_activities)} 个活动')

    # 健康检查
    log('健康检查: 尝试请求 "将才与推演"...')
    wt, html, pid, err_msg = fetch_wiki_page('将才与推演')
    if wt is None and 'HTTP 567' in err_msg:
        log(f'IP 仍被 Cloudflare 封锁 ({err_msg})')
        log('请等待更长时间（建议 30 分钟以上），然后重试')
        sys.exit(1)
    if wt is not None:
        log(f'健康检查通过!')
    else:
        log(f'健康检查警告: {err_msg}，继续...')
    log('')

    # 4. 处理每个 Type A 活动
    all_new_entries = []
    fetch_count = 0
    consec_fails = 0       # 连续失败计数（含重试耗尽和 567 错误）
    start_time = time.time()

    for act_title in type_a_activities:
        if act_title in processed_activities:
            log(f'跳过已处理: {act_title}')
            continue

        time.sleep(REQUEST_DELAY)

        # 获取活动主页面 HTML
        log(f'--- 处理活动: {act_title} ---')
        wt, html, pid, err_msg = fetch_wiki_page(act_title)

        if wt is None:
            log(f'  获取主页面失败: {err_msg}，跳过')
            consec_fails += 1
            if consec_fails >= MAX_CONSEC_FAILS:
                log(f'  *** 连续失败 {consec_fails} 次，疑似 IP 被封锁，退出 ***')
                if all_new_entries:
                    _append_entries(QUESTS_FILE, all_new_entries)
                save_checkpoint(CHECKPOINT_FILE, processed_activities)
                sys.exit(1)
            processed_activities.add(act_title)
            save_checkpoint(CHECKPOINT_FILE, processed_activities)
            continue
        else:
            consec_fails = 0  # 成功则重置

        # 解析子页面链接
        sub_pages = parse_subpage_links_from_html(html)
        log(f'  从 HTML 解析到 {len(sub_pages)} 个子页面: {sub_pages}')

        # 过滤已存在的
        missing = [sp for sp in sub_pages if sp not in existing_titles]
        log(f'  缺失 {len(missing)} 个: {missing}')

        if not missing:
            processed_activities.add(act_title)
            save_checkpoint(CHECKPOINT_FILE, processed_activities)
            continue

        # 抓取缺失的子页面
        for sub_title in missing:
            time.sleep(REQUEST_DELAY)
            fetch_count += 1

            # 批次间延迟
            if fetch_count % BATCH_SIZE == 0:
                log(f'  批次间歇 {BATCH_DELAY}s...')
                time.sleep(BATCH_DELAY)

            # 带重试的抓取
            success = False
            for retry in range(MAX_RETRIES):
                sub_wt, sub_html, sub_pid, sub_err = fetch_wiki_page(sub_title)

                if sub_wt is not None:
                    # 提取对话内容
                    dialogue = extract_dialogue_from_wikitext(sub_wt)

                    entry = build_quest_entry(sub_title, sub_pid, dialogue, act_title)
                    all_new_entries.append(entry)
                    existing_titles.add(sub_title)

                    log(f'  [{fetch_count}] {sub_title} -> OK (pageid={sub_pid}, {len(dialogue)} 字)')
                    success = True
                    break
                elif sub_err == 'missingtitle':
                    log(f'  [{fetch_count}] {sub_title} -> 页面不存在，跳过')
                    existing_titles.add(sub_title)  # 标记为已处理，避免重复尝试
                    success = True
                    break
                else:
                    wait = RETRY_WAITS[retry]
                    log(f'  [{fetch_count}] {sub_title} -> {sub_err}，重试 {retry+1}/{MAX_RETRIES}，等待 {wait}s')
                    time.sleep(wait)

            if not success:
                log(f'  [{fetch_count}] {sub_title} -> 最终失败，跳过')
                consec_fails += 1
                if consec_fails >= MAX_CONSEC_FAILS:
                    log(f'')
                    log(f'  *** 连续失败 {consec_fails} 次，疑似 IP 被 Cloudflare 封锁 ***')
                    log(f'  *** 已保存 checkpoint，等待冷却后可断点续跑 ***')
                    save_checkpoint(CHECKPOINT_FILE, processed_activities)
                    # 先保存已抓取的数据
                    if all_new_entries:
                        _append_entries(QUESTS_FILE, all_new_entries)
                    sys.exit(1)
            else:
                consec_fails = 0  # 成功则重置连续失败计数

        processed_activities.add(act_title)
        save_checkpoint(CHECKPOINT_FILE, processed_activities)

    # 5. 追加新条目到数据文件
    if all_new_entries:
        log('')
        _append_entries(QUESTS_FILE, all_new_entries)
    else:
        log('没有新条目需要追加')

    # 6. 清理 checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    elapsed = time.time() - start_time
    log('')
    log('=' * 60)
    log(f'补漏完成! 耗时: {elapsed:.0f}s')
    log(f'新增条目: {len(all_new_entries)}')
    log('=' * 60)


def save_checkpoint(cp_file, processed):
    """保存 checkpoint"""
    cp_data = {
        'processed_activities': list(processed),
    }
    tmp = cp_file + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cp_data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cp_file)


def _append_entries(filepath, entries):
    """将条目追加写入 JSON 数组文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data.extend(entries)
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)
    log(f'已追加 {len(entries)} 条数据，总计 {len(data)} 条')


if __name__ == '__main__':
    main()
