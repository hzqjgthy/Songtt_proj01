# 脑出血 CT 影像分割与穿刺路径规划流水线

基于头颅 CT 的全自动几何分析流水线，无需训练任何模型即可完成：
**DICOM 读取 → 颅骨/脑组织/血肿/脑室分割 → 微创穿刺路径规划**。

---

## 0. 项目结构

```
proj_01/
├── 头模CT/                      # 原始 DICOM 数据（按病例分目录）
│   ├── {patient-uid-1}/
│   │   ├── image/1, 2, 3, ...   # 每个子目录是一个 Series
│   │   └── info                 # 患者元信息
│   └── {patient-uid-2}/
├── output_nifti/                # 流水线产物（运行后生成）
│   ├── manifest.csv
│   ├── patient_<PID>/...
├── pipeline/                     # 新增：阶段/实现/配置驱动的编排层
├── configs/
│   ├── pipeline.logic.json       # 纯逻辑实现
│   └── pipeline.synthseg.json    # SynthSeg 混合实现
└── scripts/
    ├── requirements.txt
    ├── run_pipeline.py              # 新增：统一流水线入口
    ├── export_synthseg_masks.py     # 新增：SynthSeg 结果导出为项目内部 mask
    ├── dicom_to_nifti.py            # [步骤 1] DICOM → NIfTI
    ├── skull_segmentation.py        # [步骤 2] 颅骨分割
    ├── brain_hematoma_segmentation.py # [步骤 3] 颅腔/脑/血肿分割
    ├── ventricle_segmentation.py    # [步骤 C] 脑室分割
    ├── vessel_risk_segmentation.py  # [步骤 A] 血管风险禁区生成
    ├── brainstem_segmentation.py    # [步骤 P0] 脑干分割（几何近似）
    ├── eloquent_zone_segmentation.py # [步骤 P1] 功能区分割（几何近似）
    ├── path_planning.py             # [步骤 4] 穿刺路径规划
    ├── narrative.py                 # [步骤 5] 路径 JSON → 自然语言手术方案
    ├── case_report.py               # [步骤 5] 病例综合报告 PDF + plan.txt
    └── rename_legacy_garbled.py     # 辅助：批量修复历史文件名乱码
```

### 0.1 配置驱动的新架构

旧项目是“一组并列脚本 + README 手工串行执行”。现在新增了一层编排架构，把**阶段**和**实现方式**解耦：

- `scripts/run_pipeline.py` 只负责读取配置并按顺序执行阶段
- `pipeline/` 负责维护阶段顺序、实现注册和命令拼装
- `configs/pipeline.logic.json` 让阶段全部走原有逻辑/几何实现
- `configs/pipeline.synthseg.json` 让 `ventricle_segmentation`、`brainstem_segmentation` 改走 `SynthSeg` 推理导出

这样后续切换实现时，只需要改配置文件中的 `implementation`，不需要改下游 `path_planning.py`、`narrative.py`、`case_report.py` 对文件命名的依赖。

### 0.2 新入口用法

查看纯逻辑模式的执行计划：

```powershell
python scripts/run_pipeline.py --config configs/pipeline.logic.json --dry-run
```

查看 SynthSeg 混合模式的执行计划：

```powershell
python scripts/run_pipeline.py --config configs/pipeline.synthseg.json --dry-run
```

说明：

- 目前支持 `logic` / `synthseg` 切换的阶段是：`ventricle_segmentation`、`brainstem_segmentation`
- `eloquent_zone_segmentation` 目前仍保留逻辑实现，等皮层标签映射稳定后再补 `synthseg` 实现
- 配置中的 `python_executable` 应指向装好 `SimpleITK/nibabel/scipy` 的项目解释器
- 配置中的 `freesurfer_command` 建议直接写绝对路径，例如 `/Applications/freesurfer/8.1.0/bin/mri_synthseg`
- `pipeline.synthseg.json` 已启用 `run_output`，运行时会把指定病例复制成 `patient_<ID>_<YYYYMMDD_HHMMSS>`，再只在这些带时间戳的新目录中执行混合模式流程
- 如需固定时间戳，可加 `--run-id 20260609_160225`；不传时自动使用当前时间

---

## 1. 环境准备

### 1.1 Python 环境
本项目已使用本机 conda 环境 `test`：

