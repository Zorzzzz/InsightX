import json
from datetime import datetime

from flask_login import UserMixin

from database.db import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    full_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    summaries = db.relationship(
        "Summary",
        backref="user",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def get_id(self):
        return str(self.id)


class Summary(db.Model):
    __tablename__ = "summaries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    input_text = db.Column(db.Text, nullable=True)
    file_name = db.Column(db.String(200), nullable=True)
    summary = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default="other")
    stats_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_stats_dict(self) -> dict:
        try:
            return json.loads(self.stats_json or "{}")
        except Exception:
            return {}

    def set_stats_dict(self, stats: dict | None) -> None:
        self.stats_json = json.dumps(stats or {})
