# -*- coding: utf-8 -*-
r"""
把路径规划 JSON + 各 ROI stats 翻译成自然语言"手术方案"。

核心函数：
    build_surgical_plan_text(brain_report, paths_json, ventricle_stats,
                             vessel_stats, brainstem_stats, eloquent_stats) -> str

输出示例（节选）：
    一、病例与病灶
       - 患者 ID  : 0099039449
       - 血肿体积: 8.4 mL（位于右侧基底节区，上下径覆盖 z=93..121）
       ...
    二、推荐手术方案（Top-1）
       - 入颅点    : 患者右额，LPS=(73.4, -139.0, -508.9) mm
       - 目标点    : 血肿远端 LPS=(38.1, -151.2, -509.1) mm
       - 进针长度 : 37.3 mm
       - 入颅角度 : 19° （接近垂直进针，符合常规额角入路）
       ...
    三、备选方案（Top-2 / Top-3）
       ...
    四、术中风险提示
       - 路径主体距脑室壁 >3 mm；
       - 已自动避开...

注意：这是几何辅助方案，不替代医生判断。
"""

from __future__ import annotations

from typing import Dict, List, Optional


# ---------- 解剖位置推断（基于 LPS 坐标 / bbox） ----------
# LPS 约定：+x 患者左侧，+y 后，+z 上
# 我们的 NIfTI 采到的 LPS 物理坐标里，X 正负含义对应 SimpleITK Direction，已在前序流程统一。
def _laterality(x_lps_mm: float) -> str:
    """根据 LPS x 坐标判断左右侧。x>0 患者左侧；x<0 患者右侧；|x|<5 视为中线。"""
    if x_lps_mm > 5:
        return "患者左侧"
    if x_lps_mm < -5:
        return "患者右侧"
    return "中线区"


def _ap_position(y_lps_mm: float, intra_y_range: Optional[List[float]] = None) -> str:
    """根据 LPS y 在颅腔 y 范围内的分位粗判额/中央/枕。+y 后方。"""
    if intra_y_range is None or len(intra_y_range) != 2:
        return ""
    lo, hi = intra_y_range
    if hi - lo < 1e-6:
        return ""
    t = (y_lps_mm - lo) / (hi - lo)  # 0=最前(额), 1=最后(枕)
    if t < 0.33:
        return "额部"
    if t < 0.67:
        return "中央/顶部"
    return "枕部"


def _vertical_position(z_lps_mm: float, intra_z_range: Optional[List[float]] = None) -> str:
    """+z 向上。"""
    if intra_z_range is None or len(intra_z_range) != 2:
        return ""
    lo, hi = intra_z_range
    if hi - lo < 1e-6:
        return ""
    t = (z_lps_mm - lo) / (hi - lo)
    if t < 0.33:
        return "颅底层面"
    if t < 0.67:
        return "中央层面"
    return "顶部层面"


def _hematoma_region_guess(centroid_lps: List[float],
                           bbox_zyx: Optional[Dict] = None) -> str:
    """
    基于血肿质心相对中线的偏移 + 上下高度，粗略给一个"基底节区/脑叶/小脑/脑干旁"标签。
    无 MRI 解剖图谱时这是经验近似。
    """
    if not centroid_lps or len(centroid_lps) != 3:
        return "未知部位"
    x, y, z = centroid_lps
    side = _laterality(x)

    # 中线偏移幅度小（|x|<35mm）+ 中央层面 → 多为基底节区/丘脑
    abs_x = abs(x)
    if abs_x < 35 and not (z < -30 and abs_x < 15):
        return f"{side}基底节区/丘脑可能"
    # 远离中线（|x|>50mm）→ 脑叶
    if abs_x > 50:
        return f"{side}脑叶"
    return f"{side}深部"


# ---------- 路径角度的解读 ----------
def _angle_comment(angle_deg: float) -> str:
    if angle_deg <= 15:
        return "接近垂直进针，对穿刺器械操作友好"
    if angle_deg <= 30:
        return "小角度斜行，常规入路可接受"
    if angle_deg <= 60:
        return "中等斜角，需注意进针稳定性"
    return "大角度切线进针，建议优先考虑其他备选"


