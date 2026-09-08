import os
import sys
from flask import render_template
from sqlalchemy import text

plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from models import Gallery, GalleryImage
from api_gallery import gallery as gallery_bp

gallery_bp.template_folder = os.path.join(plugin_dir, "templates")

@gallery_bp.route("/captioning", endpoint="captioning")
def captioning():
    return render_template("captioning.html")

@gallery_bp.route("/gallery", endpoint="gallery")
def gallery_view():
    return render_template("gallery.html")

def init_plugin(app, db, plugin_info=None):
    """Initialize the ComfyUI Caption & Gallery plugin."""
    # Expose models on app.models for any callers
    try:
        import app.models as core_models
        core_models.Gallery = Gallery
        core_models.GalleryImage = GalleryImage
    except Exception:
        pass

    if "gallery" not in app.blueprints:
        app.register_blueprint(gallery_bp)

    # Initialize tables and migration checks
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                columns = [row[1] for row in conn.execute(text("PRAGMA table_info(gallery_image)")).fetchall()]
                if 'gallery_id' not in columns:
                    conn.execute(text("ALTER TABLE gallery_image ADD COLUMN gallery_id INTEGER REFERENCES gallery(id)"))
                    conn.commit()
        except Exception as e:
            app.logger.warning("Gallery migration check warning: %s", e)

        try:
            default_gal = Gallery.query.filter_by(name="Default Gallery").first()
            if not default_gal:
                default_gal = Gallery(name="Default Gallery")
                db.session.add(default_gal)
                db.session.commit()
        except Exception as e:
            app.logger.warning("Default gallery init warning: %s", e)

        # Install / sync ComfyUI custom nodes into custom-addons folder
        custom_nodes_dirs = [
            os.environ.get("COMFYUI_CUSTOM_NODES"),
            os.environ.get("COMFY_CUSTOM_NODES"),
            "/data/custom_nodes",
            "/data/comfyui_custom_nodes",
        ]
        for cdir in custom_nodes_dirs:
            if cdir and os.path.exists(cdir):
                src_nodes = os.path.join(plugin_dir, "comfyui-sdcodex")
                dest_nodes = os.path.join(cdir, "comfyui-sdcodex")
                if os.path.exists(src_nodes):
                    try:
                        import shutil
                        shutil.copytree(src_nodes, dest_nodes, dirs_exist_ok=True)
                        app.logger.info("Installed/updated ComfyUI custom nodes to %s", dest_nodes)
                    except Exception as err:
                        app.logger.warning("Could not copy ComfyUI nodes to %s: %s", dest_nodes, err)
                break
