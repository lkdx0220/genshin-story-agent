#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""使用 POST 方式批量抓取 wiki 数据，避免 URL 过长导致的 567 错误"""
import json
import os
import sys
import time
import ssl
import http.client

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(SCRIPT_DIR, 'content_data')
sys.path.insert(0, SCRIPT_DIR)
from _parse_batch import parse_batch as parse_one

BATCHES_FILE = os.path.join(CONTENT_DIR, 'npc_batch_urls_remaining.json')
API_HOST = 'wiki.biligame.com'
API_PATH = '/ys/api.php'

def log(msg):
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)


def fetch_via_post(titles):
    """用 POST 方式请求 wiki API"""
    body = urllib.parse.urlencode({
        'action': 'query',
        'titles': titles,
        'prop': 'revisions',
        'rvprop': 'content',
        'rvslots': 'main',
        'format': 'json',
    })
    for attempt in range(3):
        try:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(API_HOST, timeout=30, context=ctx)
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0',
            }
            conn.request('POST', API_PATH, body=body.encode('utf-8'), headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode('utf-8')
            conn.close()
            if resp.status != 200:
                log(f'  HTTP {resp.status}')
                if attempt < 2:
                    time.sleep(3)
                continue
            return raw
        except Exception as e:
            log(f'  attempt {attempt+1} error: {e}')
            if attempt < 2:
                time.sleep(3)
    return None


def main():
    import urllib.parse

    with open(BATCHES_FILE, 'r', encoding='utf-8') as f:
        batches = json.load(f)

    total = len(batches)
    log(f'total batches: {total}')
    processed = 0
    failed = []

    for batch in batches:
        bid = batch['batch_id']
        names = batch['names']
        count = batch['count']
        titles = '|'.join(names)
        log(f'batch {bid}/{total}: {names[0]} ~ {names[-1]} ({count})')

        raw_text = fetch_via_post(titles)
        if raw_text is None:
            log(f'  FAILED')
            failed.append(bid)
            continue

        tmp_file = os.path.join(CONTENT_DIR, f'webfetch_batch{bid}.json')
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(raw_text)

        parse_one(tmp_file)
        processed += 1
        log(f'  OK ({processed}/{total})')
        time.sleep(0.5)

    log(f'Done: {processed}/{total} ok')
    if failed:
        log(f'Failed batches: {failed}')


if __name__ == '__main__':
    import urllib.parse
    main()