def _length_comment(length_mm: float) -> str:
    if length_mm < 40:
        return "路径短，到达目标快"
    if length_mm < 70:
        return "路径长度中等"
    if length_mm < 100:
        return "路径较长，注意穿刺方向偏移"
    return "路径偏长，建议复核或选择更近的入颅点"


def _entry_region_guess(entry_lps: List[float],
                        intra_bbox: Optional[Dict] = None) -> str:
    """根据入颅点 LPS 给出"右额/左顶/枕..."等粗标签。"""
    if not entry_lps or len(entry_lps) != 3:
        return "颅顶"
    x, y, z = entry_lps
    side = _laterality(x).replace("患者", "")  # "左侧"/"右侧"/"中线区"
    side = side.replace("侧", "")  # "左"/"右"/"中线区"

    ap = ""
    vert = ""
    if intra_bbox is not None:
        # 注意 intra_bbox 是 ijk_zyx 范围，需要外部已经转好
        pass
    # 用绝对 LPS 经验阈值（mm 量级，原点近脑中心）
    if y < -130:
        ap = "额部"
    elif y < -90:
        ap = "中央/顶部"
    else:
        ap = "枕部"

    return f"{side}{ap}".replace("中线区", "中线")


# ---------- 主函数 ----------
def build_surgical_plan_text(
    *,
    patient_id: str,
    ct_filename: str,
    brain_report: Optional[Dict] = None,
    paths_json: Optional[Dict] = None,
    ventricle_stats: Optional[Dict] = None,
    vessel_stats: Optional[Dict] = None,
    brainstem_stats: Optional[Dict] = None,
    eloquent_stats: Optional[Dict] = None,
    top_k_in_text: int = 3,
) -> str:
    """生成中文手术规划文本。即使部分输入缺失也会尽力输出。"""

    L: List[str] = []
    L.append("=" * 72)
    L.append("脑出血穿刺路径规划 · 自动生成手术方案（仅供参考，最终以医生判断为准）")
    L.append("=" * 72)
    L.append("")

    # ---------- 一、病例与病灶 ----------
    L.append("一、病例与病灶")
    L.append("-" * 72)
    L.append(f"  • 患者 ID    : {patient_id}")
    L.append(f"  • CT 文件    : {ct_filename}")

    spacing = (brain_report or {}).get("spacing_mm") or (paths_json or {}).get("spacing_mm")
    if spacing:
        L.append(f"  • 体素间距   : {spacing[0]:.3f} × {spacing[1]:.3f} × {spacing[2]:.3f} mm")

    if brain_report:
        v = brain_report.get("volumes_ml", {})
        L.append(f"  • 体积概览   : 颅腔 {v.get('intracranial','?')} mL, "
                 f"脑组织 {v.get('brain','?')} mL, 血肿 {v.get('hematoma','?')} mL")
        regions = brain_report.get("hematoma_regions") or []
        if regions:
            for i, r in enumerate(regions, 1):
                cphys = r.get("centroid_physical_lps_mm") or []
                region_lbl = _hematoma_region_guess(cphys, r.get("bbox_zyx"))
                bbox = r.get("bbox_zyx") or {}
                z_rng = bbox.get("z", [None, None])
                bbox_str = f"z={z_rng[0]}..{z_rng[1]}" if z_rng[0] is not None else ""
                L.append(f"  • 血肿 #{i}    : {r.get('volume_ml','?')} mL   "
                         f"meanHU={r.get('mean_hu','?')}   solidity={r.get('solidity','?')}")
                if cphys and len(cphys) == 3:
                    L.append(f"               质心 LPS=({cphys[0]:.1f}, {cphys[1]:.1f}, {cphys[2]:.1f}) mm   "
                             f"{bbox_str}")
                    L.append(f"               推断部位：{region_lbl}")
        else:
            L.append("  • 血肿       : 当前阈值下未检出有效血肿（mask 为空）")

    # 周边关键结构体积
    flank = []
    if ventricle_stats:
        flank.append(f"脑室 {ventricle_stats.get('total_volume_ml','?')} mL")
    if vessel_stats:
        vv = vessel_stats.get("total_volume_ml") or vessel_stats.get("volume_ml") or "?"
        flank.append(f"血管风险禁区 {vv} mL")
    if brainstem_stats:
        flank.append(f"脑干 {brainstem_stats.get('volume_ml','?')} mL")
    if eloquent_stats:
        flank.append(f"功能区合计 {eloquent_stats.get('total_volume_ml','?')} mL")
    if flank:
        L.append(f"  • 周边结构   : " + " / ".join(flank))

    L.append("")

    if not paths_json or not paths_json.get("paths"):
        L.append("二、手术规划")
        L.append("-" * 72)
        L.append("  当前病例没有产生合法穿刺路径（可能是血肿为空，或所有候选入颅点")
        L.append("  均被禁区/颅骨厚度等约束拒绝）。建议：")
        if paths_json:
            rs = paths_json.get("reject_stats", {})
            if rs:
                rs_str = " / ".join(f"{k}={v}" for k, v in rs.items())
                L.append(f"    - 拒绝统计：{rs_str}")
            L.append("    - 适当放宽 --vessel-margin-mm / --brainstem-margin-mm")
            L.append("    - 检查 --eloquent-margin-mm 是否设得过严")
        else:
            L.append("    - 检查血肿分割阈值（--min-hematoma-ml）")
            L.append("    - 重新运行 path_planning.py")
        L.append("")
        L.append("=" * 72)
        return "\n".join(L)

    paths = paths_json.get("paths", [])
    targets = paths_json.get("targets", [])
    params = paths_json.get("params", {})

    # ---------- 二、推荐方案 ----------
    L.append("二、推荐手术方案")
    L.append("-" * 72)
    top1 = paths[0]
    e = top1.get("entry_phys_lps_mm", [0, 0, 0])
    t = top1.get("target_phys_lps_mm", [0, 0, 0])
    length = top1.get("length_mm", 0.0)
    angle = top1.get("angle_to_normal_deg", 0.0)
    role = top1.get("target_role", "centroid")
    role_zh = {
        "centroid": "血肿质心",
        "distal_-": "血肿主轴远端 A",
        "distal_+": "血肿主轴远端 B",
        "mid_-": "血肿主轴中段 A",
        "mid_+": "血肿主轴中段 B",
    }.get(role, role)

    entry_region = _entry_region_guess(e)
    fh = top1.get("forbidden_hits") or {}

    L.append(f"  ★ 推荐方案（Top-1，按【长度+角度】评分最优）")
    L.append(f"     入颅点 (LPS mm) : ({e[0]:+7.1f}, {e[1]:+7.1f}, {e[2]:+7.1f})   位于 {entry_region}")
    L.append(f"     目标点 (LPS mm) : ({t[0]:+7.1f}, {t[1]:+7.1f}, {t[2]:+7.1f})   {role_zh}")
    L.append(f"     进针长度        : {length:.1f} mm   ({_length_comment(length)})")
    L.append(f"     入颅角度        : {angle:.1f}°（与颅骨外法线夹角）   ({_angle_comment(angle)})")
    L.append(f"     入颅骨厚        : {top1.get('entry_skull_thickness_voxels','?')} 体素 "
             f"≈ {top1.get('entry_skull_thickness_voxels',0) * (spacing[2] if spacing else 0.8):.1f} mm")
    L.append(f"     颅腔内占比      : {top1.get('in_intracranial_ratio','?')}")
    L.append(f"     是否到达血肿    : {'是' if top1.get('hits_target') else '否'}")
    L.append(f"     禁区穿越        : {fh}（全 0 表示路径主体未触及任何禁区）")
    L.append(f"     综合评分        : {top1.get('score','?')}（越小越优）")
    L.append("")

    # 步骤化的"医生话术"
    L.append("  【操作步骤建议】")
    L.append(f"    1. 体位与定位：依据 LPS 坐标系，将立体定向坐标原点对准 CT 原点；")
    L.append(f"       入颅点位于 {entry_region}，距颅骨外表面法向。")
    L.append(f"    2. 入颅：在 ({e[0]:+.1f}, {e[1]:+.1f}, {e[2]:+.1f}) 钻孔，与外法线夹角 {angle:.0f}°。")
    L.append(f"    3. 进针：沿入颅点 → 目标点方向直线进针 {length:.1f} mm，到达 {role_zh}。")
    if targets and len(targets) > 1:
        L.append(f"    4. 引流：建议沿血肿 PCA 长轴留置引流管，可参考其他目标点位置（见下）。")
    L.append("")

    # ---------- 三、备选方案 ----------
    if len(paths) > 1:
        L.append("三、备选方案")
        L.append("-" * 72)
        for i, p in enumerate(paths[1:top_k_in_text + 1], 2):
            ee = p.get("entry_phys_lps_mm", [0, 0, 0])
            tt = p.get("target_phys_lps_mm", [0, 0, 0])
            entry_reg = _entry_region_guess(ee)
            r = p.get("target_role", "")
            r_zh = {
                "centroid": "质心",
                "distal_-": "远端A",
                "distal_+": "远端B",
                "mid_-": "中段A",
                "mid_+": "中段B",
            }.get(r, r)
            L.append(f"  · Top-{i} (target={r_zh})")
            L.append(f"      入颅 ({ee[0]:+.1f},{ee[1]:+.1f},{ee[2]:+.1f}) {entry_reg}  → "
                     f"目标 ({tt[0]:+.1f},{tt[1]:+.1f},{tt[2]:+.1f})")
            L.append(f"      长度 {p.get('length_mm',0):.1f} mm   "
                     f"角度 {p.get('angle_to_normal_deg',0):.1f}°   "
                     f"score {p.get('score','?')}")
        L.append("")

    # ---------- 四、风险提示 ----------
    L.append("四、术中风险提示")
    L.append("-" * 72)
    fz = params.get("forbidden_zones_used", [])
    margin_map = {
        "ventricle": params.get("ventricle_margin_mm"),
        "vessel":    params.get("vessel_margin_mm"),
        "brainstem": params.get("brainstem_margin_mm"),
        "eloquent":  params.get("eloquent_margin_mm"),
    }
    cn = {"ventricle": "脑室", "vessel": "血管风险区",
          "brainstem": "脑干", "eloquent": "功能区"}
    if fz:
        L.append(f"  • 已纳入避让的结构（含安全外扩 margin）：")
        for k in fz:
            L.append(f"      - {cn.get(k, k)}：margin = {margin_map.get(k, '?')} mm")
    else:
        L.append("  • 当前未启用任何禁区约束（仅按颅骨/颅腔做几何最短）")
    L.append(f"  • 候选入颅点 {paths_json.get('n_entry_candidates','?')} 个，"
             f"合法路径 {paths_json.get('n_legal_paths','?')} 条")
    rs = paths_json.get("reject_stats", {})
    if rs:
        rs_str = " / ".join(f"{k}={v}" for k, v in rs.items())
        L.append(f"  • 候选被拒原因分布：{rs_str}")

    L.append("")
    L.append("  [!] 局限性说明：")
    L.append("    1) 当前血管/脑干/功能区为基于颅腔几何的近似禁区，非 MRI/CTA 真实分割；")
    L.append("       拿到 50 GB 数据后可用 nnU-Net 提升精度。")
    L.append("    2) LPS 坐标轴方向以本数据集 CT DICOM 头为准，跨设备需重新校准。")
    L.append("    3) 路径未做软组织变形 / 脑萎缩补偿；术中需做实时影像核对。")
    L.append("")
    L.append("=" * 72)
    return "\n".join(L)
