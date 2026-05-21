# -*- coding: utf-8 -*-
r"""
病例综合报告（PDF）

把每个病例已有的产物（preview / 各分割 overlay+3D / 路径规划 overlay+3D / stats JSON）
汇总成一份可交付的 PDF：

  patient_<PID>/<CT_stem>_report.pdf

页面结构：
  Page 1  封面：病例信息 + 各 mask 体积概览 + 路径 Top-N 表
  Page 2  路径规划：paths_overlay + paths_3d + 多目标点描述
  Page 3+ 分割对比：每页 1 类（brain / ventricle / vessel / brainstem / eloquent）
                   左侧 overlay 三视图，右侧 3D 渲染 + stats 摘要

依赖：
  - matplotlib（已有，用 PdfPages 输出，无需 reportlab）
  - PIL（matplotlib 间接依赖）
  - 不会重新跑分割，仅消费已存在的 PNG/JSON

用法：
  python case_report.py                    # 处理 output_nifti 下所有有路径规划结果的病例
  python case_report.py --input <dir>      # 指定输入目录
  python case_report.py --pattern "*Hr40*.nii.gz"
  python case_report.py --force            # 强制覆盖已存在的 PDF
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib import font_manager
    _CJK_FONT = None
    for _fname in ("Microsoft YaHei", "SimHei", "DengXian", "SimSun"):
        try:
            font_manager.findfont(_fname, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [_fname]
            _CJK_FONT = _fname
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    # 等宽 + CJK 回退族（数字列按 Consolas 等宽，中文回退到 CJK 字体避免 ?）
    _MONO_FAMILY = ["Consolas", "Courier New"]
    if _CJK_FONT:
        _MONO_FAMILY.append(_CJK_FONT)
except ImportError:
    print("[错误] 未安装 matplotlib", file=sys.stderr); raise

try:
    from PIL import Image
except ImportError:
    print("[错误] 未安装 Pillow（matplotlib 依赖；conda 环境通常已装）", file=sys.stderr); raise

# 自然语言手术方案
sys.path.insert(0, str(Path(__file__).resolve().parent))
from narrative import build_surgical_plan_text  # noqa: E402


DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "output_nifti"


# ---------- 工具：JSON 安全读取 ----------
def _safe_json(p: Optional[Path]) -> Optional[Dict]:
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_imread(p: Optional[Path]):
    if p is None or not p.exists():
        return None
    try:
        return Image.open(p)
    except Exception:
        return None


# ---------- 数据组织 ----------
def collect_case_assets(ct: Path) -> Dict:
    """根据 CT 文件名 stem 把所有相关产物归集成一个 dict（值为路径或 None）。"""
    d = ct.parent
    stem = ct.name[:-len(".nii.gz")]

    def pick(name: str) -> Optional[Path]:
        p = d / f"{stem}_{name}"
        return p if p.exists() else None

    # 颅骨 overlay/3d 与 CT 不同 stem（Hr60），直接 glob 一次
    skull_overlay = next(iter(d.glob("*_skull_overlay.png")), None)
    skull_3d = next(iter(d.glob("*_skull_3d.png")), None)
    skull_stats = next(iter(d.glob("*_skull_stats.txt")), None)

    return {
        "ct_path": ct,
        "stem": stem,
        "patient_id": d.name.replace("patient_", ""),
        "preview": pick("preview.png"),

        "brain_overlay": pick("brain_overlay.png"),
        "brain_3d":      pick("brain_3d.png"),
        "brain_report":  pick("brain_report.json"),

        "ventricle_overlay": pick("ventricle_overlay.png"),
        "ventricle_3d":      pick("ventricle_3d.png"),
        "ventricle_stats":   pick("ventricle_stats.json"),

        "vessel_overlay": pick("vessel_overlay.png"),
        "vessel_3d":      pick("vessel_3d.png"),
        "vessel_stats":   pick("vessel_stats.json"),

        "brainstem_overlay": pick("brainstem_overlay.png"),
        "brainstem_3d":      pick("brainstem_3d.png"),
        "brainstem_stats":   pick("brainstem_stats.json"),

        "eloquent_overlay": pick("eloquent_overlay.png"),
        "eloquent_3d":      pick("eloquent_3d.png"),
        "eloquent_stats":   pick("eloquent_stats.json"),

        "skull_overlay": skull_overlay,
        "skull_3d":      skull_3d,
        "skull_stats":   skull_stats,

        "paths_overlay": pick("paths_overlay.png"),
        "paths_3d":      pick("paths_3d.png"),
        "paths_json":    pick("paths.json"),
    }


# ---------- 渲染：通用工具 ----------
def _add_image_axes(fig, rect, img: Optional[Image.Image], title: str = "",
                    title_fontsize: int = 9):
    ax = fig.add_axes(rect)
    if img is not None:
        ax.imshow(img)
    else:
        ax.text(0.5, 0.5, "(缺少图像)", ha="center", va="center",
                color="gray", fontsize=10)
    if title:
        ax.set_title(title, fontsize=title_fontsize, pad=4)
    ax.axis("off")
    return ax


def _add_text_axes(fig, rect, text: str, fontsize: int = 8, mono: bool = False):
    ax = fig.add_axes(rect); ax.axis("off")
    kwargs = {}
    if mono:
        kwargs["family"] = _MONO_FAMILY
    ax.text(0.0, 1.0, text, ha="left", va="top", fontsize=fontsize,
            wrap=True, **kwargs)
    return ax


# ---------- 页 1：封面 ----------
def render_cover(pdf: PdfPages, assets: Dict) -> None:
    fig = plt.figure(figsize=(11.69, 8.27))  # A4 横向
    fig.suptitle(f"病例综合报告 — {assets['stem']}", fontsize=14, y=0.97)

    # 左上：基本信息
    info_lines = [f"患者 ID    : {assets['patient_id']}",
                  f"CT 文件    : {assets['ct_path'].name}"]
    brain_rep = _safe_json(assets["brain_report"])
    if brain_rep:
        sp = brain_rep.get("spacing_mm", [])
        if sp: info_lines.append(f"Spacing mm : {sp[0]:.3f} × {sp[1]:.3f} × {sp[2]:.3f}")
        info_lines.append(f"Voxel mm³  : {brain_rep.get('voxel_mm3', '?')}")
        info_lines.append(f"骨 mask    : {brain_rep.get('skull_mask_file', '?')}")
    paths_json = _safe_json(assets["paths_json"])
    if paths_json:
        params = paths_json.get("params", {})
        info_lines.append("")
        info_lines.append("[路径规划参数]")
        info_lines.append(f"  Top-N           : {params.get('top_n', '?')}")
        info_lines.append(f"  N targets (PCA) : {params.get('n_targets', '?')}")
        info_lines.append(f"  入颅候选 / 合法 : "
                          f"{paths_json.get('n_entry_candidates','?')} / "
                          f"{paths_json.get('n_legal_paths','?')}")
        info_lines.append(f"  禁区使用        : {params.get('forbidden_zones_used', [])}")
        info_lines.append(f"  margin (mm) 脑室/血管/脑干/功能区: "
                          f"{params.get('ventricle_margin_mm','?')} / "
                          f"{params.get('vessel_margin_mm','?')} / "
                          f"{params.get('brainstem_margin_mm','?')} / "
                          f"{params.get('eloquent_margin_mm','?')}")
    _add_text_axes(fig, [0.05, 0.55, 0.40, 0.38],
                   "\n".join(info_lines), fontsize=9, mono=True)

    # 左下：体积概览
    vol_lines = ["[各 ROI 体积 (mL)]"]
    if brain_rep:
        v = brain_rep.get("volumes_ml", {})
        vol_lines.append(f"  颅腔 (intracranial) : {v.get('intracranial', '?')}")
        vol_lines.append(f"  脑组织 (brain)      : {v.get('brain', '?')}")
        vol_lines.append(f"  血肿 (hematoma)     : {v.get('hematoma', '?')}")
    vs = _safe_json(assets["ventricle_stats"])
    if vs:
        vol_lines.append(f"  脑室 (ventricle)    : {vs.get('total_volume_ml', '?')}")
    ve = _safe_json(assets["vessel_stats"])
    if ve:
        vol_lines.append(f"  血管风险禁区        : {ve.get('volume_ml', '?')}")
    bs = _safe_json(assets["brainstem_stats"])
    if bs:
        vol_lines.append(f"  脑干 (brainstem)    : {bs.get('volume_ml', '?')}")
    el = _safe_json(assets["eloquent_stats"])
    if el:
        vol_lines.append(f"  功能区 (eloquent) ↓ ")
        for k, v in (el.get("components_volume_ml") or {}).items():
            vol_lines.append(f"    - {k:<10s}: {v}")
        vol_lines.append(f"    = total           : {el.get('total_volume_ml', '?')}")
    _add_text_axes(fig, [0.05, 0.08, 0.40, 0.45],
                   "\n".join(vol_lines), fontsize=9, mono=True)

    # 右侧：CT preview + 血肿信息
    _add_image_axes(fig, [0.50, 0.50, 0.45, 0.42],
                    _safe_imread(assets["preview"]),
                    title=f"CT preview")

    hema_lines = ["[血肿信息]"]
    if brain_rep and brain_rep.get("hematoma_regions"):
        for i, r in enumerate(brain_rep["hematoma_regions"], 1):
            hema_lines.append(f"  #{i}  vol={r.get('volume_ml')} mL  "
                              f"voxels={r.get('voxels')}  meanHU={r.get('mean_hu')}  "
                              f"solidity={r.get('solidity')}")
            cphys = r.get("centroid_physical_lps_mm")
            if cphys:
                hema_lines.append(f"       centroid LPS mm = "
                                  f"({cphys[0]:.1f}, {cphys[1]:.1f}, {cphys[2]:.1f})")
    else:
        hema_lines.append("  (无血肿或未生成 brain_report.json)")

    if paths_json and paths_json.get("targets"):
        hema_lines.append("")
        hema_lines.append("[多目标点（PCA 长轴采样）]")
        for tg in paths_json["targets"]:
            hema_lines.append(f"  [{tg['idx']}] {tg['role']:<10s} t={tg['axis_t']:+.2f}  "
                              f"phys=({tg['phys_lps_mm'][0]:.1f},{tg['phys_lps_mm'][1]:.1f},"
                              f"{tg['phys_lps_mm'][2]:.1f})")

    _add_text_axes(fig, [0.50, 0.08, 0.45, 0.40],
                   "\n".join(hema_lines), fontsize=8, mono=True)

    pdf.savefig(fig, dpi=160); plt.close(fig)


# ---------- 页 2：路径规划主页 ----------
def render_paths_page(pdf: PdfPages, assets: Dict) -> None:
    paths_json = _safe_json(assets["paths_json"])
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.suptitle(f"路径规划 — {assets['stem']}", fontsize=13, y=0.97)

    # 左：overlay 三视图
    _add_image_axes(fig, [0.02, 0.40, 0.62, 0.52],
                    _safe_imread(assets["paths_overlay"]),
                    title="paths_overlay (Axial / Coronal / Sagittal + 路径投影 + 黄星=多目标)")

    # 右：3D
    _add_image_axes(fig, [0.65, 0.40, 0.33, 0.52],
                    _safe_imread(assets["paths_3d"]),
                    title="paths_3d")

    # 下方：Top-N 表 + 拒绝统计
    if paths_json:
        paths = paths_json.get("paths", [])
        header = f"{'#':>2} {'tgt':>3} {'role':>10s} {'len_mm':>8s} {'angle°':>7s} " \
                 f"{'hits':>5s} {'score':>7s}  entry_LPS_mm                target_LPS_mm"
        rows = [header, "-" * len(header)]
        for i, r in enumerate(paths, 1):
            e = r.get("entry_phys_lps_mm", [0, 0, 0])
            t = r.get("target_phys_lps_mm", [0, 0, 0])
            rows.append(f"{i:>2} {r.get('target_idx', '-'):>3} "
                        f"{r.get('target_role', '-'):>10s} "
                        f"{r.get('length_mm', 0):>8.1f} "
                        f"{r.get('angle_to_normal_deg', 0):>7.1f} "
                        f"{str(r.get('hits_target', '?')):>5s} "
                        f"{r.get('score', 0):>7.3f}  "
                        f"({e[0]:6.1f},{e[1]:6.1f},{e[2]:6.1f})  "
                        f"({t[0]:6.1f},{t[1]:6.1f},{t[2]:6.1f})")
        rows.append("")
        rows.append(f"合法路径数: {paths_json.get('n_legal_paths', '?')}    "
                    f"分目标合法数: {paths_json.get('legal_paths_per_target', {})}")
        rs = paths_json.get("reject_stats", {})
        if rs:
            rs_str = "  ".join(f"{k}={v}" for k, v in rs.items())
            rows.append(f"拒绝统计: {rs_str}")
        _add_text_axes(fig, [0.02, 0.02, 0.96, 0.34],
                       "\n".join(rows), fontsize=7, mono=True)
    else:
        _add_text_axes(fig, [0.02, 0.02, 0.96, 0.34],
                       "(无 paths.json — 该病例可能血肿为空或路径规划未运行)",
                       fontsize=10, mono=False)

    pdf.savefig(fig, dpi=160); plt.close(fig)


# ---------- 页 3+：分割对比页 ----------
def _segmentation_summary(name: str, stats: Optional[Dict]) -> str:
    if stats is None:
        return f"[{name}] (无 stats)"
    lines = [f"[{name}]"]
    keys_volume = ["volume_ml", "total_volume_ml"]
    for k in keys_volume:
        if k in stats:
            lines.append(f"  volume_ml = {stats[k]}")
            break
    if "voxels" in stats:
        lines.append(f"  voxels    = {stats['voxels']}")
    if "components_volume_ml" in stats:
        for ck, cv in stats["components_volume_ml"].items():
            lines.append(f"    {ck:<10s}: {cv} mL")
    if "regions" in stats and isinstance(stats["regions"], list):
        lines.append(f"  regions   = {len(stats['regions'])}")
    if "params" in stats:
        lines.append(f"  params    = {stats['params']}")
    return "\n".join(lines)


def render_segmentation_page(pdf: PdfPages, assets: Dict, *,
                             title: str, overlay_key: str, threed_key: str,
                             stats_key: str, name_for_stats: str) -> None:
    overlay_img = _safe_imread(assets.get(overlay_key))
    threed_img = _safe_imread(assets.get(threed_key))
    if overlay_img is None and threed_img is None:
        return  # 这个病例没有该分割产物，跳过

    fig = plt.figure(figsize=(11.69, 8.27))
    fig.suptitle(f"{title} — {assets['stem']}", fontsize=13, y=0.97)

    _add_image_axes(fig, [0.02, 0.18, 0.62, 0.74], overlay_img, title="overlay 三视图")
    _add_image_axes(fig, [0.65, 0.18, 0.33, 0.74], threed_img, title="3D")

    stats = _safe_json(assets.get(stats_key))
    summary = _segmentation_summary(name_for_stats, stats)
    _add_text_axes(fig, [0.02, 0.02, 0.96, 0.14], summary, fontsize=8, mono=True)

    pdf.savefig(fig, dpi=160); plt.close(fig)


def render_skull_page(pdf: PdfPages, assets: Dict) -> None:
    overlay_img = _safe_imread(assets.get("skull_overlay"))
    threed_img = _safe_imread(assets.get("skull_3d"))
    if overlay_img is None and threed_img is None:
        return
    fig = plt.figure(figsize=(11.69, 8.27))
    fig.suptitle(f"颅骨分割（来自 Hr60 体数据）— {assets['stem']}", fontsize=13, y=0.97)
    _add_image_axes(fig, [0.02, 0.18, 0.62, 0.74], overlay_img, title="skull_overlay")
    _add_image_axes(fig, [0.65, 0.18, 0.33, 0.74], threed_img, title="skull_3d")

    txt_lines = ["[skull]"]
    if assets.get("skull_stats") and assets["skull_stats"].exists():
        try:
            txt_lines.append(assets["skull_stats"].read_text(encoding="utf-8").strip())
        except Exception:
            pass
    _add_text_axes(fig, [0.02, 0.02, 0.96, 0.14], "\n".join(txt_lines),
                   fontsize=8, mono=True)
    pdf.savefig(fig, dpi=160); plt.close(fig)


# ---------- 页：自然语言手术方案 ----------
def render_plan_text_page(pdf: PdfPages, assets: Dict, plan_text: str) -> None:
    """把自然语言方案文本分页放入 PDF（一页若装不下自动换页）。"""
    # A4 横向每页大约能放 50-55 行 8pt mono
    LINES_PER_PAGE = 52
    lines = plan_text.splitlines()
    n_pages = max(1, (len(lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE)
    for k in range(n_pages):
        chunk = lines[k * LINES_PER_PAGE:(k + 1) * LINES_PER_PAGE]
        fig = plt.figure(figsize=(11.69, 8.27))
        suffix = f"  ({k+1}/{n_pages})" if n_pages > 1 else ""
        fig.suptitle(f"手术方案（自然语言）— {assets['stem']}{suffix}",
                     fontsize=13, y=0.97)
        _add_text_axes(fig, [0.04, 0.03, 0.92, 0.90],
                       "\n".join(chunk), fontsize=8, mono=True)
        pdf.savefig(fig, dpi=160); plt.close(fig)


# ---------- 病例 PDF 装配 ----------
def build_case_report(ct: Path, force: bool = False) -> Optional[Path]:
    assets = collect_case_assets(ct)
    out_pdf = ct.parent / f"{assets['stem']}_report.pdf"
    if out_pdf.exists() and not force:
        print(f"  [跳过] 已存在: {out_pdf.name}（用 --force 覆盖）")
        return out_pdf

    has_paths = assets["paths_json"] is not None
    has_brain = assets["brain_overlay"] is not None or assets["brain_3d"] is not None
    if not (has_paths or has_brain):
        print(f"  [跳过] 既无路径规划也无脑分割: {ct.name}")
        return None

    # 生成自然语言方案 + 落盘 .txt
    plan_text = build_surgical_plan_text(
        patient_id=assets["patient_id"],
        ct_filename=assets["ct_path"].name,
        brain_report=_safe_json(assets["brain_report"]),
        paths_json=_safe_json(assets["paths_json"]),
        ventricle_stats=_safe_json(assets["ventricle_stats"]),
        vessel_stats=_safe_json(assets["vessel_stats"]),
        brainstem_stats=_safe_json(assets["brainstem_stats"]),
        eloquent_stats=_safe_json(assets["eloquent_stats"]),
    )
    plan_txt = ct.parent / f"{assets['stem']}_plan.txt"
    plan_txt.write_text(plan_text, encoding="utf-8")
    print(f"  [OK] {plan_txt.name}  ({plan_txt.stat().st_size} B)")

    with PdfPages(out_pdf) as pdf:
        render_cover(pdf, assets)
        # 紧跟封面之后插入"自然语言手术方案"页（医生最先看到的一页）
        render_plan_text_page(pdf, assets, plan_text)
        if has_paths:
            render_paths_page(pdf, assets)
        # 颅骨/脑/脑室/血管/脑干/功能区
        render_skull_page(pdf, assets)
        render_segmentation_page(pdf, assets,
                                 title="脑组织 + 血肿分割",
                                 overlay_key="brain_overlay",
                                 threed_key="brain_3d",
                                 stats_key="brain_report",
                                 name_for_stats="brain+hematoma")
        render_segmentation_page(pdf, assets,
                                 title="脑室分割",
                                 overlay_key="ventricle_overlay",
                                 threed_key="ventricle_3d",
                                 stats_key="ventricle_stats",
                                 name_for_stats="ventricle")
        render_segmentation_page(pdf, assets,
                                 title="血管风险禁区",
                                 overlay_key="vessel_overlay",
                                 threed_key="vessel_3d",
                                 stats_key="vessel_stats",
                                 name_for_stats="vessel_risk")
        render_segmentation_page(pdf, assets,
                                 title="脑干分割（几何近似）",
                                 overlay_key="brainstem_overlay",
                                 threed_key="brainstem_3d",
                                 stats_key="brainstem_stats",
                                 name_for_stats="brainstem")
        render_segmentation_page(pdf, assets,
                                 title="功能区分割（几何近似）",
                                 overlay_key="eloquent_overlay",
                                 threed_key="eloquent_3d",
                                 stats_key="eloquent_stats",
                                 name_for_stats="eloquent")

        d = pdf.infodict()
        d["Title"] = f"病例综合报告 - {assets['stem']}"
        d["Author"] = "proj_01 / 头部 CT 自动化流水线"
        d["Subject"] = "Skull / Brain / Hematoma / Ventricle / Vessel / Brainstem / Eloquent / Path"

    print(f"  [OK] {out_pdf.name}  ({out_pdf.stat().st_size/1024:.0f} KB)")
    return out_pdf


# ---------- 主流程 ----------
def find_cts(input_dir: Path, pattern: str) -> List[Path]:
    EXCLUDE = (
        "_mask.nii.gz", "_overlay.png", "_3d.png",
        "_preview.png", "_stats.txt", "_report.json", "_paths.json",
    )
    out = []
    for p in sorted(input_dir.rglob(pattern)):
        if any(p.name.endswith(s) for s in EXCLUDE):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="病例综合报告 PDF 生成")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--pattern", default="*Hr40*.nii.gz",
                    help="匹配 CT 体数据文件名（默认软组织薄层 Hr40）")
    ap.add_argument("--force", action="store_true",
                    help="覆盖已存在的 *_report.pdf")
    args = ap.parse_args()

    in_dir = Path(args.input).resolve()
    if not in_dir.exists():
        print(f"[错误] 不存在: {in_dir}", file=sys.stderr); return 2

    cts = find_cts(in_dir, args.pattern)
    if not cts:
        print(f"[错误] 未匹配到 CT: pattern={args.pattern}", file=sys.stderr); return 3

    print(f"[输入] {in_dir}")
    print(f"[匹配] CT 数量: {len(cts)}\n")

    n_ok, n_skip, n_err = 0, 0, 0
    for ct in cts:
        print(f"=== {ct.name} ===")
        try:
            r = build_case_report(ct, force=args.force)
            if r is None:
                n_skip += 1
            else:
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
