import os
import sqlite3
import mimetypes
from aiohttp import web
from server import PromptServer

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


def _resolve_root(root_param):
    """Sanitize an sdcodex_root parameter.

    A stale workflow may persist an image file path (e.g. an old
    .../static/saved_gallery/<file>.jpeg) in the sdcodex_root widget, which
    then gets used as the base for the DB lookup. Only accept values that are
    real directories; anything else falls back to the detected default root.
    """
    root = (root_param or "").strip()
    if root:
        root = os.path.normpath(root)
        if not os.path.isdir(root):
            root = ""
    return root or default_sdcodex_root


@PromptServer.instance.routes.get("/sdcodex/images")
async def get_sdcodex_images(request):
    params = request.query
    mode = params.get("mode", "Saved Gallery")
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
        if mode == "Folder Path":
            if not folder_path:
                return web.json_response({"error": "Folder path is required in Folder Path mode"}, status=400)

            if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
                return web.json_response({"error": f"Folder does not exist or is not a directory: {folder_path}"}, status=400)

            all_files = os.listdir(folder_path)
            image_files = []
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
            for file in all_files:
                ext = os.path.splitext(file)[1].lower()
                if ext in image_extensions:
                    image_files.append(file)

            image_files.sort()
            total = len(image_files)
            total_pages = (total + limit - 1) // limit if total > 0 else 1

            start_index = (page - 1) * limit
            page_files = image_files[start_index : start_index + limit]

            for file in page_files:
                img_abs_path = os.path.join(folder_path, file)
                txt_path = os.path.join(folder_path, os.path.splitext(file)[0] + '.txt')

                caption = ""
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, 'r', encoding='utf-8') as f:
                            caption = f.read().strip()
                    except Exception:
                        pass

                results.append({
                    "file_name": file,
                    "image_path": img_abs_path,
                    "caption": caption,
                    "sd_prompt": "",
                    "sd_negative": ""
                })

        else:  # Saved Gallery
            db_path = _find_db(sdcodex_root)
            if not os.path.exists(db_path):
                return web.json_response({"error": f"SDCodex database not found (tried {db_path})"}, status=404)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Optional gallery filter (by gallery name, e.g. "Default Gallery").
            gallery_filter = params.get("gallery", "").strip()
            gallery_id = None
            if gallery_filter:
                cursor.execute("SELECT id FROM gallery WHERE name = ? LIMIT 1", (gallery_filter,))
                g = cursor.fetchone()
                if g:
                    gallery_id = g["id"]

            if gallery_filter and gallery_id is None:
                # Filter requested but no such gallery → show nothing.
                rows = []
                total = 0
            else:
                if gallery_id is not None:
                    cursor.execute("SELECT COUNT(*) FROM gallery_image WHERE gallery_id = ?", (gallery_id,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM gallery_image")
                total = cursor.fetchone()[0]
                total_pages = (total + limit - 1) // limit if total > 0 else 1

                offset = (page - 1) * limit
                if gallery_id is not None:
                    cursor.execute(
                        "SELECT id, file_name, image_path, caption, sd_prompt, sd_negative FROM gallery_image "
                        "WHERE gallery_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (gallery_id, limit, offset)
                    )
                else:
                    cursor.execute(
                        "SELECT id, file_name, image_path, caption, sd_prompt, sd_negative FROM gallery_image "
                        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, offset)
                    )
                rows = cursor.fetchall()
            conn.close()

            for row in rows:
                img_rel = row["image_path"]
                if img_rel.startswith("/static/"):
                    img_rel = img_rel[8:]
                elif img_rel.startswith("static/"):
                    img_rel = img_rel[7:]

                img_abs_path = os.path.join(sdcodex_root, "app", "static", img_rel)

                results.append({
                    "id": row["id"],
                    "file_name": row["file_name"],
                    "image_path": img_abs_path,
                    "caption": row["caption"] or "",
                    "sd_prompt": row["sd_prompt"] or "",
                    "sd_negative": row["sd_negative"] or ""
                })

        return web.json_response({
            "images": results,
            "total": total,
            "page": page,
            "totalPages": total_pages,
            "limit": limit
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@PromptServer.instance.routes.get("/sdcodex/galleries")
async def get_sdcodex_galleries(request):
    """List galleries (name + image count) for the browser node's dropdown."""
    params = request.query
    sdcodex_root = _resolve_root(params.get("sdcodex_root", ""))

    db_path = _find_db(sdcodex_root)
    if not os.path.exists(db_path):
        return web.json_response({"error": f"SDCodex database not found (tried {db_path})"}, status=404)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT g.id, g.name, COUNT(gi.id) AS image_count "
            "FROM gallery g LEFT JOIN gallery_image gi ON gi.gallery_id = g.id "
            "GROUP BY g.id ORDER BY g.created_at ASC, g.name ASC"
        )
        rows = cursor.fetchall()
        conn.close()
        return web.json_response({
            "galleries": [
                {"id": r["id"], "name": r["name"], "image_count": r["image_count"]}
                for r in rows
            ]
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
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    if ext not in image_extensions:
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
                "mode": (["Saved Gallery", "Folder Path"], {"default": "Saved Gallery"}),
                "gallery": ("STRING", {"default": "Default Gallery"}),
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

    def execute(self, mode, gallery="Default Gallery", folder_path="", sdcodex_root="", selected_image="", caption="", sd_prompt="", sd_negative=""):
        import torch
        import numpy as np
        from PIL import Image, ImageOps

        out_image = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
        out_sd_prompt = sd_prompt or ""
        out_sd_negative = sd_negative or ""

        img_path = None
        if selected_image:
            if os.path.isabs(selected_image) and os.path.exists(selected_image):
                img_path = selected_image
            elif mode == "Folder Path" and folder_path:
                img_path = os.path.join(folder_path, selected_image)

        if not img_path or not os.path.exists(img_path):
            root = _resolve_root(sdcodex_root)
            db_path = _find_db(root)

            if os.path.exists(db_path) and selected_image:
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    if gallery and gallery.strip():
                        cursor.execute(
                            "SELECT gi.image_path, gi.sd_prompt, gi.sd_negative "
                            "FROM gallery_image gi JOIN gallery g ON gi.gallery_id = g.id "
                            "WHERE gi.file_name = ? AND g.name = ? LIMIT 1",
                            (os.path.basename(selected_image), gallery.strip())
                        )
                    else:
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
            except Exception as e:
                print(f"Error loading image file {img_path}: {e}")

        return (selected_image or "", caption or "", out_sd_prompt, out_sd_negative, out_image)
