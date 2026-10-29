#!/usr/bin/env python3
"""导出 SDD API 契约：docs/sdd/api-spec.yaml。

用法：
    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --check   # CI：契约与实现不一致时退出 1

契约 = FastAPI 自动 OpenAPI + 统一错误信封（见 app/api/contract.py）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.contract import build_contract_spec
from app.main import create_app

DEFAULT_OUTPUT = Path("docs/sdd/api-spec.yaml")


def render() -> str:
    spec = build_contract_spec(create_app())
    return yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="导出 / 校验 SDD API 契约")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)

    rendered = render()
    output = Path(args.output)
    if args.check:
        if not output.exists():
            print(f"契约文件不存在：{output}", file=sys.stderr)
            raise SystemExit(1)
        if output.read_text(encoding="utf-8") != rendered:
            print(
                f"契约与实现不一致：{output}；请执行 uv run python scripts/export_openapi.py",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"契约一致：{output}")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"已导出契约：{output}")


if __name__ == "__main__":
    main()
