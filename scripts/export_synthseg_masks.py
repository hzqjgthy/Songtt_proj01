# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    print("[错误] 未安装 SimpleITK", file=sys.stderr)
    raise

try:
    from scipy import ndimage as ndi
except ImportError:
    print("[错误] 未安装 scipy", file=sys.stderr)
    raise

import ventricle_segmentation as vent_logic
import brainstem_segmentation as brainstem_logic


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"
EXCLUDE_SUFFIXES = (
    "_synthseg.nii.gz",
    "_mask.nii.gz",
    "_overlay.png",
    "_3d.png",
    "_preview.png",
    "_stats.txt",
    "_report.json",
)

VENTRICLE_LABELS = {4, 5, 14, 15, 43, 44}
BRAINSTEM_LABELS = {16}


def freesurfer_env(command: str) -> Dict[str, str]:
    env = os.environ.copy()
    command_path = Path(command)
    fs_home = env.get("FREESURFER_HOME")
    if not fs_home and command_path.is_absolute() and command_path.parent.name == "bin":
        fs_home = str(command_path.parent.parent)
    if fs_home:
        fs_home_path = Path(fs_home)
        env.setdefault("FREESURFER_HOME", str(fs_home_path))
        env.setdefault("FSFAST_HOME", str(fs_home_path / "fsfast"))
        env.setdefault("SUBJECTS_DIR", str(fs_home_path / "subjects"))
        env.setdefault("MNI_DIR", str(fs_home_path / "mni"))
        env.setdefault("FSF_OUTPUT_FORMAT", "nii.gz")
        env["PATH"] = f"{fs_home_path / 'bin'}:{env.get('PATH', '')}"
    env.setdefault("FS_LICENSE", str(Path.home() / "license.txt"))
    return env


def extract_cc_regions(mask: np.ndarray, ct_hu: np.ndarray, spacing_xyz: Tuple[float, float, float]) -> List[Dict]:
    voxel_mm3 = float(spacing_xyz[0] * spacing_xyz[1] * spacing_xyz[2])
    labels, n_cc = ndi.label(mask.astype(bool), structure=np.ones((3, 3, 3), dtype=np.uint8))
    regions: List[Dict] = []
    if n_cc == 0:
        return regions

    sizes = ndi.sum(mask, labels, index=np.arange(1, n_cc + 1))
    order = np.argsort(sizes)[::-1]
    for order_idx in order:
        cc_idx = int(order_idx) + 1
        comp = labels == cc_idx
        if not comp.any():
            continue
        zs, ys, xs = np.where(comp)
        regions.append(
            {
                "label": cc_idx,
                "voxels": int(comp.sum()),
                "volume_ml": round(float(comp.sum()) * voxel_mm3 / 1000.0, 3),
                "centroid_ijk_zyx": [
                    round(float(zs.mean()), 2),
                    round(float(ys.mean()), 2),
                    round(float(xs.mean()), 2),
                ],
                "bbox_zyx": {
                    "z": [int(zs.min()), int(zs.max())],
                    "y": [int(ys.min()), int(ys.max())],
                    "x": [int(xs.min()), int(xs.max())],
                },
                "mean_hu": round(float(ct_hu[comp].mean()), 2),
            }
        )
    return regions


def build_mask(seg: np.ndarray, labels: Iterable[int]) -> np.ndarray:
    return np.isin(seg, list(labels)).astype(np.uint8)


def ensure_synthseg(
    *,
    ct_path: Path,
    synthseg_path: Path,
    command: str,
    threads: Optional[int],
    cpu: bool,
    ct: bool,
    keepgeom: bool,
    addctab: bool,
    force: bool,
    skip_if_exists: bool,
) -> None:
    if synthseg_path.exists() and skip_if_exists and not force:
        return

    argv: List[str] = [command, "--i", str(ct_path), "--o", str(synthseg_path)]
    vol_csv = synthseg_path.with_name(synthseg_path.name.replace("_synthseg.nii.gz", "_synthseg_volumes.csv"))
    qc_csv = synthseg_path.with_name(synthseg_path.name.replace("_synthseg.nii.gz", "_synthseg_qc.csv"))
    argv.extend(["--vol", str(vol_csv), "--qc", str(qc_csv)])
    if ct:
        argv.append("--ct")
    if cpu:
        argv.append("--cpu")
    if keepgeom:
        argv.append("--keepgeom")
    if addctab:
        argv.append("--addctab")
    if threads:
        argv.extend(["--threads", str(threads)])
    subprocess.run(argv, check=True, env=freesurfer_env(command))


