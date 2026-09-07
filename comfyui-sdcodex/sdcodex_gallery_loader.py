import os
import sqlite3

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(parent_dir, "app", "civitr.db")) or os.path.exists(os.path.join(parent_dir, "run.py")):
    default_sdcodex_root = parent_dir
else:
    default_sdcodex_root = "/home/naked/dev/SDCodex"


def _find_db(root):
    """Locate the SDCodex SQLite database relative to an sdcodex_root.

    Tries, in order:
      <root>/db/sdcodex.db    (Docker compose bind mount at ./db)
      <root>/app/civitr.db    (classic repo layout / bare-metal dev)
      <root>/civitr.db        (repo root fallback)
    """
    candidates = [
        os.path.join(root, "db", "sdcodex.db"),
        os.path.join(root, "app", "civitr.db"),
        os.path.join(root, "civitr.db"),
    ]
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    return candidates[0]  # report the first expected path in errors


class SDCodexGalleryLoader:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "selected_image": ("STRING", {"default": ""}),
                "caption": ("STRING", {"default": "", "multiline": True}),
                "sd_prompt": ("STRING", {"default": "", "multiline": True}),
                "sd_negative": ("STRING", {"default": "", "multiline": True}),
                "prefix": ("STRING", {"default": "", "multiline": True}),
                "suffix": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("caption", "image", "sd_prompt", "sd_negative")
    FUNCTION = "execute"
    CATEGORY = "SDCodex"

    def execute(self, selected_image, caption, sd_prompt, sd_negative, prefix="", suffix=""):
        import torch
        import numpy as np
        from PIL import Image, ImageOps

        out_image = torch.zeros((1, 512, 512, 3), dtype=torch.float32)

        final_caption = caption or ""
        out_sd_prompt = sd_prompt or ""
        out_sd_negative = sd_negative or ""

        img_path = None
        if selected_image:
            if os.path.isabs(selected_image) and os.path.exists(selected_image):
                img_path = selected_image

        if not img_path and selected_image:
            root = default_sdcodex_root
            db_path = _find_db(root)

            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT image_path, sd_prompt, sd_negative FROM gallery_image WHERE file_name = ? LIMIT 1",
                        (os.path.basename(selected_image),)
                    )
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        img_rel = row["image_path"]
                        if img_rel.startswith("/static/"):
                            img_rel = img_rel[8:]
                        elif img_rel.startswith("static/"):
                            img_rel = img_rel[7:]
                        img_path = os.path.join(root, "app", "static", img_rel)
                        if not out_sd_prompt:
                            out_sd_prompt = row["sd_prompt"] or ""
                        if not out_sd_negative:
                            out_sd_negative = row["sd_negative"] or ""
                except Exception as e:
                    print(f"Error loading image path from database: {e}")

        if img_path and os.path.exists(img_path):
            try:
                img = Image.open(img_path)
                img = ImageOps.exif_transpose(img)
                img_rgb = img.convert("RGB")

                np_img = np.array(img_rgb).astype(np.float32) / 255.0
                out_image = torch.from_numpy(np_img)[None,]

                if not out_sd_prompt:
                    if "workflow" not in img.info and "prompt" in img.info:
                        try:
                            metadata = img.info["prompt"]
                            if isinstance(metadata, str):
                                lines = [line.strip() for line in metadata.split("\n") if line.strip()]
                                if lines:
                                    out_sd_prompt = lines[0]
                                    for line in lines[1:]:
                                        if line.lower().startswith("negative prompt:"):
                                            out_sd_negative = line[16:].strip()
                                            break
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error loading image file {img_path}: {e}")

        if prefix and prefix.strip():
            p = prefix.strip()
            if final_caption:
                sep = " " if not (p.endswith(",") or p.endswith(" ")) else ""
                final_caption = f"{p}{sep}{final_caption}"
            else:
                final_caption = p
            if out_sd_prompt:
                sep = " " if not (p.endswith(",") or p.endswith(" ")) else ""
                out_sd_prompt = f"{p}{sep}{out_sd_prompt}"
            else:
                out_sd_prompt = p

        if suffix and suffix.strip():
            s = suffix.strip()
            if final_caption:
                sep = " " if not (s.startswith(",") or s.startswith(" ")) else ""
                final_caption = f"{final_caption}{sep}{s}"
            else:
                final_caption = s
            if out_sd_prompt:
                sep = " " if not (s.startswith(",") or s.startswith(" ")) else ""
                out_sd_prompt = f"{out_sd_prompt}{sep}{s}"
            else:
                out_sd_prompt = s

        return (final_caption, out_image, out_sd_prompt, out_sd_negative)
