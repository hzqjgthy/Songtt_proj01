# -*- coding: utf-8 -*-
r"""
颅骨分割（基于 CT，纯几何方法，无需训练）

输入：  第 1 步生成的 CT NIfTI（.nii.gz），推荐使用 *Hr60* 骨重建序列
输出：
  - <name>_skull_mask.nii.gz       颅骨二值 mask（0/1，uint8）
  - <name>_skull_overlay.png       三视图：CT 灰度 + 颅骨红色半透明叠加
  - <name>_skull_3d.png            颅骨表面 3D 渲染（marching cubes）
  - <name>_skull_stats.txt         体积、HU 范围、连通域信息

算法流程：
  1. 读取 CT (HU 单位)；
  2. 阈值 [bone_low, bone_high] 提取候选骨；默认 (300, 3000) HU；
  3. 形态学开运算去掉孤立小点（如 CT 床、空气交界噪声）；
  4. 3D 连通域分析，仅保留最大连通域 -> 颅骨整体；
     这样可自动剔除 CT 床、定位棒、面部小骨片等；
  5. 形态学闭运算填补骨缝裂隙；
  6. 写出 mask；
  7. 生成可视化。

依赖：
  pip install -r requirements.txt
  (SimpleITK, numpy, scipy, scikit-image, matplotlib)

用法：
  # 处理目录下所有 NIfTI（默认只挑 Hr60）
  python skull_segmentation.py

  # 指定单个文件
  python skull_segmentation.py --input "..\output_nifti\patient_xxx\CT_xxx_Hr60.nii.gz"

  # 处理整个目录的所有 .nii.gz（不挑 Hr60）
  python skull_segmentation.py --input "..\output_nifti" --pattern "*.nii.gz"

  # 调阈值
  python skull_segmentation.py --hu-low 250 --hu-high 3000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    print("[错误] 未安装 SimpleITK，请先 pip install -r requirements.txt", file=sys.stderr); raise

try:
    import nibabel as nib
except ImportError:
    print("[错误] 未安装 nibabel，请先 pip install nibabel", file=sys.stderr); raise


# ----- 中文路径 IO 适配 -----
# Windows 下 SimpleITK 使用 ANSI 打开文件，含中文路径会失败；
# 改走 nibabel（纯 Python）读 NIfTI，再转换为 sitk.Image 保留正确 geometry。

def _nib_affine_to_sitk(affine: np.ndarray) -> Tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]:
    """nibabel RAS+ affine -> SimpleITK (origin, spacing, direction(LPS))。"""
    # nibabel 的 affine 把体素 (i,j,k) 映射到 RAS+ 物理坐标
    # SimpleITK 内部使用 LPS，所以要把前两行（X,Y）取负
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
    """SimpleITK (origin, spacing, direction in LPS) -> nibabel RAS+ affine."""
    spacing = np.array(img.GetSpacing(), dtype=np.float64)
    origin = np.array(img.GetOrigin(), dtype=np.float64)
    direction = np.array(img.GetDirection(), dtype=np.float64).reshape(3, 3)
    rot = direction @ np.diag(spacing)
    lps_affine = np.eye(4)
    lps_affine[:3, :3] = rot
    lps_affine[:3, 3] = origin
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    return flip @ lps_affine


def _sitk_read(path: Path) -> sitk.Image:
    nii = nib.load(str(path))
    arr = np.asarray(nii.dataobj)  # nibabel 顺序 (x, y, z)
    # SimpleITK 内部数组顺序是 (z, y, x)
    arr_zyx = np.transpose(arr, (2, 1, 0)).copy()
    img = sitk.GetImageFromArray(arr_zyx)
    origin, spacing, direction = _nib_affine_to_sitk(nii.affine)
    img.SetOrigin(origin)
    img.SetSpacing(spacing)
    img.SetDirection(direction)
    return img


def _sitk_write(img: sitk.Image, path: Path) -> None:
    arr_zyx = sitk.GetArrayFromImage(img)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    affine = _sitk_to_nib_affine(img)
    nii = nib.Nifti1Image(arr_xyz, affine)
    nib.save(nii, str(path))

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
    for _fname in ("Microsoft YaHei", "SimHei", "DengXian", "SimSun"):
        try:
            font_manager.findfont(_fname, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [_fname]; break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: F401
except ImportError:
    print("[错误] 未安装 matplotlib，请先 pip install -r requirements.txt", file=sys.stderr); raise


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"


# ---------------- 核心分割算法 ----------------
def segment_skull(
    ct_image: sitk.Image,
    hu_low: float = 300.0,
    hu_high: float = 3000.0,
    open_radius: int = 1,
    close_radius: int = 2,
    keep_largest: bool = True,
) -> Tuple[sitk.Image, dict]:
    """
    返回 (mask_image, stats)

    mask_image: 与输入同 spacing/origin/direction 的 uint8 image，骨=1 背景=0
    stats: dict 含体积、连通域数、HU 统计等
    """
    arr = sitk.GetArrayFromImage(ct_image).astype(np.float32)  # (z,y,x), HU
    spacing = ct_image.GetSpacing()  # (sx, sy, sz)
    voxel_mm3 = float(spacing[0] * spacing[1] * spacing[2])

    stats: dict = {
        "shape": tuple(int(v) for v in arr.shape),
        "spacing_mm": tuple(round(float(v), 4) for v in spacing),
        "voxel_mm3": round(voxel_mm3, 6),
        "hu_min": float(arr.min()),
        "hu_max": float(arr.max()),
        "hu_low": hu_low,
        "hu_high": hu_high,
    }

    # 1) 阈值
    raw = (arr >= hu_low) & (arr <= hu_high)
    stats["voxels_after_threshold"] = int(raw.sum())

    # 2) 开运算：去除孤立小点 / 噪声
    if open_radius > 0:
        raw = ndi.binary_opening(raw, structure=morphology.ball(open_radius))
        stats["voxels_after_opening"] = int(raw.sum())

    # 3) 连通域：保留最大连通域（自动剔除 CT 床、面部小骨片、定位棒）
    if keep_largest:
        labels, n_cc = ndi.label(raw, structure=np.ones((3, 3, 3), dtype=np.uint8))
        stats["n_connected_components_initial"] = int(n_cc)
        if n_cc > 0:
            sizes = ndi.sum(raw, labels, index=np.arange(1, n_cc + 1))
            largest = int(np.argmax(sizes)) + 1
            raw = (labels == largest)
            stats["largest_component_voxels"] = int(sizes.max())
        else:
            stats["largest_component_voxels"] = 0

    # 4) 闭运算：填补骨缝
    if close_radius > 0:
        raw = ndi.binary_closing(raw, structure=morphology.ball(close_radius))
        stats["voxels_after_closing"] = int(raw.sum())

    skull_mask = raw.astype(np.uint8)
    stats["skull_voxels"] = int(skull_mask.sum())
    stats["skull_volume_cm3"] = round(skull_mask.sum() * voxel_mm3 / 1000.0, 2)

    mask_img = sitk.GetImageFromArray(skull_mask)
    mask_img.CopyInformation(ct_image)
    return mask_img, stats


# ---------------- 可视化 ----------------
def window_image(arr: np.ndarray, wl: float = 600.0, ww: float = 2000.0) -> np.ndarray:
    """骨窗 (默认 WL=600 WW=2000)"""
    lo, hi = wl - ww / 2, wl + ww / 2
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0)


def save_overlay_preview(ct: np.ndarray, mask: np.ndarray, out_png: Path,
                         spacing: Tuple[float, float, float], title: str) -> None:
    """三视图（轴/冠/矢） + 颅骨红色叠加"""
    z, y, x = ct.shape
    cuts = [
        ("Axial",    ct[z // 2, :, :],            mask[z // 2, :, :],            1.0),
        ("Coronal",  ct[:, y // 2, :][::-1],      mask[:, y // 2, :][::-1],      spacing[2] / spacing[1] if spacing[1] else 1.0),
        ("Sagittal", ct[:, :, x // 2][::-1],      mask[:, :, x // 2][::-1],      spacing[2] / spacing[0] if spacing[0] else 1.0),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax_obj, (name, ct_slice, mk_slice, aspect) in zip(axes, cuts):
        ax_obj.imshow(window_image(ct_slice), cmap="gray", aspect=aspect)
        # 红色 mask 叠加
        rgba = np.zeros(mk_slice.shape + (4,), dtype=np.float32)
        rgba[..., 0] = 1.0  # R
        rgba[..., 3] = mk_slice.astype(np.float32) * 0.45  # alpha
        ax_obj.imshow(rgba, aspect=aspect)
        ax_obj.set_title(name, fontsize=11)
        ax_obj.axis("off")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def save_3d_render(mask: np.ndarray, spacing: Tuple[float, float, float],
                   out_png: Path, title: str, downsample: int = 2) -> None:
    """用 marching cubes 提颅骨表面，做一张 3D PNG。"""
    if mask.sum() == 0:
        print("  [3D] mask 为空，跳过 3D 渲染")
        return

    # 下采样以加速
    if downsample > 1:
        m = mask[::downsample, ::downsample, ::downsample]
        sp = (spacing[0] * downsample, spacing[1] * downsample, spacing[2] * downsample)
    else:
        m = mask
        sp = spacing

    try:
        # marching_cubes 的 spacing 顺序是 (z, y, x)
        verts, faces, _normals, _vals = measure.marching_cubes(
            m.astype(np.uint8), level=0.5,
            spacing=(sp[2], sp[1], sp[0]),  # (z, y, x) physical
            allow_degenerate=False,
        )
    except Exception as e:
        print(f"  [3D] marching_cubes 失败: {e}")
        return

    fig = plt.figure(figsize=(10, 9))
    ax = fig.add_subplot(111, projection="3d")
    mesh = Poly3DCollection(verts[faces], alpha=0.5, linewidths=0)
    mesh.set_facecolor((0.95, 0.92, 0.85))  # 米白色像骨头
    mesh.set_edgecolor("none")
    ax.add_collection3d(mesh)

    # 设置坐标范围（注意 verts 列顺序 z,y,x）
    ax.set_xlim(verts[:, 0].min(), verts[:, 0].max())
    ax.set_ylim(verts[:, 1].min(), verts[:, 1].max())
    ax.set_zlim(verts[:, 2].min(), verts[:, 2].max())
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


# ---------------- 主流程 ----------------
def find_inputs(input_arg: Path, pattern: str) -> List[Path]:
    if input_arg.is_file():
        return [input_arg]
    files: List[Path] = []
    for p in sorted(input_arg.rglob(pattern)):
        if p.name.endswith("_skull_mask.nii.gz"):
            continue
        if p.name.endswith("_synthseg.nii.gz"):
            continue
        files.append(p)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="颅骨分割（CT, 阈值+形态学+连通域）")
    ap.add_argument("--input",   default=str(DEFAULT_INPUT),
                    help="单个 .nii.gz 文件，或包含 .nii.gz 的目录")
    ap.add_argument("--pattern", default="*Hr60*.nii.gz",
                    help="目录模式下的文件 glob，默认仅处理骨重建 Hr60")
    ap.add_argument("--hu-low",  type=float, default=300.0, help="骨阈值下限 HU")
    ap.add_argument("--hu-high", type=float, default=3000.0, help="骨阈值上限 HU")
    ap.add_argument("--open-radius",  type=int, default=1, help="开运算结构元半径")
    ap.add_argument("--close-radius", type=int, default=2, help="闭运算结构元半径")
    ap.add_argument("--no-3d", action="store_true", help="不生成 3D 渲染图")
    args = ap.parse_args()

    in_path = Path(args.input).resolve()
    if not in_path.exists():
        print(f"[错误] 输入不存在: {in_path}", file=sys.stderr); return 2

    inputs = find_inputs(in_path, args.pattern)
    if not inputs:
        print(f"[错误] 未找到匹配文件 (input={in_path}, pattern={args.pattern})", file=sys.stderr)
        return 3

    print(f"[输入] {in_path}")
    print(f"[匹配] pattern='{args.pattern}'  共 {len(inputs)} 个文件")
    print(f"[阈值] HU [{args.hu_low}, {args.hu_high}]  open_r={args.open_radius}  close_r={args.close_radius}\n")

    n_ok, n_err = 0, 0
    for nii in inputs:
        print(f"=== 处理: {nii.name} ===")
        try:
            ct = _sitk_read(nii)
            print(f"  size={ct.GetSize()}  spacing={tuple(round(v,3) for v in ct.GetSpacing())}")

            mask_img, stats = segment_skull(
                ct,
                hu_low=args.hu_low, hu_high=args.hu_high,
                open_radius=args.open_radius, close_radius=args.close_radius,
            )

            base = nii.with_suffix("").with_suffix("")  # 去 .nii.gz 两层后缀
            mask_path    = base.parent / f"{base.name}_skull_mask.nii.gz"
            overlay_path = base.parent / f"{base.name}_skull_overlay.png"
            stats_path   = base.parent / f"{base.name}_skull_stats.txt"
            render_path  = base.parent / f"{base.name}_skull_3d.png"

            _sitk_write(mask_img, mask_path)
            print(f"  -> mask: {mask_path.name}  体素 {stats['skull_voxels']}  体积 {stats['skull_volume_cm3']} cm^3")

            ct_arr = sitk.GetArrayFromImage(ct)
            mk_arr = sitk.GetArrayFromImage(mask_img)
            save_overlay_preview(ct_arr, mk_arr, overlay_path,
                                 spacing=ct.GetSpacing(), title=f"{base.name}  skull overlay")
            print(f"  -> overlay: {overlay_path.name}")

            if not args.no_3d:
                save_3d_render(mk_arr, ct.GetSpacing(), render_path,
                               title=f"{base.name}  skull 3D")
                print(f"  -> 3D: {render_path.name}")

            with open(stats_path, "w", encoding="utf-8") as f:
                for k, v in stats.items():
                    f.write(f"{k}: {v}\n")

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
