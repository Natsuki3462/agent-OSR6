from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "vendor", "osr-emu.dist.min.js")
URL = ("https://raw.githubusercontent.com/ayvasoftware/osr-emu/main/"
       "dist/osr-emu.dist.min.js")
EXPECT_BYTES = 8_218_495

PART = DEST + ".part"
PART_ETAG = DEST + ".part.etag"
CHUNK = 256 * 1024

LICENSE_DEST = os.path.join(HERE, "vendor", "LICENSE-osr-emu.txt")
LICENSE_URLS = (
    "https://raw.githubusercontent.com/ayvasoftware/osr-emu/main/LICENSE",
    "https://raw.githubusercontent.com/ayvasoftware/osr-emu/main/LICENSE.md",
)

UA = "OSR-Control-fetch/1.1"


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch_license(quiet=False, budget=6.0) -> bool:
    if os.path.exists(LICENSE_DEST) and os.path.getsize(LICENSE_DEST) > 100:
        if not quiet:
            print(f"{_rel(LICENSE_DEST)}: 已存在")
        return True

    def _try_all():
        for url in LICENSE_URLS:
            try:
                with _opener().open(url, timeout=5) as r:
                    data = r.read()
                if len(data) >= 100:
                    return data
            except Exception:
                continue
        return None

    import threading
    box = {}

    def _run():
        box["data"] = _try_all()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(budget)
    data = box.get("data")

    if not data:
        if not quiet:
            print("（许可证没下到，不影响使用；上游是 MIT）")
        return False
    os.makedirs(os.path.dirname(LICENSE_DEST), exist_ok=True)
    with open(LICENSE_DEST, "wb") as f:
        f.write(data)
    if not quiet:
        print(f"已保存 {_rel(LICENSE_DEST)}")
    return True


def check() -> bool:
    if not os.path.exists(DEST):
        print(f"缺少 {_rel(DEST)}")
        return False
    size = os.path.getsize(DEST)
    print(f"{_rel(DEST)}: {size:,} 字节")
    if abs(size - EXPECT_BYTES) > 4096:
        print(f"  注意：和 0.7.0 的 {EXPECT_BYTES:,} 字节不一致（可能是新版本）")
    if not os.path.exists(LICENSE_DEST):
        print("  另：缺 vendor/LICENSE-osr-emu.txt（不影响运行，可 fetch 一下补上）")
    return size > 100_000


def _rel(p: str) -> str:
    try:
        return os.path.relpath(p, HERE)
    except ValueError:
        return p


def _bar(done: int, total: int, t0: float, width=26) -> str:
    elapsed = max(1e-6, time.time() - t0)
    speed = done / elapsed
    if total:
        pct = done / total * 100
        filled = min(width, int(width * done / total))
        bar = "=" * filled
        if filled < width:
            bar += ">" + " " * (width - filled - 1)
        eta = (total - done) / speed if speed > 1 else 0
        tail = (f"{done / 1048576:5.1f}/{total / 1048576:.1f} MB  "
                f"{speed / 1048576:5.2f} MB/s  剩 {eta:3.0f}s")
    else:
        pct = 0.0
        bar = "?" * width
        tail = f"{done / 1048576:5.1f} MB  {speed / 1048576:5.2f} MB/s"
    return f"  [{bar}] {pct:5.1f}%  {tail}"


def _remote_meta():
    try:
        req = urllib.request.Request(URL, method="HEAD",
                                     headers={"User-Agent": UA})
        with _opener().open(req, timeout=15) as r:
            size = int(r.headers.get("Content-Length") or 0)
            etag = (r.headers.get("ETag") or "").strip().strip('"')
            return size, etag
    except Exception:
        return 0, None


def _download(resume_from: int, total: int):
    headers = {"User-Agent": UA}
    mode = "wb"
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
        mode = "ab"

    req = urllib.request.Request(URL, headers=headers)
    with _opener().open(req, timeout=60) as r:
        status = getattr(r, "status", None) or 200
        if resume_from and status != 206:
            print("  服务器不支持断点续传（返回 200），从头开始")
            resume_from = 0
            mode = "wb"

        if not total:
            cr = r.headers.get("Content-Range") or ""
            if "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    total = 0
            if not total:
                total = int(r.headers.get("Content-Length") or 0) + resume_from

        done = resume_from
        t0 = time.time()
        last = 0.0
        tty = sys.stdout.isatty()
        with open(PART, mode) as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if tty and now - last > 0.12:
                    last = now
                    print(_bar(done, total, t0), end="", flush=True)
        if tty:
            print(_bar(done, total, t0))
    return done, total


