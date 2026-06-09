from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import PipelineConfig
from .registry import StageCommand, build_stage_plan


@dataclass
class PipelineRunner:
    config: PipelineConfig
    run_id: Optional[str] = None

    def plan(self) -> List[StageCommand]:
        return list(build_stage_plan(self.config))

    def _timestamp(self) -> str:
        if self.run_id:
            return self.run_id
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _prepare_run_inputs(self, *, dry_run: bool = False) -> List[Path]:
        run_output = self.config.run_output
        if not run_output.get("enabled", False):
            return []

        source_input = Path(run_output.get("source_input", "output_nifti"))
        if not source_input.is_absolute():
            source_input = self.config.project_root / source_input
        patient_dirs = list(run_output.get("patient_dirs", []))
        if not patient_dirs:
            raise ValueError("run_output.patient_dirs must be configured when run_output.enabled=true")

        timestamp = self._timestamp()
        prepared: List[Path] = []
        for patient_dir_name in patient_dirs:
            source_dir = source_input / patient_dir_name
            target_dir = source_input / f"{patient_dir_name}_{timestamp}"
            if not source_dir.exists():
                raise FileNotFoundError(f"source patient dir not found: {source_dir}")
            if target_dir.exists():
                raise FileExistsError(f"timestamped output dir already exists: {target_dir}")
            if not dry_run:
                shutil.copytree(source_dir, target_dir)
            prepared.append(target_dir)
        return prepared

    @staticmethod
    def _with_input_dir(command: StageCommand, input_dir: Path) -> StageCommand:
        argv = list(command.argv)
        if "--input" in argv:
            idx = argv.index("--input")
            if idx + 1 >= len(argv):
                raise ValueError(f"--input has no value in command: {command.shell_preview()}")
            argv[idx + 1] = str(input_dir)
        else:
            argv.extend(["--input", str(input_dir)])
        return StageCommand(
            stage_name=command.stage_name,
            implementation=command.implementation,
            argv=argv,
        )

    def run(self, *, dry_run: bool = False) -> List[StageCommand]:
        base_commands = self.plan()
        run_inputs = self._prepare_run_inputs(dry_run=dry_run)
        if run_inputs:
            commands = [
                self._with_input_dir(command, run_input)
                for run_input in run_inputs
                for command in base_commands
            ]
        else:
            commands = base_commands

        if dry_run:
            return commands
        for command in commands:
            subprocess.run(
                command.argv,
                cwd=self.config.project_root,
                check=True,
            )
        return commands
