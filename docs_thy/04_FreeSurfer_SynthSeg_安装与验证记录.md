# FreeSurfer / SynthSeg 安装与验证记录

本文记录在本机 Apple Silicon Mac 上安装和配置 FreeSurfer 8.1.0，并验证 `mri_synthseg` 可用于本项目 CT NIfTI 数据的过程。

## 1. 本机环境

- 设备：MacBook Air，Apple M4
- 内存：16 GB
- 系统架构：arm64
- 项目目录：`/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01`
- FreeSurfer 版本：`freesurfer-macOS-darwin_arm64-8.1.0`

选择 8.1.0 的原因：该版本提供 Apple Silicon 原生 `darwin_arm64` 安装包，且不需要像 8.2.0 那样额外执行官方补丁脚本。

## 2. 安装依赖 XQuartz

FreeSurfer 在 macOS 上依赖 XQuartz 提供部分图形和 X11 运行支持。

安装方式：

1. 打开 `https://www.xquartz.org/`
2. 下载并安装最新版 XQuartz
3. 安装后建议重启一次 macOS

本机检查结果：

```bash
test -d /Applications/Utilities/XQuartz.app && echo XQUARTZ_APP_PRESENT
```

结果显示 `XQUARTZ_APP_PRESENT`，说明 XQuartz 已安装。

## 3. 安装 FreeSurfer 8.1.0 arm64

下载 Apple Silicon 版安装包：

```text
freesurfer-macOS-darwin_arm64-8.1.0.pkg
```

安装方式：

1. 双击 `.pkg` 安装包
2. 按安装向导完成安装
3. 保持默认安装目录

本机安装路径：

```text
/Applications/freesurfer/8.1.0
```

检查命令：

```bash
ls -d /Applications/freesurfer /Applications/freesurfer/8.1.0
```

## 4. 配置 FreeSurfer 许可证

FreeSurfer 需要 license 文件才能正常运行。许可证从 FreeSurfer 官网注册页面申请。

申请成功后，页面会给出若干行 license 内容。不要用 Word 或 Pages 保存，应该保存成纯文本文件。

本机保存位置：

```text
/Users/thy/license.txt
```

检查命令：

```bash
test -f /Users/thy/license.txt
wc -l /Users/thy/license.txt
```

本机检查结果：`license.txt` 存在，共 5 行。

注意：本文档不记录 license 原文，避免把个人许可证密钥写入项目文档。

## 5. 配置 zsh 环境变量

在 `~/.zshrc` 末尾加入：

```bash
# ========== FreeSurfer ==========
export FREESURFER_HOME="/Applications/freesurfer/8.1.0"
export FS_LICENSE="$HOME/license.txt"
source "$FREESURFER_HOME/SetUpFreeSurfer.sh"
```

这三行分别做了以下事情：

- `FREESURFER_HOME`：告诉终端 FreeSurfer 安装在哪里。
- `FS_LICENSE`：告诉 FreeSurfer license 文件在哪里。
- `source ...SetUpFreeSurfer.sh`：把 FreeSurfer 的命令、脚本、模型路径等加入当前 shell 环境。

修改后，新打开一个终端即可自动生效。当前终端可以手动执行：

```bash
source ~/.zshrc
```

## 6. 验证 mri_synthseg

验证命令：

```bash
which mri_synthseg
mri_synthseg --help
```

本机验证结果：

```text
/Applications/freesurfer/8.1.0/bin/mri_synthseg
```

`mri_synthseg --help` 可以正常显示帮助信息，说明命令已可用。

说明：用脚本检查时，`zsh -lc` 不会读取 `~/.zshrc`；如果需要模拟正常终端，应使用：

```bash
zsh -ic 'which mri_synthseg'
```

## 7. 单病例 SynthSeg 测试

为了确认不只是命令可启动，而是模型推理链路也能真正运行，已用项目中的一个 CT NIfTI 做了测试。

输入文件：

```text
/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262.nii.gz
```

执行命令：

```bash
mri_synthseg \
  --i /Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262.nii.gz \
  --o /Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg.nii.gz \
  --vol /Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg_volumes.csv \
  --qc /Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg_qc.csv \
  --ct \
  --cpu \
  --threads 8 \
  --keepgeom \
  --addctab
```

参数说明：

- `--ct`：按 CT 输入处理，会对 HU 做 SynthSeg 需要的裁剪。
- `--cpu`：强制使用 CPU，避免 Apple Silicon GPU 兼容问题。
- `--threads 8`：使用 8 个 CPU 线程。
- `--keepgeom`：让输出保持和输入相同的空间几何。
- `--addctab`：在输出分割文件中嵌入 FreeSurfer colortable。
- `--vol`：输出各脑结构体积表。
- `--qc`：输出质量控制评分。

本次测试结果：

- 推理模式：CPU
- 线程数：8
- 耗时：约 2 分钟
- 输出分割文件大小：约 2.4 MB
- 输出几何：`512 x 512 x 196`
- 输出体素尺寸：`0.486348 x 0.486348 x 0.800000`
- 输出方向：`LPS`

生成文件：

```text
/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg.nii.gz
/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg_volumes.csv
/Users/thy/Desktop/KeTiZu/proj_260608/Songtt_proj01/output_nifti/patient_0099039449/CT_brain_0.80_Hr40_S3_00000262_synthseg_qc.csv
```

## 8. 后续接入项目的建议

SynthSeg 不应替代当前项目的全部流程。它更适合用于增强或替换以下模块：

- 脑室分割
- 脑干禁区
- 深部核团禁区
- 部分皮层/功能区近似禁区

不建议用 SynthSeg 替代以下模块：

- 颅骨分割
- 血肿分割
- 血管风险区分割
- 穿刺路径规划

推荐接入方式：

1. 保留现有 CT 转换、颅骨分割和血肿分割流程。
2. 新增一个 SynthSeg 批处理步骤，给每个病例生成 `_synthseg.nii.gz`、`_synthseg_volumes.csv` 和 `_synthseg_qc.csv`。
3. 从 `_synthseg.nii.gz` 中按标签提取脑室、脑干、深部核团和皮层相关 mask。
4. 把这些 mask 转换成当前 `path_planning.py` 使用的禁区 mask。
5. 对比接入前后的路径规划结果，人工检查路径是否更合理。

## 9. 常用检查命令

检查 FreeSurfer 环境：

```bash
echo $FREESURFER_HOME
echo $FS_LICENSE
which mri_synthseg
```

检查 license：

```bash
test -f "$FS_LICENSE"
wc -l "$FS_LICENSE"
```

检查 SynthSeg 输出：

```bash
ls -lh *_synthseg.nii.gz *_synthseg_volumes.csv *_synthseg_qc.csv
mri_info *_synthseg.nii.gz
```

如果新终端找不到 `mri_synthseg`，先执行：

```bash
source ~/.zshrc
which mri_synthseg
```
