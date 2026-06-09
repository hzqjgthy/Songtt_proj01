# -*- coding: utf-8 -*-
r"""
脑干分割（CT 几何近似，无需训练）

⚠️ 说明：
  脑干在 CT 上与周围脑实质 HU 完全重叠，无法直接阈值分割。
  本脚本基于"颅腔几何位置 + HU 约束"做保守近似，输出的 mask 用作路径规划禁区。
  接入 MRI 时应替换为 FastSurfer / TotalSegmentator 的精确分割。

输入：
  - CT NIfTI（推荐 *Hr40*.nii.gz）
  - 颅腔 mask  *_intracranial_mask.nii.gz
  - 脑组织 mask *_brain_mask.nii.gz
  - 颅骨 mask  *_skull_mask.nii.gz
  可选：
  - 脑室 mask  *_ventricle_mask.nii.gz （用于辅助定位上界）
  - 血肿 mask  *_hematoma_mask.nii.gz   （从禁区扣除）

输出：
  *_brainstem_mask.nii.gz   脑干二值 mask
  *_brainstem_overlay.png   三视图 + 脑干（橄榄色）
  *_brainstem_3d.png        3D 渲染
  *_brainstem_stats.json    体积/质心/几何参数

算法：
  1) 颅腔下部 lower_z_ratio（默认 0.30）
  2) 中线 ±lateral_mm（默认 25mm）横向带
  3) 中前 / 中后 ±ap_mm（默认 25mm）纵向带（y 取颅腔中位）
  4) ∩ 脑组织 ∩ HU∈[hu_low, hu_high]
  5) 排除脑室与血肿
  6) 闭运算 -> 最大连通域

依赖：见 requirements.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    print("[错误] 未安装 SimpleITK", file=sys.stderr); raise
try:
    import nibabel as nib
except ImportError:
    print("[错误] 未安装 nibabel", file=sys.stderr); raise
try:
    from scipy import ndimage as ndi
except ImportError:
    print("[错误] 未安装 scipy", file=sys.stderr); raise
try:
    from skimage import measure, morphology
except ImportError:
    print("[错误] 未安装 scikit-image", file=sys.stderr); raise
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: F401
    for _fname in ("Microsoft YaHei", "SimHei", "DengXian", "SimSun"):
        try:
            font_manager.findfont(_fname, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [_fname]; break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
except ImportError:
    print("[错误] 未安装 matplotlib", file=sys.stderr); raise


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"


# ---------- IO ----------
def _nib_affine_to_sitk(affine):
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    lps = flip @ affine
    rot = lps[:3, :3]
    origin = tuple(float(v) for v in lps[:3, 3])
    spacing = tuple(float(np.linalg.norm(rot[:, i])) for i in range(3))
    direction_mat = np.zeros((3, 3), dtype=np.float64)
    for i in range(3):
        if spacing[i] > 0:
            direction_mat[:, i] = rot[:, i] / spacing[i]
        else:
            direction_mat[i, i] = 1.0
    return origin, spacing, tuple(float(v) for v in direction_mat.flatten(order="C"))


def _sitk_to_nib_affine(img):
    spacing = np.array(img.GetSpacing(), dtype=np.float64)
    origin = np.array(img.GetOrigin(), dtype=np.float64)
    direction = np.array(img.GetDirection(), dtype=np.float64).reshape(3, 3)
    rot = direction @ np.diag(spacing)
    lps = np.eye(4); lps[:3, :3] = rot; lps[:3, 3] = origin
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    return flip @ lps


def read_nifti(path: Path) -> sitk.Image:
    nii = nib.load(str(path))
    arr = np.asarray(nii.dataobj)
    arr_zyx = np.transpose(arr, (2, 1, 0)).copy()
    img = sitk.GetImageFromArray(arr_zyx)
    o, s, d = _nib_affine_to_sitk(nii.affine)
    img.SetOrigin(o); img.SetSpacing(s); img.SetDirection(d)
    return img


def write_nifti(img: sitk.Image, path: Path) -> None:
    arr_zyx = sitk.GetArrayFromImage(img)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    nib.save(nib.Nifti1Image(arr_xyz, _sitk_to_nib_affine(img)), str(path))


# ---------- 核心算法 ----------
def segment_brainstem(ct_hu: np.ndarray, intracranial: np.ndarray,
                      brain: np.ndarray,
                      ventricle: Optional[np.ndarray],
                      hematoma: Optional[np.ndarray],
                      spacing: Tuple[float, float, float],
                      lower_z_ratio: float = 0.30,
                      lateral_mm: float = 25.0,
                      ap_mm: float = 25.0,
                      hu_low: float = 20.0,
                      hu_high: float = 50.0,
                      close_radius: int = 2) -> Tuple[np.ndarray, Dict]:
    """
    几何近似脑干 mask。
    """
    sz_mm, sy_mm, sx_mm = spacing[2], spacing[1], spacing[0]
    voxel_mm3 = sz_mm * sy_mm * sx_mm

    intra = intracranial.astype(bool)
    z_dim, y_dim, x_dim = intra.shape

    zs, ys, xs = np.where(intra)
    if len(zs) == 0:
        return np.zeros_like(intra, dtype=np.uint8), {}

    z_min, z_max = int(zs.min()), int(zs.max())
    z_thresh = int(z_min + lower_z_ratio * (z_max - z_min))
    x_center = int(np.median(xs))
    y_center = int(np.median(ys))

    lat_v = max(int(round(lateral_mm / sx_mm)), 1)
    ap_v  = max(int(round(ap_mm / sy_mm)), 1)

    z_idx = np.arange(z_dim)[:, None, None]
    y_idx = np.arange(y_dim)[None, :, None]
    x_idx = np.arange(x_dim)[None, None, :]

    box = ((z_idx <= z_thresh) &
           (np.abs(x_idx - x_center) <= lat_v) &
           (np.abs(y_idx - y_center) <= ap_v))
    box = np.broadcast_to(box, intra.shape)

    cand = intra & box & brain.astype(bool) & (ct_hu >= hu_low) & (ct_hu <= hu_high)

    # 排除脑室和血肿
    if ventricle is not None:
        cand &= ~ventricle.astype(bool)
    if hematoma is not None:
        cand &= ~hematoma.astype(bool)

    # 形态学闭合 -> 最大连通域
    if close_radius > 0:
        cand = ndi.binary_closing(cand, structure=morphology.ball(close_radius))
    cand &= intra  # 闭运算可能膨出，再约束回颅腔

    labels, n_cc = ndi.label(cand, structure=np.ones((3, 3, 3), dtype=np.uint8))
    if n_cc == 0:
        return np.zeros_like(intra, dtype=np.uint8), {}
    sizes = ndi.sum(cand, labels, index=np.arange(1, n_cc + 1))
    largest = int(np.argmax(sizes)) + 1
    mask = (labels == largest).astype(np.uint8)

    # 统计
    zs2, ys2, xs2 = np.where(mask)
    stats = {
        "voxels": int(mask.sum()),
        "volume_ml": round(float(mask.sum()) * voxel_mm3 / 1000.0, 3),
        "centroid_ijk_zyx": [round(float(zs2.mean()), 2),
                             round(float(ys2.mean()), 2),
                             round(float(xs2.mean()), 2)],
        "bbox_zyx": {
            "z": [int(zs2.min()), int(zs2.max())],
            "y": [int(ys2.min()), int(ys2.max())],
            "x": [int(xs2.min()), int(xs2.max())],
        },
        "mean_hu": round(float(ct_hu[mask.astype(bool)].mean()), 2),
        "params": {
            "lower_z_ratio": lower_z_ratio,
            "lateral_mm": lateral_mm,
            "ap_mm": ap_mm,
            "hu_range": [hu_low, hu_high],
            "close_radius": close_radius,
        },
    }
    return mask, stats


# ---------- 可视化 ----------
def window_image(arr, wl=40, ww=80):
    lo, hi = wl - ww/2, wl + ww/2
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def overlay_color(mask, rgb_a):
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[..., 0] = rgb_a[0]; out[..., 1] = rgb_a[1]; out[..., 2] = rgb_a[2]
    out[..., 3] = mask.astype(np.float32) * rgb_a[3]
    return out


def save_overlay(ct, skull, brainstem, ventricle, hematoma,
                 out_png, spacing, title):
    z, y, x = ct.shape
    if brainstem.sum() > 0:
        cz = int(np.argmax(brainstem.sum(axis=(1, 2))))
    else:
        cz = z // 2

    cuts = [
        ("Axial", cz, ct[cz], skull[cz], brainstem[cz],
         ventricle[cz] if ventricle is not None else None,
         hematoma[cz] if hematoma is not None else None, 1.0),
        ("Coronal", y // 2, ct[:, y//2, :][::-1], skull[:, y//2, :][::-1],
         brainstem[:, y//2, :][::-1],
         ventricle[:, y//2, :][::-1] if ventricle is not None else None,
         hematoma[:, y//2, :][::-1] if hematoma is not None else None,
         spacing[2]/spacing[1] if spacing[1] else 1.0),
        ("Sagittal", x // 2, ct[:, :, x//2][::-1], skull[:, :, x//2][::-1],
         brainstem[:, :, x//2][::-1],
         ventricle[:, :, x//2][::-1] if ventricle is not None else None,
         hematoma[:, :, x//2][::-1] if hematoma is not None else None,
         spacing[2]/spacing[0] if spacing[0] else 1.0),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax_obj, (name, idx, c, sk, bs, ve, he, aspect) in zip(axes, cuts):
        ax_obj.imshow(window_image(c), cmap="gray", aspect=aspect)
        ax_obj.imshow(overlay_color(sk, (1.0, 1.0, 1.0, 0.30)), aspect=aspect)
        if ve is not None:
            ax_obj.imshow(overlay_color(ve, (0.20, 0.85, 0.95, 0.35)), aspect=aspect)
        ax_obj.imshow(overlay_color(bs, (0.55, 0.40, 0.10, 0.65)), aspect=aspect)  # 棕色 = 脑干
        if he is not None:
            ax_obj.imshow(overlay_color(he, (1.0, 0.15, 0.15, 0.65)), aspect=aspect)
        ax_obj.set_title(f"{name}  idx={idx}", fontsize=11); ax_obj.axis("off")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout(); fig.savefig(out_png, dpi=120, bbox_inches="tight"); plt.close(fig)


def save_3d(skull, brainstem, ventricle, hematoma, spacing, out_png, title, downsample=3):
    if skull.sum() == 0: return

    def _ds(arr): return arr[::downsample, ::downsample, ::downsample] if downsample > 1 else arr
    sp = (spacing[0]*downsample, spacing[1]*downsample, spacing[2]*downsample)

    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection="3d")
    all_v = None

    v, f, _, _ = measure.marching_cubes(_ds(skull).astype(np.uint8), level=0.5,
                                        spacing=(sp[2], sp[1], sp[0]),
                                        allow_degenerate=False)
    m = Poly3DCollection(v[f], alpha=0.10, linewidths=0)
    m.set_facecolor((0.95, 0.92, 0.85)); m.set_edgecolor("none")
    ax.add_collection3d(m); all_v = v

    if ventricle is not None and ventricle.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(ventricle).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]), allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.30, linewidths=0)
        m.set_facecolor((0.20, 0.75, 0.90)); m.set_edgecolor("none")
        ax.add_collection3d(m); all_v = np.vstack([all_v, v])

    if brainstem.sum() > 0:
        ds_bs = _ds(brainstem).astype(np.uint8)
        if ds_bs.max() > 0 and ds_bs.min() < 1:
            v, f, _, _ = measure.marching_cubes(ds_bs, level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]), allow_degenerate=False)
            m = Poly3DCollection(v[f], alpha=0.85, linewidths=0)
            m.set_facecolor((0.55, 0.40, 0.10)); m.set_edgecolor("none")
            ax.add_collection3d(m); all_v = np.vstack([all_v, v])

    if hematoma is not None and hematoma.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(hematoma).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]), allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.95, linewidths=0)
        m.set_facecolor((0.95, 0.15, 0.15)); m.set_edgecolor("none")
        ax.add_collection3d(m); all_v = np.vstack([all_v, v])

    ax.set_xlim(all_v[:, 0].min(), all_v[:, 0].max())
    ax.set_ylim(all_v[:, 1].min(), all_v[:, 1].max())
    ax.set_zlim(all_v[:, 2].min(), all_v[:, 2].max())
    ax.set_xlabel("Z (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("X (mm)")
    ax.view_init(elev=20, azim=-70)
    try: ax.set_box_aspect((1, 1, 1))
    except Exception: pass
    ax.set_title(title, fontsize=10)
    fig.tight_layout(); fig.savefig(out_png, dpi=110, bbox_inches="tight"); plt.close(fig)


# ---------- 主流程 ----------
def find_cases(input_dir: Path, ct_pattern: str) -> List[Dict[str, Path]]:
    cases: List[Dict[str, Path]] = []
    EXCLUDE = ("_synthseg.nii.gz", "_mask.nii.gz", "_overlay.png", "_3d.png", "_preview.png", "_stats.txt", "_report.json")
    for ct in sorted(input_dir.rglob(ct_pattern)):
        if any(ct.name.endswith(s) for s in EXCLUDE): continue
        d = ct.parent
        stem = ct.name[:-len(".nii.gz")]
        intra_p = d / f"{stem}_intracranial_mask.nii.gz"
        brain_p = d / f"{stem}_brain_mask.nii.gz"
        if not (intra_p.exists() and brain_p.exists()): continue
        skull_list = sorted(d.glob("*_skull_mask.nii.gz"))
        if not skull_list: continue
        skull_list.sort(key=lambda p: (0 if "0.80" in p.name else 1, p.name))
        vent_p = d / f"{stem}_ventricle_mask.nii.gz"
        hema_p = d / f"{stem}_hematoma_mask.nii.gz"
        cases.append({
            "ct": ct,
            "intracranial": intra_p,
            "brain": brain_p,
            "skull": skull_list[0],
            "ventricle": vent_p if vent_p.exists() else None,
            "hematoma": hema_p if hema_p.exists() else None,
        })
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="脑干分割（CT 几何近似）")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz")
    ap.add_argument("--lower-z-ratio", type=float, default=0.30,
                    help="颅腔下部比例（z <= z_min + ratio*z_range 视为脑干层）")
    ap.add_argument("--lateral-mm", type=float, default=25.0)
    ap.add_argument("--ap-mm", type=float, default=25.0)
    ap.add_argument("--hu-low",  type=float, default=20.0)
    ap.add_argument("--hu-high", type=float, default=50.0)
    ap.add_argument("--close-radius", type=int, default=2)
    ap.add_argument("--no-3d", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cases = find_cases(in_dir, args.pattern)
    if not cases:
        print("[错误] 未找到 (CT + intracranial + brain + skull) 配对", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(cases)} 例")
    print(f"[参数] lower_z={args.lower_z_ratio}  lateral={args.lateral_mm}mm  "
          f"ap={args.ap_mm}mm  HU[{args.hu_low},{args.hu_high}]\n")

    n_ok, n_err = 0, 0
    for c in cases:
        ct_path: Path = c["ct"]
        print(f"=== Case: {ct_path.name} ===")
        try:
            ct_img    = read_nifti(ct_path)
            skull_img = read_nifti(c["skull"])
            intra_img = read_nifti(c["intracranial"])
            brain_img = read_nifti(c["brain"])
            vent_img  = read_nifti(c["ventricle"]) if c["ventricle"] else None
            hema_img  = read_nifti(c["hematoma"]) if c["hematoma"] else None

            for tag, im in [("skull", skull_img), ("intra", intra_img), ("brain", brain_img),
                            ("vent", vent_img), ("hema", hema_img)]:
                if im is None: continue
                if im.GetSize() != ct_img.GetSize():
                    rs = sitk.ResampleImageFilter()
                    rs.SetReferenceImage(ct_img); rs.SetInterpolator(sitk.sitkNearestNeighbor)
                    if tag == "skull": skull_img = rs.Execute(skull_img)
                    elif tag == "intra": intra_img = rs.Execute(intra_img)
                    elif tag == "brain": brain_img = rs.Execute(brain_img)
                    elif tag == "vent": vent_img = rs.Execute(vent_img)
                    elif tag == "hema": hema_img = rs.Execute(hema_img)
                    print(f"  [警告] 重采样 {tag} -> CT 网格")

            ct = sitk.GetArrayFromImage(ct_img).astype(np.float32)
            skull = (sitk.GetArrayFromImage(skull_img) > 0).astype(np.uint8)
            intra = (sitk.GetArrayFromImage(intra_img) > 0).astype(np.uint8)
            brain = (sitk.GetArrayFromImage(brain_img) > 0).astype(np.uint8)
            vent  = (sitk.GetArrayFromImage(vent_img) > 0).astype(np.uint8) if vent_img else None
            hema  = (sitk.GetArrayFromImage(hema_img) > 0).astype(np.uint8) if hema_img else None
            spacing = ct_img.GetSpacing()

            print("  -> 提取脑干 ...")
            mask, stats = segment_brainstem(
                ct, intra, brain, vent, hema, spacing,
                lower_z_ratio=args.lower_z_ratio,
                lateral_mm=args.lateral_mm, ap_mm=args.ap_mm,
                hu_low=args.hu_low, hu_high=args.hu_high,
                close_radius=args.close_radius,
            )

            base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
            mask_p = base.parent / f"{base.name}_brainstem_mask.nii.gz"
            ovr_p  = base.parent / f"{base.name}_brainstem_overlay.png"
            d3_p   = base.parent / f"{base.name}_brainstem_3d.png"
            stats_p = base.parent / f"{base.name}_brainstem_stats.json"

            mask_img = sitk.GetImageFromArray(mask.astype(np.uint8))
            mask_img.CopyInformation(ct_img)
            write_nifti(mask_img, mask_p)
            print(f"  -> mask: {mask_p.name}  vol={stats.get('volume_ml', 0)} mL")

            save_overlay(ct, skull, mask, vent, hema, ovr_p, spacing,
                         title=f"{base.name}  brainstem (brown)")
            print(f"  -> overlay: {ovr_p.name}")
            if not args.no_3d:
                save_3d(skull, mask, vent, hema, spacing, d3_p,
                        title=f"{base.name}  skull(t) + ventricle(cyan) + brainstem(brown) + hematoma(red)")
                print(f"  -> 3D: {d3_p.name}")

            with open(stats_p, "w", encoding="utf-8") as f:
                json.dump({
                    "ct_file": ct_path.name,
                    "spacing_mm": [round(float(v), 4) for v in spacing],
                    **stats,
                }, f, ensure_ascii=False, indent=2)
            print(f"  -> stats: {stats_p.name}")
            n_ok += 1
        except Exception as e:
            n_err += 1
            print(f"  [错误] {e}")
            import traceback; traceback.print_exc()
        print()

    print(f"========== 完成 ==========\n  成功: {n_ok}  失败: {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