```
解释器: C:\Users\aippletian\miniconda3\envs\test\python.exe
Python: 3.12.0
```

如果是新机器，可以重新建一个：
```powershell
conda create -n test python=3.10 -y
conda activate test
```

### 1.2 安装依赖

```powershell
C:\Users\aippletian\miniconda3\envs\test\python.exe -m pip install -r c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\requirements.txt
```

依赖列表（`scripts/requirements.txt`）：
| 库 | 用途 |
|---|---|
| `pydicom` | DICOM 头解析（中文字符集 GB18030 自动解码） |
| `SimpleITK` | 医学影像 IO，自动应用 RescaleSlope/Intercept 还原 HU 值 |
| `nibabel` | NIfTI 读写（绕开 SimpleITK Windows 中文路径问题） |
| `numpy` / `scipy` | 数值/形态学/距离变换 |
| `scikit-image` | marching cubes、连通域、regionprops |
| `matplotlib` | 三视图叠加 + 3D 渲染 |

### 1.3 关于 Windows 中文路径
- DICOM 原始目录可以是中文（`头模CT/`），脚本已处理。
- 输出路径**必须**使用 ASCII。NIfTI 的写入用 nibabel，避免 SimpleITK 在 Windows 下因 ANSI 路径打不开 UTF-8 文件名。
- 文件命名清洗：`dicom_to_nifti.py` 内置 `_TERM_MAP` 把中文 SeriesDescription 转成英文（如 `脑部 → brain`、`定位像 → topogram`）。

---

## 2. 流水线运行步骤

> 本流水线**不提供一键脚本**。请按下述顺序逐步执行，每一步执行完后可在 `output_nifti/` 查看产物，确认无误再进行下一步。
> 所有命令都在工作目录 `c:\Users\aippletian\Desktop\songtt\projs\proj_01` 之外执行也可（脚本通过 `__file__` 解析默认输入/输出路径）。

为简化阅读，以下用 `PY` 代表：
```
C:\Users\aippletian\miniconda3\envs\test\python.exe
```

### 步骤 1：DICOM → NIfTI

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\dicom_to_nifti.py
```

**作用**
- 递归扫描 `头模CT/`，按 `SeriesInstanceUID` 分组所有 DICOM 文件。
- 自动跳过：定位像、剂量报告、患者方案、切片数 < 5、分辨率 < 64×64。
- 通过 `SimpleITK.ImageSeriesReader` 读出体数据（CT 已应用 HU rescale），`nibabel.save` 写出 `.nii.gz`。

**产物**
```
output_nifti/
├── manifest.csv
└── patient_<PID>/
    ├── CT_brain_<thk>_<recon>_<uid8>.nii.gz       # 体数据
    └── CT_brain_<thk>_<recon>_<uid8>_preview.png  # 三视图预览
```

**常用参数**
```powershell
%PY% scripts\dicom_to_nifti.py --window bone     # 骨窗预览
%PY% scripts\dicom_to_nifti.py --no-preview      # 只转 NIfTI 不出图（快）
%PY% scripts\dicom_to_nifti.py --input "D:\全部50G数据" --output "D:\out_nifti"
```

---

### 步骤 2：颅骨分割

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\skull_segmentation.py
```

**作用**
- 默认仅处理 `*Hr60*.nii.gz`（CT 骨重建序列）。
- 算法：HU 阈值 `[300, 3000]` → 形态学开运算 → **3D 连通域取最大**（自动剔除 CT 床、定位棒）→ 闭运算填补骨缝。

**产物**（每个 Hr60 序列）
- `*_skull_mask.nii.gz` 颅骨二值 mask
- `*_skull_overlay.png` 三视图：CT + 红色颅骨叠加
- `*_skull_3d.png` 颅骨 3D 表面渲染
- `*_skull_stats.txt` 体素数 / 体积 / HU 范围 / 连通域信息

**常用参数**
```powershell
%PY% scripts\skull_segmentation.py --hu-low 250 --hu-high 3000
%PY% scripts\skull_segmentation.py --pattern "*Hr40*.nii.gz"   # 用软组织重建
%PY% scripts\skull_segmentation.py --no-3d
```

---