def find_cases(input_dir: Path, ct_pattern: str) -> List[Dict[str, Optional[Path]]]:
    cases: List[Dict[str, Optional[Path]]] = []
    for ct in sorted(input_dir.rglob(ct_pattern)):
        if any(ct.name.endswith(suffix) for suffix in EXCLUDE_SUFFIXES):
            continue
        stem = ct.name[:-len(".nii.gz")]
        d = ct.parent
        skull_list = sorted(d.glob("*_skull_mask.nii.gz"))
        skull = skull_list[0] if skull_list else None
        cases.append(
            {
                "ct": ct,
                "skull": skull,
                "hematoma": d / f"{stem}_hematoma_mask.nii.gz" if (d / f"{stem}_hematoma_mask.nii.gz").exists() else None,
                "ventricle": d / f"{stem}_ventricle_mask.nii.gz" if (d / f"{stem}_ventricle_mask.nii.gz").exists() else None,
                "synthseg": d / f"{stem}_synthseg.nii.gz",
            }
        )
    return cases


def write_mask(mask: np.ndarray, ref_img: sitk.Image, out_path: Path) -> None:
    out_img = sitk.GetImageFromArray(mask.astype(np.uint8))
    out_img.CopyInformation(ref_img)
    vent_logic.write_nifti(out_img, out_path)


