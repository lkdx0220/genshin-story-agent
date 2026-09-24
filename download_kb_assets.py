# -*- coding: utf-8 -*-
"""Docker 构建期知识库资产下载器。

用法：
    python download_kb_assets.py <目标目录> <url> <sha256> [<url> <sha256> ...]

每个资产必须是 zip 文件；下载后校验 SHA256，再解压到目标目录。
zip 内部已经带有 kb_vectors/ 前缀，因此解压到 /app 后即得到 /app/kb_vectors。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile


def _download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) < 4 or (len(sys.argv) - 2) % 2 != 0:
        print("用法: download_kb_assets.py <目标目录> <url> <sha256> [<url> <sha256> ...]")
        return 2

    dest_dir = sys.argv[1]
    args = sys.argv[2:]
    pairs = [(args[i], args[i + 1]) for i in range(0, len(args), 2)]
    os.makedirs(dest_dir, exist_ok=True)

    for url, expected in pairs:
        fd, tmp_path = tempfile.mkstemp(prefix="kb_asset_", suffix=".zip")
        os.close(fd)
        try:
            print(f"[下载] {url}")
            _download(url, tmp_path)
            actual = _sha256(tmp_path)
            if actual.lower() != expected.lower():
                raise SystemExit(
                    f"[校验失败] {url}\n期望: {expected}\n实际: {actual}"
                )
            print(f"[校验通过] {url}")
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(dest_dir)
            print(f"[解压完成] -> {dest_dir}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
