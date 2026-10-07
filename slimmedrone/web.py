"""De website (het grondstation-dashboard), gemaakt met Flask.

Pagina's:    /login, /, /gezichten
Livestream:  /video_feed  (MJPEG: een stroom JPEG-beelden, werkt in elke browser)
API:         /api/...     (JSON, gebruikt door de JavaScript in static/)

Alles behalve /login is alleen te zien als je bent ingelogd.
"""
import functools
import time
from datetime import timedelta

import cv2
import numpy as np
from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

from .auth import LoginLimiter
from .faces import clean_name
from .zoom import crop

API_HEADER = "X-SlimmeDrone"  # andere websites kunnen deze header niet meesturen (CSRF-bescherming)


def create_app(cfg, pipeline, faces, objects, alerts, assistant, users) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        SECRET_KEY=cfg.secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    limiter = LoginLimiter()

    # -- inloggen ------------------------------------------------------------

    def login_required(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if "user" not in session:
                if request.path.startswith("/api/"):
                    return jsonify(fout="Niet ingelogd"), 401
                return redirect(url_for("login", next=request.path))
            if request.path.startswith("/api/") and request.method != "GET" \
                    and request.headers.get(API_HEADER) != "1":
                return jsonify(fout="Ongeldig verzoek"), 403
            return view(*args, **kwargs)
        return wrapper

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        username = ""
        if request.method == "POST":
            ip = request.remote_addr or "?"
            username = request.form.get("gebruiker", "").strip().lower()[:64]
            if limiter.blocked(ip):
                error = "Te veel mislukte pogingen. Wacht 5 minuten."
            elif users.verify(username, request.form.get("wachtwoord", "")):
                limiter.succeeded(ip)
                session.clear()
                session.permanent = True
                session["user"] = username
                target = request.args.get("next", "/")
                return redirect(target if target.startswith("/") and not target.startswith("//") else "/")
            else:
                limiter.failed(ip)
                time.sleep(0.5)
                error = "Gebruikersnaam of wachtwoord klopt niet."
        return render_template("login.html", error=error, username=username)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # -- pagina's ------------------------------------------------------------

    @app.get("/")
    @login_required
    def dashboard():
        return render_template("dashboard.html", user=session["user"], page="live",
                               ai_mode=assistant.mode)

    @app.get("/gezichten")
    @login_required
    def faces_page():
        return render_template("gezichten.html", user=session["user"], page="gezichten")

    # -- livestream ----------------------------------------------------------

    @app.get("/video_feed")
    @login_required
    def video_feed():
        def frames():
            last_id, min_interval = -1, 1.0 / max(1, cfg.stream_fps)
            while True:
                started = time.time()
                frame_id, jpeg = pipeline.wait_jpeg(last_id, timeout=2.0)
                if jpeg is None or frame_id == last_id:
                    continue
                last_id = frame_id
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                rest = min_interval - (time.time() - started)
                if rest > 0:
                    time.sleep(rest)

        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/snapshot")
    @login_required
    def snapshot():
        jpeg = pipeline.latest_jpeg()
        if jpeg is None:
            abort(404)
        name = time.strftime("slimmedrone_%Y%m%d_%H%M%S.jpg")
        return Response(jpeg, mimetype="image/jpeg",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # -- status & bediening --------------------------------------------------

    @app.get("/api/status")
    @login_required
    def status():
        snap = pipeline.snapshot()
        after = request.args.get("na", 0, type=int)
        return jsonify(
            camera={"verbonden": pipeline.camera.connected, "fout": pipeline.camera.error,
                    "bron": pipeline.camera.source},
            fps=round(pipeline.fps, 1),
            objecten=[{"label": d.label, "naam": d.naam, "zekerheid": round(d.confidence, 2),
                       "kader": list(d.box)} for d in snap.detections],
            gezichten=[{"naam": f.name, "overeenkomst": round(f.similarity, 2)} for f in snap.faces],
            zoom=pipeline.zoom.status(),
            meldingen=alerts.since(after),
            ai=assistant.mode,
            objectherkenning=objects.description,
        )

    @app.post("/api/zoom")
    @login_required
    def zoom():
        data = request.get_json(silent=True) or {}
        action = data.get("actie")
        if action == "uit":
            pipeline.zoom.reset()
        elif action == "object":
            box = data.get("kader")
            if not (isinstance(box, list) and len(box) == 4):
                return jsonify(fout="Ongeldig kader"), 400
            pipeline.zoom.follow(str(data.get("label")), [float(v) for v in box],
                                 str(data.get("naam", data.get("label"))))
        elif action == "punt":
            try:
                x, y = float(data["x"]), float(data["y"])
            except (KeyError, TypeError, ValueError):
                return jsonify(fout="Ongeldig punt"), 400
            pipeline.zoom.zoom_at(min(1, max(0, x)), min(1, max(0, y)))
        else:
            return jsonify(fout="Onbekende actie"), 400
        return jsonify(ok=True)

    # -- chat met Jarvis -----------------------------------------------------

    @app.post("/api/chat")
    @login_required
    def chat():
        data = request.get_json(silent=True) or {}
        question = str(data.get("bericht", "")).strip()[:1000]
        if not question:
            return jsonify(fout="Lege vraag"), 400
        return jsonify(assistant.ask(session["user"], question))

    @app.post("/api/chat/reset")
    @login_required
    def chat_reset():
        assistant.reset(session["user"])
        return jsonify(ok=True)

    # -- gezichten beheren ---------------------------------------------------

    @app.get("/api/faces")
    @login_required
    def list_faces():
        return jsonify(personen=faces.people())

    @app.get("/api/faces/<name>/foto")
    @login_required
    def face_photo(name):
        try:
            path = faces.photo_path(name)
        except ValueError:
            abort(404)
        if path is None:
            abort(404)
        return send_file(path, mimetype="image/jpeg", max_age=0)

    @app.post("/api/faces")
    @login_required
    def add_faces():
        name = request.form.get("naam", "")
        try:
            name = clean_name(name)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        results = []
        for upload in request.files.getlist("fotos"):
            data = np.frombuffer(upload.read(), dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
            try:
                faces.enroll(name, image)
                results.append({"bestand": upload.filename, "ok": True})
            except ValueError as exc:
                results.append({"bestand": upload.filename, "ok": False, "fout": str(exc)})
        if not results:
            return jsonify(fout="Kies minstens één foto."), 400
        return jsonify(naam=name, resultaten=results)

    @app.post("/api/faces/capture")
    @login_required
    def capture_face():
        data = request.get_json(silent=True) or {}
        snap = pipeline.snapshot()
        if snap.frame is None:
            return jsonify(fout="Er is geen camerabeeld."), 400
        # Ingezoomd? Dan alleen het stuk gebruiken dat je ziet.
        frame = crop(snap.frame, snap.zoom_rect) if snap.zoom_rect else snap.frame
        try:
            name = clean_name(str(data.get("naam", "")))
            faces.enroll(name, frame, single=True)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        return jsonify(naam=name, ok=True)

    @app.delete("/api/faces/<name>")
    @login_required
    def delete_face(name):
        try:
            faces.remove(name)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        return jsonify(ok=True)

    return app
