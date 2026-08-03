#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""循环运行 _build_npc_full.py 直到全部嵌入完成"""
import subprocess
import sys
import os
import json
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CP_FILE = os.path.join(SCRIPT_DIR, 'kb_vectors', 'npc_full_step_cp.json')

def log(msg):
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)

round_num = 0
while True:
    round_num += 1
    log(f'=== Round {round_num} ===')

    result = subprocess.run(
        [sys.executable, '_build_npc_full.py'],
        cwd=SCRIPT_DIR,
        capture_output=False
    )

    if result.returncode != 0:
        log(f'Error: exit code {result.returncode}')
        sys.exit(1)

    # Check if done
    if os.path.exists(CP_FILE):
        with open(CP_FILE, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        log(f'Checkpoint: {cp["idx"]}')
    else:
        log('Complete! Checkpoint file removed.')
        break

    time.sleep(1)

log('All done!')
