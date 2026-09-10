from flask import Blueprint, jsonify, request, Response
import os
import subprocess
import shutil
import base64
import json
import mimetypes
from PIL import Image
from io import BytesIO
from openai import OpenAI
import piexif
import piexif.helper
try:
    from sd_prompt_reader.image_data_reader import ImageDataReader
except ImportError:
    ImageDataReader = None

def extract_comfy_workflow(image_path):
    """Robustly extract ComfyUI workflow/prompt from PNG and WebP/JPEG images."""
    def extract_json_if_valid(val):
        if not isinstance(val, str):
            val = str(val)
        val = val.strip()
        if val.startswith('{') or val.startswith('['):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    return val
            except:
                pass
        return None

    try:
        with Image.open(image_path) as img:
            img.load()
            # Check PNG text chunks
            if "workflow" in img.info:
                val = extract_json_if_valid(img.info["workflow"])
                if val: return val
            if "prompt" in img.info:
                val = extract_json_if_valid(img.info["prompt"])
                if val: return val
            
            # Check Exif UserComment for WEBP / JPEG
            if "exif" in img.info:
                try:
                    exif_dict = piexif.load(img.info["exif"])
                    if "Exif" in exif_dict and piexif.ExifIFD.UserComment in exif_dict["Exif"]:
                        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                        try:
                            comment_str = piexif.helper.UserComment.load(user_comment)
                        except Exception:
                            if isinstance(user_comment, bytes):
                                comment_str = user_comment.decode("utf-8", errors="ignore")
                                if comment_str.startswith("ASCII\0\0\0"):
                                    comment_str = comment_str[8:]
                                elif comment_str.startswith("UNICODE\0"):
                                    comment_str = comment_str[8:]
                            else:
                                comment_str = str(user_comment)
                        
                        val = extract_json_if_valid(comment_str)
                        if val: return val
                except Exception as e:
                    pass
    except Exception as e:
        print(f"Error extracting workflow from {image_path}: {e}")
    return None

gallery = Blueprint("gallery", __name__)


