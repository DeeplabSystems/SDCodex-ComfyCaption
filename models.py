from datetime import datetime
from app import db

class Gallery(db.Model):
    __tablename__ = 'gallery'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    images = db.relationship('GalleryImage', backref='gallery', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Gallery {self.name}>'

class GalleryImage(db.Model):
    __tablename__ = 'gallery_image'
    id = db.Column(db.Integer, primary_key=True)
    gallery_id = db.Column(db.Integer, db.ForeignKey('gallery.id'), nullable=True)
    file_name = db.Column(db.String(256), nullable=False)
    image_path = db.Column(db.String(512), nullable=False)
    caption = db.Column(db.Text)
    sd_prompt = db.Column(db.Text)
    sd_negative = db.Column(db.Text)
    sd_setting = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<GalleryImage {self.file_name}>'
