# -*- coding: utf-8 -*-
r"""
脑室分割（CT，纯几何方法）

输入：
  - CT NIfTI（推荐 *Hr40*.nii.gz，含 HU 值）
  - 同一病例的颅腔 mask（intracranial_mask）

输出：
  *_ventricle_mask.nii.gz     脑室二值 mask
  *_ventricle_overlay.png     三视图：CT + 颅骨白 + 脑室青色 + 血肿红
  *_ventricle_3d.png          3D：颅骨半透明 + 脑室青色 + 血肿红
  *_ventricle_stats.json      体积 + 各连通域统计 + 双侧侧脑室对称性指标

算法：
  1) 颅腔内 HU ∈ [hu_low, hu_high]  ->  候选脑脊液
  2) 开运算去噪
  3) 距颅骨 >= dist_mm 过滤（去脑沟/蛛网膜下腔贴近骨内板的部分）
  4) 3D 连通域分析
  5) 仅保留体积 >= min_volume_ml 的几个最大连通域（典型：双侧侧脑室 + 第三脑室 +/- 第四脑室）

依赖：
  pip install -r requirements.txt
  (SimpleITK, numpy, scipy, scikit-image, matplotlib, nibabel)

用法：
  python ventricle_segmentation.py
  python ventricle_segmentation.py --hu-low -10 --hu-high 18
  python ventricle_segmentation.py --min-volume-ml 0.3 --keep-top-n 4
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


# ---------- 安全 IO ----------
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
    lps = np.eye(4)
    lps[:3, :3] = rot
    lps[:3, 3] = origin
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    return flip @ lps


def read_nifti(path: Path) -> sitk.Image:
    nii = nib.load(str(path))
    arr_xyz = np.asarray(nii.dataobj)
    arr_zyx = np.transpose(arr_xyz, (2, 1, 0)).copy()
    img = sitk.GetImageFromArray(arr_zyx)
    o, s, d = _nib_affine_to_sitk(nii.affine)
    img.SetOrigin(o); img.SetSpacing(s); img.SetDirection(d)
    return img


def write_nifti(img: sitk.Image, path: Path) -> None:
    arr_zyx = sitk.GetArrayFromImage(img)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    affine = _sitk_to_nib_affine(img)
    nib.save(nib.Nifti1Image(arr_xyz, affine), str(path))


# ---------- 核心算法 ----------
def segment_ventricle(ct_hu: np.ndarray, intracranial: np.ndarray, skull: np.ndarray,
                      spacing: Tuple[float, float, float],
                      hu_low: float = -10.0, hu_high: float = 18.0,
                      open_radius: int = 1,
                      skull_distance_mm: float = 6.0,
                      min_volume_ml: float = 0.3,
                      keep_top_n: int = 5) -> Tuple[np.ndarray, List[Dict]]:
    """
    从 CT 提取脑室 mask。

    思路：
      颅腔 ∩ HU∈[hu_low, hu_high]  -> 候选 CSF
      去除距骨 < skull_distance_mm 的部分（避免脑沟/蛛网膜下腔 CSF）
      3D 开运算去噪
      连通域分析 -> 保留体积 >= min_volume_ml 的前 keep_top_n 个最大连通域
    """
    voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])

    cand = intracranial.astype(bool) & (ct_hu >= hu_low) & (ct_hu <= hu_high)

    # 距骨过滤：脑沟里的 CSF 紧贴颅骨，应剔除
    if skull_distance_mm > 0 and skull.any():
        dist = ndi.distance_transform_edt(
            ~skull.astype(bool),
            sampling=(spacing[2], spacing[1], spacing[0]))
        cand &= (dist >= skull_distance_mm)

    if open_radius > 0:
        cand = ndi.binary_opening(cand, structure=morphology.ball(open_radius))

    labels, n_cc = ndi.label(cand, structure=np.ones((3, 3, 3), dtype=np.uint8))
    if n_cc == 0:
        return np.zeros_like(cand, dtype=np.uint8), []

    sizes = ndi.sum(cand, labels, index=np.arange(1, n_cc + 1))
    order = np.argsort(sizes)[::-1]
    min_vox = int(np.ceil(min_volume_ml * 1000.0 / voxel_mm3))

    keep = np.zeros_like(cand, dtype=bool)
    regions: List[Dict] = []
    kept_count = 0
    for idx_in_order in order:
        if kept_count >= keep_top_n:
            break
        idx = int(idx_in_order) + 1
        sz = int(sizes[idx_in_order])
        if sz < min_vox:
            break
        comp = (labels == idx)
        keep |= comp
        zs, ys, xs = np.where(comp)
        vol_ml = float(sz * voxel_mm3 / 1000.0)
        regions.append({
            "label": int(idx),
            "voxels": sz,
            "volume_ml": round(vol_ml, 3),
            "centroid_ijk_zyx": [round(float(zs.mean()), 2),
                                 round(float(ys.mean()), 2),
                                 round(float(xs.mean()), 2)],
            "bbox_zyx": {
                "z": [int(zs.min()), int(zs.max())],
                "y": [int(ys.min()), int(ys.max())],
                "x": [int(xs.min()), int(xs.max())],
            },
            "mean_hu": round(float(ct_hu[comp].mean()), 2),
        })
        kept_count += 1
    return keep.astype(np.uint8), regions


# ---------- 可视化 ----------
def window_image(arr: np.ndarray, wl=40, ww=80):
    lo, hi = wl - ww / 2, wl + ww / 2
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def _overlay_rgba(mask: np.ndarray, rgba: Tuple[float, float, float, float]) -> np.ndarray:
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[..., 0] = rgba[0]; out[..., 1] = rgba[1]; out[..., 2] = rgba[2]
    out[..., 3] = mask.astype(np.float32) * rgba[3]
    return out


def save_overlay(ct: np.ndarray, skull: np.ndarray, ventricle: np.ndarray,
                 hematoma: Optional[np.ndarray],
                 out_png: Path, spacing: Tuple[float, float, float], title: str,
                 focus_z: Optional[int] = None) -> None:
    z, y, x = ct.shape
    if focus_z is None:
        # 优先把视图定位到脑室最厚的切片
        if ventricle.sum() > 0:
            focus_z = int(np.argmax(ventricle.sum(axis=(1, 2))))
        else:
            focus_z = z // 2

    cuts = [
        ("Axial", focus_z,
         ct[focus_z], skull[focus_z], ventricle[focus_z],
         hematoma[focus_z] if hematoma is not None else None, 1.0),
        ("Coronal", y // 2,
         ct[:, y // 2, :][::-1], skull[:, y // 2, :][::-1], ventricle[:, y // 2, :][::-1],
         hematoma[:, y // 2, :][::-1] if hematoma is not None else None,
         spacing[2] / spacing[1] if spacing[1] else 1.0),
        ("Sagittal", x // 2,
         ct[:, :, x // 2][::-1], skull[:, :, x // 2][::-1], ventricle[:, :, x // 2][::-1],
         hematoma[:, :, x // 2][::-1] if hematoma is not None else None,
         spacing[2] / spacing[0] if spacing[0] else 1.0),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax_obj, (name, idx, ct_s, sk_s, ve_s, he_s, aspect) in zip(axes, cuts):
        ax_obj.imshow(window_image(ct_s), cmap="gray", aspect=aspect)
        ax_obj.imshow(_overlay_rgba(sk_s, (1.0, 1.0, 1.0, 0.30)), aspect=aspect)
        ax_obj.imshow(_overlay_rgba(ve_s, (0.20, 0.85, 0.95, 0.55)), aspect=aspect)  # 青色 = 脑室
        if he_s is not None:
            ax_obj.imshow(_overlay_rgba(he_s, (1.0, 0.15, 0.15, 0.65)), aspect=aspect)
        ax_obj.set_title(f"{name}  idx={idx}", fontsize=11); ax_obj.axis("off")

    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def save_3d(skull: np.ndarray, ventricle: np.ndarray, hematoma: Optional[np.ndarray],
            spacing: Tuple[float, float, float],
            out_png: Path, title: str, downsample: int = 3) -> None:
    if skull.sum() == 0:
        print("  [3D] skull 为空，跳过"); return

    def _ds(arr): return arr[::downsample, ::downsample, ::downsample] if downsample > 1 else arr
    sp = (spacing[0] * downsample, spacing[1] * downsample, spacing[2] * downsample)

    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection="3d")
    all_v = None

    # 颅骨
    v_sk, f_sk, _, _ = measure.marching_cubes(_ds(skull).astype(np.uint8), level=0.5,
                                              spacing=(sp[2], sp[1], sp[0]),
                                              allow_degenerate=False)
    m_sk = Poly3DCollection(v_sk[f_sk], alpha=0.10, linewidths=0)
    m_sk.set_facecolor((0.95, 0.92, 0.85)); m_sk.set_edgecolor("none")
    ax.add_collection3d(m_sk); all_v = v_sk

    # 脑室（青色）
    if ventricle.sum() > 0:
        v_v, f_v, _, _ = measure.marching_cubes(_ds(ventricle).astype(np.uint8), level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]),
                                                allow_degenerate=False)
        m_v = Poly3DCollection(v_v[f_v], alpha=0.70, linewidths=0)
        m_v.set_facecolor((0.20, 0.75, 0.90)); m_v.set_edgecolor("none")
        ax.add_collection3d(m_v); all_v = np.vstack([all_v, v_v])

    # 血肿（红色）
    if hematoma is not None and hematoma.sum() > 0:
        v_h, f_h, _, _ = measure.marching_cubes(_ds(hematoma).astype(np.uint8), level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]),
                                                allow_degenerate=False)
        m_h = Poly3DCollection(v_h[f_h], alpha=0.95, linewidths=0)
        m_h.set_facecolor((0.95, 0.15, 0.15)); m_h.set_edgecolor("none")
        ax.add_collection3d(m_h); all_v = np.vstack([all_v, v_h])

    ax.set_xlim(all_v[:, 0].min(), all_v[:, 0].max())
    ax.set_ylim(all_v[:, 1].min(), all_v[:, 1].max())
    ax.set_zlim(all_v[:, 2].min(), all_v[:, 2].max())
    ax.set_xlabel("Z (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("X (mm)")
    ax.view_init(elev=20, azim=-70)
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)


# ---------- 主流程 ----------
def find_cases(input_dir: Path, ct_pattern: str) -> List[Dict[str, Path]]:
    """需要 CT + skull + intracranial mask；hematoma 可选（仅用于可视化）。"""
    cases: List[Dict[str, Path]] = []
    EXCLUDE = ("_mask.nii.gz", "_overlay.png", "_3d.png", "_preview.png", "_stats.txt", "_report.json")
    for ct in sorted(input_dir.rglob(ct_pattern)):
        if any(ct.name.endswith(s) for s in EXCLUDE):
            continue
        d = ct.parent
        stem = ct.name[:-len(".nii.gz")]
        intra_p = d / f"{stem}_intracranial_mask.nii.gz"
        if not intra_p.exists():
            continue
        skull_list = sorted(d.glob("*_skull_mask.nii.gz"))
        if not skull_list:
            continue
        skull_list.sort(key=lambda p: (0 if "0.80" in p.name else 1, p.name))
        hema_p = d / f"{stem}_hematoma_mask.nii.gz"
        cases.append({
            "ct": ct,
            "intracranial": intra_p,
            "skull": skull_list[0],
            "hematoma": hema_p if hema_p.exists() else None,
        })
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="脑室分割（CT 几何方法）")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz")
    ap.add_argument("--hu-low",  type=float, default=-10.0, help="CSF HU 下限")
    ap.add_argument("--hu-high", type=float, default=18.0,  help="CSF HU 上限")
    ap.add_argument("--open-radius", type=int, default=1)
    ap.add_argument("--skull-distance-mm", type=float, default=6.0,
                    help="距颅骨小于该距离的 CSF 被剔除（剔除脑沟/蛛网膜下腔）")
    ap.add_argument("--min-volume-ml", type=float, default=0.3)
    ap.add_argument("--keep-top-n", type=int, default=5,
                    help="保留前 N 个最大连通域（双侧侧脑室+3+4 脑室通常 2-4 个）")
    ap.add_argument("--no-3d", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cases = find_cases(in_dir, args.pattern)
    if not cases:
        print(f"[错误] 未找到 (CT + intracranial + skull) 配对", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(cases)} 例")
    print(f"[参数] HU [{args.hu_low}, {args.hu_high}]  距骨>= {args.skull_distance_mm}mm  "
          f"min_vol={args.min_volume_ml}mL  top_n={args.keep_top_n}\n")

    n_ok, n_err = 0, 0
    for c in cases:
        ct_path: Path = c["ct"]
        print(f"=== Case: {ct_path.name} ===")
        try:
            ct_img    = read_nifti(ct_path)
            skull_img = read_nifti(c["skull"])
            intra_img = read_nifti(c["intracranial"])
            hema_img  = read_nifti(c["hematoma"]) if c["hematoma"] else None

            # 几何对齐
            for tag, im_ref in [("skull", skull_img), ("intra", intra_img),
                                ("hema",  hema_img)]:
                if im_ref is None: continue
                if im_ref.GetSize() != ct_img.GetSize():
                    rs = sitk.ResampleImageFilter()
                    rs.SetReferenceImage(ct_img); rs.SetInterpolator(sitk.sitkNearestNeighbor)
                    if tag == "skull": skull_img = rs.Execute(skull_img)
                    elif tag == "intra": intra_img = rs.Execute(intra_img)
                    elif tag == "hema": hema_img = rs.Execute(hema_img)
                    print(f"  [警告] 重采样 {tag} mask -> CT 网格")

            ct_arr    = sitk.GetArrayFromImage(ct_img).astype(np.float32)
            skull_arr = (sitk.GetArrayFromImage(skull_img) > 0).astype(np.uint8)
            intra_arr = (sitk.GetArrayFromImage(intra_img) > 0).astype(np.uint8)
            hema_arr  = (sitk.GetArrayFromImage(hema_img)  > 0).astype(np.uint8) if hema_img else None
            spacing = ct_img.GetSpacing()
            voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])

            print("  -> 提取脑室 ...")
            vent, regions = segment_ventricle(
                ct_arr, intra_arr, skull_arr, spacing,
                hu_low=args.hu_low, hu_high=args.hu_high,
                open_radius=args.open_radius,
                skull_distance_mm=args.skull_distance_mm,
                min_volume_ml=args.min_volume_ml,
                keep_top_n=args.keep_top_n,
            )

            # 写出
            base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
            mask_p = base.parent / f"{base.name}_ventricle_mask.nii.gz"
            ovr_p  = base.parent / f"{base.name}_ventricle_overlay.png"
            d3_p   = base.parent / f"{base.name}_ventricle_3d.png"
            stats_p = base.parent / f"{base.name}_ventricle_stats.json"

            mask_img = sitk.GetImageFromArray(vent.astype(np.uint8))
            mask_img.CopyInformation(ct_img)
            write_nifti(mask_img, mask_p)

            total_vol = float(vent.sum() * voxel_mm3 / 1000.0)
            print(f"  -> mask: {mask_p.name}  total {total_vol:.2f} mL  连通域={len(regions)}")
            for i, r in enumerate(regions, 1):
                print(f"     #{i}  vol={r['volume_ml']} mL  meanHU={r['mean_hu']}  "
                      f"centroid_zyx={r['centroid_ijk_zyx']}")

            save_overlay(ct_arr, skull_arr, vent, hema_arr, ovr_p, spacing,
                         title=f"{base.name}  ventricle overlay (cyan=ventricle, red=hematoma)")
            print(f"  -> overlay: {ovr_p.name}")
            if not args.no_3d:
                save_3d(skull_arr, vent, hema_arr, spacing, d3_p,
                        title=f"{base.name}  skull(translucent) + ventricle(cyan) + hematoma(red)")
                print(f"  -> 3D: {d3_p.name}")

            with open(stats_p, "w", encoding="utf-8") as f:
                json.dump({
                    "ct_file": ct_path.name,
                    "spacing_mm": [round(float(v), 4) for v in spacing],
                    "voxel_mm3": round(voxel_mm3, 6),
                    "params": {
                        "hu_range": [args.hu_low, args.hu_high],
                        "skull_distance_mm": args.skull_distance_mm,
                        "min_volume_ml": args.min_volume_ml,
                        "open_radius": args.open_radius,
                        "keep_top_n": args.keep_top_n,
                    },
                    "total_volume_ml": round(total_vol, 3),
                    "n_components": len(regions),
                    "components": regions,
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
