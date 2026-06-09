from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping


DEFAULT_STAGE_ORDER = [
    "dicom_to_nifti",
    "skull_segmentation",
    "brain_hematoma_segmentation",
    "ventricle_segmentation",
    "vessel_risk_segmentation",
    "brainstem_segmentation",
    "eloquent_zone_segmentation",
    "path_planning",
    "narrative",
    "case_report",
]


@dataclass(frozen=True)
class StageConfig:
    enabled: bool = True
    implementation: str = "logic"
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    project_root: Path
    python_executable: str
    freesurfer_command: str
    run_output: Dict[str, Any]
    stage_order: List[str]
    synthseg: Dict[str, Any]
    stages: Dict[str, StageConfig]

    def stage(self, name: str) -> StageConfig:
        try:
            return self.stages[name]
        except KeyError as exc:
            raise KeyError(f"stage not configured: {name}") from exc


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON config: {path}") from exc


def _load_stage_configs(raw: Mapping[str, Any]) -> Dict[str, StageConfig]:
    stages: Dict[str, StageConfig] = {}
    for stage_name, stage_raw in raw.items():
        if not isinstance(stage_raw, Mapping):
            raise ValueError(f"stage config must be an object: {stage_name}")
        enabled = bool(stage_raw.get("enabled", True))
        implementation = str(stage_raw.get("implementation", "logic")).strip()
        args = dict(stage_raw.get("args", {}))
        stages[stage_name] = StageConfig(
            enabled=enabled,
            implementation=implementation,
            args=args,
        )
    return stages


def load_pipeline_config(config_path: str | Path, project_root: str | Path) -> PipelineConfig:
    cfg_path = Path(config_path).resolve()
    root = Path(project_root).resolve()
    raw = _read_json(cfg_path)

    stages_raw = raw.get("stages")
    if not isinstance(stages_raw, Mapping) or not stages_raw:
        raise ValueError("config.stages must be a non-empty object")

    stage_order = list(raw.get("stage_order") or DEFAULT_STAGE_ORDER)
    configured_stage_names = set(stages_raw.keys())
    unknown_from_order = [name for name in stage_order if name not in configured_stage_names]
    if unknown_from_order:
        raise ValueError(f"stage_order contains unknown stages: {unknown_from_order}")

    for stage_name in configured_stage_names:
        if stage_name not in stage_order:
            stage_order.append(stage_name)

    return PipelineConfig(
        config_path=cfg_path,
        project_root=root,
        python_executable=str(raw.get("python_executable", "python")),
        freesurfer_command=str(raw.get("freesurfer_command", "mri_synthseg")),
        run_output=dict(raw.get("run_output", {})),
        stage_order=stage_order,
        synthseg=dict(raw.get("synthseg", {})),
        stages=_load_stage_configs(stages_raw),
    )
