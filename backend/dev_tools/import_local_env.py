"""从另一个本地项目安全迁移指定环境变量。

脚本只输出变量名，不输出值；目标文件通过临时文件原子替换。
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

ALLOWED_KEYS = frozenset({"WECOM_BOT_ID", "WECOM_BOT_SECRET"})


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def replace_values(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    replaced: set[str] = set()
    result: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                replaced.add(key)
                continue
        result.append(line)
    for key in sorted(updates.keys() - replaced):
        result.append(f"{key}={updates[key]}")
    return "\n".join(result) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="安全迁移本地环境变量")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("keys", nargs="+", choices=sorted(ALLOWED_KEYS))
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    if source == target:
        parser.error("源文件与目标文件不能相同")
    if not source.is_file() or not target.is_file():
        parser.error("源文件和目标文件都必须已存在")

    source_values = read_env(source)
    missing = [key for key in args.keys if not source_values.get(key)]
    if missing:
        parser.error(f"源文件缺少变量：{', '.join(missing)}")

    updates = {key: source_values[key] for key in args.keys}
    atomic_write(target, replace_values(target.read_text(encoding="utf-8"), updates))
    print(f"已迁移变量：{', '.join(args.keys)}（值已隐藏）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
