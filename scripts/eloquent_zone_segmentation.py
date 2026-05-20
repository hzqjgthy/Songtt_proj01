# -*- coding: utf-8 -*-
r"""
功能区几何近似分割（CT 上无法精确分割功能区，使用基于颅腔归一化坐标的解剖学先验）

⚠️ 重要说明：
  真正的功能区分割必须使用 MRI（T1 + atlas 配准 / fMRI）。
  本脚本基于颅腔的归一化解剖坐标，画出 4 类**经验性功能区禁区**，
  作为路径规划的硬约束。这是**保守过近似**，不是真功能区分割。
  当 MRI 数据可用时，应替换为 FastSurfer / FreeSurfer 输出的精确分区。

输入：
  - CT NIfTI（推荐 *Hr40*.nii.gz）
  - 颅腔 mask  *_intracranial_mask.nii.gz
  - 脑组织 mask *_brain_mask.nii.gz
  - 颅骨 mask  *_skull_mask.nii.gz
  可选：
  - 血肿 mask  *_hematoma_mask.nii.gz   （从禁区扣除）
  - 脑室 mask  *_ventricle_mask.nii.gz  （从禁区扣除）

输出：
  *_eloquent_zone_mask.nii.gz       合并版二值禁区（所有功能区合并）
  *_eloquent_motor_mask.nii.gz      运动皮层带（中央前回 M1）
  *_eloquent_language_mask.nii.gz   语言区（左半球，Broca + Wernicke）
  *_eloquent_visual_mask.nii.gz     视觉皮层（枕叶后部）
  *_eloquent_deep_mask.nii.gz       深部核团（基底节 + 丘脑，已有血肿处自动扣除）
  *_eloquent_overlay.png            三视图，4 类不同颜色
  *_eloquent_3d.png                 3D
  *_eloquent_stats.json

颜色：
  motor      洋红 (1.0, 0.20, 0.55)
  language   绿  (0.20, 0.85, 0.30)
  visual     蓝  (0.20, 0.40, 0.95)
  deep       紫  (0.55, 0.30, 0.85)

归一化定义：
  对颅腔取每个轴的 [min, max]，把每个体素映射到 [0, 1]：
    z' = (z - z_min) / (z_max - z_min)   z'≈0 颅底，z'≈1 颅顶
    y' = (y - y_min) / (y_max - y_min)   y'≈0 前部（额叶），y'≈1 后部（枕叶）
    x' = (x - x_min) / (x_max - x_min)   x'≈0 左，x'≈1 右
  这只是颅腔 bounding box 的归一化，**不是 MNI 配准**，仅作为粗略先验。
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
    from skimage import measure
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

# ---------- IO（与其他脚本一致）----------
def _nib_affine_to_sitk(affine):
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    lps = flip @ affine
    rot = lps[:3, :3]
    origin = tuple(float(v) for v in lps[:3, 3])
    spacing = tuple(float(np.linalg.norm(rot[:, i])) for i in range(3))
    direction_mat = np.zeros((3, 3), dtype=np.float64)
    for i in range(3):
        if spacing[i] > 0: direction_mat[:, i] = rot[:, i] / spacing[i]
        else: direction_mat[i, i] = 1.0
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
def build_eloquent_zones(intracranial: np.ndarray, brain: np.ndarray,
                         hematoma: Optional[np.ndarray],
                         ventricle: Optional[np.ndarray],
                         spacing: Tuple[float, float, float]
                         ) -> Tuple[Dict[str, np.ndarray], Dict]:
    """
    返回 (zones, stats)
    zones: {'motor', 'language', 'visual', 'deep', 'all'} -> uint8 mask
    """
    sx_mm = spacing[0]; sy_mm = spacing[1]; sz_mm = spacing[2]
    voxel_mm3 = sx_mm * sy_mm * sz_mm

    intra = intracranial.astype(bool)
    brain_b = brain.astype(bool)
    z_dim, y_dim, x_dim = intra.shape

    zs, ys, xs = np.where(intra)
    if len(zs) == 0:
        empty = np.zeros_like(intra, dtype=np.uint8)
        return {"motor": empty, "language": empty, "visual": empty,
                "deep": empty, "all": empty}, {}

    z_min, z_max = float(zs.min()), float(zs.max())
    y_min, y_max = float(ys.min()), float(ys.max())
    x_min, x_max = float(xs.min()), float(xs.max())
    z_range = max(z_max - z_min, 1.0)
    y_range = max(y_max - y_min, 1.0)
    x_range = max(x_max - x_min, 1.0)
    x_center = float(np.median(xs))

    # 归一化坐标网格
    zi = np.arange(z_dim)[:, None, None].astype(np.float32)
    yi = np.arange(y_dim)[None, :, None].astype(np.float32)
    xi = np.arange(x_dim)[None, None, :].astype(np.float32)
    zn = (zi - z_min) / z_range  # [0, 1]
    yn = (yi - y_min) / y_range
    xn = (xi - x_min) / x_range

    # ---- 1) 运动皮层带（中央前回 M1）：颅顶下方一段、左右贯穿、前后中段 ----
    # M1 大致位置：z' ∈ [0.78, 0.95]（顶部）, y' ∈ [0.40, 0.62]（中央沟附近）
    motor_mask = ((zn >= 0.78) & (zn <= 0.95) &
                  (yn >= 0.40) & (yn <= 0.62))
    motor_mask = np.broadcast_to(motor_mask, intra.shape)
    motor = (intra & brain_b & motor_mask).astype(np.uint8)

    # ---- 2) 语言区：仅左半球（x < x_center），覆盖 Broca 和 Wernicke ----
    # Broca: 左额下回，y' ∈ [0.20, 0.40], z' ∈ [0.55, 0.78]
    # Wernicke: 左颞上后部，y' ∈ [0.55, 0.75], z' ∈ [0.45, 0.65]
    broca = ((xi < x_center) &
             (yn >= 0.20) & (yn <= 0.40) &
             (zn >= 0.55) & (zn <= 0.78))
    wernicke = ((xi < x_center) &
                (yn >= 0.55) & (yn <= 0.75) &
                (zn >= 0.45) & (zn <= 0.65))
    lang_mask = broca | wernicke
    lang_mask = np.broadcast_to(lang_mask, intra.shape)
    language = (intra & brain_b & lang_mask).astype(np.uint8)

    # ---- 3) 视觉皮层：枕叶后部 ----
    # y' ∈ [0.78, 1.00], z' ∈ [0.30, 0.65]
    visual_mask = ((yn >= 0.78) &
                   (zn >= 0.30) & (zn <= 0.65))
    visual_mask = np.broadcast_to(visual_mask, intra.shape)
    visual = (intra & brain_b & visual_mask).astype(np.uint8)

    # ---- 4) 深部核团（基底节 + 丘脑）：中线两侧的椭球 ----
    # x 距中线归一化距离 dx = |x - x_center| / (x_range/2) ∈ [0, 1]
    # 仅取 dx ∈ [0.05, 0.40]（双侧紧邻中线但不贴中线）
    # y' ∈ [0.40, 0.62] (脑中央前后)
    # z' ∈ [0.42, 0.62] (中央高度)
    dx_norm = np.abs(xi - x_center) / max(x_range / 2, 1.0)
    deep_mask = ((dx_norm >= 0.05) & (dx_norm <= 0.40) &
                 (yn >= 0.40) & (yn <= 0.62) &
                 (zn >= 0.42) & (zn <= 0.62))
    deep_mask = np.broadcast_to(deep_mask, intra.shape)
    deep = (intra & brain_b & deep_mask).astype(np.uint8)

    # 排除血肿和脑室（避免把血肿区当禁区）
    for arr in (motor, language, visual, deep):
        if hematoma is not None:
            arr &= (~hematoma.astype(bool)).astype(np.uint8)
        if ventricle is not None:
            arr &= (~ventricle.astype(bool)).astype(np.uint8)

    # 合并版
    all_mask = (motor.astype(bool) | language.astype(bool) |
                visual.astype(bool) | deep.astype(bool)).astype(np.uint8)

    stats = {
        "x_center_voxel": int(x_center),
        "intracranial_bbox_zyx": {
            "z": [int(z_min), int(z_max)],
            "y": [int(y_min), int(y_max)],
            "x": [int(x_min), int(x_max)],
        },
        "components_volume_ml": {
            "motor":    round(float(motor.sum())    * voxel_mm3 / 1000.0, 2),
            "language": round(float(language.sum()) * voxel_mm3 / 1000.0, 2),
            "visual":   round(float(visual.sum())   * voxel_mm3 / 1000.0, 2),
            "deep":     round(float(deep.sum())     * voxel_mm3 / 1000.0, 2),
        },
        "total_volume_ml": round(float(all_mask.sum()) * voxel_mm3 / 1000.0, 2),
    }

    return {"motor": motor, "language": language, "visual": visual,
            "deep": deep, "all": all_mask}, stats


# ---------- 可视化 ----------
COLORS = {
    "motor":    (1.00, 0.20, 0.55, 0.45),  # 洋红
    "language": (0.20, 0.85, 0.30, 0.45),  # 绿
    "visual":   (0.20, 0.40, 0.95, 0.45),  # 蓝
    "deep":     (0.55, 0.30, 0.85, 0.45),  # 紫
}


def window_image(arr, wl=40, ww=80):
    lo, hi = wl - ww/2, wl + ww/2
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def overlay_color(mask, rgba):
    out = np.zeros(mask.shape + (4,), dtype=np.float32)
    out[..., 0] = rgba[0]; out[..., 1] = rgba[1]; out[..., 2] = rgba[2]
    out[..., 3] = mask.astype(np.float32) * rgba[3]
    return out


def save_overlay(ct, skull, zones, hematoma, out_png, spacing, title):
    z, y, x = ct.shape
    cz = z // 2
    if zones["all"].sum() > 0:
        cz = int(np.argmax(zones["all"].sum(axis=(1, 2))))

    cuts = [
        ("Axial", cz, cz, "z"),
        ("Coronal", y // 2, y // 2, "y"),
        ("Sagittal", x // 2, x // 2, "x"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))
    for ax_obj, (name, idx, _, axis) in zip(axes, cuts):
        if axis == "z":
            ct_s = ct[idx]; sk_s = skull[idx]
            zone_slices = {k: zones[k][idx] for k in COLORS}
            he_s = hematoma[idx] if hematoma is not None else None
            aspect = 1.0
        elif axis == "y":
            ct_s = ct[:, idx, :][::-1]; sk_s = skull[:, idx, :][::-1]
            zone_slices = {k: zones[k][:, idx, :][::-1] for k in COLORS}
            he_s = hematoma[:, idx, :][::-1] if hematoma is not None else None
            aspect = spacing[2] / spacing[1] if spacing[1] else 1.0
        else:
            ct_s = ct[:, :, idx][::-1]; sk_s = skull[:, :, idx][::-1]
            zone_slices = {k: zones[k][:, :, idx][::-1] for k in COLORS}
            he_s = hematoma[:, :, idx][::-1] if hematoma is not None else None
            aspect = spacing[2] / spacing[0] if spacing[0] else 1.0

        ax_obj.imshow(window_image(ct_s), cmap="gray", aspect=aspect)
        ax_obj.imshow(overlay_color(sk_s, (1.0, 1.0, 1.0, 0.30)), aspect=aspect)
        for k, color in COLORS.items():
            ax_obj.imshow(overlay_color(zone_slices[k], color), aspect=aspect)
        if he_s is not None:
            ax_obj.imshow(overlay_color(he_s, (1.0, 0.15, 0.15, 0.70)), aspect=aspect)
        ax_obj.set_title(f"{name}  idx={idx}", fontsize=11); ax_obj.axis("off")

    # 图例
    legend_handles = [plt.Line2D([0], [0], marker='s', color='w',
                                 markerfacecolor=color[:3], markersize=10, label=name)
                      for name, color in COLORS.items()]
    legend_handles.append(plt.Line2D([0], [0], marker='s', color='w',
                                     markerfacecolor=(1.0, 0.15, 0.15), markersize=10, label='hematoma'))
    fig.legend(handles=legend_handles, loc='upper right', ncol=5, fontsize=9, frameon=False)

    fig.suptitle(title, fontsize=10)
    fig.tight_layout(); fig.savefig(out_png, dpi=120, bbox_inches="tight"); plt.close(fig)


def save_3d(skull, zones, hematoma, spacing, out_png, title, downsample=3):
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

    for name, rgba in COLORS.items():
        mask = zones[name]
        ds_m = _ds(mask).astype(np.uint8)
        if ds_m.max() == 0: continue
        v, f, _, _ = measure.marching_cubes(ds_m, level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]), allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.55, linewidths=0)
        m.set_facecolor(rgba[:3]); m.set_edgecolor("none")
        ax.add_collection3d(m); all_v = np.vstack([all_v, v])

    if hematoma is not None and hematoma.sum() > 0:
        ds_h = _ds(hematoma).astype(np.uint8)
        if ds_h.max() > 0:
            v, f, _, _ = measure.marching_cubes(ds_h, level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]), allow_degenerate=False)
            m = Poly3DCollection(v[f], alpha=0.95, linewidths=0)
            m.set_facecolor((1.0, 0.15, 0.15)); m.set_edgecolor("none")
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
    EXCLUDE = ("_mask.nii.gz", "_overlay.png", "_3d.png", "_preview.png", "_stats.txt", "_report.json")
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
            "ct": ct, "intracranial": intra_p, "brain": brain_p, "skull": skull_list[0],
            "ventricle": vent_p if vent_p.exists() else None,
            "hematoma":  hema_p if hema_p.exists() else None,
        })
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="功能区几何近似（CT 颅腔归一化先验）")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz")
    ap.add_argument("--no-3d", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cases = find_cases(in_dir, args.pattern)
    if not cases:
        print("[错误] 未找到 (CT + intracranial + brain + skull) 配对", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(cases)} 例\n")

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

            print("  -> 构造功能区禁区 ...")
            zones, stats = build_eloquent_zones(intra, brain, hema, vent, spacing)

            base = ct_path.parent / ct_path.name[:-len(".nii.gz")]
            paths = {
                "all":     base.parent / f"{base.name}_eloquent_zone_mask.nii.gz",
                "motor":   base.parent / f"{base.name}_eloquent_motor_mask.nii.gz",
                "language":base.parent / f"{base.name}_eloquent_language_mask.nii.gz",
                "visual":  base.parent / f"{base.name}_eloquent_visual_mask.nii.gz",
                "deep":    base.parent / f"{base.name}_eloquent_deep_mask.nii.gz",
            }
            for k, p in paths.items():
                m = sitk.GetImageFromArray(zones[k].astype(np.uint8))
                m.CopyInformation(ct_img)
                write_nifti(m, p)

            print(f"  -> 总体积 {stats['total_volume_ml']} mL")
            for k, v in stats["components_volume_ml"].items():
                print(f"     {k}: {v} mL")

            ovr_p  = base.parent / f"{base.name}_eloquent_overlay.png"
            d3_p   = base.parent / f"{base.name}_eloquent_3d.png"
            stats_p = base.parent / f"{base.name}_eloquent_stats.json"

            save_overlay(ct, skull, zones, hema, ovr_p, spacing,
                         title=f"{base.name}  eloquent zones (geometric prior)")
            print(f"  -> overlay: {ovr_p.name}")
            if not args.no_3d:
                save_3d(skull, zones, hema, spacing, d3_p,
                        title=f"{base.name}  eloquent zones 3D (motor=magenta, lang=green, visual=blue, deep=purple)")
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
