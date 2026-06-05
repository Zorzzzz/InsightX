"""
InsightX Flask application entrypoint.

This file keeps the route layer and startup behavior while delegating
database, authentication, and summary logic to dedicated modules.
"""

import os
import secrets
import sys
import time
import traceback

# Force fully offline mode for transformers/HF before importing model code.
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from database.db import init_extensions, initialize_database
from database.summary_repository import delete_summary, get_user_summaries
from database.models import Summary, User
from database.user_repository import delete_user, update_full_name, update_user_fields, list_users, get_user_by_id
from services.auth_service import (
    AuthServiceError,
    authenticate_user,
    change_password_for_email,
    configure_login_manager,
    hash_password,
    register_user,
)
from services.summary_service import (
    SummaryServiceError,
    extract_document_summary,
    extract_text_from_upload,
    load_available_summarizer,
    summarize_input,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

init_extensions(app)
configure_login_manager()


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "")
    password = data.get("password", "")
    action = data.get("action", "login")

    try:
        if action == "register":
            user = register_user(
                email=email,
                password=password,
                username=data.get("username", "").strip(),
                full_name=data.get("full_name", "").strip(),
            )
            login_user(user)
            return jsonify({"message": "Account created!", "redirect": url_for("index")})

        user = authenticate_user(email=email, password=password)
        login_user(user)
        return jsonify({"message": "Welcome back!", "redirect": url_for("index")})
    except AuthServiceError as error:
        return jsonify({"error": error.message}), error.status_code


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_page"))


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user)


@app.route("/history")
@login_required
def history():
    records = get_user_summaries(current_user.id)
    return render_template("history.html", records=records)


@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    new_password = data.get("new_password", "")

    try:
        change_password_for_email(current_user.email, new_password, label="New password")
    except AuthServiceError as error:
        return jsonify({"error": error.message}), error.status_code

    return jsonify({"message": "Password updated successfully."})


@app.route("/api/summarize", methods=["POST"])
@login_required
def api_summarize():
    text = ""
    file_name = None
    is_data_report = False

    if request.is_json:
        body = request.get_json(silent=True) or {}
        text = body.get("text", "").strip()
        category_override = body.get("content_category")
    else:
        category_override = request.form.get("content_category")
        uploaded = request.files.get("file")
        if not uploaded or uploaded.filename == "":
            return jsonify({"error": "No file received."}), 400
        try:
            text, is_data_report = extract_text_from_upload(uploaded)
            file_name = uploaded.filename
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

    try:
        payload = summarize_input(
            text=text,
            user_id=current_user.id,
            file_name=file_name,
            category_override=category_override,
            is_data_report=is_data_report,
        )
        return jsonify(payload)
    except SummaryServiceError as error:
        return jsonify({"error": error.message}), error.status_code
    except Exception as error:
        traceback.print_exc()
        return jsonify({"error": str(error)}), 500


@app.route("/api/extract", methods=["POST"])
@login_required
def api_extract():
    uploaded = request.files.get("file")
    category_override = request.form.get("content_category", "other")

    if not uploaded or uploaded.filename == "":
        return jsonify({"error": "No file received."}), 400

    try:
        payload = extract_document_summary(
            uploaded_file=uploaded,
            user_id=current_user.id,
            category_override=category_override,
        )
        return jsonify(payload)
    except SummaryServiceError as error:
        return jsonify({"error": error.message}), error.status_code
    except Exception as error:
        traceback.print_exc()
        return jsonify({"error": str(error)}), 500


@app.route("/api/me", methods=["GET"])
@login_required
def api_me():
    return jsonify({
        "id": current_user.id,
        "full_name": current_user.full_name or "",
        "username": current_user.username,
        "email": current_user.email,
        "joined": current_user.created_at.strftime("%Y-%m-%d"),
    })


@app.route("/api/update-profile", methods=["POST"])
@login_required
def api_update_profile():
    data = request.get_json(silent=True) or {}
    new_name = data.get("name", "").strip()
    if not new_name:
        return jsonify({"error": "Name cannot be empty."}), 400

    if update_full_name(current_user.id, new_name) is None:
        return jsonify({"error": "User not found."}), 404

    return jsonify({"message": "Name updated successfully."})