def _docker_default_gateway():
    """Best-effort host-bridge IP inside a container (fallback 172.17.0.1)."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    import socket
                    import struct
                    return socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))
    except Exception:
        pass
    return "172.17.0.1"


def reachable_lm_studio_url(url):
    """When the server runs inside Docker, the container cannot reach the host
    via ``localhost``/``127.0.0.1`` (that is the container itself). Rewrite such
    URLs to the Docker default-gateway IP so LM Studio on the host is reached.
    Outside Docker the URL is returned unchanged."""
    if not url:
        return url or "http://localhost:1234/v1"
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(url)
        if not parts.hostname or parts.hostname.lower() not in ("localhost", "127.0.0.1"):
            return url
        if not _in_docker():
            return url
        host = parts.hostname
        netloc = parts.netloc.replace(host, _docker_default_gateway(), 1)
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return url

@gallery.route("/api/browse", methods=["GET"])
def browse_folder():
    """API to browse folders using native filepickers.

    NOTE: this opens a dialog on the *server*. Inside Docker / headless
    servers there is no display (and no zenity/kdialog), so it fails — the
    frontend then falls back to the web-based /api/browse-folders browser,
    which works everywhere.
    """
    try:
        import platform
        import shutil

        if platform.system() == "Windows":
            cmd = ['powershell', '-command', 'Add-Type -AssemblyName System.Windows.Forms; $folderBrowser = New-Object System.Windows.Forms.FolderBrowserDialog; $null = $folderBrowser.ShowDialog(); $folderBrowser.SelectedPath']
        elif platform.system() == "Darwin":
            cmd = ['osascript', '-e', 'tell application "System Events" to return POSIX path of (choose folder)']
        else:
            if shutil.which("zenity"):
                cmd = ["zenity", "--file-selection", "--directory"]
            elif shutil.which("kdialog"):
                cmd = ["kdialog", "--getexistingdirectory"]
            else:
                return jsonify({
                    "error": "Native folder picker unavailable on the server "
                             "(expected inside Docker/headless). Use the folder "
                             "browser or type a container path like /data/downloads.",
                    "fallback": True,
                }), 500

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            folder_path = result.stdout.strip()
            if folder_path:
                return jsonify({"path": folder_path})

        return jsonify({"error": "No folder selected", "fallback": True}), 400
    except Exception as e:
        print(f"exec error: {e}")
        return jsonify({"error": f"Failed to open filepicker: {e}", "fallback": True}), 500


def _in_docker():
    return os.path.exists("/.dockerenv") or os.environ.get("REMBG_OUTPUT", "").startswith("/data")


@gallery.route("/api/browse-folders", methods=["GET"])
def browse_folders():
    """Web-based directory browser (works in Docker/headless).

    Query: ?path=/data/downloads → {cwd, parent, dirs:[{name,path}], isDocker}
    Paths are container/server-side — the same paths /api/images expects.
    """
    req_path = request.args.get("path") or "/data"
    req_path = os.path.abspath(os.path.expanduser(req_path.strip('"\'')))
    if not os.path.exists(req_path):
        return jsonify({"error": f"Folder does not exist: {req_path}"}), 400
    if not os.path.isdir(req_path):
        req_path = os.path.dirname(req_path)
    try:
        entries = sorted(os.listdir(req_path))
    except PermissionError:
        return jsonify({"error": f"Permission denied: {req_path}"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    dirs = []
    for name in entries:
        if name.startswith(".") and name not in (".", ".."):
            continue
        full = os.path.join(req_path, name)
        try:
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full})
        except Exception:
            continue
        if len(dirs) >= 500:
            break
    parent = os.path.dirname(req_path.rstrip(os.sep)) or os.sep
    return jsonify({
        "cwd": req_path,
        "parent": parent,
        "dirs": dirs,
        "isDocker": _in_docker(),
    })


@gallery.route("/api/browse-roots", methods=["GET"])
def browse_roots():
    """Quick-pick roots for the folder browser (container-side paths)."""
    candidates = [
        "/data", "/data/downloads", "/data/tasks", "/data/config",
        os.path.expanduser("~"), "/app", "/",
    ]
    seen = []
    for p in candidates:
        try:
            ap = os.path.abspath(p)
            if ap not in seen and os.path.isdir(ap):
                seen.append(ap)
        except Exception:
            continue
    return jsonify({"roots": seen, "isDocker": _in_docker()})


@gallery.route("/api/check-connection", methods=["POST"])
def check_connection():
    """API to check connection to LLM Studio"""
    data = request.json or {}
    lm_studio_url = reachable_lm_studio_url(data.get("lmStudioUrl", "http://localhost:1234/v1"))
    print(f"Checking connection to: {lm_studio_url}")

    try:
        # Attempt to list models via openai library
        client = OpenAI(api_key="not-needed", base_url=lm_studio_url, timeout=5.0)
        models = client.models.list()
        model_list = [{"id": m.id} for m in models.data]
        
        print(f"Connection successful via models.list(). Models found: {len(model_list)}")
        return jsonify({
            "connected": True, 
            "models": model_list
        })
    except Exception as e:
        import requests
        print(f"openai.models.list() failed for {lm_studio_url}: {e}. Trying direct probe...")
        try:
            # Fallback direct probe
            response = requests.get(lm_studio_url, timeout=5.0)
            print(f"Direct probe to {lm_studio_url} returned status: {response.status_code}")
            return jsonify({
                "connected": True, 
                "warning": "Server responded but models.list() failed"
            })
        except requests.exceptions.RequestException as fetch_err:
            print(f"All connection attempts failed for {lm_studio_url}: {fetch_err}")
            return jsonify({
                "connected": False, 
                "error": str(fetch_err)
            })

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

@gallery.route("/api/images", methods=["GET"])
def get_images():
    """Optimized API to get images and existing captions"""
    folder_path = request.args.get("folderPath")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))

    if not folder_path:
        return jsonify({"error": "Folder path is required"}), 400
        
    folder_path = folder_path.strip('"\'')
    page = max(1, page)
    limit = max(1, limit)

    if not os.path.exists(folder_path):
        return jsonify({"error": f"Folder does not exist: {folder_path}"}), 400
        
    if not os.path.isdir(folder_path):
        return jsonify({"error": "Path is not a directory"}), 400

    try:
        all_files = os.listdir(folder_path)
        
        # Filter image files
        image_files = []
        for file in all_files:
            ext = os.path.splitext(file)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file)
                
        # Sort files to ensure stable pagination
        image_files.sort()

        total = len(image_files)
        # Compute proper ceiling
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        start_index = (page - 1) * limit
        page_files = image_files[start_index : start_index + limit]

        results = []
        for file in page_files:
            image_path = os.path.join(folder_path, file)
            txt_path = os.path.join(folder_path, os.path.splitext(file)[0] + '.txt')
            
            caption = ""
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, 'r', encoding='utf-8') as f:
                        caption = f.read()
                except Exception as e:
                    print(f"Could not read caption for {file}: {e}")
            
            sd_prompt = ""
            sd_negative = ""
            sd_setting = ""
            if ImageDataReader:
                try:
                    # Initialize ImageDataReader with the image path
                    reader = ImageDataReader(image_path)
                    # Use the raw string representing positive prompt if exists
                    if reader.positive:
                        sd_prompt = str(reader.positive).strip()
                    if reader.negative:
                        sd_negative = str(reader.negative).strip()
                    if reader.setting:
                        sd_setting = str(reader.setting).strip()
                except Exception as e:
                    print(f"Failed to read SD info for {file}: {e}")
            
            base64_img = ""
            has_workflow = extract_comfy_workflow(image_path) is not None
            try:
                with Image.open(image_path) as img:
                    img.load()
                
                with open(image_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    m, _ = mimetypes.guess_type(image_path)
                    m = m or 'image/jpeg'
                    base64_img = f"data:{m};base64,{encoded_string}"
            except Exception as e:
                print(f"Could not read image {file}: {e}")

            results.append({
                "file": file,
                "caption": caption,
                "sdPrompt": sd_prompt,
                "sdNegative": sd_negative,
                "sdSetting": sd_setting,
                "image": base64_img,
                "hasCaption": len(caption) > 0,
                "hasWorkflow": has_workflow
            })

        return jsonify({
            "images": results,
            "total": total,
            "page": page,
            "totalPages": total_pages,
            "limit": limit
        })
    except Exception as e:
        print(f"Error in /api/images: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/workflow", methods=["GET"])
def download_workflow():
    """API to download ComfyUI workflow JSON from image metadata"""
    folder_path = request.args.get("folderPath")
    file_name = request.args.get("file")

    if not folder_path or not file_name:
        return jsonify({"error": "Folder path and file name are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    image_path = os.path.join(folder_path, file_name)

    if not is_safe_path(folder_path, image_path):
        return jsonify({"error": "Invalid file name"}), 400

    if not os.path.exists(image_path):
        return jsonify({"error": "Image file does not exist"}), 400

    try:
        workflow_data = extract_comfy_workflow(image_path)
            
        if not workflow_data:
            return jsonify({"error": "No ComfyUI workflow found in image metadata"}), 404

        # Ensure workflow_data is valid JSON format for the file instead of a plain string
        try:
            parsed_json = json.loads(workflow_data)
            formatted_data = json.dumps(parsed_json, indent=2)
        except json.JSONDecodeError:
            # Fallback to raw string if it's not valid JSON
            formatted_data = workflow_data

        return Response(
            formatted_data,
            mimetype="application/json",
            headers={
                "Content-disposition": f"attachment; filename={os.path.splitext(file_name)[0]}_workflow.json"
            }
        )
    except Exception as e:
        print(f"Error in /api/workflow: {e}")
        return jsonify({"error": str(e)}), 500

def is_safe_path(base, target):
    """Basic path traversal protection"""
    resolved_base = os.path.abspath(base)
    resolved_target = os.path.abspath(target)
    return resolved_target.startswith(resolved_base)

def process_image_for_llm(image_path):
    """Converts image to JPEG and resizes to 1024 max dimension for compatibility"""
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (e.g., for PNG with alpha)
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                bg.paste(img, mask=img.split()[3])
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')
                
            # Resize logic (fit inside 1024x1024 without enlargement)
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            
            # Save to BytesIO
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{img_str}"
    except Exception as e:
        print(f"Error processing image for LLM: {e}")
        raise e

import re

@gallery.route("/api/caption-single", methods=["POST"])
def caption_single():
    """API to caption a single image"""
    data = request.json or {}
    folder_path = data.get("folderPath")
    file_name = data.get("fileName")
    prompt = data.get("prompt", "Caption this image")
    lm_studio_url = reachable_lm_studio_url(data.get("lmStudioUrl", "http://localhost:1234/v1"))
    model = data.get("model", "model-identifier")
    trigger_tag = data.get("triggerTag", "")

    if not folder_path or not file_name:
        return jsonify({"error": "Folder path and file name are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    image_path = os.path.join(folder_path, file_name)
    text_file_path = os.path.join(folder_path, os.path.splitext(file_name)[0] + '.txt')

    print(f"[caption-single] folderPath={folder_path!r} fileName={file_name!r} "
          f"lmStudioUrl={lm_studio_url!r} exists={os.path.exists(image_path) and os.path.isfile(image_path)}")

    if not is_safe_path(folder_path, image_path):
        return jsonify({"error": "Invalid file name"}), 400

    if not os.path.exists(image_path):
        return jsonify({"error": "Image file does not exist"}), 400

    client = OpenAI(api_key="not-needed", base_url=lm_studio_url)

    try:
        # Read full image for display
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            m, _ = mimetypes.guess_type(image_path)
            m = m or 'image/jpeg'
            display_image_data = f"data:{m};base64,{encoded_string}"

        # Get resized JPEG for LLM
        llm_image_data = process_image_for_llm(image_path)

        # Call OpenAI
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": llm_image_data},
                        },
                    ],
                }
            ],
        )

        full_content = response.choices[0].message.content
        
        # Remove think tags if generated
        caption = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()
        
        if trigger_tag and trigger_tag.strip():
            caption = f"{trigger_tag.strip()}, {caption}"
            
        # Extract and append SD Prompt if available
        sd_prompt = ""
        sd_negative = ""
        sd_setting = ""
        if ImageDataReader:
            try:
                reader = ImageDataReader(image_path)
                if reader.positive:
                    sd_prompt = str(reader.positive).strip()
                if reader.negative:
                    sd_negative = str(reader.negative).strip()
                if reader.setting:
                    sd_setting = str(reader.setting).strip()
                    
                # Append them behind the caption
                appended_text = ""
                if sd_prompt or sd_negative or sd_setting:
                    appended_text += "\n\n"
                    if sd_prompt:
                        appended_text += f"{sd_prompt}\n"
                    if sd_negative:
                        appended_text += f"Negative prompt: {sd_negative}\n"
                    if sd_setting:
                        appended_text += f"{sd_setting}"
                        
                if appended_text:
                    caption = f"{caption}{appended_text}"
            except Exception as e:
                print(f"Failed to read SD info for {file_name}: {e}")

        # Save to disk
        with open(text_file_path, "w", encoding="utf-8") as f:
            f.write(caption)

        return jsonify({
            "status": "success",
            "file": file_name,
            "caption": caption,
            "sdPrompt": sd_prompt,
            "sdNegative": sd_negative,
            "sdSetting": sd_setting,
            "image": display_image_data,
            "hasWorkflow": extract_comfy_workflow(image_path) is not None
        })
    except Exception as e:
        print(f"Error in /api/caption-single: {e}")
        return jsonify({"error": str(e)}), 500


@gallery.route("/api/caption", methods=["POST"])
def caption_batch():
    """API to process captions using Server-Sent Events"""
    data = request.json or {}
    folder_path = data.get("folderPath")
    prompt = data.get("prompt", "Caption this image")
    lm_studio_url = reachable_lm_studio_url(data.get("lmStudioUrl", "http://localhost:1234/v1"))
    model = data.get("model", "model-identifier")
    mode = data.get("mode")
    trigger_tag = data.get("triggerTag", "")

    if not folder_path:
        return jsonify({"error": "Folder path is required"}), 400
        
    folder_path = folder_path.strip('"\'')

    if not os.path.exists(folder_path):
        return jsonify({"error": "Invalid folder path"}), 400

    client = OpenAI(api_key="not-needed", base_url=lm_studio_url)

    # Pre-check connection
    try:
        client.models.list(timeout=5.0)
    except Exception as err:
        return jsonify({"error": f"LLM instance not connected: {err}"}), 500

    try:
        all_files = os.listdir(folder_path)
        image_files = []
        for file in all_files:
            ext = os.path.splitext(file)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file)

        if not image_files:
            return jsonify({"error": "No images found"}), 400

        total = len(image_files)

        def generate():
            current = 0
            
            for image_file in image_files:
                current += 1
                image_path = os.path.join(folder_path, image_file)
                text_file_path = os.path.join(folder_path, os.path.splitext(image_file)[0] + '.txt')
                
                exists = os.path.exists(text_file_path)

                if mode == 'missing' and exists:
                    try:
                        with open(text_file_path, 'r', encoding='utf-8') as f:
                            existing_caption = f.read()
                        
                        m, _ = mimetypes.guess_type(image_path)
                        m = m or 'image/jpeg'
                        
                        with open(image_path, "rb") as bf:
                            encoded_string = base64.b64encode(bf.read()).decode('utf-8')
                        
                        sd_prompt_existing = ""
                        sd_negative_existing = ""
                        sd_setting_existing = ""
                        if ImageDataReader:
                            try:
                                reader = ImageDataReader(image_path)
                                if reader.positive:
                                    sd_prompt_existing = str(reader.positive).strip()
                                if reader.negative:
                                    sd_negative_existing = str(reader.negative).strip()
                                if reader.setting:
                                    sd_setting_existing = str(reader.setting).strip()
                            except Exception:
                                pass
                                
                        yield f"data: {json.dumps({ 'status': 'success', 'current': current, 'total': total, 'file': image_file, 'caption': existing_caption, 'sdPrompt': sd_prompt_existing, 'sdNegative': sd_negative_existing, 'sdSetting': sd_setting_existing, 'image': f'data:{m};base64,{encoded_string}', 'skipped': True, 'hasWorkflow': extract_comfy_workflow(image_path) is not None })}\n\n"
                    except Exception as e:
                        print(f"Error reading existing file {image_file}: {e}")
                    continue

                try:
                    with open(image_path, "rb") as image_f:
                        encoded_string = base64.b64encode(image_f.read()).decode('utf-8')
                        m, _ = mimetypes.guess_type(image_path)
                        m = m or 'image/jpeg'
                        display_image_data = f"data:{m};base64,{encoded_string}"

                    llm_image_data = process_image_for_llm(image_path)

                    yield f"data: {json.dumps({ 'status': 'started', 'current': current, 'total': total, 'file': image_file, 'image': display_image_data })}\n\n"

                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": llm_image_data},
                                    },
                                ],
                            }
                        ],
                    )

                    full_content = response.choices[0].message.content
                    caption = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()
                    
                    if trigger_tag and trigger_tag.strip():
                        caption = f"{trigger_tag.strip()}, {caption}"
                        
                    # Extract and append SD Prompt if available
                    sd_prompt = ""
                    sd_negative = ""
                    sd_setting = ""
                    if ImageDataReader:
                        try:
                            reader = ImageDataReader(image_path)
                            if reader.positive:
                                sd_prompt = str(reader.positive).strip()
                            if reader.negative:
                                sd_negative = str(reader.negative).strip()
                            if reader.setting:
                                sd_setting = str(reader.setting).strip()
                                
                            # Append them behind the caption
                            appended_text = ""
                            if sd_prompt or sd_negative or sd_setting:
                                appended_text += "\n\n"
                                if sd_prompt:
                                    appended_text += f"{sd_prompt}\n"
                                if sd_negative:
                                    appended_text += f"Negative prompt: {sd_negative}\n"
                                if sd_setting:
                                    appended_text += f"{sd_setting}"
                                    
                            if appended_text:
                                caption = f"{caption}{appended_text}"
                        except Exception as e:
                            print(f"Failed to read SD info for {image_file}: {e}")

                    with open(text_file_path, "w", encoding="utf-8") as f:
                        f.write(caption)

                    yield f"data: {json.dumps({ 'status': 'success', 'current': current, 'total': total, 'file': image_file, 'caption': caption, 'sdPrompt': sd_prompt, 'sdNegative': sd_negative, 'sdSetting': sd_setting, 'image': display_image_data, 'hasWorkflow': extract_comfy_workflow(image_path) is not None })}\n\n"

                except Exception as err:
                    yield f"data: {json.dumps({ 'status': 'error', 'file': image_file, 'error': str(err) })}\n\n"

            yield f"data: {json.dumps({ 'status': 'complete', 'total': total })}\n\n"

        return Response(generate(), mimetype='text/event-stream')

    except Exception as err:
        print(f"Error in /api/caption: {err}")
        return jsonify({"error": str(err)}), 500

