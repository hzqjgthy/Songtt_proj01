# -*- coding: utf-8 -*-
r"""
脑组织（颅腔）提取 + 血肿粗分割（基于 CT，纯几何方法，无需训练）

输入：
  - CT NIfTI（推荐使用软组织重建 Hr40 / *Hr40*.nii.gz，HU 值已正确还原）
  - 同一病例的颅骨 mask（第 2 步 skull_segmentation.py 生成的 *_skull_mask.nii.gz）

输出（每个病例）：
  - <name>_intracranial_mask.nii.gz   颅腔 mask（颅骨内部空腔）
  - <name>_brain_mask.nii.gz          脑组织 mask（颅腔内 HU∈[0,80] 的软组织）
  - <name>_hematoma_mask.nii.gz       血肿粗分割（脑组织内 HU∈[45,80] + 连通域筛选）
  - <name>_brain_overlay.png          三视图叠加预览：CT + 颅骨(白) + 颅腔(黄) + 脑(绿) + 血肿(红)
  - <name>_brain_3d.png               3D：颅骨半透明 + 血肿实体
  - <name>_brain_report.json          关键指标：体积、血肿连通域列表（质心/BBox/平均HU/体积）

算法流程：
  1) 颅腔提取
     skull -> 闭运算填合骨缝 -> 逐切片 + 整体 binary_fill_holes -> 减去骨本身 -> 取最大连通域
  2) 脑组织
     颅腔 ∩ ( HU >= 0 ) ∩ ( HU <= 80 )  （排除空气/低密度水/CSF 偏低值与高密度钙化骨）
     可选 erosion 去掉与颅骨贴合的薄边
  3) 血肿（粗分割）
     脑组织 ∩ ( HU >= 45 ) ∩ ( HU <= 80 )
     形态学 opening 去噪 -> 连通域分析 -> 仅保留体积 >= min_volume_ml 的连通域
     输出每个连通域的 (体积、质心(IJK & 物理 mm)、Bounding Box、平均HU)

依赖：
  pip install -r requirements.txt
  (SimpleITK, numpy, scipy, scikit-image, matplotlib, nibabel)

用法（默认按命名规则自动配对）：
  python brain_hematoma_segmentation.py
  python brain_hematoma_segmentation.py --pattern "*Hr40*.nii.gz"
  python brain_hematoma_segmentation.py --hematoma-low 45 --hematoma-high 80
  python brain_hematoma_segmentation.py --min-hematoma-ml 2.0
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
    print("[错误] 未安装 SimpleITK，请先 pip install -r requirements.txt", file=sys.stderr); raise

try:
    import nibabel as nib
except ImportError:
    print("[错误] 未安装 nibabel，请先 pip install -r requirements.txt", file=sys.stderr); raise

try:
    from scipy import ndimage as ndi
except ImportError:
    print("[错误] 未安装 scipy，请先 pip install -r requirements.txt", file=sys.stderr); raise

try:
    from skimage import measure, morphology
except ImportError:
    print("[错误] 未安装 scikit-image，请先 pip install -r requirements.txt", file=sys.stderr); raise

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
    print("[错误] 未安装 matplotlib，请先 pip install -r requirements.txt", file=sys.stderr); raise


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"


# ---------- 中文路径安全 IO ----------
def _nib_affine_to_sitk(affine: np.ndarray) -> Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]:
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    lps_affine = flip @ affine
    rot = lps_affine[:3, :3]
    origin = tuple(float(v) for v in lps_affine[:3, 3])
    spacing = tuple(float(np.linalg.norm(rot[:, i])) for i in range(3))
    direction_mat = np.zeros((3, 3), dtype=np.float64)
    for i in range(3):
        if spacing[i] > 0:
            direction_mat[:, i] = rot[:, i] / spacing[i]
        else:
            direction_mat[i, i] = 1.0
    direction = tuple(float(v) for v in direction_mat.flatten(order="C"))
    return origin, spacing, direction


def _sitk_to_nib_affine(img: sitk.Image) -> np.ndarray:
    spacing = np.array(img.GetSpacing(), dtype=np.float64)
    origin = np.array(img.GetOrigin(), dtype=np.float64)
    direction = np.array(img.GetDirection(), dtype=np.float64).reshape(3, 3)
    rot = direction @ np.diag(spacing)
    lps_affine = np.eye(4)
    lps_affine[:3, :3] = rot
    lps_affine[:3, 3] = origin
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    return flip @ lps_affine


def read_nifti(path: Path) -> sitk.Image:
    nii = nib.load(str(path))
    arr_xyz = np.asarray(nii.dataobj)
    arr_zyx = np.transpose(arr_xyz, (2, 1, 0)).copy()
    img = sitk.GetImageFromArray(arr_zyx)
    origin, spacing, direction = _nib_affine_to_sitk(nii.affine)
    img.SetOrigin(origin); img.SetSpacing(spacing); img.SetDirection(direction)
    return img


def write_nifti(img: sitk.Image, path: Path) -> None:
    arr_zyx = sitk.GetArrayFromImage(img)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    affine = _sitk_to_nib_affine(img)
    nib.save(nib.Nifti1Image(arr_xyz, affine), str(path))


# ---------- 核心算法 ----------
def extract_intracranial(skull_mask: np.ndarray) -> np.ndarray:
    """
    颅骨 -> 颅腔（颅骨内部空腔）
    思路：闭运算填合骨缝 -> 逐切片二维填洞（捕捉颅顶/颅底） -> 三维填洞兜底 -> 减去骨本身。
    """
    # 1) 闭运算填合骨缝（半径 3 一般足够）
    closed = ndi.binary_closing(skull_mask, structure=morphology.ball(3))

    # 2) 三维 fill_holes 填颅腔（先做这个，因为大多数切片颅骨在轴位是闭合环）
    filled = ndi.binary_fill_holes(closed)

    # 3) 在轴位上再做一次 2D 填洞，处理三维填洞遗漏的边缘切片
    for z in range(filled.shape[0]):
        filled[z] = ndi.binary_fill_holes(filled[z])

    # 4) 减去骨本身 -> 颅腔
    intracranial = filled & (~skull_mask.astype(bool))

    # 5) 取最大连通域（剔除眼眶/鼻窦等小腔）
    labels, n_cc = ndi.label(intracranial, structure=np.ones((3, 3, 3), dtype=np.uint8))
    if n_cc > 0:
        sizes = ndi.sum(intracranial, labels, index=np.arange(1, n_cc + 1))
        largest = int(np.argmax(sizes)) + 1
        intracranial = (labels == largest)
    return intracranial.astype(np.uint8)


def extract_brain_tissue(ct_hu: np.ndarray, intracranial: np.ndarray,
                         hu_low: float = 0.0, hu_high: float = 80.0,
                         erode_radius: int = 1) -> np.ndarray:
    """颅腔 ∩ HU∈[hu_low, hu_high] -> 脑组织 ROI（含血肿）。"""
    roi = intracranial.astype(bool)
    if erode_radius > 0:
        # 轻度腐蚀，避免颅骨内板残留高密度污染
        roi = ndi.binary_erosion(roi, structure=morphology.ball(erode_radius))
    brain = roi & (ct_hu >= hu_low) & (ct_hu <= hu_high)
    return brain.astype(np.uint8)


def extract_hematoma(ct_hu: np.ndarray, brain_mask: np.ndarray,
                     skull_mask: np.ndarray,
                     hu_low: float = 45.0, hu_high: float = 80.0,
                     open_radius: int = 1,
                     min_volume_ml: float = 2.0,
                     skull_distance_mm: float = 4.0,
                     min_solidity: float = 0.5,
                     spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
                     voxel_mm3: float = 1.0) -> Tuple[np.ndarray, List[Dict]]:
    """
    在脑组织内提取血肿候选区域，并通过多重几何约束剔除钙化/骨界面伪影。

    过滤策略：
      1) HU 阈值
      2) 形态学开运算去离散噪点
      3) 距颅骨 < skull_distance_mm 的体素被剔除（去除骨内板部分容积效应）
      4) 连通域分析：体积 >= min_volume_ml
      5) 实心度 (filled / convex_hull_volume) >= min_solidity，过滤散点状钙化
    """
    cand = brain_mask.astype(bool) & (ct_hu >= hu_low) & (ct_hu <= hu_high)

    if open_radius > 0:
        cand = ndi.binary_opening(cand, structure=morphology.ball(open_radius))

    # —— 距颅骨 < N mm 的位置剔除（部分容积效应区域）——
    if skull_distance_mm > 0 and skull_mask.any():
        dist = ndi.distance_transform_edt(~skull_mask.astype(bool),
                                          sampling=(spacing[2], spacing[1], spacing[0]))  # zyx
        near_skull = (dist < skull_distance_mm)
    else:
        near_skull = np.zeros_like(cand, dtype=bool)
        dist = None

    # 连通域分析：先用未剔除距骨的版本做连通域，避免大血肿被切碎
    labels, n_cc = ndi.label(cand, structure=np.ones((3, 3, 3), dtype=np.uint8))
    keep = np.zeros_like(cand, dtype=bool)
    regions: List[Dict] = []
    if n_cc == 0:
        return keep.astype(np.uint8), regions

    min_vox = int(np.ceil(min_volume_ml * 1000.0 / voxel_mm3))
    sizes = ndi.sum(cand, labels, index=np.arange(1, n_cc + 1))
    LARGE_BLEED_ML = 30.0  # 体积超过此值认为是大血肿，不再用距骨过滤切除其外层

    for idx, sz in enumerate(sizes, start=1):
        comp = (labels == idx)
        vol_ml = float(sz * voxel_mm3 / 1000.0)

        # —— 距骨过滤：对每个候选连通域，剔除其紧贴颅骨的"外壳"，
        #     再判断剩余主体是否仍然 >= min_volume_ml ——
        if vol_ml < LARGE_BLEED_ML:
            comp_kept = comp & (~near_skull)
        else:
            # 大血肿不剔除外层，避免切断真实病灶
            comp_kept = comp

        kept_vox = int(comp_kept.sum())
        if kept_vox * voxel_mm3 / 1000.0 < min_volume_ml:
            continue

        # —— 实心度过滤 ——
        try:
            zs, ys, xs = np.where(comp_kept)
            sub = comp_kept[zs.min():zs.max() + 1,
                            ys.min():ys.max() + 1,
                            xs.min():xs.max() + 1]
            props = measure.regionprops(sub.astype(np.uint8))
            solidity = float(props[0].solidity) if props else 0.0
        except Exception:
            solidity = 1.0

        if solidity < min_solidity:
            continue

        keep |= comp_kept
        zs, ys, xs = np.where(comp_kept)
        centroid_zyx = (float(zs.mean()), float(ys.mean()), float(xs.mean()))
        regions.append({
            "label": int(idx),
            "voxels": kept_vox,
            "volume_ml": round(float(kept_vox * voxel_mm3 / 1000.0), 3),
            "centroid_ijk_zyx": [round(v, 2) for v in centroid_zyx],
            "bbox_zyx": {
                "z": [int(zs.min()), int(zs.max())],
                "y": [int(ys.min()), int(ys.max())],
                "x": [int(xs.min()), int(xs.max())],
            },
            "mean_hu": round(float(ct_hu[comp_kept].mean()), 2),
            "max_hu": round(float(ct_hu[comp_kept].max()), 2),
            "solidity": round(solidity, 3),
        })

    regions.sort(key=lambda r: r["volume_ml"], reverse=True)
    return keep.astype(np.uint8), regions


# ---------- 可视化 ----------
def window_image(arr: np.ndarray, wl: float = 40.0, ww: float = 80.0) -> np.ndarray:
    """脑窗，便于看脑组织/血肿对比。"""
    lo, hi = wl - ww / 2, wl + ww / 2
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0)


def _overlay_rgba(mask: np.ndarray, rgba: Tuple[float, float, float, float]) -> np.ndarray:
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[..., 0] = rgba[0]; out[..., 1] = rgba[1]; out[..., 2] = rgba[2]
    out[..., 3] = mask.astype(np.float32) * rgba[3]
    return out


def save_overlay(ct: np.ndarray, skull: np.ndarray, intra: np.ndarray,
                 brain: np.ndarray, hema: np.ndarray,
                 out_png: Path, spacing: Tuple[float, float, float],
                 title: str, focus_z: Optional[int] = None) -> None:
    """三视图：CT + 颅骨白 + 颅腔黄(浅) + 脑绿(浅) + 血肿红"""
    z, y, x = ct.shape
    if focus_z is None:
        # 自动定位到血肿最大切片
        if hema.sum() > 0:
            slice_sum = hema.sum(axis=(1, 2))
            focus_z = int(np.argmax(slice_sum))
        else:
            focus_z = z // 2

    cuts = [
        ("Axial",    focus_z, ct[focus_z], skull[focus_z], intra[focus_z], brain[focus_z], hema[focus_z], 1.0),
        ("Coronal",  y // 2,  ct[:, y // 2, :][::-1], skull[:, y // 2, :][::-1],
                              intra[:, y // 2, :][::-1], brain[:, y // 2, :][::-1], hema[:, y // 2, :][::-1],
                              spacing[2] / spacing[1] if spacing[1] else 1.0),
        ("Sagittal", x // 2,  ct[:, :, x // 2][::-1], skull[:, :, x // 2][::-1],
                              intra[:, :, x // 2][::-1], brain[:, :, x // 2][::-1], hema[:, :, x // 2][::-1],
                              spacing[2] / spacing[0] if spacing[0] else 1.0),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax_obj, (name, idx, ct_s, sk_s, ic_s, br_s, he_s, aspect) in zip(axes, cuts):
        ax_obj.imshow(window_image(ct_s), cmap="gray", aspect=aspect)
        # 颅腔淡黄（最底）
        ax_obj.imshow(_overlay_rgba(ic_s, (1.0, 0.95, 0.4, 0.10)), aspect=aspect)
        # 脑组织淡绿
        ax_obj.imshow(_overlay_rgba(br_s, (0.2, 0.9, 0.4, 0.18)), aspect=aspect)
        # 颅骨白边
        ax_obj.imshow(_overlay_rgba(sk_s, (1.0, 1.0, 1.0, 0.35)), aspect=aspect)
        # 血肿红色（最上）
        ax_obj.imshow(_overlay_rgba(he_s, (1.0, 0.15, 0.15, 0.65)), aspect=aspect)
        ax_obj.set_title(f"{name}  idx={idx}", fontsize=11)
        ax_obj.axis("off")

    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def save_3d(skull: np.ndarray, hema: np.ndarray,
            spacing: Tuple[float, float, float],
            out_png: Path, title: str, downsample: int = 3) -> None:
    """颅骨半透明 + 血肿实体红色。"""
    if skull.sum() == 0:
        print("  [3D] skull 为空，跳过"); return

    def _ds(arr):
        if downsample > 1:
            return arr[::downsample, ::downsample, ::downsample]
        return arr
    sp = (spacing[0] * max(downsample, 1), spacing[1] * max(downsample, 1), spacing[2] * max(downsample, 1))

    fig = plt.figure(figsize=(11, 9))
    ax = fig.add_subplot(111, projection="3d")

    # 颅骨表面
    try:
        v_sk, f_sk, _, _ = measure.marching_cubes(_ds(skull).astype(np.uint8), level=0.5,
                                                  spacing=(sp[2], sp[1], sp[0]),
                                                  allow_degenerate=False)
        mesh_sk = Poly3DCollection(v_sk[f_sk], alpha=0.18, linewidths=0)
        mesh_sk.set_facecolor((0.95, 0.92, 0.85))
        mesh_sk.set_edgecolor("none")
        ax.add_collection3d(mesh_sk)
        all_v = v_sk
    except Exception as e:
        print(f"  [3D] 颅骨重建失败: {e}")
        return

    # 血肿
    if hema.sum() > 0:
        try:
            v_he, f_he, _, _ = measure.marching_cubes(_ds(hema).astype(np.uint8), level=0.5,
                                                      spacing=(sp[2], sp[1], sp[0]),
                                                      allow_degenerate=False)
            mesh_he = Poly3DCollection(v_he[f_he], alpha=0.95, linewidths=0)
            mesh_he.set_facecolor((0.95, 0.15, 0.15))
            mesh_he.set_edgecolor("none")
            ax.add_collection3d(mesh_he)
            all_v = np.vstack([all_v, v_he])
        except Exception as e:
            print(f"  [3D] 血肿重建失败: {e}")

    ax.set_xlim(all_v[:, 0].min(), all_v[:, 0].max())
    ax.set_ylim(all_v[:, 1].min(), all_v[:, 1].max())
    ax.set_zlim(all_v[:, 2].min(), all_v[:, 2].max())
    ax.set_xlabel("Z (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("X (mm)")
    ax.view_init(elev=15, azim=-70)
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)


# ---------- IJK -> 物理坐标（用于规划阶段） ----------
def ijk_to_physical(ct_img: sitk.Image, ijk_zyx: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """传入 (z,y,x) 体素坐标，返回 SimpleITK LPS 物理 (x,y,z) mm。"""
    z, y, x = ijk_zyx
    # SimpleITK 接收 (x, y, z) 顺序的连续坐标
    p = ct_img.TransformContinuousIndexToPhysicalPoint((float(x), float(y), float(z)))
    return (float(p[0]), float(p[1]), float(p[2]))


# ---------- 主流程 ----------
def find_pairs(input_dir: Path, ct_pattern: str) -> List[Tuple[Path, Path]]:
    """
    寻找 (CT, skull_mask) 文件对：
    - CT 文件按 ct_pattern 匹配（默认 *Hr40*.nii.gz 软组织重建）；
    - 同目录下找 'CT_*Hr60*_skull_mask.nii.gz'，优先按 series_uid 后 8 位匹配。
    """
    pairs: List[Tuple[Path, Path]] = []
    for ct in sorted(input_dir.rglob(ct_pattern)):
        if "_mask" in ct.name or "_skull_" in ct.name:
            continue
        # 同目录找 skull_mask
        mask_candidates = list(ct.parent.glob("*_skull_mask.nii.gz"))
        if not mask_candidates:
            print(f"[警告] 未找到 skull_mask: {ct.name}")
            continue
        # 优先取最薄层（带 0.80 标识）的骨重建 mask
        mask_candidates.sort(key=lambda p: (0 if "0.80" in p.name else 1, p.name))
        pairs.append((ct, mask_candidates[0]))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="脑组织/颅腔/血肿粗分割（CT 几何方法）")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="output_nifti 根目录")
    ap.add_argument("--pattern", default="*Hr40*.nii.gz",
                    help="CT 匹配模式（默认软组织重建）")
    ap.add_argument("--brain-low", type=float, default=0.0, help="脑组织 HU 下限")
    ap.add_argument("--brain-high", type=float, default=80.0, help="脑组织 HU 上限")
    ap.add_argument("--hematoma-low", type=float, default=45.0, help="血肿 HU 下限")
    ap.add_argument("--hematoma-high", type=float, default=80.0, help="血肿 HU 上限")
    ap.add_argument("--erode-radius", type=int, default=1, help="颅腔向内腐蚀半径")
    ap.add_argument("--hematoma-open-radius", type=int, default=2,
                    help="血肿开运算半径（>=2 才能断开沿脑沟/骨界面的伪桥接，避免假大连通域）")
    ap.add_argument("--min-hematoma-ml", type=float, default=2.0, help="保留血肿最小体积 (mL)")
    ap.add_argument("--skull-distance-mm", type=float, default=2.5,
                    help="距颅骨小于该距离的体素被剔除（去除骨内板部分容积；大血肿>30mL不应用）")
    ap.add_argument("--min-solidity", type=float, default=0.4,
                    help="最小实心度（filled/convex_hull），过滤散点状钙化")
    ap.add_argument("--no-3d", action="store_true", help="不生成 3D 图")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    pairs = find_pairs(in_dir, args.pattern)
    if not pairs:
        print(f"[错误] 未找到 (CT, skull_mask) 配对", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(pairs)} 对")
    print(f"[阈值] 脑组织 HU [{args.brain_low}, {args.brain_high}]  "
          f"血肿 HU [{args.hematoma_low}, {args.hematoma_high}]  "
          f"min_hematoma={args.min_hematoma_ml} mL\n")

    n_ok, n_err = 0, 0
    for ct_path, mask_path in pairs:
        print(f"=== 处理 ===")
        print(f"  CT:    {ct_path.name}")
        print(f"  Skull: {mask_path.name}")
        try:
            ct_img = read_nifti(ct_path)
            skull_img = read_nifti(mask_path)

            # 几何一致性检查
            if ct_img.GetSize() != skull_img.GetSize():
                print(f"  [警告] CT size {ct_img.GetSize()} != skull size {skull_img.GetSize()}，"
                      f"将把 skull 重采样到 CT 网格")
                rs = sitk.ResampleImageFilter()
                rs.SetReferenceImage(ct_img)
                rs.SetInterpolator(sitk.sitkNearestNeighbor)
                skull_img = rs.Execute(skull_img)

            ct_arr = sitk.GetArrayFromImage(ct_img).astype(np.float32)
            skull_arr = (sitk.GetArrayFromImage(skull_img) > 0).astype(np.uint8)
            spacing = ct_img.GetSpacing()
            voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])

            print("  -> 提取颅腔 ...")
            intracranial = extract_intracranial(skull_arr)

            print("  -> 提取脑组织 ...")
            brain = extract_brain_tissue(ct_arr, intracranial,
                                         hu_low=args.brain_low, hu_high=args.brain_high,
                                         erode_radius=args.erode_radius)

            print("  -> 提取血肿候选 ...")
            hema, regions = extract_hematoma(ct_arr, brain, skull_arr,
                                             hu_low=args.hematoma_low, hu_high=args.hematoma_high,
                                             open_radius=args.hematoma_open_radius,
                                             min_volume_ml=args.min_hematoma_ml,
                                             skull_distance_mm=args.skull_distance_mm,
                                             min_solidity=args.min_solidity,
                                             spacing=spacing,
                                             voxel_mm3=voxel_mm3)

            # 写出
            base = ct_path.parent / ct_path.stem.replace(".nii", "")
            ic_path   = base.parent / f"{base.name}_intracranial_mask.nii.gz"
            br_path   = base.parent / f"{base.name}_brain_mask.nii.gz"
            he_path   = base.parent / f"{base.name}_hematoma_mask.nii.gz"
            ov_path   = base.parent / f"{base.name}_brain_overlay.png"
            d3_path   = base.parent / f"{base.name}_brain_3d.png"
            rep_path  = base.parent / f"{base.name}_brain_report.json"

            for arr_u8, p in [(intracranial, ic_path), (brain, br_path), (hema, he_path)]:
                m = sitk.GetImageFromArray(arr_u8.astype(np.uint8))
                m.CopyInformation(ct_img)
                write_nifti(m, p)

            print(f"  -> mask 写出 OK")
            print(f"     颅腔 voxels={int(intracranial.sum())}  vol≈{intracranial.sum()*voxel_mm3/1000:.1f} mL")
            print(f"     脑组织 voxels={int(brain.sum())}  vol≈{brain.sum()*voxel_mm3/1000:.1f} mL")
            print(f"     血肿候选 voxels={int(hema.sum())}  vol≈{hema.sum()*voxel_mm3/1000:.2f} mL  连通域={len(regions)}")

            # 把 IJK 质心转物理坐标，方便后续路径规划
            for r in regions:
                phys = ijk_to_physical(ct_img, tuple(r["centroid_ijk_zyx"]))
                r["centroid_physical_lps_mm"] = [round(v, 2) for v in phys]

            for i, r in enumerate(regions[:5], 1):
                print(f"     #{i}  vol={r['volume_ml']} mL  meanHU={r['mean_hu']}  "
                      f"centroid_ijk={r['centroid_ijk_zyx']}  phys={r['centroid_physical_lps_mm']}")

            # overlay + 3D
            save_overlay(ct_arr, skull_arr, intracranial, brain, hema,
                         ov_path, spacing, title=f"{base.name}  brain & hematoma overlay")
            print(f"  -> overlay: {ov_path.name}")
            if not args.no_3d:
                save_3d(skull_arr, hema, spacing, d3_path,
                        title=f"{base.name}  skull(translucent) + hematoma(red)")
                print(f"  -> 3D: {d3_path.name}")

            # 报告
            report = {
                "ct_file": ct_path.name,
                "skull_mask_file": mask_path.name,
                "spacing_mm": [round(float(v), 4) for v in spacing],
                "voxel_mm3": round(voxel_mm3, 6),
                "params": {
                    "brain_hu_range": [args.brain_low, args.brain_high],
                    "hematoma_hu_range": [args.hematoma_low, args.hematoma_high],
                    "erode_radius": args.erode_radius,
                    "hematoma_open_radius": args.hematoma_open_radius,
                    "min_hematoma_ml": args.min_hematoma_ml,
                    "skull_distance_mm": args.skull_distance_mm,
                    "min_solidity": args.min_solidity,
                },
                "volumes_ml": {
                    "intracranial": round(float(intracranial.sum()) * voxel_mm3 / 1000, 2),
                    "brain":        round(float(brain.sum()) * voxel_mm3 / 1000, 2),
                    "hematoma":     round(float(hema.sum()) * voxel_mm3 / 1000, 2),
                },
                "hematoma_regions": regions,
            }
            with open(rep_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"  -> report: {rep_path.name}")

            n_ok += 1
        except Exception as e:
            n_err += 1
            print(f"  [错误] {e}")
            import traceback; traceback.print_exc()
        print()

    print(f"========== 完成 ==========\n  成功: {n_ok}\n  失败: {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