### 步骤 3：颅腔 + 脑组织 + 血肿粗分割

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\brain_hematoma_segmentation.py
```

**作用**
- 默认匹配 `*Hr40*.nii.gz`（软组织重建），自动配对同目录下的 `*Hr60*_skull_mask.nii.gz`。
- 颅腔：颅骨 → 闭运算 → 3D 填洞 → 逐切片 2D 填洞 → 减骨 → 最大连通域。
- 脑组织：颅腔 ∩ HU∈[0, 80]，向内腐蚀 1 体素去骨内板部分容积。
- 血肿候选：脑组织 ∩ HU∈[45, 80] → **开运算半径=2** 断开伪桥接 → 距骨 ≥2.5 mm → 实心度 ≥0.4 → 体积 ≥2 mL。

> ⚠️ **关键经验**：开运算半径 1 时血肿与脉络丛/骨界面会沿薄路径串成一个稀疏假大块（solidity≈0.06），半径 2 才能正确断开。这是默认值。

**产物**（每个 Hr40 序列）
- `*_intracranial_mask.nii.gz` 颅腔
- `*_brain_mask.nii.gz` 脑组织
- `*_hematoma_mask.nii.gz` 血肿（按合理性过滤后）
- `*_brain_overlay.png` 三视图：颅骨白 + 颅腔淡黄 + 脑绿 + 血肿红
- `*_brain_3d.png` 3D：颅骨半透明 + 血肿实体
- `*_brain_report.json` 各连通域：体素数 / 体积 / 质心 IJK & **物理坐标 LPS** / BBox / 平均 HU / 实心度

**常用参数**
```powershell
%PY% scripts\brain_hematoma_segmentation.py --hematoma-low 45 --hematoma-high 80
%PY% scripts\brain_hematoma_segmentation.py --min-hematoma-ml 2.0
%PY% scripts\brain_hematoma_segmentation.py --skull-distance-mm 2.5 --min-solidity 0.4
```

---

### 步骤 C：脑室分割（在步骤 4 之前执行）

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\ventricle_segmentation.py
```

**作用**
- 颅腔 ∩ HU∈[-10, 18]（CSF 范围） → 距骨 ≥6 mm（剔除脑沟/蛛网膜下腔贴近骨板部分） → 开运算 → 3D 连通域取前 5 大且每个 ≥0.3 mL。
- 输出双侧侧脑室 + 第三/第四脑室（合体或分体取决于第三脑室是否狭窄连通）。

**产物**
- `*_ventricle_mask.nii.gz` 脑室二值 mask
- `*_ventricle_overlay.png` 三视图：青色脑室 + 红色血肿
- `*_ventricle_3d.png` 3D：颅骨 + 脑室 + 血肿
- `*_ventricle_stats.json` 体积 / 各连通域统计

**常用参数**
```powershell
%PY% scripts\ventricle_segmentation.py --hu-low -10 --hu-high 18
%PY% scripts\ventricle_segmentation.py --keep-top-n 4 --min-volume-ml 0.5
```

---

### 步骤 A：血管风险禁区（在步骤 4 之前执行）

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\vessel_risk_segmentation.py
```

**作用**

⚠️ 当前数据为**平扫 CT**（无 CTA/MRA），未钙化的动静脉与脑实质 HU 重叠，无法直接阈值分割。
本步骤生成的是 **"血管风险禁区"**（保守过近似），由两部分组成：

1. **检测到的颅内高密度结构**（钙化血管 / 脉络丛钙化 / 松果体 / 大脑镰钙化）：颅腔内 HU∈[80, 250]，排除已知血肿。
2. **解剖学几何先验**（基于颅腔几何位置）：
   - **上矢状窦带** = 颅腔顶部 12 mm + 中线 ±10 mm
   - **大脑镰** = 中线 ±3 mm，纵贯前后
   - **颅底血管区** = 颅腔最底部 15 mm + 距颅腔外缘 ≤12 mm（覆盖横窦/乙状窦/Willis 环位置）

后续接入 CTA 时，应替换本步骤为基于强化对比的真实血管分割。

**产物**
- `*_vessel_risk_mask.nii.gz` 风险禁区二值 mask
- `*_vessel_overlay.png` 三视图：CT + 颅骨白 + 红色禁区 + 黄色血肿
- `*_vessel_3d.png` 3D：颅骨半透明 + 红色禁区 + 黄色血肿
- `*_vessel_stats.json` 各组成成分体积

**常用参数**
```powershell
%PY% scripts\vessel_risk_segmentation.py --hu-low 80 --hu-high 250
%PY% scripts\vessel_risk_segmentation.py --no-anatomical    # 仅高密度检测
%PY% scripts\vessel_risk_segmentation.py --no-detected      # 仅解剖先验
%PY% scripts\vessel_risk_segmentation.py --falx-mm 5 --sagittal-band-mm 12
```

---

### 步骤 P0：脑干分割（在步骤 4 之前执行）

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\brainstem_segmentation.py
```

