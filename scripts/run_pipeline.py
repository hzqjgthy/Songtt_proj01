from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import PipelineRunner, load_pipeline_config


def main() -> int:
    parser = argparse.ArgumentParser(description="配置驱动的项目流水线入口")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs" / "pipeline.logic.json"),
        help="JSON 配置文件路径",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="本次运行的时间戳后缀；不传时使用当前时间 YYYYMMDD_HHMMSS",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印执行计划，不真正运行")
    args = parser.parse_args()

    config = load_pipeline_config(args.config, PROJECT_ROOT)
    runner = PipelineRunner(config, run_id=args.run_id)
    commands = runner.run(dry_run=args.dry_run)

    print(f"[config] {config.config_path}")
    print(f"[project] {config.project_root}")
    print(f"[mode] {'dry-run' if args.dry_run else 'execute'}")
    print("")
    for idx, command in enumerate(commands, start=1):
        print(f"{idx:02d}. {command.stage_name} [{command.implementation}]")
        print(f"    {command.shell_preview()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