def export_ventricle(case: Dict[str, Optional[Path]], ct_img: sitk.Image, ct_arr: np.ndarray, seg_arr: np.ndarray, no_3d: bool) -> None:
    ct_path = case["ct"]
    if ct_path is None:
        return
    base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
    skull_arr = None
    hema_arr = None
    if case.get("skull"):
        skull_arr = (sitk.GetArrayFromImage(vent_logic.read_nifti(case["skull"])) > 0).astype(np.uint8)
    if case.get("hematoma"):
        hema_arr = (sitk.GetArrayFromImage(vent_logic.read_nifti(case["hematoma"])) > 0).astype(np.uint8)

    mask = build_mask(seg_arr, VENTRICLE_LABELS)
    spacing = ct_img.GetSpacing()
    regions = extract_cc_regions(mask, ct_arr, spacing)
    total_volume_ml = round(float(mask.sum()) * spacing[0] * spacing[1] * spacing[2] / 1000.0, 3)

    mask_path = base.parent / f"{base.name}_ventricle_mask.nii.gz"
    stats_path = base.parent / f"{base.name}_ventricle_stats.json"
    overlay_path = base.parent / f"{base.name}_ventricle_overlay.png"
    view3d_path = base.parent / f"{base.name}_ventricle_3d.png"

    write_mask(mask, ct_img, mask_path)
    if skull_arr is not None:
        vent_logic.save_overlay(
            ct_arr,
            skull_arr,
            mask,
            hema_arr,
            overlay_path,
            spacing,
            title=f"{base.name}  ventricle overlay (SynthSeg)",
        )
        if not no_3d:
            vent_logic.save_3d(
                skull_arr,
                mask,
                hema_arr,
                spacing,
                view3d_path,
                title=f"{base.name}  skull + ventricle(SynthSeg)",
            )

    payload = {
        "source": "synthseg",
        "label_ids": sorted(VENTRICLE_LABELS),
        "ct_file": ct_path.name,
        "spacing_mm": [round(float(v), 4) for v in spacing],
        "total_volume_ml": total_volume_ml,
        "regions": regions,
    }
    stats_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_brainstem(case: Dict[str, Optional[Path]], ct_img: sitk.Image, ct_arr: np.ndarray, seg_arr: np.ndarray, no_3d: bool) -> None:
    ct_path = case["ct"]
    if ct_path is None:
        return
    base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
    skull_arr = None
    hema_arr = None
    vent_arr = None
    if case.get("skull"):
        skull_arr = (sitk.GetArrayFromImage(brainstem_logic.read_nifti(case["skull"])) > 0).astype(np.uint8)
    if case.get("hematoma"):
        hema_arr = (sitk.GetArrayFromImage(brainstem_logic.read_nifti(case["hematoma"])) > 0).astype(np.uint8)
    if case.get("ventricle"):
        vent_arr = (sitk.GetArrayFromImage(brainstem_logic.read_nifti(case["ventricle"])) > 0).astype(np.uint8)

    mask = build_mask(seg_arr, BRAINSTEM_LABELS)
    spacing = ct_img.GetSpacing()
    regions = extract_cc_regions(mask, ct_arr, spacing)
    first_region = regions[0] if regions else {}

    mask_path = base.parent / f"{base.name}_brainstem_mask.nii.gz"
    stats_path = base.parent / f"{base.name}_brainstem_stats.json"
    overlay_path = base.parent / f"{base.name}_brainstem_overlay.png"
    view3d_path = base.parent / f"{base.name}_brainstem_3d.png"

    write_mask(mask, ct_img, mask_path)
    if skull_arr is not None:
        brainstem_logic.save_overlay(
            ct_arr,
            skull_arr,
            mask,
            vent_arr,
            hema_arr,
            overlay_path,
            spacing,
            title=f"{base.name}  brainstem overlay (SynthSeg)",
        )
        if not no_3d:
            brainstem_logic.save_3d(
                skull_arr,
                mask,
                vent_arr,
                hema_arr,
                spacing,
                view3d_path,
                title=f"{base.name}  skull + brainstem(SynthSeg)",
            )

    payload = {
        "source": "synthseg",
        "label_ids": sorted(BRAINSTEM_LABELS),
        "ct_file": ct_path.name,
        "spacing_mm": [round(float(v), 4) for v in spacing],
        **first_region,
    }
    stats_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 SynthSeg 导出脑室/脑干 mask")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--pattern", default="*Hr40*.nii.gz")
    parser.add_argument("--targets", nargs="+", choices=["ventricle", "brainstem"], required=True)
    parser.add_argument("--synthseg-command", default="mri_synthseg")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--ct", action="store_true")
    parser.add_argument("--keepgeom", action="store_true")
    parser.add_argument("--addctab", action="store_true")
    parser.add_argument("--skip-if-synthseg-exists", action="store_true")
    parser.add_argument("--force-synthseg", action="store_true")
    parser.add_argument("--no-3d", action="store_true")
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
    print(f"[目标] {', '.join(args.targets)}")
    print(f"[病例] {len(cases)}\n")

    for case in cases:
        ct_path = case["ct"]
        synthseg_path = case["synthseg"]
        if ct_path is None or synthseg_path is None:
            continue
        print(f"=== Case: {ct_path.name} ===")
        ensure_synthseg(
            ct_path=ct_path,
            synthseg_path=synthseg_path,
            command=args.synthseg_command,
            threads=args.threads,
            cpu=args.cpu,
            ct=args.ct,
            keepgeom=args.keepgeom,
            addctab=args.addctab,
            force=args.force_synthseg,
            skip_if_exists=args.skip_if_synthseg_exists,
        )

        ct_img = vent_logic.read_nifti(ct_path)
        seg_img = vent_logic.read_nifti(synthseg_path)
        if seg_img.GetSize() != ct_img.GetSize():
            rs = sitk.ResampleImageFilter()
            rs.SetReferenceImage(ct_img)
            rs.SetInterpolator(sitk.sitkNearestNeighbor)
            seg_img = rs.Execute(seg_img)
            print("  [警告] SynthSeg 输出已重采样到 CT 网格")

        ct_arr = sitk.GetArrayFromImage(ct_img).astype(np.float32)
        seg_arr = sitk.GetArrayFromImage(seg_img).astype(np.int32)

        if "ventricle" in args.targets:
            export_ventricle(case, ct_img, ct_arr, seg_arr, args.no_3d)
            case["ventricle"] = ct_path.parent / f"{ct_path.name[:-len('.nii.gz')]}_ventricle_mask.nii.gz"
            print("  -> ventricle mask exported")
        if "brainstem" in args.targets:
            export_brainstem(case, ct_img, ct_arr, seg_arr, args.no_3d)
            print("  -> brainstem mask exported")
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