**作用**

⚠️ 脑干在 CT 上与周围脑实质 HU 完全重叠，无法直接阈值分割。本步骤基于"颅腔几何位置 + HU 约束"做保守近似，输出的 mask 用作路径规划禁区。接入 MRI 时应替换为 FastSurfer / TotalSegmentator 的精确分割。

**算法**：颅腔下部 30% × 中线 ±25mm 横向带 × 中央 ±25mm 纵向带 ∩ 脑组织 ∩ HU∈[20,50] − 脑室 − 血肿 → 闭运算 → 最大连通域。

**产物**
- `*_brainstem_mask.nii.gz` 脑干二值 mask
- `*_brainstem_overlay.png` 三视图：CT + 颅骨白 + 脑室青 + 棕色脑干 + 红色血肿
- `*_brainstem_3d.png` 3D
- `*_brainstem_stats.json` 体积/质心/几何参数

**常用参数**
```powershell
%PY% scripts\brainstem_segmentation.py --lower-z-ratio 0.30 --lateral-mm 25
%PY% scripts\brainstem_segmentation.py --hu-low 20 --hu-high 50
```

---

### 步骤 P1：功能区几何近似（在步骤 4 之前执行）

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\eloquent_zone_segmentation.py
```

**作用**

⚠️ 真正的功能区分割需要 MRI（T1 + atlas 配准 / fMRI），CT 上无法精确分割。本步骤基于颅腔归一化坐标画出 4 类**经验性功能区禁区**作为路径规划硬约束。这是**保守过近似**，不是真功能区分割。当 MRI 数据可用时应替换为 FastSurfer / FreeSurfer 输出。

**4 类功能区**（颅腔 bounding box 归一化坐标）：

| 区域 | 位置 | 颜色 | 临床后果 |
|---|---|---|---|
| 运动皮层带 (M1) | z∈[0.78,0.95], y∈[0.40,0.62] | 洋红 | 偏瘫 |
| 语言区 | 仅左半球；Broca + Wernicke | 绿 | 失语 |
| 视觉皮层 | y∈[0.78,1.00], z∈[0.30,0.65] | 蓝 | 偏盲 |
| 深部核团 | 中线两侧 dx∈[0.05,0.40] 椭球（自动扣除血肿） | 紫 | 加重运动/感觉障碍 |

**产物**
- `*_eloquent_zone_mask.nii.gz` 合并版（4 类并集）
- `*_eloquent_motor_mask.nii.gz` / `_language_` / `_visual_` / `_deep_` 各类独立 mask
- `*_eloquent_overlay.png` 三视图（4 色叠加 + 图例）
- `*_eloquent_3d.png` 3D
- `*_eloquent_stats.json` 各类体积

```powershell
%PY% scripts\eloquent_zone_segmentation.py
%PY% scripts\eloquent_zone_segmentation.py --no-3d
```

---

### 步骤 4：穿刺路径规划

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\path_planning.py
```

