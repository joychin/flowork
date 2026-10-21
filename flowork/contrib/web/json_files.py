"""定义目录的 JSON 文件读写共用件：根解析、路径防护、原子写、读取。

定义文件（``*.json``）与场景脚本（``*.scenario.json``）同住一个目录，共用同一套
安全边界：文件名不得含路径分隔符、不得以点开头、必须是 ``.json``、解析后必须
落在授权根目录内。两处各写一遍必然漂移，故单源于此。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

__all__ = [
    "FILE_SUFFIX",
    "read_json",
    "resolve_json_path",
    "root_of",
    "write_json_atomic",
]

FILE_SUFFIX = ".json"


def root_of(directory: str | Path) -> Path:
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_json_path(root: Path, file_name: str) -> Path:
    """把请求里的文件名解析成根目录内的路径（拒绝穿越与隐藏文件）。"""
    if (
        not file_name
        or file_name != Path(file_name).name
        or file_name.startswith(".")
        or "/" in file_name
        or "\\" in file_name
    ):
        raise HTTPException(status_code=400, detail=f"非法文件名：{file_name!r}")
    if not file_name.endswith(FILE_SUFFIX):
        raise HTTPException(status_code=400, detail=f"只接受 {FILE_SUFFIX} 文件：{file_name!r}")
    path = (root / file_name).resolve()
    if path.parent != root:
        raise HTTPException(status_code=400, detail=f"路径越界：{file_name!r}")
    return path


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"JSON 解析失败：{exc}") from exc


def write_json_atomic(path: Path, content: Any) -> None:
    """同目录临时文件 + rename：读者永远看到完整文件，不会读到半个 JSON。"""
    text = json.dumps(content, ensure_ascii=False, indent=2) + "\n"
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
