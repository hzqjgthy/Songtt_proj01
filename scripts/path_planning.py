# -*- coding: utf-8 -*-
r"""
手术路径规划（脑出血穿刺引流路径）

输入：
  - CT NIfTI（用于物理坐标变换）
  - 颅骨 mask    *_skull_mask.nii.gz      （用于提取外表面 = 候选入颅点）
  - 血肿 mask    *_hematoma_mask.nii.gz   （提供目标点）
  - 颅腔 mask    *_intracranial_mask.nii.gz （路径必须经过脑实质内）

输出（每个有血肿的病例）：
  *_paths.json        Top-N 候选路径（入颅点/目标点/长度/角度/评分等）
  *_paths_3d.png      3D：颅骨半透明 + 血肿 + Top-N 路径线
  *_paths_overlay.png 穿过血肿中心的 2D 切片 + 路径投影

算法概要：
  1) 颅骨外表面采样 -> 候选入颅点；排除面部/颅底，仅保留上半颅
  2) 目标点 = 沿血肿 PCA 长轴等距采样 N 个点（默认 3：proximal/centroid/distal），
     每个点都"吸附"到血肿 mask 内最近体素，保证物理可达
  3) 对每条 (entry -> target_k) 射线，沿线密集采样体素：
     - 必须只在入颅点处穿越颅骨（不能再穿）
     - 路径主体必须落在脑组织/颅腔内
     - 不得穿越禁区（脑室/血管/脑干/功能区，可配 margin）
     - 计算路径长度 + 入颅夹角
  4) 多目标加权评分；为保证每个目标都有候选，先按目标分别取 Top-K，
     再合并、按入颅点物理距离去重，输出全局 Top-N

依赖：
  pip install -r requirements.txt
  (SimpleITK, numpy, scipy, scikit-image, matplotlib, nibabel)

用法：
  python path_planning.py
  python path_planning.py --top-n 10 --max-entry-points 1500
  python path_planning.py --w-length 0.6 --w-angle 0.4
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


# ---------- 安全 IO（中文路径） ----------
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


def read_nifti(path: Path) -> sitk.Image:
    nii = nib.load(str(path))
    arr_xyz = np.asarray(nii.dataobj)
    arr_zyx = np.transpose(arr_xyz, (2, 1, 0)).copy()
    img = sitk.GetImageFromArray(arr_zyx)
    o, s, d = _nib_affine_to_sitk(nii.affine)
    img.SetOrigin(o); img.SetSpacing(s); img.SetDirection(d)
    return img


# ---------- 候选入颅点采样 ----------
def sample_entry_points(skull: np.ndarray, spacing: Tuple[float, float, float],
                        ct_img: sitk.Image,
                        max_points: int = 1500,
                        upper_z_ratio: float = 0.45,
                        only_outer: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """
    在颅骨外表面均匀采样候选入颅点。
    返回：(points_ijk_zyx [N,3], normals_zyx [N,3] 朝外单位向量)

    upper_z_ratio: 仅保留 z >= z_min + upper_z_ratio*(z_max-z_min) 的部分（避开面部/颅底）。
    only_outer:    仅保留外表面（用形态学：颅骨与"颅骨外膨胀"相交，去掉内表面）。
    """
    z_dim = skull.shape[0]

    # 1) 用 marching_cubes 提颅骨表面（spacing 顺序 z,y,x）
    verts, faces, normals, _ = measure.marching_cubes(
        skull.astype(np.uint8), level=0.5,
        spacing=(spacing[2], spacing[1], spacing[0]),
        allow_degenerate=False,
    )
    # verts/normals 是 (z_phys, y_phys, x_phys) 顺序

    # 2) 把物理坐标转回 ijk 体素索引（z,y,x）
    verts_ijk = np.zeros_like(verts)
    verts_ijk[:, 0] = verts[:, 0] / spacing[2]  # z 索引 = z_phys / sz
    verts_ijk[:, 1] = verts[:, 1] / spacing[1]
    verts_ijk[:, 2] = verts[:, 2] / spacing[0]

    # 3) z 上半过滤（颅骨上半 = 顶/额骨/上颞骨；下半为颅底面部）
    z_min, z_max = verts_ijk[:, 0].min(), verts_ijk[:, 0].max()
    z_threshold = z_min + (z_max - z_min) * upper_z_ratio
    keep = verts_ijk[:, 0] >= z_threshold
    verts_ijk = verts_ijk[keep]
    normals = normals[keep]

    # 4) 仅保留外表面：法向量朝向脑外（远离质心）
    if only_outer and len(verts_ijk) > 0:
        skull_centroid_ijk = np.array(np.where(skull)).mean(axis=1)  # (z,y,x)
        # marching_cubes 法向量在物理空间，先转回 ijk 单位（除以 spacing）并归一化
        normals_ijk = normals.copy()
        normals_ijk[:, 0] /= spacing[2]
        normals_ijk[:, 1] /= spacing[1]
        normals_ijk[:, 2] /= spacing[0]
        normals_ijk /= (np.linalg.norm(normals_ijk, axis=1, keepdims=True) + 1e-9)
        # 顶点指向质心方向（用于判断"内向"）
        to_center = skull_centroid_ijk[None, :] - verts_ijk
        to_center_norm = to_center / (np.linalg.norm(to_center, axis=1, keepdims=True) + 1e-9)
        # 外表面：法向量与"指向质心"方向相反（dot < 0）
        outer = np.einsum("ij,ij->i", normals_ijk, to_center_norm) < 0
        verts_ijk = verts_ijk[outer]
        normals_ijk = normals_ijk[outer]
        to_center_norm = to_center_norm[outer]
        # 强制朝外：若仍朝内（dot>0）则取反
        flip_mask = np.einsum("ij,ij->i", normals_ijk, to_center_norm) > 0
        normals_ijk[flip_mask] = -normals_ijk[flip_mask]
    else:
        normals_ijk = np.zeros_like(verts_ijk)

    # 5) 下采样到 max_points
    if len(verts_ijk) > max_points:
        idx = np.random.RandomState(42).choice(len(verts_ijk), max_points, replace=False)
        verts_ijk = verts_ijk[idx]
        normals_ijk = normals_ijk[idx]

    return verts_ijk, normals_ijk


# ---------- 路径采样（射线沿线检查） ----------
def sample_line_voxels(p0_zyx: np.ndarray, p1_zyx: np.ndarray,
                       step_voxel: float = 0.5) -> np.ndarray:
    """从 p0 到 p1 等步长采样体素整数坐标。返回 [M, 3]。"""
    diff = p1_zyx - p0_zyx
    n = max(int(np.ceil(np.linalg.norm(diff) / step_voxel)), 2)
    ts = np.linspace(0.0, 1.0, n)
    pts = p0_zyx[None, :] + ts[:, None] * diff[None, :]
    return np.round(pts).astype(np.int64)


# ---------- 多目标点采样（沿血肿 PCA 长轴） ----------
def sample_targets_along_pca_axis(
    hematoma: np.ndarray,
    spacing: Tuple[float, float, float],
    n_targets: int = 3,
    coverage: float = 0.7,
) -> List[Dict]:
    """
    沿血肿 PCA 主轴采样 N 个目标点。

    返回列表，每项 dict：
      - idx        : 0..N-1
      - role       : 'centroid' / 'distal_+' / 'distal_-' / 'mid_+' / 'mid_-'
      - ijk_zyx    : np.ndarray(3,) 体素坐标（已吸附到血肿 mask 内）
      - axis_t     : float 在主轴上的归一化位置 ∈ [-1, 1]

    coverage: 沿主轴采样范围占主轴半长的比例（0~1），<1 防止采到血肿边缘。
    """
    zs, ys, xs = np.where(hematoma > 0)
    if len(zs) == 0:
        return []

    # 体素坐标 -> 物理坐标（mm）做 PCA
    pts_ijk = np.stack([zs, ys, xs], axis=1).astype(np.float64)  # (M, 3) z,y,x
    sp = np.array([spacing[2], spacing[1], spacing[0]])  # 对应 z,y,x mm
    pts_phys = pts_ijk * sp[None, :]
    centroid_phys = pts_phys.mean(axis=0)
    X = pts_phys - centroid_phys

    if len(X) >= 3:
        cov = np.cov(X.T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        # 主轴 = 最大特征值对应特征向量
        order = np.argsort(eigvals)[::-1]
        axis = eigvecs[:, order[0]]  # 单位向量（z,y,x mm 方向）
        # 沿主轴投影范围
        proj = X @ axis
        p_lo, p_hi = proj.min(), proj.max()
    else:
        axis = np.array([1.0, 0.0, 0.0])
        p_lo, p_hi = -1.0, 1.0

    centroid_ijk = pts_ijk.mean(axis=0)

    # 生成 N 个采样位置（覆盖主轴 [p_lo, p_hi] 的 coverage 比例）
    t_min = p_lo * coverage
    t_max = p_hi * coverage
    if n_targets <= 1:
        ts = np.array([0.0])
    else:
        ts = np.linspace(t_min, t_max, n_targets)

    # 物理坐标 -> 体素坐标（除以 spacing）
    targets: List[Dict] = []
    half = (p_hi - p_lo) / 2 + 1e-9
    for i, t in enumerate(ts):
        target_phys = centroid_phys + t * axis
        target_ijk = target_phys / sp  # (z,y,x)

        # 吸附到血肿 mask 内最近体素（避免主轴端点跑出 mask）
        target_ijk = _snap_to_mask(target_ijk, hematoma)
        if target_ijk is None:
            continue

        # 角色标签
        norm_t = float(t / half)  # 约 [-coverage, coverage]
        if abs(norm_t) < 0.15:
            role = "centroid"
        elif norm_t > 0:
            role = "distal_+" if abs(norm_t) > 0.5 else "mid_+"
        else:
            role = "distal_-" if abs(norm_t) > 0.5 else "mid_-"

        targets.append({
            "idx": i,
            "role": role,
            "ijk_zyx": target_ijk,
            "axis_t": round(norm_t, 3),
        })

    # 去重：相同体素坐标只保留一个
    uniq: List[Dict] = []
    seen = set()
    for tg in targets:
        key = tuple(np.round(tg["ijk_zyx"]).astype(int).tolist())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(tg)

    # 至少保留质心
    if not uniq:
        uniq = [{
            "idx": 0, "role": "centroid",
            "ijk_zyx": centroid_ijk, "axis_t": 0.0,
        }]
    # 重新编号
    for k, tg in enumerate(uniq):
        tg["idx"] = k
    return uniq


def _snap_to_mask(p_ijk: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
    """若 p 已在 mask 内则原样返回；否则返回 mask 中欧氏距离最近的体素坐标。"""
    z, y, x = p_ijk
    z_max, y_max, x_max = mask.shape
    iz = int(np.clip(round(z), 0, z_max - 1))
    iy = int(np.clip(round(y), 0, y_max - 1))
    ix = int(np.clip(round(x), 0, x_max - 1))
    if mask[iz, iy, ix] > 0:
        return p_ijk
    zs, ys, xs = np.where(mask > 0)
    if len(zs) == 0:
        return None
    d2 = (zs - z) ** 2 + (ys - y) ** 2 + (xs - x) ** 2
    j = int(np.argmin(d2))
    return np.array([float(zs[j]), float(ys[j]), float(xs[j])])


def evaluate_path(entry_ijk: np.ndarray, target_ijk: np.ndarray,
                  skull: np.ndarray, intracranial: np.ndarray, hematoma: np.ndarray,
                  spacing: Tuple[float, float, float],
                  step_voxel: float = 0.5,
                  max_skull_voxels_at_entry: int = 14,
                  max_skull_after_entry: int = 4,
                  min_intracranial_ratio: float = 0.55,
                  forbidden_masks: Optional[List[Tuple[str, np.ndarray]]] = None,
                  reject_stats: Optional[Dict[str, int]] = None) -> Optional[Dict]:
    """
    评估一条 (entry -> target) 路径。返回 dict 或 None（如果非法）。

    合法性：
      - 入颅段（entry 段连续穿骨且不在颅腔）厚度 <= max_skull_voxels_at_entry
      - 入颅之后路径上额外的 skull 击中 <= max_skull_after_entry
      - 入颅之后路径主体（>= min_intracranial_ratio）落在颅腔内
      - 路径主体不得穿过任何 forbidden_masks（每个为 (name, mask) 二元组；命中即拒绝）
        注意：终点在血肿内不算"穿过禁区"，所以最后 3 个体素免检。
    指标：
      length_mm        路径长度（mm）
      passes_skull     穿过的骨体素数（不含入颅段）
      hits_target      末端是否进入血肿
      forbidden_hits   各禁区被穿过的体素数（dict）
    """
    pts = sample_line_voxels(entry_ijk, target_ijk, step_voxel=step_voxel)

    z_max, y_max, x_max = skull.shape
    valid = ((pts[:, 0] >= 0) & (pts[:, 0] < z_max) &
             (pts[:, 1] >= 0) & (pts[:, 1] < y_max) &
             (pts[:, 2] >= 0) & (pts[:, 2] < x_max))
    if not valid.all():
        if reject_stats is not None: reject_stats["out_of_bounds"] = reject_stats.get("out_of_bounds", 0) + 1
        return None
    zs, ys, xs = pts[:, 0], pts[:, 1], pts[:, 2]

    skull_hits = skull[zs, ys, xs].astype(bool)
    intra_hits = intracranial[zs, ys, xs].astype(bool)
    hema_hits = hematoma[zs, ys, xs].astype(bool)

    # 入颅段
    n = len(pts)
    i = 0
    entry_skull_count = 0
    while i < n and (skull_hits[i] or not intra_hits[i]):
        if skull_hits[i]:
            entry_skull_count += 1
        i += 1
        if i < n and intra_hits[i] and not skull_hits[i]:
            break
    if entry_skull_count > max_skull_voxels_at_entry:
        if reject_stats is not None: reject_stats["entry_too_thick"] = reject_stats.get("entry_too_thick", 0) + 1
        return None

    rest_skull = int(skull_hits[i:].sum())
    if rest_skull > max_skull_after_entry:
        if reject_stats is not None: reject_stats["multi_skull_cross"] = reject_stats.get("multi_skull_cross", 0) + 1
        return None

    in_intra_ratio = float(intra_hits[i:].mean()) if n - i > 0 else 0.0
    if in_intra_ratio < min_intracranial_ratio:
        if reject_stats is not None: reject_stats["low_intracranial_ratio"] = reject_stats.get("low_intracranial_ratio", 0) + 1
        return None

    # 禁区检查：除最后 3 个体素外不得命中任何禁区
    body_slice = slice(i, max(n - 3, i))
    forbidden_hits_dict: Dict[str, int] = {}
    if forbidden_masks:
        for name, fm in forbidden_masks:
            if fm is None:
                continue
            fh = fm[zs, ys, xs].astype(bool)
            cnt = int(fh[body_slice].sum())
            forbidden_hits_dict[name] = cnt
            if cnt > 0:
                if reject_stats is not None:
                    key = f"hit_{name}"
                    reject_stats[key] = reject_stats.get(key, 0) + 1
                return None

    hits_target = bool(hema_hits[-3:].any())

    diff_phys = ((target_ijk - entry_ijk) * np.array([spacing[2], spacing[1], spacing[0]]))
    length_mm = float(np.linalg.norm(diff_phys))

    return {
        "length_mm": length_mm,
        "passes_skull_after_entry": rest_skull,
        "in_intracranial_ratio": round(in_intra_ratio, 3),
        "hits_target": hits_target,
        "forbidden_hits": forbidden_hits_dict,
        "entry_skull_thickness_voxels": int(entry_skull_count),
        "entry_ijk_zyx": [round(float(v), 2) for v in entry_ijk],
        "target_ijk_zyx": [round(float(v), 2) for v in target_ijk],
    }


# ---------- 物理坐标转换 ----------
def ijk_to_phys(ct_img: sitk.Image, ijk_zyx: np.ndarray) -> np.ndarray:
    """传入 (z,y,x) 索引，返回 LPS 物理坐标 (x,y,z) mm。支持 [N,3] 数组或单点。"""
    pts = np.atleast_2d(ijk_zyx)
    out = np.zeros_like(pts, dtype=np.float64)
    for i, p in enumerate(pts):
        z, y, x = p
        phys = ct_img.TransformContinuousIndexToPhysicalPoint((float(x), float(y), float(z)))
        out[i] = phys
    return out if pts.shape[0] > 1 else out[0]


# ---------- 可视化 ----------
def render_3d(skull: np.ndarray, hema: np.ndarray, paths: List[Dict],
              spacing: Tuple[float, float, float], out_png: Path, title: str,
              ventricle: Optional[np.ndarray] = None,
              vessel: Optional[np.ndarray] = None,
              brainstem: Optional[np.ndarray] = None,
              eloquent: Optional[np.ndarray] = None,
              targets: Optional[List[Dict]] = None,
              downsample: int = 3) -> None:
    fig = plt.figure(figsize=(12, 10))
    ax = fig.add_subplot(111, projection="3d")

    def _ds(arr): return arr[::downsample, ::downsample, ::downsample] if downsample > 1 else arr
    sp = (spacing[0] * downsample, spacing[1] * downsample, spacing[2] * downsample)

    all_v = None
    if skull.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(skull).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.12, linewidths=0)
        m.set_facecolor((0.95, 0.92, 0.85)); m.set_edgecolor("none")
        ax.add_collection3d(m)
        all_v = v

    if hema.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(hema).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.95, linewidths=0)
        m.set_facecolor((0.95, 0.15, 0.15)); m.set_edgecolor("none")
        ax.add_collection3d(m)
        all_v = v if all_v is None else np.vstack([all_v, v])

    # 脑室（青色半透明）
    if ventricle is not None and ventricle.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(ventricle).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.45, linewidths=0)
        m.set_facecolor((0.20, 0.75, 0.90)); m.set_edgecolor("none")
        ax.add_collection3d(m)
        all_v = v if all_v is None else np.vstack([all_v, v])

    # 血管禁区（橙色半透明）
    if vessel is not None and vessel.sum() > 0:
        v, f, _, _ = measure.marching_cubes(_ds(vessel).astype(np.uint8), level=0.5,
                                            spacing=(sp[2], sp[1], sp[0]),
                                            allow_degenerate=False)
        m = Poly3DCollection(v[f], alpha=0.30, linewidths=0)
        m.set_facecolor((1.0, 0.55, 0.10)); m.set_edgecolor("none")
        ax.add_collection3d(m)
        all_v = v if all_v is None else np.vstack([all_v, v])

    # 脑干（棕色）
    if brainstem is not None and brainstem.sum() > 0:
        ds_bs = _ds(brainstem).astype(np.uint8)
        if ds_bs.max() > 0:
            v, f, _, _ = measure.marching_cubes(ds_bs, level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]),
                                                allow_degenerate=False)
            m = Poly3DCollection(v[f], alpha=0.55, linewidths=0)
            m.set_facecolor((0.55, 0.40, 0.10)); m.set_edgecolor("none")
            ax.add_collection3d(m)
            all_v = v if all_v is None else np.vstack([all_v, v])

    # 功能区（粉紫色合并显示）
    if eloquent is not None and eloquent.sum() > 0:
        ds_el = _ds(eloquent).astype(np.uint8)
        if ds_el.max() > 0:
            v, f, _, _ = measure.marching_cubes(ds_el, level=0.5,
                                                spacing=(sp[2], sp[1], sp[0]),
                                                allow_degenerate=False)
            m = Poly3DCollection(v[f], alpha=0.20, linewidths=0)
            m.set_facecolor((0.85, 0.30, 0.65)); m.set_edgecolor("none")
            ax.add_collection3d(m)
            all_v = v if all_v is None else np.vstack([all_v, v])

    # 路径线（注意 verts 物理坐标顺序是 (z,y,x)，render 轴也相同）
    cmap = plt.get_cmap("viridis")
    n_paths = len(paths)
    for i, p in enumerate(paths):
        e = np.array(p["entry_ijk_zyx"]) * np.array([spacing[2], spacing[1], spacing[0]])
        t = np.array(p["target_ijk_zyx"]) * np.array([spacing[2], spacing[1], spacing[0]])
        color = cmap(i / max(n_paths - 1, 1))
        ax.plot([e[0], t[0]], [e[1], t[1]], [e[2], t[2]],
                color=color, linewidth=2.0, alpha=0.9)
        # 入颅点小球
        ax.scatter([e[0]], [e[1]], [e[2]], color=color, s=30, edgecolor="k", linewidth=0.5)

    # 多目标点（黄色星标）
    if targets:
        for tg in targets:
            t_ijk = tg["ijk_zyx"]
            tp = np.array(t_ijk) * np.array([spacing[2], spacing[1], spacing[0]])
            ax.scatter([tp[0]], [tp[1]], [tp[2]], marker="*", s=140,
                       color=(1.0, 0.95, 0.20), edgecolor="k", linewidth=0.6, zorder=10)

    if all_v is not None:
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


def render_overlay(ct: np.ndarray, skull: np.ndarray, hema: np.ndarray,
                   paths: List[Dict], target_ijk: np.ndarray,
                   spacing: Tuple[float, float, float],
                   out_png: Path, title: str,
                   ventricle: Optional[np.ndarray] = None,
                   vessel: Optional[np.ndarray] = None,
                   brainstem: Optional[np.ndarray] = None,
                   eloquent: Optional[np.ndarray] = None,
                   targets: Optional[List[Dict]] = None) -> None:
    """三视图：经过血肿中心切片，叠加 Top-N 路径在该平面的投影。"""
    z, y, x = ct.shape
    cz = int(round(target_ijk[0]))
    cy = int(round(target_ijk[1]))
    cx = int(round(target_ijk[2]))

    def win(a, wl=40, ww=80):
        lo, hi = wl - ww/2, wl + ww/2
        return np.clip((a - lo) / (hi - lo), 0, 1)

    def overlay_color(mask, rgb_a):
        out = np.zeros(mask.shape + (4,), dtype=np.float32)
        out[..., 0] = rgb_a[0]; out[..., 1] = rgb_a[1]; out[..., 2] = rgb_a[2]
        out[..., 3] = mask.astype(np.float32) * rgb_a[3]
        return out

    cmap = plt.get_cmap("viridis")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5))

    # Axial
    ax_ax = axes[0]
    ax_ax.imshow(win(ct[cz]), cmap="gray")
    ax_ax.imshow(overlay_color(skull[cz], (1.0, 1.0, 1.0, 0.30)))
    if eloquent is not None:
        ax_ax.imshow(overlay_color(eloquent[cz], (0.85, 0.30, 0.65, 0.18)))
    if vessel is not None:
        ax_ax.imshow(overlay_color(vessel[cz], (1.0, 0.55, 0.10, 0.30)))
    if brainstem is not None:
        ax_ax.imshow(overlay_color(brainstem[cz], (0.55, 0.40, 0.10, 0.55)))
    if ventricle is not None:
        ax_ax.imshow(overlay_color(ventricle[cz], (0.20, 0.85, 0.95, 0.40)))
    ax_ax.imshow(overlay_color(hema[cz], (1.0, 0.15, 0.15, 0.65)))
    for i, p in enumerate(paths):
        e = p["entry_ijk_zyx"]; t = p["target_ijk_zyx"]
        c = cmap(i / max(len(paths) - 1, 1))
        ax_ax.plot([e[2], t[2]], [e[1], t[1]], color=c, linewidth=1.5, alpha=0.9)
        ax_ax.scatter([e[2]], [e[1]], color=c, s=20, edgecolor="k", linewidth=0.4, zorder=5)
    if targets:
        for tg in targets:
            ti = tg["ijk_zyx"]
            ax_ax.scatter([ti[2]], [ti[1]], marker="*", s=120,
                          color=(1.0, 0.95, 0.20), edgecolor="k", linewidth=0.6, zorder=6)
    ax_ax.set_title(f"Axial  z={cz}", fontsize=11); ax_ax.axis("off")

    # Coronal
    ax_co = axes[1]
    aspect_co = spacing[2] / spacing[1] if spacing[1] else 1.0
    co = ct[:, cy, :][::-1]
    sk = skull[:, cy, :][::-1]
    he = hema[:, cy, :][::-1]
    ve = ventricle[:, cy, :][::-1] if ventricle is not None else None
    vs = vessel[:, cy, :][::-1] if vessel is not None else None
    bs = brainstem[:, cy, :][::-1] if brainstem is not None else None
    el = eloquent[:, cy, :][::-1] if eloquent is not None else None
    ax_co.imshow(win(co), cmap="gray", aspect=aspect_co)
    ax_co.imshow(overlay_color(sk, (1.0, 1.0, 1.0, 0.30)), aspect=aspect_co)
    if el is not None:
        ax_co.imshow(overlay_color(el, (0.85, 0.30, 0.65, 0.18)), aspect=aspect_co)
    if vs is not None:
        ax_co.imshow(overlay_color(vs, (1.0, 0.55, 0.10, 0.30)), aspect=aspect_co)
    if bs is not None:
        ax_co.imshow(overlay_color(bs, (0.55, 0.40, 0.10, 0.55)), aspect=aspect_co)
    if ve is not None:
        ax_co.imshow(overlay_color(ve, (0.20, 0.85, 0.95, 0.40)), aspect=aspect_co)
    ax_co.imshow(overlay_color(he, (1.0, 0.15, 0.15, 0.65)), aspect=aspect_co)
    for i, p in enumerate(paths):
        e = p["entry_ijk_zyx"]; t = p["target_ijk_zyx"]
        c = cmap(i / max(len(paths) - 1, 1))
        ze = (z - 1) - e[0]; zt = (z - 1) - t[0]
        ax_co.plot([e[2], t[2]], [ze, zt], color=c, linewidth=1.5, alpha=0.9)
        ax_co.scatter([e[2]], [ze], color=c, s=20, edgecolor="k", linewidth=0.4, zorder=5)
    if targets:
        for tg in targets:
            ti = tg["ijk_zyx"]
            zi = (z - 1) - ti[0]
            ax_co.scatter([ti[2]], [zi], marker="*", s=120,
                          color=(1.0, 0.95, 0.20), edgecolor="k", linewidth=0.6, zorder=6)
    ax_co.set_title(f"Coronal  y={cy}", fontsize=11); ax_co.axis("off")

    # Sagittal
    ax_sa = axes[2]
    aspect_sa = spacing[2] / spacing[0] if spacing[0] else 1.0
    sa = ct[:, :, cx][::-1]
    sk = skull[:, :, cx][::-1]
    he = hema[:, :, cx][::-1]
    ve = ventricle[:, :, cx][::-1] if ventricle is not None else None
    vs = vessel[:, :, cx][::-1] if vessel is not None else None
    bs = brainstem[:, :, cx][::-1] if brainstem is not None else None
    el = eloquent[:, :, cx][::-1] if eloquent is not None else None
    ax_sa.imshow(win(sa), cmap="gray", aspect=aspect_sa)
    ax_sa.imshow(overlay_color(sk, (1.0, 1.0, 1.0, 0.30)), aspect=aspect_sa)
    if el is not None:
        ax_sa.imshow(overlay_color(el, (0.85, 0.30, 0.65, 0.18)), aspect=aspect_sa)
    if vs is not None:
        ax_sa.imshow(overlay_color(vs, (1.0, 0.55, 0.10, 0.30)), aspect=aspect_sa)
    if bs is not None:
        ax_sa.imshow(overlay_color(bs, (0.55, 0.40, 0.10, 0.55)), aspect=aspect_sa)
    if ve is not None:
        ax_sa.imshow(overlay_color(ve, (0.20, 0.85, 0.95, 0.40)), aspect=aspect_sa)
    ax_sa.imshow(overlay_color(he, (1.0, 0.15, 0.15, 0.65)), aspect=aspect_sa)
    for i, p in enumerate(paths):
        e = p["entry_ijk_zyx"]; t = p["target_ijk_zyx"]
        c = cmap(i / max(len(paths) - 1, 1))
        ze = (z - 1) - e[0]; zt = (z - 1) - t[0]
        ax_sa.plot([e[1], t[1]], [ze, zt], color=c, linewidth=1.5, alpha=0.9)
        ax_sa.scatter([e[1]], [ze], color=c, s=20, edgecolor="k", linewidth=0.4, zorder=5)
    if targets:
        for tg in targets:
            ti = tg["ijk_zyx"]
            zi = (z - 1) - ti[0]
            ax_sa.scatter([ti[1]], [zi], marker="*", s=120,
                          color=(1.0, 0.95, 0.20), edgecolor="k", linewidth=0.6, zorder=6)
    ax_sa.set_title(f"Sagittal  x={cx}", fontsize=11); ax_sa.axis("off")

    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---------- 主流程 ----------
def find_cases(input_dir: Path, ct_pattern: str) -> List[Dict[str, Path]]:
    """
    自动发现可规划的病例：要求同目录存在 hematoma/intracranial/skull mask 三种文件，且血肿非空。
    """
    cases: List[Dict[str, Path]] = []
    # 主 CT 体数据的命名特征：以系列号结尾（无 _mask/_brain_xxx_mask 等后缀）
    EXCLUDE_SUFFIXES = (
        "_mask.nii.gz", "_overlay.png", "_3d.png",
        "_preview.png", "_stats.txt", "_report.json",
    )
    for ct in sorted(input_dir.rglob(ct_pattern)):
        if any(ct.name.endswith(s) for s in EXCLUDE_SUFFIXES):
            continue
        d = ct.parent
        stem = ct.name[:-len(".nii.gz")]  # 去 .nii.gz 后缀
        hema_p  = d / f"{stem}_hematoma_mask.nii.gz"
        intra_p = d / f"{stem}_intracranial_mask.nii.gz"
        if not (hema_p.exists() and intra_p.exists()):
            continue
        skull_list = sorted(d.glob("*_skull_mask.nii.gz"))
        if not skull_list:
            continue
        skull_list.sort(key=lambda p: (0 if "0.80" in p.name else 1, p.name))
        vent_p = d / f"{stem}_ventricle_mask.nii.gz"
        vessel_p = d / f"{stem}_vessel_risk_mask.nii.gz"
        brainstem_p = d / f"{stem}_brainstem_mask.nii.gz"
        eloquent_p = d / f"{stem}_eloquent_zone_mask.nii.gz"
        cases.append({
            "ct": ct,
            "hematoma": hema_p,
            "intracranial": intra_p,
            "skull": skull_list[0],
            "ventricle": vent_p if vent_p.exists() else None,
            "vessel_risk": vessel_p if vessel_p.exists() else None,
            "brainstem": brainstem_p if brainstem_p.exists() else None,
            "eloquent": eloquent_p if eloquent_p.exists() else None,
        })
    return cases


def main() -> int:
    ap = argparse.ArgumentParser(description="脑出血穿刺路径规划")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz")
    ap.add_argument("--top-n", type=int, default=8, help="输出前 N 条候选路径")
    ap.add_argument("--max-entry-points", type=int, default=1500,
                    help="候选入颅点最大数量（下采样以加速）")
    ap.add_argument("--upper-z-ratio", type=float, default=0.45,
                    help="仅保留 z >= z_min + ratio*(z_max-z_min) 的颅骨表面")
    ap.add_argument("--step-voxel", type=float, default=0.5, help="路径采样步长（体素单位）")
    ap.add_argument("--w-length", type=float, default=0.6, help="评分中长度权重（越短越好）")
    ap.add_argument("--w-angle", type=float, default=0.4, help="评分中入颅角度权重（越小越好）")
    ap.add_argument("--ventricle-margin-mm", type=float, default=3.0,
                    help="路径与脑室壁的最小安全距离（mm）；<=0 表示不使用脑室禁区")
    ap.add_argument("--vessel-margin-mm", type=float, default=2.0,
                    help="路径与血管风险区的最小安全距离（mm）；<=0 表示不使用血管禁区")
    ap.add_argument("--brainstem-margin-mm", type=float, default=2.0,
                    help="路径与脑干的最小安全距离（mm）；<=0 表示不使用脑干禁区")
    ap.add_argument("--eloquent-margin-mm", type=float, default=0.0,
                    help="路径与功能区的最小安全距离（mm）；功能区本身已是保守近似，"
                         "默认 0 表示直接用 mask 不再扩张；<0 表示不使用功能区禁区")
    ap.add_argument("--no-3d", action="store_true")
    ap.add_argument("--n-targets", type=int, default=3,
                    help="沿血肿 PCA 长轴采样目标点数量；1 表示只用质心（向后兼容）")
    ap.add_argument("--per-target-top", type=int, default=3,
                    help="每个目标点先各自取的最优路径数（保证目标覆盖度），随后再全局合并")
    ap.add_argument("--target-coverage", type=float, default=0.7,
                    help="主轴采样范围占主轴半长的比例（0~1），<1 避开血肿边缘")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cases = find_cases(in_dir, args.pattern)
    if not cases:
        print(f"[错误] 未找到可规划病例（需要 CT + skull + intracranial + hematoma 同目录）", file=sys.stderr)
        return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT pattern='{args.pattern}'  共 {len(cases)} 例")
    print(f"[参数] top-n={args.top_n}  max_entry={args.max_entry_points}  "
          f"upper_z_ratio={args.upper_z_ratio}  weights L={args.w_length}/A={args.w_angle}")
    print(f"[多目标] n_targets={args.n_targets}  per_target_top={args.per_target_top}  "
          f"coverage={args.target_coverage}\n")

    n_ok, n_skip, n_err = 0, 0, 0
    for case in cases:
        ct_path: Path = case["ct"]
        print(f"=== Case: {ct_path.name} ===")
        try:
            ct_img = read_nifti(ct_path)
            skull_img = read_nifti(case["skull"])
            intra_img = read_nifti(case["intracranial"])
            hema_img  = read_nifti(case["hematoma"])
            vent_img  = read_nifti(case["ventricle"]) if case.get("ventricle") else None
            vessel_img = read_nifti(case["vessel_risk"]) if case.get("vessel_risk") else None
            bs_img = read_nifti(case["brainstem"]) if case.get("brainstem") else None
            elo_img = read_nifti(case["eloquent"]) if case.get("eloquent") else None

            # 几何对齐
            for tag, im in [("skull", skull_img), ("intra", intra_img),
                            ("hema", hema_img), ("vent", vent_img), ("vessel", vessel_img),
                            ("brainstem", bs_img), ("eloquent", elo_img)]:
                if im is None: continue
                if im.GetSize() != ct_img.GetSize():
                    rs = sitk.ResampleImageFilter()
                    rs.SetReferenceImage(ct_img)
                    rs.SetInterpolator(sitk.sitkNearestNeighbor)
                    if tag == "skull": skull_img = rs.Execute(skull_img)
                    elif tag == "intra": intra_img = rs.Execute(intra_img)
                    elif tag == "hema":  hema_img  = rs.Execute(hema_img)
                    elif tag == "vent":  vent_img  = rs.Execute(vent_img)
                    elif tag == "vessel": vessel_img = rs.Execute(vessel_img)
                    elif tag == "brainstem": bs_img = rs.Execute(bs_img)
                    elif tag == "eloquent": elo_img = rs.Execute(elo_img)
                    print(f"  [警告] 重采样 {tag} mask 到 CT 网格")

            ct_arr = sitk.GetArrayFromImage(ct_img).astype(np.float32)
            skull = (sitk.GetArrayFromImage(skull_img) > 0).astype(np.uint8)
            intra = (sitk.GetArrayFromImage(intra_img) > 0).astype(np.uint8)
            hema  = (sitk.GetArrayFromImage(hema_img)  > 0).astype(np.uint8)
            vent  = (sitk.GetArrayFromImage(vent_img)  > 0).astype(np.uint8) if vent_img else None
            vessel = (sitk.GetArrayFromImage(vessel_img) > 0).astype(np.uint8) if vessel_img else None
            brainstem = (sitk.GetArrayFromImage(bs_img) > 0).astype(np.uint8) if bs_img else None
            eloquent  = (sitk.GetArrayFromImage(elo_img) > 0).astype(np.uint8) if elo_img else None
            spacing = ct_img.GetSpacing()

            # —— 构造禁区列表 ——
            forbidden_masks: List[Tuple[str, np.ndarray]] = []

            # 1) 脑室禁区
            if vent is not None and vent.sum() > 0 and args.ventricle_margin_mm > 0:
                d2v = ndi.distance_transform_edt(
                    ~vent.astype(bool),
                    sampling=(spacing[2], spacing[1], spacing[0]))
                vent_forbidden = (d2v <= args.ventricle_margin_mm).astype(np.uint8)
                vent_forbidden &= (~hema.astype(bool)).astype(np.uint8)  # 血肿免检
                forbidden_masks.append(("ventricle", vent_forbidden))
                print(f"  -> 脑室禁区 (margin {args.ventricle_margin_mm}mm) "
                      f"voxels={int(vent_forbidden.sum())}")
            elif vent is None:
                print(f"  -> 未找到脑室 mask")

            # 2) 血管风险禁区
            if vessel is not None and vessel.sum() > 0 and args.vessel_margin_mm > 0:
                d2vessel = ndi.distance_transform_edt(
                    ~vessel.astype(bool),
                    sampling=(spacing[2], spacing[1], spacing[0]))
                vessel_forbidden = (d2vessel <= args.vessel_margin_mm).astype(np.uint8)
                vessel_forbidden &= (~hema.astype(bool)).astype(np.uint8)
                forbidden_masks.append(("vessel", vessel_forbidden))
                print(f"  -> 血管禁区 (margin {args.vessel_margin_mm}mm) "
                      f"voxels={int(vessel_forbidden.sum())}")
            elif vessel is None:
                print(f"  -> 未找到血管 risk mask")

            # 3) 脑干禁区
            if brainstem is not None and brainstem.sum() > 0 and args.brainstem_margin_mm > 0:
                d2bs = ndi.distance_transform_edt(
                    ~brainstem.astype(bool),
                    sampling=(spacing[2], spacing[1], spacing[0]))
                bs_forbidden = (d2bs <= args.brainstem_margin_mm).astype(np.uint8)
                bs_forbidden &= (~hema.astype(bool)).astype(np.uint8)
                forbidden_masks.append(("brainstem", bs_forbidden))
                print(f"  -> 脑干禁区 (margin {args.brainstem_margin_mm}mm) "
                      f"voxels={int(bs_forbidden.sum())}")
            elif brainstem is None:
                print(f"  -> 未找到 brainstem mask")

            # 4) 功能区禁区
            if eloquent is not None and eloquent.sum() > 0 and args.eloquent_margin_mm >= 0:
                if args.eloquent_margin_mm > 0:
                    d2el = ndi.distance_transform_edt(
                        ~eloquent.astype(bool),
                        sampling=(spacing[2], spacing[1], spacing[0]))
                    el_forbidden = (d2el <= args.eloquent_margin_mm).astype(np.uint8)
                else:
                    el_forbidden = eloquent.copy()
                el_forbidden &= (~hema.astype(bool)).astype(np.uint8)
                forbidden_masks.append(("eloquent", el_forbidden))
                print(f"  -> 功能区禁区 (margin {args.eloquent_margin_mm}mm) "
                      f"voxels={int(el_forbidden.sum())}")
            elif eloquent is None:
                print(f"  -> 未找到 eloquent mask")

            if hema.sum() == 0:
                print("  [跳过] 血肿 mask 为空")
                n_skip += 1
                continue

            # 1) 候选入颅点
            entries_ijk, normals_ijk = sample_entry_points(
                skull, spacing, ct_img,
                max_points=args.max_entry_points,
                upper_z_ratio=args.upper_z_ratio,
                only_outer=True,
            )
            print(f"  -> 候选入颅点: {len(entries_ijk)}")

            # 2) 目标点 = 沿血肿 PCA 主轴 N 点（默认 3）
            zs, ys, xs = np.where(hema)
            centroid_ijk = np.array([zs.mean(), ys.mean(), xs.mean()])
            centroid_phys = ijk_to_phys(ct_img, centroid_ijk)

            targets = sample_targets_along_pca_axis(
                hema, spacing,
                n_targets=max(args.n_targets, 1),
                coverage=args.target_coverage,
            )
            print(f"  -> 血肿质心 ijk=({centroid_ijk[0]:.1f},{centroid_ijk[1]:.1f},{centroid_ijk[2]:.1f})  "
                  f"phys_mm=({centroid_phys[0]:.1f},{centroid_phys[1]:.1f},{centroid_phys[2]:.1f})")
            print(f"  -> 目标点数: {len(targets)}")
            for tg in targets:
                t_ijk = tg["ijk_zyx"]
                t_phys = ijk_to_phys(ct_img, t_ijk)
                tg["phys_lps_mm"] = t_phys
                print(f"     [{tg['idx']}] role={tg['role']:>10s}  t={tg['axis_t']:+.2f}  "
                      f"ijk=({t_ijk[0]:.1f},{t_ijk[1]:.1f},{t_ijk[2]:.1f})  "
                      f"phys=({t_phys[0]:.1f},{t_phys[1]:.1f},{t_phys[2]:.1f})")

            # 3) 评估每条 (entry, target_k) 路径
            results: List[Dict] = []
            reject_stats: Dict[str, int] = {}
            for tg in targets:
                t_ijk = tg["ijk_zyx"]
                t_idx = tg["idx"]
                t_role = tg["role"]
                for entry, normal in zip(entries_ijk, normals_ijk):
                    r = evaluate_path(entry, t_ijk, skull, intra, hema, spacing,
                                      step_voxel=args.step_voxel,
                                      forbidden_masks=forbidden_masks,
                                      reject_stats=reject_stats)
                    if r is None:
                        continue
                    # 入颅角度（路径方向 vs 法向量内向 = -normal）
                    path_dir_phys = (t_ijk - entry) * np.array([spacing[2], spacing[1], spacing[0]])
                    inward_normal_phys = -normal * np.array([spacing[2], spacing[1], spacing[0]])
                    pn = np.linalg.norm(path_dir_phys); nn = np.linalg.norm(inward_normal_phys)
                    if pn < 1e-9 or nn < 1e-9:
                        angle_deg = 90.0
                    else:
                        cos = float(np.dot(path_dir_phys, inward_normal_phys) / (pn * nn))
                        cos = max(-1.0, min(1.0, cos))
                        angle_deg = float(np.degrees(np.arccos(cos)))
                    r["angle_to_normal_deg"] = round(angle_deg, 2)
                    r["target_idx"] = t_idx
                    r["target_role"] = t_role
                    results.append(r)

            print(f"  -> 合法路径: {len(results)}  (跨 {len(targets)} 个目标点)")
            if reject_stats:
                print(f"     拒绝统计: {reject_stats}")
            if not results:
                print("  [跳过] 无合法路径"); n_skip += 1; continue

            # 4) 评分（全局归一化）
            lens = np.array([r["length_mm"] for r in results])
            angs = np.array([r["angle_to_normal_deg"] for r in results])
            l_range = float(np.ptp(lens)) + 1e-9
            a_range = float(np.ptp(angs)) + 1e-9
            l_n = (lens - lens.min()) / l_range
            a_n = (angs - angs.min()) / a_range
            score = args.w_length * l_n + args.w_angle * a_n
            for r, s in zip(results, score):
                r["score"] = round(float(s), 4)

            # —— 5) 多目标分组挑选：先按 target 各取 Top-K，再合并、按入颅点 8mm 去重，截断到 top_n ——
            results.sort(key=lambda r: r["score"])
            per_target_top = max(args.per_target_top, 1)
            grouped: Dict[int, List[Dict]] = {}
            for r in results:
                grouped.setdefault(r["target_idx"], []).append(r)
            # 每个目标的前 K 条作为候选池
            candidate_pool: List[Dict] = []
            for t_idx in sorted(grouped.keys()):
                candidate_pool.extend(grouped[t_idx][:per_target_top])
            # 候选池按 score 排序
            candidate_pool.sort(key=lambda r: r["score"])

            # 入颅点空间去重（8mm bin），同时尽量保证每个目标至少 1 条
            picked: List[Dict] = []
            picked_phys: List[np.ndarray] = []
            seen_targets: set = set()
            BIN_MM = 8.0

            def _try_add(r: Dict, enforce_dedup: bool = True) -> bool:
                e_phys = ijk_to_phys(ct_img, np.array(r["entry_ijk_zyx"]))
                if enforce_dedup:
                    for q in picked_phys:
                        if np.linalg.norm(e_phys - q) < BIN_MM:
                            return False
                t_phys = ijk_to_phys(ct_img, np.array(r["target_ijk_zyx"]))
                r["entry_phys_lps_mm"] = [round(float(v), 2) for v in e_phys]
                r["target_phys_lps_mm"] = [round(float(v), 2) for v in t_phys]
                picked.append(r); picked_phys.append(e_phys)
                seen_targets.add(r["target_idx"])
                return True

            # 第一轮：每个目标至少塞 1 条最佳的（不做跨目标去重，保证多目标覆盖）
            for t_idx in sorted(grouped.keys()):
                for r in grouped[t_idx]:
                    if _try_add(r, enforce_dedup=False):
                        break
                if len(picked) >= args.top_n:
                    break
            # 第二轮：按全局 score 补齐剩余位
            for r in candidate_pool:
                if len(picked) >= args.top_n:
                    break
                if r in picked:
                    continue
                _try_add(r)
            # 第三轮：候选池仍不够，从全部 results 兜底
            if len(picked) < args.top_n:
                for r in results:
                    if len(picked) >= args.top_n:
                        break
                    if r in picked:
                        continue
                    _try_add(r)

            # 最终按 score 重新排序，便于可视化颜色映射有序
            picked.sort(key=lambda r: r["score"])

            # 输出
            base = ct_path.parent / ct_path.stem.replace(".nii", "")
            paths_json = base.parent / f"{base.name}_paths.json"
            paths_3d   = base.parent / f"{base.name}_paths_3d.png"
            paths_ovr  = base.parent / f"{base.name}_paths_overlay.png"

            print(f"\n  Top-{len(picked)} 路径（覆盖目标 {sorted(seen_targets)}）：")
            print(f"  {'idx':>3} {'tgt':>3} {'role':>10} {'len_mm':>8} {'angle':>7} {'hits':>5} {'score':>7}")
            for i, r in enumerate(picked, 1):
                print(f"  {i:>3} {r['target_idx']:>3} {r['target_role']:>10} "
                      f"{r['length_mm']:>8.1f} {r['angle_to_normal_deg']:>7.1f} "
                      f"{str(r['hits_target']):>5} {r['score']:>7.3f}")

            with open(paths_json, "w", encoding="utf-8") as f:
                json.dump({
                    "ct_file": ct_path.name,
                    "spacing_mm": [round(float(v), 4) for v in spacing],
                    "hematoma_centroid_ijk_zyx": [round(float(v), 2) for v in centroid_ijk],
                    "hematoma_centroid_phys_lps_mm": [round(float(v), 2) for v in centroid_phys],
                    "targets": [
                        {
                            "idx": tg["idx"],
                            "role": tg["role"],
                            "axis_t": tg["axis_t"],
                            "ijk_zyx": [round(float(v), 2) for v in tg["ijk_zyx"]],
                            "phys_lps_mm": [round(float(v), 2) for v in tg["phys_lps_mm"]],
                        } for tg in targets
                    ],
                    "params": {
                        "top_n": args.top_n,
                        "n_targets": args.n_targets,
                        "per_target_top": args.per_target_top,
                        "target_coverage": args.target_coverage,
                        "upper_z_ratio": args.upper_z_ratio,
                        "step_voxel": args.step_voxel,
                        "w_length": args.w_length,
                        "w_angle": args.w_angle,
                        "ventricle_margin_mm": args.ventricle_margin_mm,
                        "vessel_margin_mm": args.vessel_margin_mm,
                        "brainstem_margin_mm": args.brainstem_margin_mm,
                        "eloquent_margin_mm": args.eloquent_margin_mm,
                        "forbidden_zones_used": [name for name, _ in forbidden_masks],
                    },
                    "n_entry_candidates": int(len(entries_ijk)),
                    "n_legal_paths": int(len(results)),
                    "legal_paths_per_target": {str(k): len(v) for k, v in grouped.items()},
                    "reject_stats": reject_stats,
                    "paths": picked,
                }, f, ensure_ascii=False, indent=2)
            print(f"  -> json: {paths_json.name}")

            render_overlay(ct_arr, skull, hema, picked, centroid_ijk, spacing,
                           paths_ovr, title=f"{base.name}  Top-{len(picked)} paths  "
                                            f"(N_targets={len(targets)})",
                           ventricle=vent, vessel=vessel,
                           brainstem=brainstem, eloquent=eloquent,
                           targets=targets)
            print(f"  -> overlay: {paths_ovr.name}")
            if not args.no_3d:
                render_3d(skull, hema, picked, spacing, paths_3d,
                          title=f"{base.name}  Top-{len(picked)} paths "
                                f"(N_targets={len(targets)}, skull translucent)",
                          ventricle=vent, vessel=vessel,
                          brainstem=brainstem, eloquent=eloquent,
                          targets=targets)
                print(f"  -> 3D: {paths_3d.name}")

            n_ok += 1
        except Exception as e:
            n_err += 1
            print(f"  [错误] {e}")
            import traceback; traceback.print_exc()
        print()

    print(f"========== 完成 ==========\n  成功: {n_ok}  跳过: {n_skip}  失败: {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