**作用**
- 自动跳过血肿为空的病例。
- 对每个有血肿的病例：
  1. 颅骨外表面 marching cubes → 仅保留 `z >= z_min + 0.45·(z_max-z_min)` 的部分（避开面部/颅底）→ 法向量与"指向骨质心"反向的为外表面 → 随机下采样 1500 点。
  2. **多目标点（PCA 长轴）**：对血肿做 3D PCA，沿主轴等距采样 N 个目标（默认 N=3：`distal_-` / `centroid` / `distal_+`），主轴覆盖率 `--target-coverage` 默认 0.7（避开血肿边缘）；每个采样点会被"吸附"到血肿 mask 内最近体素，保证物理可达。
  3. 对每条 (entry → target_k) 射线沿线采样 0.5 体素步长，做合法性检查：
     - 入颅段连续骨厚 ≤14 体素
     - 入颅之后骨命中 ≤4
     - 入颅之后路径 ≥55% 在颅腔内
     - **路径主体不得穿过脑室禁区**（脑室 + `--ventricle-margin-mm` 默认 3 mm；血肿本身免检）
     - **路径主体不得穿过血管风险禁区**（血管 + `--vessel-margin-mm` 默认 2 mm；血肿本身免检）
     - **路径主体不得穿过脑干**（脑干 + `--brainstem-margin-mm` 默认 2 mm；血肿本身免检）
     - **路径主体不得穿过功能区**（功能区 + `--eloquent-margin-mm` 默认 0 mm，即直接用 mask；血肿本身免检；<0 表示关闭）
  4. 评分 = 0.6·归一化长度 + 0.4·归一化入颅角度（越小越优）。
  5. **多目标分组挑选**：
     - 第一轮：每个目标至少塞 1 条（不做去重，保证目标覆盖）；
     - 第二轮：候选池（每目标 `--per-target-top` 条）按 score 排序，按入颅点 8 mm 物理距离去重；
     - 第三轮：仍不足 N 时从全集兜底；
     - 最终按 score 重排得到 Top-N。

**产物**（每个有血肿的病例）
- `*_paths.json` 完整规划结果（含 `targets` 列表 + 每条路径 `target_idx`/`target_role`/物理 LPS 坐标/长度/入颅角度/命中情况/评分；含每个目标的合法路径数 `legal_paths_per_target`；含拒绝统计；含 `forbidden_zones_used`）
- `*_paths_overlay.png` 三视图：颅骨白 + 脑室青 + 血管禁区橙 + 脑干棕 + 功能区紫 + 血肿红 + 彩色路径线 + **黄色星标 = 多目标点**
- `*_paths_3d.png` 3D：颅骨 + 脑室 + 血管禁区 + 脑干 + 功能区 + 血肿 + Top-N 路径 + **黄色星标多目标**

**常用参数**
```powershell
%PY% scripts\path_planning.py --top-n 10 --max-entry-points 1500
%PY% scripts\path_planning.py --w-length 0.7 --w-angle 0.3
%PY% scripts\path_planning.py --n-targets 5 --per-target-top 2     # 多目标加密：5 个采样点
%PY% scripts\path_planning.py --n-targets 1                        # 仅用质心（向后兼容）
%PY% scripts\path_planning.py --target-coverage 0.5                # 主轴只取中段 50%（更靠近质心）
%PY% scripts\path_planning.py --ventricle-margin-mm 5.0       # 提高脑室安全边界
%PY% scripts\path_planning.py --vessel-margin-mm 4.0          # 提高血管安全边界
%PY% scripts\path_planning.py --brainstem-margin-mm 4.0       # 提高脑干安全边界
%PY% scripts\path_planning.py --eloquent-margin-mm 3.0        # 把功能区也扩张 3mm（更严）
%PY% scripts\path_planning.py --eloquent-margin-mm -1         # 不使用功能区禁区（对比）
%PY% scripts\path_planning.py --ventricle-margin-mm 0 --vessel-margin-mm 0 --brainstem-margin-mm 0 --eloquent-margin-mm -1   # 全部关闭（对比）
```

---

## 步骤 5：自然语言手术方案 + 病例综合报告 PDF

```powershell
%PY% c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts\case_report.py
```

**作用**
- 不重新跑分割，仅消费上一步生成的 PNG / JSON。
- **生成两个产物**：
  - `*_plan.txt`：纯文本「自然语言手术方案」，医生可读，可直接打印
  - `*_report.pdf`：完整 PDF 报告，封面 + 方案文字 + 各分割三视图 + 路径图
- PDF 页面结构（A4 横向）：
  1. **封面**：患者 ID / CT 文件 / spacing / 体积概览 / 路径规划参数 / CT preview / 多目标点列表
  2. **手术方案（自然语言）**：从入颅点 / 目标点 / 进针长度 / 角度 / 操作步骤 / 备选方案 / 风险提示，全部翻译成中文文字
  3. **路径规划主页**：`paths_overlay` + `paths_3d` + Top-N 路径表
  4. **颅骨 / 脑+血肿 / 脑室 / 血管禁区 / 脑干 / 功能区** 各 1 页

