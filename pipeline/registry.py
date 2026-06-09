from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .config import PipelineConfig, StageConfig


@dataclass(frozen=True)
class StageCommand:
    stage_name: str
    implementation: str
    argv: List[str]

    def shell_preview(self) -> str:
        return " ".join(shlex.quote(part) for part in self.argv)


SCRIPT_MAP: Dict[Tuple[str, str], str] = {
    ("dicom_to_nifti", "logic"): "scripts/dicom_to_nifti.py",
    ("skull_segmentation", "logic"): "scripts/skull_segmentation.py",
    ("brain_hematoma_segmentation", "logic"): "scripts/brain_hematoma_segmentation.py",
    ("ventricle_segmentation", "logic"): "scripts/ventricle_segmentation.py",
    ("vessel_risk_segmentation", "logic"): "scripts/vessel_risk_segmentation.py",
    ("brainstem_segmentation", "logic"): "scripts/brainstem_segmentation.py",
    ("eloquent_zone_segmentation", "logic"): "scripts/eloquent_zone_segmentation.py",
    ("path_planning", "logic"): "scripts/path_planning.py",
    ("narrative", "logic"): "scripts/narrative.py",
    ("case_report", "logic"): "scripts/case_report.py",
    ("ventricle_segmentation", "synthseg"): "scripts/export_synthseg_masks.py",
    ("brainstem_segmentation", "synthseg"): "scripts/export_synthseg_masks.py",
}


def _append_cli_args(argv: List[str], args: Dict[str, object]) -> None:
    for key, value in args.items():
        flag = f"--{key.replace('_', '-')}"
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        if isinstance(value, (list, tuple)):
            argv.append(flag)
            argv.extend(str(item) for item in value)
            continue
        argv.extend([flag, str(value)])


def build_stage_command(cfg: PipelineConfig, stage_name: str, stage_cfg: StageConfig) -> StageCommand:
    key = (stage_name, stage_cfg.implementation)
    try:
        script_rel = SCRIPT_MAP[key]
    except KeyError as exc:
        raise ValueError(
            f"unsupported implementation '{stage_cfg.implementation}' for stage '{stage_name}'"
        ) from exc

    script_path = cfg.project_root / script_rel
    argv: List[str] = [cfg.python_executable, str(script_path)]

    if stage_cfg.implementation == "synthseg":
        target = "ventricle" if stage_name == "ventricle_segmentation" else "brainstem"
        argv.extend(["--targets", target])
        synthseg_defaults = {
            "synthseg_command": cfg.freesurfer_command,
            "threads": cfg.synthseg.get("threads"),
            "cpu": cfg.synthseg.get("cpu"),
            "ct": cfg.synthseg.get("ct", True),
            "keepgeom": cfg.synthseg.get("keepgeom", True),
            "addctab": cfg.synthseg.get("addctab", True),
            "skip_if_synthseg_exists": cfg.synthseg.get("skip_if_exists", True),
        }
        _append_cli_args(argv, synthseg_defaults)

    _append_cli_args(argv, stage_cfg.args)
    return StageCommand(stage_name=stage_name, implementation=stage_cfg.implementation, argv=argv)


def build_stage_plan(cfg: PipelineConfig) -> Iterable[StageCommand]:
    for stage_name in cfg.stage_order:
        stage_cfg = cfg.stage(stage_name)
        if not stage_cfg.enabled:
            continue
        yield build_stage_command(cfg, stage_name, stage_cfg)
