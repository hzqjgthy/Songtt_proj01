# -*- coding: utf-8 -*-
"""
批量修复 output_nifti 下文件名中的乱码字符。

历史遗留：早期 dicom_to_nifti.py 直接用 SeriesDescription 做文件名，
在 Windows 路径编码差异下，'脑部' 被双重编码污染，磁盘上变成了
U+9474 U+6226 U+5134（看起来像 '鑴戦儴'），并导致一些工具读不开。

本脚本：
  1. 把所有文件名中含上述 3 个字符序列的文件批量重命名为 'brain'；
  2. 顺带把 'manifest.csv' 内的相同字符串也替换；
  3. 全程用 Python IO（pathlib），不受 PowerShell/CMD 控制台编码影响。

用法：
  python rename_legacy_garbled.py
  python rename_legacy_garbled.py --root path/to/output_nifti --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "output_nifti"

# 双重编码后的字符序列：U+9474 U+6226 U+5134
GARBLED = chr(0x9474) + chr(0x6226) + chr(0x5134)
# 极少数情况下 console 列表中显示出的"脑部"也直接映射
ALIASES = [GARBLED, "脑部"]
REPLACEMENT = "brain"


def replace_in_name(name: str) -> str:
    out = name
    for a in ALIASES:
        if a in out:
            out = out.replace(a, REPLACEMENT)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 output_nifti 下文件名乱码")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="要处理的根目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印不实际重命名")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[错误] 目录不存在: {root}", file=sys.stderr)
        return 2

    n_renamed, n_skip, n_conflict = 0, 0, 0

    # 文件层重命名（先文件后目录，避免父目录改名后子文件路径失效）
    targets = sorted(root.rglob("*"), key=lambda p: (-len(p.parts), str(p)))
    for p in targets:
        new_name = replace_in_name(p.name)
        if new_name == p.name:
            n_skip += 1
            continue
        new_path = p.with_name(new_name)
        if new_path.exists():
            print(f"  [冲突] 目标已存在，跳过: {p} -> {new_path}")
            n_conflict += 1
            continue
        if args.dry_run:
            print(f"  [DRY] {p.name}  ->  {new_name}")
        else:
            p.rename(new_path)
            print(f"  [改名] {p.name}  ->  {new_name}")
        n_renamed += 1

    # 修正 manifest.csv
    mf = root / "manifest.csv"
    if mf.exists():
        try:
            text = mf.read_text(encoding="utf-8-sig", errors="replace")
            new_text = text
            for a in ALIASES:
                new_text = new_text.replace(a, REPLACEMENT)
            if new_text != text:
                if args.dry_run:
                    print(f"  [DRY] manifest.csv 内容会被替换")
                else:
                    mf.write_text(new_text, encoding="utf-8-sig")
                    print(f"  [修正] manifest.csv 内容已更新")
        except Exception as e:
            print(f"  [警告] 处理 manifest.csv 失败: {e}")

    print()
    print(f"[完成] 重命名 {n_renamed} 个，未变 {n_skip} 个，冲突 {n_conflict} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
