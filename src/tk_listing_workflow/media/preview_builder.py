from __future__ import annotations

from math import ceil
from pathlib import Path

from ..storage import write_json


class PreviewBuilder:
    def build_round_previews(self, task_dir: Path, round_number: int) -> dict[str, str]:
        self._require_pillow()
        round_dir = task_dir / "media" / f"round_{round_number:02d}"
        preview_dir = round_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)

        main_preview = self._build_main_preview(round_dir, preview_dir)
        sub_contact_sheet = self._build_sub_contact_sheet(round_dir, preview_dir)

        payload = {
            "round": round_number,
            "main_preview": str(main_preview) if main_preview else "",
            "sub_contact_sheet": str(sub_contact_sheet) if sub_contact_sheet else "",
        }
        write_json(preview_dir / "preview_manifest.json", payload)
        return payload

    def _build_main_preview(self, round_dir: Path, preview_dir: Path) -> Path | None:
        Image, _, _ = self._require_pillow()
        main_dir = round_dir / "main"
        first_main = self._pick_first_image(main_dir)
        if not first_main:
            return None

        target = preview_dir / "main_preview.jpg"
        with Image.open(first_main) as image:
            preview = self._fit_cover(image.convert("RGB"), (1200, 1200))
            preview.save(target, format="JPEG", quality=92)
        return target

    def _build_sub_contact_sheet(self, round_dir: Path, preview_dir: Path) -> Path | None:
        Image, ImageDraw, ImageFont = self._require_pillow()
        sub_dir = round_dir / "sub"
        sub_images = self._list_images(sub_dir)
        if not sub_images:
            return None

        cell_size = (900, 900)
        columns = 4 if len(sub_images) > 4 else min(len(sub_images), 4)
        rows = ceil(len(sub_images) / columns)
        gutter = 24
        header_h = 64
        sheet_width = columns * cell_size[0] + (columns + 1) * gutter
        sheet_height = rows * cell_size[1] + (rows + 1) * gutter

        sheet = Image.new("RGB", (sheet_width, sheet_height), color=(245, 245, 245))
        font = ImageFont.load_default()
        draw = ImageDraw.Draw(sheet)

        for index, image_path in enumerate(sub_images):
            row = index // columns
            col = index % columns
            x = gutter + col * (cell_size[0] + gutter)
            y = gutter + row * (cell_size[1] + gutter)

            with Image.open(image_path) as image:
                tile = self._fit_cover(image.convert("RGB"), (cell_size[0], cell_size[1]))
            sheet.paste(tile, (x, y))

            label = self._derive_slot_label(image_path)
            label_w = int(draw.textlength(label, font=font))
            box = (x, y, x + label_w + 28, y + header_h)
            draw.rounded_rectangle(box, radius=16, fill=(0, 0, 0))
            draw.text((x + 14, y + 20), label, fill=(255, 255, 255), font=font)

        target = preview_dir / "sub_contact_sheet.jpg"
        sheet.save(target, format="JPEG", quality=90)
        return target

    def _pick_first_image(self, directory: Path) -> Path | None:
        images = self._list_images(directory)
        return images[0] if images else None

    def _list_images(self, directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return [
            path
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]

    def _derive_slot_label(self, image_path: Path) -> str:
        parts = image_path.stem.split("_")
        if len(parts) >= 2:
            return f"{parts[0]}_{parts[1]}"
        return image_path.stem

    def _fit_cover(self, image, size: tuple[int, int]):
        target_w, target_h = size
        src_w, src_h = image.size
        scale = max(target_w / src_w, target_h / src_h)
        resized = image.resize((int(src_w * scale), int(src_h * scale)))

        left = max((resized.width - target_w) // 2, 0)
        top = max((resized.height - target_h) // 2, 0)
        return resized.crop((left, top, left + target_w, top + target_h))

    def _require_pillow(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ModuleNotFoundError as exc:
            raise RuntimeError("Pillow is required for build-previews. Install it with `py -3 -m pip install Pillow`.") from exc
        return Image, ImageDraw, ImageFont
