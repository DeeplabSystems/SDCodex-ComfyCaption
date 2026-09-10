import os
import mimetypes
from aiohttp import web
from server import PromptServer

# The gallery is fully disk-based: images live in real folders and captions are
# read from "<image>.txt" sidecar files (as written by the captioning plugin).
# SD prompts / negative prompts / generation info are read from image metadata.
# No SDCodex database is used.

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(parent_dir, "run.py")):
    default_sdcodex_root = parent_dir
else:
    default_sdcodex_root = "/home/naked/dev/SDCodex"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}


def _resolve_root(root_param):
    """Sanitize an sdcodex_root parameter.

    A stale workflow may persist an image file path (e.g. an old
    .../static/saved_gallery/<file>.jpeg) in the sdcodex_root widget, which
    then gets used as the base for folder lookup. Only accept values that are
    real directories; anything else falls back to the detected default root.
    """
    root = (root_param or "").strip()
    if root:
        root = os.path.normpath(root)
        if not os.path.isdir(root):
            root = ""
    return root or default_sdcodex_root


def _read_sidecar_caption(image_path):
    """Read the .txt sidecar caption beside an image, if present."""
    base, _ = os.path.splitext(image_path)
    txt_path = base + ".txt"
    if os.path.isfile(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as fh:
                return fh.read().strip()
        except Exception:
            pass
    return ""


def _read_sd_prompt(image_path):
    """Best-effort SD positive/negative from image metadata (A1111-style)."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            meta = img.info.get("prompt") or img.info.get("parameters")
            if meta and isinstance(meta, str) and "workflow" not in meta:
                lines = [ln.strip() for ln in meta.split("\n") if ln.strip()]
                if lines:
                    positive = lines[0]
                    negative = ""
                    for ln in lines[1:]:
                        if ln.lower().startswith("negative prompt:"):
                            negative = ln[len("negative prompt:"):].strip()
                            break
                    return positive, negative
    except Exception:
        pass
    return "", ""


def _list_images(folder_path):
    """Return sorted image file names in a directory (case-insensitive ext)."""
    try:
        names = os.listdir(folder_path)
    except Exception:
        return []
    return sorted(
        n for n in names
        if os.path.splitext(n)[1].lower() in IMAGE_EXTENSIONS
    )


@PromptServer.instance.routes.get("/sdcodex/images")
async def get_sdcodex_images(request):
    params = request.query
    mode = params.get("mode", "Folder Path")
    folder_path = params.get("folder_path", "").strip()
    sdcodex_root = _resolve_root(params.get("sdcodex_root", ""))
    page = int(params.get("page", 1))
    limit = int(params.get("limit", 24))

    page = max(1, page)
    limit = max(1, limit)

    results = []
    total = 0
    total_pages = 1

    try:
        # Both modes browse a real folder on disk. "Saved Gallery" historically
        # read from the (now removed) database; it now falls back to folder_path
        # (or a gallery root dir under sdcodex_root) like "Folder Path".
        base_dir = folder_path
        if not base_dir or not os.path.isdir(base_dir):
            base_dir = os.path.join(sdcodex_root, "downloads")

        if not os.path.isdir(base_dir):
            return web.json_response(
                {"error": f"Folder does not exist or is not a directory: {base_dir}"},
                status=400,
            )

        image_files = _list_images(base_dir)
        total = len(image_files)
        total_pages = (total + limit - 1) // limit if total > 0 else 1

        start_index = (page - 1) * limit
        page_files = image_files[start_index: start_index + limit]

        for file in page_files:
            img_abs_path = os.path.join(base_dir, file)
            caption = _read_sidecar_caption(img_abs_path)
            sd_pos, sd_neg = _read_sd_prompt(img_abs_path)
            results.append({
                "file_name": file,
                "image_path": img_abs_path,
                "caption": caption,
                "sd_prompt": sd_pos,
                "sd_negative": sd_neg,
            })

        return web.json_response({
            "images": results,
            "total": total,
            "page": page,
            "totalPages": total_pages,
            "limit": limit,
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@PromptServer.instance.routes.get("/sdcodex/image")
async def get_sdcodex_image_content(request):
    params = request.query
    path = params.get("path", "").strip()
    if not path:
        return web.Response(status=400, text="Path parameter is required")

    if not os.path.exists(path):
        return web.Response(status=404, text="File not found")

    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        return web.Response(status=400, text="File requested is not a valid image format")

    try:
        with open(path, "rb") as f:
            content = f.read()
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "image/jpeg"
        return web.Response(body=content, content_type=mime)
    except Exception as e:
        return web.Response(status=500, text=str(e))


class SDCodexGalleryBrowser:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mode": (["Folder Path", "Saved Gallery"], {"default": "Folder Path"}),
                "gallery": ("STRING", {"default": ""}),
                "folder_path": ("STRING", {"default": ""}),
                "sdcodex_root": ("STRING", {"default": ""}),
                "selected_image": ("STRING", {"default": ""}),
                "caption": ("STRING", {"default": "", "multiline": True}),
                "sd_prompt": ("STRING", {"default": "", "multiline": True}),
                "sd_negative": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "IMAGE")
    RETURN_NAMES = ("selected_image", "caption", "sd_prompt", "sd_negative", "image")
    FUNCTION = "execute"
    CATEGORY = "SDCodex"

    def execute(self, mode="Folder Path", gallery="", folder_path="", sdcodex_root="", selected_image="", caption="", sd_prompt="", sd_negative=""):
        import torch
        import numpy as np
        from PIL import Image, ImageOps

        out_image = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
        out_sd_prompt = sd_prompt or ""
        out_sd_negative = sd_negative or ""

        img_path = None
        root = _resolve_root(sdcodex_root)

        if selected_image:
            if os.path.isabs(selected_image) and os.path.exists(selected_image):
                img_path = selected_image
            elif folder_path and os.path.isdir(folder_path):
                img_path = os.path.join(folder_path, selected_image)
            else:
                # Fall back to a gallery root under sdcodex_root.
                candidate = os.path.join(root, "downloads", selected_image)
                if os.path.isfile(candidate):
                    img_path = candidate

        if img_path and os.path.isfile(img_path):
            try:
                img = Image.open(img_path)
                img = ImageOps.exif_transpose(img)
                img_rgb = img.convert("RGB")

                np_img = np.array(img_rgb).astype(np.float32) / 255.0
                out_image = torch.from_numpy(np_img)[None,]

                if not caption.strip():
                    sidecar = _read_sidecar_caption(img_path)
                    if sidecar:
                        caption = sidecar
                if not out_sd_prompt or not out_sd_negative:
                    sd_pos, sd_neg = _read_sd_prompt(img_path)
                    if not out_sd_prompt:
                        out_sd_prompt = sd_pos
                    if not out_sd_negative:
                        out_sd_negative = sd_neg
            except Exception as e:
                print(f"Error loading image file {img_path}: {e}")

        return (selected_image or "", caption or "", out_sd_prompt, out_sd_negative, out_image)