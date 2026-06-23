# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    print("[错误] 未安装 SimpleITK", file=sys.stderr)
    raise

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("[错误] 未安装 matplotlib", file=sys.stderr)
    raise

from export_synthseg_masks import freesurfer_env, is_source_ct


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"


def read_image(path: Path) -> sitk.Image:
    return sitk.ReadImage(str(path))


def image_stats(path: Path) -> Dict:
    img = read_image(path)
    arr = sitk.GetArrayFromImage(img).astype(np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        summary = {"min": None, "p01": None, "mean": None, "p99": None, "max": None}
    else:
        summary = {
            "min": round(float(finite.min()), 3),
            "p01": round(float(np.percentile(finite, 1)), 3),
            "mean": round(float(finite.mean()), 3),
            "p99": round(float(np.percentile(finite, 99)), 3),
            "max": round(float(finite.max()), 3),
        }
    return {
        "path": str(path),
        "size_xyz": list(img.GetSize()),
        "spacing_xyz_mm": [round(float(v), 4) for v in img.GetSpacing()],
        "intensity": summary,
    }


def window(arr: np.ndarray) -> np.ndarray:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros_like(arr, dtype=np.float32)
    low, high = np.percentile(finite, [1, 99])
    if high <= low:
        high = low + 1.0
    return np.clip((arr - low) / (high - low), 0, 1)


def save_preview(image_path: Path, out_png: Path) -> None:
    img = read_image(image_path)
    arr = sitk.GetArrayFromImage(img).astype(np.float32)
    z, y, x = arr.shape
    cuts = [
        ("Axial", arr[z // 2]),
        ("Coronal", arr[:, y // 2, :][::-1]),
        ("Sagittal", arr[:, :, x // 2][::-1]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for ax, (title, cut) in zip(axes, cuts):
        ax.imshow(window(cut), cmap="gray")
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    fig.suptitle(image_path.name, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def find_cases(input_dir: Path, pattern: str) -> List[Path]:
    return [p for p in sorted(input_dir.rglob(pattern)) if p.is_file() and is_source_ct(p)]


def run_synthsr(
    *,
    ct_path: Path,
    out_path: Path,
    command: str,
    ct: bool,
    cpu: bool,
    disable_sharpening: bool,
    disable_flipping: bool,
    threads: int | None,
    force: bool,
) -> None:
    if out_path.exists() and not force:
        return

    argv = [command, "--i", str(ct_path), "--o", str(out_path)]
    if ct:
        argv.append("--ct")
    if cpu:
        argv.append("--cpu")
    if disable_sharpening:
        argv.append("--disable_sharpening")
    if disable_flipping:
        argv.append("--disable_flipping")
    if threads:
        argv.extend(["--threads", str(threads)])
    subprocess.run(argv, check=True, env=freesurfer_env(command))


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 SynthSR 生成 CT 对应的 synthetic 1mm MP-RAGE 辅助输入")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--pattern", default="*5.00_Hr40*.nii.gz")
    parser.add_argument("--synthsr-command", default="mri_synthsr")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--ct", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--disable-sharpening", action="store_true")
    parser.add_argument("--disable-flipping", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input).resolve()
    if not input_dir.exists():
        print(f"[错误] 不存在: {input_dir}", file=sys.stderr)
        return 2

    cases = find_cases(input_dir, args.pattern)
    if not cases:
        print("[错误] 未找到可处理的 CT 病例", file=sys.stderr)
        return 3

    print(f"[输入] {input_dir}")
    print(f"[模型] synthsr")
    print(f"[病例] {len(cases)}\n")

    for ct_path in cases:
        stem = ct_path.name[:-len(".nii.gz")]
        out_path = ct_path.parent / f"{stem}_synthsr.nii.gz"
        stats_path = ct_path.parent / f"{stem}_synthsr_stats.json"
        preview_path = ct_path.parent / f"{stem}_synthsr_preview.png"

        print(f"=== Case: {ct_path.name} ===")
        run_synthsr(
            ct_path=ct_path,
            out_path=out_path,
            command=args.synthsr_command,
            ct=args.ct,
            cpu=args.cpu,
            disable_sharpening=args.disable_sharpening,
            disable_flipping=args.disable_flipping,
            threads=args.threads,
            force=args.force,
        )
        payload = {
            "source": "synthsr",
            "ct_file": ct_path.name,
            "note": "Synthetic 1mm MP-RAGE auxiliary output generated from CT; not a HU-preserving CT volume.",
            "input": image_stats(ct_path),
            "output": image_stats(out_path),
        }
        stats_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        save_preview(out_path, preview_path)
        print(f"  -> {out_path.name}")
        print(f"  -> {stats_path.name}")
        print(f"  -> {preview_path.name}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