def _drop_part():
    for p in (PART, PART_ETAG):
        try:
            os.remove(p)
        except OSError:
            pass


def _finish(done: int, total: int) -> int:
    if total and done != total:
        print(f"大小不符：下了 {done:,}，应该是 {total:,}")
        print("半成品保留着，重跑会接着下。")
        return 1
    if done < 100_000:
        print(f"下载内容太小（{done:,} 字节），不像正常的库文件。")
        _drop_part()
        return 1
    os.replace(PART, DEST)
    _drop_part()
    print(f"已保存 {done:,} 字节 -> {_rel(DEST)}")
    try:
        with open(DEST, "rb") as f:
            print("sha256:", hashlib.sha256(f.read()).hexdigest()[:16], "…")
    except OSError:
        pass
    print("页面会自动用它；没有它则退回手绘视图，功能不受影响。")
    fetch_license()
    return 0


def status():
    print(f"目标文件：{_rel(DEST)}")
    if os.path.exists(DEST):
        print(f"  已完整：{os.path.getsize(DEST):,} 字节")
    else:
        print("  还没有完整文件")
    if os.path.exists(PART):
        n = os.path.getsize(PART)
        print(f"  未完成：{n:,} 字节（{n / 1048576:.1f} MB）"
              " —— 重跑本命令会自动续传")
    else:
        print("  没有未完成的下载")
    if os.path.exists(LICENSE_DEST):
        print(f"  许可证：{os.path.getsize(LICENSE_DEST):,} 字节")
    else:
        print("  许可证：缺失（不影响运行）")


def fetch(force: bool) -> int:
    if os.path.exists(DEST) and not force and check():
        print("已存在，跳过。要重下加 --force。")
        fetch_license()
        return 0

    os.makedirs(os.path.dirname(DEST), exist_ok=True)

    have = os.path.getsize(PART) if os.path.exists(PART) else 0
    if force and have:
        print(f"丢弃未完成的下载（{have / 1048576:.1f} MB），重新开始")
        _drop_part()
        have = 0

    total, etag = _remote_meta()

    if have and etag:
        old = ""
        try:
            with open(PART_ETAG, encoding="utf-8") as f:
                old = f.read().strip()
        except OSError:
            pass
        if old and old != etag:
            print("上游文件已更新，之前的半成品作废，从头下")
            _drop_part()
            have = 0

    if have and total and have >= total:
        print(f"上次已经下完（{have:,} 字节），直接收尾")
        return _finish(have, total)

    if have:
        print(f"发现未完成的下载：{have / 1048576:.1f} MB"
              + (f" / {total / 1048576:.1f} MB" if total else "")
              + "，接着下…")
    else:
        print(f"正在下载 {URL}")
        if total:
            print(f"  文件大小 {total:,} 字节（{total / 1048576:.1f} MB）")
        print("  中途可以 Ctrl+C，进度会保留，重跑继续。")

    if etag:
        try:
            with open(PART_ETAG, "w", encoding="utf-8") as f:
                f.write(etag)
        except OSError:
            pass

    try:
        done, total = _download(have, total)
    except KeyboardInterrupt:
        print("\n\n已中断 —— 进度已保存，重跑本命令会接着下。")
        return 130
    except Exception as e:
        print(f"\n下载出错：{type(e).__name__}: {e}")
        if os.path.exists(PART):
            print("已下到的部分保留着，重跑会续传。")
        return 1

    return _finish(done, total)


def main():
    p = argparse.ArgumentParser(description="下载 osr-emu 3D 库（支持断点续传）")
    p.add_argument("--force", action="store_true", help="已存在也重下")
    p.add_argument("--check", action="store_true", help="只检查")
    p.add_argument("--status", action="store_true",
                   help="看有没有下到一半的（可续传）")
    a = p.parse_args()
    if a.check:
        return 0 if check() else 1
    if a.status:
        status()
        return 0
    return fetch(a.force)


if __name__ == "__main__":
    sys.exit(main())