**自然语言方案样例**（节选自 `*_plan.txt`）：
```
二、推荐手术方案
  ★ 推荐方案（Top-1，按【长度+角度】评分最优）
     入颅点 (LPS mm) : (  +73.4,  -139.0,  -508.9)   位于 左额部
     目标点 (LPS mm) : (  +38.1,  -151.2,  -509.1)   血肿主轴远端 A
     进针长度        : 37.3 mm   (路径短，到达目标快)
     入颅角度        : 19.0°（与颅骨外法线夹角）   (小角度斜行，常规入路可接受)
     入颅骨厚        : 5 体素 ≈ 4.0 mm
     ...
  【操作步骤建议】
    1. 体位与定位：依据 LPS 坐标系，将立体定向坐标原点对准 CT 原点；
    2. 入颅：在 (+73.4, -139.0, -508.9) 钻孔，与外法线夹角 19°。
    3. 进针：沿入颅点 → 目标点方向直线进针 37.3 mm，到达 血肿主轴远端 A。
    4. 引流：建议沿血肿 PCA 长轴留置引流管，可参考其他目标点位置。
```

**常用参数**
```powershell
%PY% scripts\case_report.py                          # 生成所有 *Hr40* 病例的 PDF + plan.txt
%PY% scripts\case_report.py --force                  # 覆盖已存在的 *_report.pdf
%PY% scripts\case_report.py --pattern "*Hr40*S3_00000262.nii.gz"  # 只跑指定病例
```

**产物**
- `*_plan.txt`（每病例 ~3 KB，纯中文文本）
- `*_report.pdf`（每病例 ~2-3 MB，A4 横向，约 8 页）

---

## 3. 完整运行（顺序执行 9 条命令）

```powershell
$PY = "C:\Users\aippletian\miniconda3\envs\test\python.exe"
$DIR = "c:\Users\aippletian\Desktop\songtt\projs\proj_01\scripts"

& $PY $DIR\dicom_to_nifti.py
& $PY $DIR\skull_segmentation.py
& $PY $DIR\brain_hematoma_segmentation.py
& $PY $DIR\ventricle_segmentation.py
& $PY $DIR\vessel_risk_segmentation.py
& $PY $DIR\brainstem_segmentation.py
& $PY $DIR\eloquent_zone_segmentation.py
& $PY $DIR\path_planning.py
& $PY $DIR\case_report.py
```

每一步约 30 秒到 2 分钟（取决于体数据大小），全流程在当前 2 例上约 6-8 分钟。

---

## 4. 输出文件总览（每个病例目录）

```
patient_<PID>/
├── CT_brain_0.80_Hr40_S3_<uid>.nii.gz                  # 步骤 1: 软组织 CT
├── CT_brain_0.80_Hr40_S3_<uid>_preview.png
├── CT_brain_0.80_Hr60_S3_<uid>.nii.gz                  # 步骤 1: 骨重建 CT
├── CT_brain_0.80_Hr60_S3_<uid>_preview.png
├── CT_brain_0.80_Hr60_S3_<uid>_skull_mask.nii.gz       # 步骤 2
├── CT_brain_0.80_Hr60_S3_<uid>_skull_overlay.png
├── CT_brain_0.80_Hr60_S3_<uid>_skull_3d.png
├── CT_brain_0.80_Hr60_S3_<uid>_skull_stats.txt
├── CT_brain_0.80_Hr40_S3_<uid>_intracranial_mask.nii.gz   # 步骤 3
├── CT_brain_0.80_Hr40_S3_<uid>_brain_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_hematoma_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_brain_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_brain_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_brain_report.json
├── CT_brain_0.80_Hr40_S3_<uid>_ventricle_mask.nii.gz   # 步骤 C
├── CT_brain_0.80_Hr40_S3_<uid>_ventricle_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_ventricle_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_ventricle_stats.json
├── CT_brain_0.80_Hr40_S3_<uid>_vessel_risk_mask.nii.gz # 步骤 A
├── CT_brain_0.80_Hr40_S3_<uid>_vessel_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_vessel_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_vessel_stats.json
├── CT_brain_0.80_Hr40_S3_<uid>_brainstem_mask.nii.gz   # 步骤 P0
├── CT_brain_0.80_Hr40_S3_<uid>_brainstem_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_brainstem_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_brainstem_stats.json
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_zone_mask.nii.gz   # 步骤 P1
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_motor_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_language_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_visual_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_deep_mask.nii.gz
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_eloquent_stats.json
├── CT_brain_0.80_Hr40_S3_<uid>_paths.json              # 步骤 4
├── CT_brain_0.80_Hr40_S3_<uid>_paths_overlay.png
├── CT_brain_0.80_Hr40_S3_<uid>_paths_3d.png
├── CT_brain_0.80_Hr40_S3_<uid>_plan.txt                # 步骤 5：自然语言手术方案
└── CT_brain_0.80_Hr40_S3_<uid>_report.pdf              # 步骤 5：综合报告 PDF
```