@app.route("/api/delete-account", methods=["POST"])
@login_required
def api_delete_account():
    user_id = current_user.id
    logout_user()

    if not delete_user(user_id=user_id):
        return jsonify({"error": "User not found."}), 404

    return jsonify({"message": "Account deleted.", "redirect": "/login"})


@app.route("/register", methods=["GET"])
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("register.html")


@app.route("/api/history", methods=["GET"])
@login_required
def api_history():
    records = get_user_summaries(current_user.id, limit=50)
    return jsonify([
        {
            "id": record.id,
            "summary": record.summary,
            "category": record.category,
            "file_name": record.file_name,
            "input_text": record.input_text,
            "stats": record.get_stats_dict(),
            "created_at": record.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for record in records
    ])


@app.route("/api/history/<int:record_id>", methods=["DELETE"])
@login_required
def api_delete_history(record_id: int):
    if not delete_summary(record_id, user_id=current_user.id):
        return jsonify({"error": "Not found."}), 404
    return jsonify({"message": "Deleted."})


@app.route("/admin")
@login_required
def admin_panel():
    if not current_user.is_admin:
        return redirect(url_for("index"))
    users = list_users()
    return render_template("admin.html", users=users)


@app.route("/api/admin/users", methods=["GET"])
@login_required
def api_admin_list_users():
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    users = list_users()
    return jsonify([
        {
            "id": u.id,
            "full_name": u.full_name or "",
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "joined": u.created_at.strftime("%Y-%m-%d"),
        }
        for u in users
    ])


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@login_required
def api_admin_update_user(user_id: int):
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    fields = {}

    if "full_name" in data:
        fields["full_name"] = (data["full_name"] or "").strip() or None
    if "username" in data:
        username = (data["username"] or "").strip()
        if not username:
            return jsonify({"error": "Username cannot be empty."}), 400
        fields["username"] = username
    if "email" in data:
        from services.auth_service import normalize_email
        new_email = normalize_email(data["email"])
        if not new_email:
            return jsonify({"error": "Email cannot be empty."}), 400
        from database.user_repository import get_user_by_email
        existing = get_user_by_email(new_email)
        if existing and existing.id != user_id:
            return jsonify({"error": "Email already in use."}), 400
        fields["email"] = new_email
    if "password" in data:
        new_pw = data["password"]
        if len(new_pw or "") < 6:
            return jsonify({"error": "Password must be at least 6 characters."}), 400
        fields["password"] = hash_password(new_pw)

    if not fields:
        return jsonify({"error": "No fields to update."}), 400

    user = update_user_fields(user_id, fields)
    if user is None:
        return jsonify({"error": "User not found."}), 404

    return jsonify({"message": "User updated."})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@login_required
def api_admin_delete_user(user_id: int):
    if not current_user.is_admin:
        return jsonify({"error": "Forbidden"}), 403
    if user_id == current_user.id:
        return jsonify({"error": "Cannot delete your own admin account."}), 400
    if not delete_user(user_id=user_id):
        return jsonify({"error": "User not found."}), 404
    return jsonify({"message": "User deleted."})





with app.app_context():
    initialize_database()


@app.route("/api/admin/summaries", methods=["GET"])
@login_required
def api_admin_summaries():
    if not current_user.is_admin:
        return jsonify({"error":"Forbidden"}),403
    rows = Summary.query.order_by(Summary.created_at.desc()).limit(200).all()
    return jsonify([{
        "id": s.id,
        "user": User.query.get(s.user_id).email if User.query.get(s.user_id) else "Unknown",
        "file_name": s.file_name,
        "category": s.category,
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M")
    } for s in rows])


if __name__ == "__main__":
    import threading
    import webbrowser

    


with app.app_context():
    load_available_summarizer()

    def open_browser():
        time.sleep(2.0)
        webbrowser.open("http://127.0.0.1:5000")

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        from waitress import serve

        print("Starting with Waitress WSGI server (4 threads) ...")
        serve(app, host="0.0.0.0", port=5000, threads=4)
    except ImportError:
        print("Waitress not found, falling back to Flask dev server.")
        print("Install for better performance: pip install waitress")
        app.run(debug=False, port=5000)
