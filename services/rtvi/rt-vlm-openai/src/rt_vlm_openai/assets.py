# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local uploaded-asset storage."""

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile

from .models import FileInfo

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Asset:
    info: FileInfo
    path: Path


class AssetStore:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self._root = root
        self._max_upload_bytes = max_upload_bytes
        self._assets: dict[UUID, Asset] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for metadata_path in self._root.glob("*/metadata.json"):
            try:
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                info = FileInfo.model_validate(data)
                media_path = next(path for path in metadata_path.parent.iterdir() if path.name != "metadata.json")
                self._assets[info.id] = Asset(info=info, path=media_path)
            except (OSError, ValueError, StopIteration):
                continue

    async def save(
        self,
        upload: UploadFile,
        *,
        file_id: UUID | None,
        purpose: str,
        media_type: str,
        creation_time: str | None,
        sensor_name: str,
    ) -> Asset:
        asset_id = file_id or uuid4()
        filename = _SAFE_FILENAME.sub("_", Path(upload.filename or "upload.bin").name) or "upload.bin"
        asset_dir = self._root / str(asset_id)
        path = asset_dir / filename

        async with self._lock:
            if asset_id in self._assets or asset_dir.exists():
                raise FileExistsError(f"Asset {asset_id} already exists")
            asset_dir.mkdir(parents=True)

        size = 0
        try:
            with path.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._max_upload_bytes:
                        raise ValueError(f"File exceeds maximum size of {self._max_upload_bytes} bytes")
                    destination.write(chunk)
            info = FileInfo(
                id=asset_id,
                bytes=size,
                filename=filename,
                creation_time=creation_time,
                purpose=purpose,
                sensor_name=sensor_name,
                media_type=media_type,
            )
            (asset_dir / "metadata.json").write_text(info.model_dump_json(indent=2), encoding="utf-8")
            asset = Asset(info=info, path=path)
            async with self._lock:
                self._assets[asset_id] = asset
            return asset
        except Exception:
            shutil.rmtree(asset_dir, ignore_errors=True)
            raise
        finally:
            await upload.close()

    async def list(self) -> list[Asset]:
        async with self._lock:
            return sorted(self._assets.values(), key=lambda asset: asset.info.filename)

    async def get(self, asset_id: UUID) -> Asset:
        async with self._lock:
            asset = self._assets.get(asset_id)
        if asset is None:
            raise KeyError(str(asset_id))
        return asset

    async def delete(self, asset_id: UUID) -> None:
        async with self._lock:
            asset = self._assets.pop(asset_id, None)
        if asset is None:
            raise KeyError(str(asset_id))
        shutil.rmtree(asset.path.parent)