---

## 5. 验证 / 可视化

### 5.1 用 3D Slicer 加载（推荐）
1. 下载 [3D Slicer](https://download.slicer.org/)
2. `Add Data` 选 `*.nii.gz`，可同时加载多个 mask 作为 LabelMap：
   - `*_skull_mask.nii.gz`、`*_intracranial_mask.nii.gz`、`*_hematoma_mask.nii.gz`、`*_ventricle_mask.nii.gz`
3. `Modules → Volume Rendering` 可做 3D 体绘制；`Modules → Segment Editor` 可手动微调。

### 5.2 直接看 PNG 预览
所有 `*_overlay.png` 和 `*_3d.png` 是流水线生成的快速核验图，用任意图片查看器直接打开即可。

### 5.3 路径结果查看
打开 `*_paths.json`，每条 path 包含：
```json
{
  "score": 0.001,
  "length_mm": 47.4,
  "angle_to_normal_deg": 8.0,
  "hits_target": true,
  "entry_phys_lps_mm": [..., ..., ...],   # 入颅点 LPS 物理坐标 (x,y,z) mm
  "target_phys_lps_mm": [..., ..., ...],  # 血肿质心 LPS 物理坐标
  "in_intracranial_ratio": 0.95,
  "entry_skull_thickness_voxels": 9,
  "forbidden_hits": 0
}
```
入颅点 + 目标点的 LPS 物理坐标可直接喂给立体定向手术系统。

---

## 6. 辅助脚本

### 6.1 历史文件名乱码修复

如果之前用旧版本 `dicom_to_nifti.py` 生成过文件，文件名里可能带有 `鑴戦儴`（编码错位的"脑部"）。运行：
```powershell
%PY% scripts\rename_legacy_garbled.py --dry-run    # 预览
%PY% scripts\rename_legacy_garbled.py              # 实际改名
```
会把所有 `鑴戦儴` → `brain`，并同步修正 `manifest.csv`。

---

## 7. 已知限制与后续路线

| 模块 | 当前实现 | 限制 |
|---|---|---|
| 血肿分割 | HU 阈值 + 形态学 + 实心度 | 厚层 5mm 数据下漏检；与钙化区分依赖经验阈值 |
| 脑室分割 | HU 阈值 + 距骨过滤 | 无法分离左右侧脑室（需对称面分割或 atlas 配准） |
| 路径规划 | 颅骨外表面采样 + 直线评估 | 入颅角是相对单点法向量，未考虑骨缝、颞肌、面部血管 |
| 血管禁区 | **几何近似已实现**（步骤 A）：高密度结构 + 上矢状窦 + 大脑镰 + 颅底环带 | 平扫 CT 不能分割未钙化血管管腔；接入 CTA 后应替换 |
| 脑干 | **几何近似已实现**（步骤 P0）：颅腔下部中线区域 + HU 约束 + 最大连通域 | 形状是规则盒子，不贴合真实脑干曲面；接入 MRI 后应替换 |
| 功能区 | **几何近似已实现**（步骤 P1）：颅腔归一化坐标 + 4 类经验性禁区（运动/语言/视觉/深部核团） | 与真实功能区差异较大，仅作为**保守过近似**；接入 MRI atlas 后应替换 |

> 后续推进顺序建议：**多目标点扩展（血肿浅缘）** → 50GB 数据接入（确认是否有 MRI/CTA） → 交互可视化 → nnU-Net 替换粗分割 → MRI atlas 配准替换功能区/脑干几何近似。
