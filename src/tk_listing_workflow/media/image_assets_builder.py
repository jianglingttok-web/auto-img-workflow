from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from ..storage import read_json, write_json


class ImageAssetsBuilder:
    def sync_round(self, task_dir: Path, round_number: int) -> dict[str, Any]:
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        if not round_dir.exists():
            raise FileNotFoundError(f"round media directory not found: {round_dir}")

        main_images = self._list_images(round_dir / "main")
        sub_images = self._list_images(round_dir / "sub")
        if not main_images and not sub_images:
            raise ValueError(f"no generated images found under {round_dir}")

        image_assets_path = task_dir / "image_assets.json"
        image_assets = read_json(image_assets_path)
        result_meta = self._read_round_result_meta(task_dir, round_number)

        image_assets["main_images"] = main_images
        image_assets["sub_images"] = sub_images
        image_assets.setdefault("detail_images", [])
        image_assets.setdefault("a_plus_images", [])
        image_assets["generation_meta"] = {
            "tool": "seedream",
            "version": result_meta.get("model", ""),
            "created_at": utc_now_iso(),
            "round": round_number,
            "main_count": len(main_images),
            "sub_count": len(sub_images),
            "result_file": str(task_dir / "media" / f"round_{round_number:02d}_seedream_results.json"),
            "preview_manifest": str(round_dir / "preview" / "preview_manifest.json"),
        }
        write_json(image_assets_path, image_assets)

        manifest_path = task_dir / "manifest.json"
        manifest = read_json(manifest_path)
        manifest["updated_at"] = utc_now_iso()
        manifest.setdefault("outputs", {})["image_assets"] = {
            "path": str(image_assets_path),
            "built": True,
            "round": round_number,
            "main_count": len(main_images),
            "sub_count": len(sub_images),
        }
        manifest.setdefault("events", []).append(
            {
                "timestamp": utc_now_iso(),
                "event": "image_assets_synced",
                "detail": {
                    "round": round_number,
                    "main_count": len(main_images),
                    "sub_count": len(sub_images),
                },
            }
        )
        write_json(manifest_path, manifest)

        return {
            "task_id": manifest.get("task_id", ""),
            "round": round_number,
            "main_images": main_images,
            "sub_images": sub_images,
            "generation_meta": image_assets["generation_meta"],
        }

    def _list_images(self, directory: Path) -> list[str]:
        if not directory.exists():
            return []
        return [
            str(path)
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]

    def _read_round_result_meta(self, task_dir: Path, round_number: int) -> dict[str, Any]:
        result_path = task_dir / "media" / f"round_{round_number:02d}_seedream_results.json"
        if not result_path.exists():
            return {}
        payload = read_json(result_path)
        for item in payload.get("results", []):
            response = item.get("response", {})
            if response.get("model"):
                return {"model": response.get("model", "")}
        return {}