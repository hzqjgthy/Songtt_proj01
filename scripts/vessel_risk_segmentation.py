# -*- coding: utf-8 -*-
r"""
血管风险区域生成（平扫 CT 几何近似）

⚠️ 重要说明：
  当前数据为平扫 CT（无 CTA/MRA），未钙化的动静脉与脑实质 HU 重叠，无法直接阈值分割。
  本脚本生成的是 **"血管风险禁区"**（vessel risk mask），用于路径规划的硬约束，
  组成：
    1) 检测到的颅内高密度结构（钙化血管 / 脉络丛钙化 / 松果体 / 大脑镰钙化）
       —— 颅腔内 HU∈[hu_low, hu_high]（默认 80-250），排除已知血肿
    2) 基于颅腔几何的解剖学先验：
       a) 上矢状窦带 ：颅腔顶部 + 中线 ±sagittal_band_mm
       b) 大脑镰     ：中线 ±falx_mm
       c) 颅底血管区：颅腔最底部 floor_thickness_mm 厚度的环带
                      （包含横窦/乙状窦/Willis 环/基底动脉的大致位置）

  最终输出 vessel_risk_mask.nii.gz（uint8 二值）。
  这是对真正血管的 **保守过近似** —— 路径规划中只要避开它，临床安全性会大幅提升。

  若后续接入 CTA，应替换本脚本为基于强化对比的真实血管分割。

输入：
  - CT NIfTI（推荐 *Hr40*.nii.gz，含 HU）
  - 颅腔 mask *_intracranial_mask.nii.gz
  - 颅骨 mask *_skull_mask.nii.gz （仅用于定位中线/顶/底）
  可选：
  - 血肿 mask *_hematoma_mask.nii.gz （从风险区扣除，避免把血肿当成钙化）

输出：
  *_vessel_risk_mask.nii.gz   血管风险禁区
  *_vessel_overlay.png        三视图：CT + 颅骨白 + 风险区红 + 血肿黄
  *_vessel_3d.png             3D：颅骨半透明 + 风险区红
  *_vessel_stats.json         体积 + 各组成成分体积

依赖：见 requirements.txt

用法：
  python vessel_risk_segmentation.py
  python vessel_risk_segmentation.py --hu-low 80 --hu-high 250
  python vessel_risk_segmentation.py --no-anatomical    # 仅检测高密度结构
  python vessel_risk_segmentation.py --no-detected      # 仅使用解剖先验
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


# ---------- 核心 ----------
def detect_high_density_structures(ct_hu: np.ndarray, intracranial: np.ndarray,
                                   hematoma: Optional[np.ndarray],
                                   hu_low: float = 80.0, hu_high: float = 250.0,
                                   open_radius: int = 1) -> np.ndarray:
    """
    颅腔内 HU∈[hu_low, hu_high]，排除血肿 -> 高密度结构（钙化）。
    """
    cand = intracranial.astype(bool) & (ct_hu >= hu_low) & (ct_hu <= hu_high)
    if hematoma is not None:
        cand &= ~hematoma.astype(bool)
    if open_radius > 0:
        cand = ndi.binary_opening(cand, structure=morphology.ball(open_radius))
    return cand.astype(np.uint8)


def build_anatomical_vessel_prior(intracranial: np.ndarray,
                                  spacing: Tuple[float, float, float],
                                  sagittal_band_mm: float = 10.0,
                                  falx_mm: float = 3.0,
                                  top_band_mm: float = 12.0,
                                  floor_thickness_mm: float = 15.0,
                                  edge_thickness_mm: float = 12.0) -> Dict[str, np.ndarray]:
    """
    基于颅腔几何，构造解剖学血管先验（保守过近似）。

    返回 dict:
      'sagittal_sinus' : 上矢状窦带（顶部 top_band_mm + 中线 ±sagittal_band_mm）
      'falx'           : 大脑镰（中线 ±falx_mm，纵贯前后）
      'floor_ring'     : 颅底血管区（颅腔最底部 floor_thickness 厚度，
                          且距颅腔边界 < edge_thickness_mm）
      'all'            : 上述三者并集
    """
    sz_mm, sy_mm, sx_mm = spacing[2], spacing[1], spacing[0]  # (z,y,x)
    intra = intracranial.astype(bool)
    z_dim, y_dim, x_dim = intra.shape

    # ---- 1) 颅腔的 z/x 范围（用于"顶/底/中线"参考）----
    zs, ys, xs = np.where(intra)
    if len(zs) == 0:
        empty = np.zeros_like(intra)
        return {"sagittal_sinus": empty, "falx": empty, "floor_ring": empty, "all": empty}
    z_top, z_bottom = int(zs.max()), int(zs.min())  # 注意：DICOM 头部通常 z 越大越靠头顶
    # 中线 x：取颅腔每层 x 中位数的全局中位数
    x_center = int(np.median(xs))

    # ---- 2) 上矢状窦带：z 在顶部 top_band_mm 内 + |x - x_center| <= sagittal_band_mm ----
    top_band_voxel = max(int(round(top_band_mm / sz_mm)), 1)
    sag_band_voxel = max(int(round(sagittal_band_mm / sx_mm)), 1)
    z_top_thresh = z_top - top_band_voxel
    sagittal = np.zeros_like(intra)
    z_idx = np.arange(z_dim)[:, None, None]
    x_idx = np.arange(x_dim)[None, None, :]
    sagittal_mask = (z_idx >= z_top_thresh) & (np.abs(x_idx - x_center) <= sag_band_voxel)
    sagittal_mask = np.broadcast_to(sagittal_mask, intra.shape)
    sagittal = (intra & sagittal_mask).astype(np.uint8)

    # ---- 3) 大脑镰：|x - x_center| <= falx_mm，全 z/y ----
    falx_voxel = max(int(round(falx_mm / sx_mm)), 1)
    falx_mask = np.abs(x_idx - x_center) <= falx_voxel
    falx_mask = np.broadcast_to(falx_mask, intra.shape)
    falx = (intra & falx_mask).astype(np.uint8)

    # ---- 4) 颅底血管区：z 在底部 floor_thickness_mm 内 + 距颅腔外缘 <= edge_thickness_mm ----
    floor_voxel = max(int(round(floor_thickness_mm / sz_mm)), 1)
    z_floor_thresh = z_bottom + floor_voxel
    floor_band = np.zeros_like(intra)
    floor_band[(np.arange(z_dim) <= z_floor_thresh)] = True
    # 距颅腔边界距离图（在颅腔内：到非颅腔体素的距离）
    dist_to_edge = ndi.distance_transform_edt(
        intra, sampling=(sz_mm, sy_mm, sx_mm))
    edge_zone = (dist_to_edge <= edge_thickness_mm)
    floor_ring = (intra & floor_band & edge_zone).astype(np.uint8)

    all_mask = (sagittal.astype(bool) | falx.astype(bool) | floor_ring.astype(bool)).astype(np.uint8)

    return {
        "sagittal_sinus": sagittal,
        "falx": falx,
        "floor_ring": floor_ring,
        "all": all_mask,
    }


def build_vessel_risk_mask(ct_hu: np.ndarray, intracranial: np.ndarray, skull: np.ndarray,
                           hematoma: Optional[np.ndarray],
                           spacing: Tuple[float, float, float],
                           hu_low: float = 80.0, hu_high: float = 250.0,
                           use_detected: bool = True,
                           use_anatomical: bool = True,
                           **anat_kwargs) -> Tuple[np.ndarray, Dict]:
    """
    生成完整的血管风险禁区。
    """
    voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])
    parts: Dict[str, np.ndarray] = {}

    if use_detected:
        det = detect_high_density_structures(ct_hu, intracranial, hematoma,
                                             hu_low=hu_low, hu_high=hu_high)
        parts["detected_high_density"] = det

    if use_anatomical:
        priors = build_anatomical_vessel_prior(intracranial, spacing, **anat_kwargs)
        parts["sagittal_sinus"] = priors["sagittal_sinus"]
        parts["falx"] = priors["falx"]
        parts["floor_ring"] = priors["floor_ring"]

    # 合并
    if not parts:
        return np.zeros_like(intracranial, dtype=np.uint8), {}

    merged = np.zeros_like(intracranial, dtype=bool)
    for v in parts.values():
        merged |= v.astype(bool)
    # 限制在颅腔内
    merged &= intracranial.astype(bool)
    # 排除血肿
    if hematoma is not None:
        merged &= ~hematoma.astype(bool)

    stats = {
        "total_volume_ml": round(float(merged.sum()) * voxel_mm3 / 1000.0, 2),
        "components_volume_ml": {
            k: round(float(v.sum()) * voxel_mm3 / 1000.0, 2) for k, v in parts.items()
        },
    }
    return merged.astype(np.uint8), stats


# ---------- 可视化 ----------
def window_image(arr, wl=40, ww=80):
    lo, hi = wl - ww/2, wl + ww/2
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def overlay_color(mask, rgb_a):
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[..., 0] = rgb_a[0]; out[..., 1] = rgb_a[1]; out[..., 2] = rgb_a[2]
    out[..., 3] = mask.astype(np.float32) * rgb_a[3]
    return out


def save_overlay(ct: np.ndarray, skull: np.ndarray, vessel: np.ndarray,
                 hematoma: Optional[np.ndarray],
                 out_png: Path, spacing, title: str) -> None:
    z, y, x = ct.shape
    if hematoma is not None and hematoma.sum() > 0:
        cz = int(np.argmax(hematoma.sum(axis=(1, 2))))
    elif vessel.sum() > 0:
        cz = int(np.argmax(vessel.sum(axis=(1, 2))))
    else:
        cz = z // 2

    cuts = [
        ("Axial", cz,
         ct[cz], skull[cz], vessel[cz], hematoma[cz] if hematoma is not None else None, 1.0),
        ("Coronal", y // 2,
         ct[:, y // 2, :][::-1], skull[:, y // 2, :][::-1],
         vessel[:, y // 2, :][::-1],
         hematoma[:, y // 2, :][::-1] if hematoma is not None else None,
         spacing[2] / spacing[1] if spacing[1] else 1.0),
        ("Sagittal", x // 2,
         ct[:, :, x // 2][::-1], skull[:, :, x // 2][::-1],
         vessel[:, :, x // 2][::-1],
         hematoma[:, :, x // 2][::-1] if hematoma is not None else None,
         spacing[2] / spacing[0] if spacing[0] else 1.0),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax_obj, (name, idx, c_s, sk_s, ve_s, he_s, aspect) in zip(axes, cuts):
        ax_obj.imshow(window_image(c_s), cmap="gray", aspect=aspect)
        ax_obj.imshow(overlay_color(sk_s, (1.0, 1.0, 1.0, 0.30)), aspect=aspect)
        ax_obj.imshow(overlay_color(ve_s, (1.0, 0.30, 0.30, 0.50)), aspect=aspect)  # 红 = 血管风险区
        if he_s is not None:
            ax_obj.imshow(overlay_color(he_s, (1.0, 0.95, 0.10, 0.70)), aspect=aspect)  # 黄 = 血肿
        ax_obj.set_title(f"{name}  idx={idx}", fontsize=11); ax_obj.axis("off")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout(); fig.savefig(out_png, dpi=120, bbox_inches="tight"); plt.close(fig)


def save_3d(skull: np.ndarray, vessel: np.ndarray, hematoma: Optional[np.ndarray],
            spacing, out_png: Path, title: str, downsample: int = 3) -> None:
    if skull.sum() == 0:
        return

    def _ds(arr): return arr[::downsample, ::downsample, ::downsample] if downsample > 1 else arr
    sp = (spacing[0] * downsample, spacing[1] * downsample, spacing[2] * downsample)

    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection="3d")
    all_v = None

    v, f, _, _ = measure.marching_cubes(_ds(skull).astype(np.uint8), level=0.5,
                                        spacing=(sp[2], sp[1], sp[0]),
                                        allow_degenerate=False)
    m = Poly3DCollection(v[f], alpha=0.10, linewidths=0)
    m.set_facecolor((0.95, 0.92, 0.85)); m.set_edgecolor("none")
    ax.add_collection3d(m); all_v = v

    if vessel.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(vessel).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.55, linewidths=0)
        m.set_facecolor((0.95, 0.20, 0.20)); m.set_edgecolor("none")
        ax.add_collection3d(m); all_v = np.vstack([all_v, v])

    if hematoma is not None and hematoma.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(hematoma).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.95, linewidths=0)
        m.set_facecolor((1.0, 0.85, 0.10)); m.set_edgecolor("none")
        ax.add_collection3d(m); all_v = np.vstack([all_v, v])

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
    fig.tight_layout(); fig.savefig(out_png, dpi=110, bbox_inches="tight"); plt.close(fig)


# ---------- 主流程 ----------
def find_cases(input_dir: Path, ct_pattern: str) -> List[Dict[str, Path]]:
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
    ap = argparse.ArgumentParser(description="血管风险区域生成（平扫 CT 几何近似）")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz")
    ap.add_argument("--hu-low",  type=float, default=80.0,  help="高密度结构 HU 下限")
    ap.add_argument("--hu-high", type=float, default=250.0, help="高密度结构 HU 上限（< 骨）")
    ap.add_argument("--no-detected", action="store_true", help="不使用 HU 阈值检测高密度结构")
    ap.add_argument("--no-anatomical", action="store_true", help="不使用解剖学先验")
    ap.add_argument("--sagittal-band-mm", type=float, default=10.0, help="上矢状窦左右半宽")
    ap.add_argument("--falx-mm", type=float, default=3.0, help="大脑镰左右半宽")
    ap.add_argument("--top-band-mm", type=float, default=12.0, help="上矢状窦带从颅顶向下厚度")
    ap.add_argument("--floor-thickness-mm", type=float, default=15.0, help="颅底血管区高度")
    ap.add_argument("--edge-thickness-mm", type=float, default=12.0, help="颅底血管区距外缘距离")
    ap.add_argument("--no-3d", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cases = find_cases(in_dir, args.pattern)
    if not cases:
        print("[错误] 未找到 (CT + intracranial + skull) 配对", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(cases)} 例")
    print(f"[参数] detected={not args.no_detected} HU[{args.hu_low},{args.hu_high}]  "
          f"anatomical={not args.no_anatomical}\n")

    n_ok, n_err = 0, 0
    for c in cases:
        ct_path: Path = c["ct"]
        print(f"=== Case: {ct_path.name} ===")
        try:
            ct_img    = read_nifti(ct_path)
            skull_img = read_nifti(c["skull"])
            intra_img = read_nifti(c["intracranial"])
            hema_img  = read_nifti(c["hematoma"]) if c["hematoma"] else None

            for tag, im in [("skull", skull_img), ("intra", intra_img), ("hema", hema_img)]:
                if im is None: continue
                if im.GetSize() != ct_img.GetSize():
                    rs = sitk.ResampleImageFilter()
                    rs.SetReferenceImage(ct_img); rs.SetInterpolator(sitk.sitkNearestNeighbor)
                    if tag == "skull": skull_img = rs.Execute(skull_img)
                    elif tag == "intra": intra_img = rs.Execute(intra_img)
                    elif tag == "hema": hema_img = rs.Execute(hema_img)
                    print(f"  [警告] 重采样 {tag} -> CT 网格")

            ct_arr = sitk.GetArrayFromImage(ct_img).astype(np.float32)
            skull = (sitk.GetArrayFromImage(skull_img) > 0).astype(np.uint8)
            intra = (sitk.GetArrayFromImage(intra_img) > 0).astype(np.uint8)
            hema  = (sitk.GetArrayFromImage(hema_img) > 0).astype(np.uint8) if hema_img else None
            spacing = ct_img.GetSpacing()
            voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])

            print("  -> 构造血管风险禁区 ...")
            anat_kwargs = dict(
                sagittal_band_mm=args.sagittal_band_mm,
                falx_mm=args.falx_mm,
                top_band_mm=args.top_band_mm,
                floor_thickness_mm=args.floor_thickness_mm,
                edge_thickness_mm=args.edge_thickness_mm,
            )
            vessel, stats = build_vessel_risk_mask(
                ct_arr, intra, skull, hema, spacing,
                hu_low=args.hu_low, hu_high=args.hu_high,
                use_detected=not args.no_detected,
                use_anatomical=not args.no_anatomical,
                **anat_kwargs,
            )

            base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
            mask_p = base.parent / f"{base.name}_vessel_risk_mask.nii.gz"
            ovr_p  = base.parent / f"{base.name}_vessel_overlay.png"
            d3_p   = base.parent / f"{base.name}_vessel_3d.png"
            stats_p = base.parent / f"{base.name}_vessel_stats.json"

            mask_img = sitk.GetImageFromArray(vessel.astype(np.uint8))
            mask_img.CopyInformation(ct_img)
            write_nifti(mask_img, mask_p)

            print(f"  -> mask: {mask_p.name}  total {stats['total_volume_ml']} mL")
            for k, v in stats["components_volume_ml"].items():
                print(f"     {k}: {v} mL")

            save_overlay(ct_arr, skull, vessel, hema, ovr_p, spacing,
                         title=f"{base.name}  vessel risk overlay (red=risk, yellow=hematoma)")
            print(f"  -> overlay: {ovr_p.name}")
            if not args.no_3d:
                save_3d(skull, vessel, hema, spacing, d3_p,
                        title=f"{base.name}  skull(translucent) + vessel risk(red) + hematoma(yellow)")
                print(f"  -> 3D: {d3_p.name}")

            with open(stats_p, "w", encoding="utf-8") as f:
                json.dump({
                    "ct_file": ct_path.name,
                    "spacing_mm": [round(float(v), 4) for v in spacing],
                    "voxel_mm3": round(voxel_mm3, 6),
                    "params": {
                        "hu_range": [args.hu_low, args.hu_high],
                        "use_detected": not args.no_detected,
                        "use_anatomical": not args.no_anatomical,
                        **anat_kwargs,
                    },
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
