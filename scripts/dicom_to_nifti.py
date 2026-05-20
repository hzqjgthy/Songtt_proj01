# -*- coding: utf-8 -*-
"""
DICOM -> NIfTI 转换 + 切片预览

功能：
  1. 递归扫描指定根目录下的 DICOM 序列；
  2. 自动按 (PatientID, StudyInstanceUID, SeriesInstanceUID) 分组；
  3. 跳过定位像（Topogram / Localizer）和 Dose Report 等非体数据序列；
  4. 把每个体数据序列转成 NIfTI（.nii.gz），保留正确的世界坐标 (spacing/origin/direction)；
  5. 为每个序列保存一张三视图（轴位/冠状位/矢状位）切片预览 PNG；
  6. 输出一份 manifest.csv，列出每个序列的元数据和落盘路径。

依赖：
  pip install -r requirements.txt
  （pydicom, SimpleITK, numpy, matplotlib）

用法：
  python dicom_to_nifti.py
  python dicom_to_nifti.py --input "C:/path/to/头模CT" --output "C:/path/to/out"
  python dicom_to_nifti.py --window brain     # 用脑窗 (40/80) 预览
  python dicom_to_nifti.py --window bone      # 用骨窗 (600/2000) 预览
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import pydicom
    from pydicom.errors import InvalidDicomError
except ImportError:
    print("[错误] 未安装 pydicom，请先执行: pip install -r requirements.txt", file=sys.stderr)
    raise

try:
    import SimpleITK as sitk
except ImportError:
    print("[错误] 未安装 SimpleITK，请先执行: pip install -r requirements.txt", file=sys.stderr)
    raise

try:
    import nibabel as nib
except ImportError:
    print("[错误] 未安装 nibabel，请先执行: pip install nibabel", file=sys.stderr)
    raise

try:
    import matplotlib
    matplotlib.use("Agg")  # 无显示环境也能保存图片
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    # 配置中文字体（Windows 自带），避免标题里的中文显示为方块
    for _fname in ("Microsoft YaHei", "SimHei", "DengXian", "SimSun", "Arial Unicode MS"):
        try:
            font_manager.findfont(_fname, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [_fname]
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
except ImportError:
    print("[错误] 未安装 matplotlib，请先执行: pip install -r requirements.txt", file=sys.stderr)
    raise


# ---------- 默认路径（按当前工作区结构） ----------
DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "头模CT"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "output_nifti"


# ---------- 文件名安全化：把非 ASCII 字符替换为英文/拼音 ----------
# 常见医学术语映射，命中后整体替换成英文短词
_TERM_MAP = {
    "脑部":   "brain",
    "脑":     "brain",
    "头部":   "head",
    "头颅":   "head",
    "头":     "head",
    "颅":     "skull",
    "颅骨":   "skull",
    "胸部":   "chest",
    "腹部":   "abdomen",
    "骨":     "bone",
    "血管":   "vessel",
    "动脉":   "artery",
    "静脉":   "vein",
    "血肿":   "hematoma",
    "肿瘤":   "tumor",
    "梗死":   "infarct",
    "增强":   "enhanced",
    "平扫":   "plain",
    "定位像": "topogram",
    "定位":   "localizer",
    "矢状位": "sagittal",
    "冠状位": "coronal",
    "轴位":   "axial",
    "轴":     "axial",
    "薄层":   "thin",
    "厚层":   "thick",
    "重建":   "recon",
    "患者方案": "protocol",
    "方案":   "protocol",
    "剂量":   "dose",
    "报告":   "report",
}


def to_safe_ascii(text: str) -> str:
    """
    把任意 Unicode 字符串清洗为安全的 ASCII 文件名片段：
    - 优先把已知的中文医学术语整体替换为英文；
    - 其余非 ASCII 字符直接剥离（用于避免 GBK/UTF-8 双重编码乱码污染文件名）；
    - 把空白和文件名禁用字符替换为下划线。
    """
    if text is None:
        return ""
    s = str(text)
    # 1) 术语整体映射
    for cn, en in _TERM_MAP.items():
        if cn in s:
            s = s.replace(cn, en)
    # 2) 剥离剩余的非 ASCII 字符（含双重编码乱码字节序列）
    s = s.encode("ascii", errors="ignore").decode("ascii")
    # 3) 替换文件名禁用字符与空白
    for ch in '\\/:*?"<>|\t\r\n':
        s = s.replace(ch, "_")
    s = s.replace(" ", "_")
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("._-")
    return s or "series"


# ---------- nibabel 写 NIfTI（避免 SimpleITK 在 Windows 下中文路径打不开的问题） ----------
def _sitk_to_nib_affine(img: sitk.Image) -> np.ndarray:
    """SimpleITK (origin, spacing, direction in LPS) -> nibabel RAS+ affine。"""
    spacing = np.array(img.GetSpacing(), dtype=np.float64)
    origin = np.array(img.GetOrigin(), dtype=np.float64)
    direction = np.array(img.GetDirection(), dtype=np.float64).reshape(3, 3)
    rot = direction @ np.diag(spacing)
    lps_affine = np.eye(4)
    lps_affine[:3, :3] = rot
    lps_affine[:3, 3] = origin
    flip = np.diag([-1.0, -1.0, 1.0, 1.0])
    return flip @ lps_affine


def write_nifti(img: sitk.Image, path: Path) -> None:
    """
    用 nibabel 写出 .nii.gz：
    - 路径纯 Python IO，可正常处理 Unicode；
    - 数据数组顺序：SimpleITK 内部 (z,y,x) 转回 nibabel 期待的 (x,y,z)；
    - affine 由 SimpleITK 的 origin/spacing/direction 严格转换得到，几何完全保留。
    """
    arr_zyx = sitk.GetArrayFromImage(img)
    arr_xyz = np.transpose(arr_zyx, (2, 1, 0))
    affine = _sitk_to_nib_affine(img)
    nii = nib.Nifti1Image(arr_xyz, affine)
    nib.save(nii, str(path))


# ---------- 数据结构 ----------
@dataclass
class SeriesInfo:
    series_uid: str
    study_uid: str
    patient_id: str
    modality: str = ""
    series_desc: str = ""
    study_desc: str = ""
    body_part: str = ""
    slice_thickness: str = ""
    rows: int = 0
    cols: int = 0
    files: List[Tuple[float, str]] = field(default_factory=list)  # (slice_loc, filepath)

    @property
    def n_slices(self) -> int:
        return len(self.files)

    @property
    def safe_name(self) -> str:
        desc = to_safe_ascii(self.series_desc) or "series"
        return f"{self.modality or 'UNK'}_{desc}_{self.series_uid[-8:]}"


# ---------- 扫描 DICOM ----------
def scan_dicom(root: Path) -> Dict[str, SeriesInfo]:
    """递归扫描，按 SeriesInstanceUID 分组。"""
    series_map: Dict[str, SeriesInfo] = {}
    n_total, n_dicom, n_skipped = 0, 0, 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            n_total += 1
            fp = os.path.join(dirpath, fn)
            try:
                ds = pydicom.dcmread(fp, stop_before_pixels=True, force=False)
            except (InvalidDicomError, Exception):
                n_skipped += 1
                continue

            uid = getattr(ds, "SeriesInstanceUID", None)
            if not uid:
                n_skipped += 1
                continue

            n_dicom += 1
            if uid not in series_map:
                series_map[uid] = SeriesInfo(
                    series_uid=uid,
                    study_uid=getattr(ds, "StudyInstanceUID", ""),
                    patient_id=str(getattr(ds, "PatientID", "")),
                    modality=str(getattr(ds, "Modality", "")),
                    series_desc=str(getattr(ds, "SeriesDescription", "")),
                    study_desc=str(getattr(ds, "StudyDescription", "")),
                    body_part=str(getattr(ds, "BodyPartExamined", "")),
                    slice_thickness=str(getattr(ds, "SliceThickness", "")),
                    rows=int(getattr(ds, "Rows", 0) or 0),
                    cols=int(getattr(ds, "Columns", 0) or 0),
                )

            # 用 ImagePositionPatient[2] 或 SliceLocation 作为排序键
            ipp = getattr(ds, "ImagePositionPatient", None)
            if ipp is not None and len(ipp) == 3:
                z = float(ipp[2])
            else:
                z = float(getattr(ds, "SliceLocation", 0.0) or 0.0)
            series_map[uid].files.append((z, fp))

    print(f"[扫描] 共遍历 {n_total} 个文件，识别 DICOM {n_dicom} 个，跳过 {n_skipped} 个；")
    print(f"[扫描] 共得到 {len(series_map)} 个 series。")
    return series_map


# ---------- 序列过滤：跳过定位像/剂量报告等非体数据 ----------
SKIP_DESC_KEYWORDS = ("topogram", "localizer", "scout", "dose", "report", "patient protocol")


def is_volume_series(s: SeriesInfo) -> Tuple[bool, str]:
    if s.n_slices < 5:
        return False, f"切片数过少({s.n_slices})"
    desc_l = s.series_desc.lower()
    for kw in SKIP_DESC_KEYWORDS:
        if kw in desc_l:
            return False, f"非体数据序列(关键词:{kw})"
    if s.rows < 64 or s.cols < 64:
        return False, f"分辨率过低({s.rows}x{s.cols})"
    return True, ""


# ---------- DICOM 序列 -> SimpleITK Image ----------
def read_series_as_sitk(s: SeriesInfo) -> sitk.Image:
    """用 SimpleITK 读，自动应用 RescaleSlope/Intercept，得到 HU 值（CT）。"""
    files_sorted = [fp for _z, fp in sorted(s.files, key=lambda x: x[0])]
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(files_sorted)
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()
    img = reader.Execute()
    return img


# ---------- 窗宽窗位 ----------
WINDOWS = {
    "brain": (40, 80),       # 脑窗
    "bone":  (600, 2000),    # 骨窗
    "soft":  (50, 400),      # 软组织窗
    "lung":  (-600, 1500),   # 肺窗
    "auto":  None,
}


def apply_window(arr: np.ndarray, window: str) -> np.ndarray:
    if window == "auto" or window not in WINDOWS or WINDOWS[window] is None:
        lo, hi = np.percentile(arr, [1, 99])
    else:
        wl, ww = WINDOWS[window]
        lo, hi = wl - ww / 2, wl + ww / 2
    if hi <= lo:
        hi = lo + 1.0
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0)


# ---------- 三视图预览 ----------
def save_preview(img: sitk.Image, out_png: Path, title: str, window: str) -> None:
    """轴位/冠状位/矢状位中心切片三视图。"""
    arr = sitk.GetArrayFromImage(img)  # shape: (z, y, x)
    if arr.ndim != 3:
        print(f"  [预览] 跳过非 3D 数据 shape={arr.shape}")
        return

    z, y, x = arr.shape
    ax = arr[z // 2, :, :]          # 轴位
    co = arr[:, y // 2, :][::-1]    # 冠状位（翻转使头部朝上）
    sa = arr[:, :, x // 2][::-1]    # 矢状位（翻转使头部朝上）

    spacing = img.GetSpacing()  # (sx, sy, sz)
    aspect_co = spacing[2] / spacing[1] if spacing[1] else 1.0
    aspect_sa = spacing[2] / spacing[0] if spacing[0] else 1.0

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    for ax_obj, data, name, aspect in [
        (axes[0], apply_window(ax, window), f"Axial  z={z//2}/{z}",   1.0),
        (axes[1], apply_window(co, window), f"Coronal y={y//2}/{y}",  aspect_co),
        (axes[2], apply_window(sa, window), f"Sagittal x={x//2}/{x}", aspect_sa),
    ]:
        ax_obj.imshow(data, cmap="gray", aspect=aspect)
        ax_obj.set_title(name, fontsize=11)
        ax_obj.axis("off")

    fig.suptitle(f"{title}\nshape={arr.shape}  spacing={tuple(round(v,3) for v in spacing)}  window={window}",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ---------- 主流程 ----------
def main() -> int:
    parser = argparse.ArgumentParser(description="DICOM -> NIfTI 转换 + 切片预览")
    parser.add_argument("--input",  default=str(DEFAULT_INPUT),  help="DICOM 根目录")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出根目录")
    parser.add_argument("--window", default="brain",
                        choices=list(WINDOWS.keys()),
                        help="预览窗位 (brain/bone/soft/lung/auto)")
    parser.add_argument("--no-preview", action="store_true", help="只转换 NIfTI，不生成预览图")
    args = parser.parse_args()

    in_dir = Path(args.input).resolve()
    out_dir = Path(args.output).resolve()
    if not in_dir.exists():
        print(f"[错误] 输入目录不存在: {in_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[输入] {in_dir}")
    print(f"[输出] {out_dir}")
    print(f"[窗位] {args.window}")

    series_map = scan_dicom(in_dir)
    if not series_map:
        print("[错误] 未发现任何 DICOM 文件", file=sys.stderr)
        return 3

    # 按 patient/study 分桶，便于打印
    buckets: Dict[Tuple[str, str], List[SeriesInfo]] = defaultdict(list)
    for s in series_map.values():
        buckets[(s.patient_id, s.study_uid)].append(s)

    manifest_rows: List[Dict[str, str]] = []
    n_ok, n_skip, n_err = 0, 0, 0

    for (pid, _stu), series_list in buckets.items():
        case_dir = out_dir / f"patient_{pid or 'unknown'}"
        case_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n=== Patient {pid} | Study series 数量: {len(series_list)} ===")

        for s in sorted(series_list, key=lambda x: x.series_desc):
            ok, reason = is_volume_series(s)
            tag = "[转换]" if ok else "[跳过]"
            print(f"  {tag} desc='{s.series_desc}' modality={s.modality} "
                  f"slices={s.n_slices} thk={s.slice_thickness} "
                  f"size={s.rows}x{s.cols}{(' -> '+reason) if not ok else ''}")
            if not ok:
                n_skip += 1
                manifest_rows.append({
                    "patient": pid, "modality": s.modality, "series_desc": s.series_desc,
                    "slices": str(s.n_slices), "status": "skip", "reason": reason,
                    "nifti": "", "preview": "",
                })
                continue

            try:
                img = read_series_as_sitk(s)
                nifti_path = case_dir / f"{s.safe_name}.nii.gz"
                write_nifti(img, nifti_path)

                preview_path = ""
                if not args.no_preview:
                    pv = case_dir / f"{s.safe_name}_preview.png"
                    save_preview(img, pv, title=s.safe_name, window=args.window)
                    preview_path = str(pv)

                spacing = img.GetSpacing()
                size = img.GetSize()
                print(f"      -> {nifti_path.name}  size={size}  spacing={tuple(round(v,3) for v in spacing)}")
                manifest_rows.append({
                    "patient": pid, "modality": s.modality, "series_desc": s.series_desc,
                    "slices": str(s.n_slices), "status": "ok", "reason": "",
                    "nifti": str(nifti_path), "preview": preview_path,
                })
                n_ok += 1
            except Exception as e:
                n_err += 1
                print(f"      [错误] {e}")
                traceback.print_exc()
                manifest_rows.append({
                    "patient": pid, "modality": s.modality, "series_desc": s.series_desc,
                    "slices": str(s.n_slices), "status": "error", "reason": str(e),
                    "nifti": "", "preview": "",
                })

    # 写 manifest
    manifest_csv = out_dir / "manifest.csv"
    with open(manifest_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "patient", "modality", "series_desc", "slices", "status", "reason", "nifti", "preview"
        ])
        writer.writeheader()
        writer.writerows(manifest_rows)

    print("\n========== 完成 ==========")
    print(f"  成功转换: {n_ok}")
    print(f"  跳过序列: {n_skip}")
    print(f"  失败序列: {n_err}")
    print(f"  清单文件: {manifest_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
