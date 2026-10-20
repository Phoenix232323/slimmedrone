"""De website (het grondstation-dashboard), gemaakt met Flask.

Pagina's:    /login, /, /gezichten, /instellingen, /media, /statistieken
Livestream:  /video_feed  (MJPEG: een stroom JPEG-beelden, werkt in elke browser)
API:         /api/...     (JSON, gebruikt door de JavaScript in static/)
Chat:        /api/chat/stream  (het antwoord van J.A.R.V.I.S. komt woord voor woord
             binnen als "server-sent events": regels "event: ..." en "data: {...}")
Media:       /api/opname, /api/fotos, /api/media/...  (opnemen, foto's, galerij)
Overig:      /api/statistiek, /api/meldingen.csv, /api/gebruikers, /api/wachtwoord

Alles behalve /login is alleen te zien als je bent ingelogd. Wordt een gebruiker
verwijderd of verandert zijn wachtwoord, dan is hij overal meteen uitgelogd.
"""
import csv
import functools
import io
import json
import logging
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import urlparse

import cv2
import numpy as np
from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   send_file, session, stream_with_context, url_for)

from . import system
from .auth import LoginLimiter, check_password
from .faces import clean_name
from .assistant import pipeline_stats, safe_source
from .media import Media, MediaBusy, save_photo
from .recorder import RecorderError
from .settings import Settings
from .zoom import crop

log = logging.getLogger(__name__)
API_HEADER = "X-SlimmeDrone"  # andere websites kunnen deze header niet meesturen (CSRF-bescherming)
STREAM_CHECK = 5.0    # seconden: zo vaak kijkt een lopende videostroom of je nog mag kijken
STAMP_GRACE = 60.0    # seconden: zo lang telt na een eigen wachtwoordwijziging ook het oude stempel


def create_app(cfg, pipeline, faces, objects, alerts, assistant, users,
               settings=None, activity=None, recorder=None, media=None, stats=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.update(
        SECRET_KEY=cfg.secret_key(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    limiter = LoginLimiter()
    # Wie zijn eigen wachtwoord wijzigt, krijgt een nieuw stempel in zijn cookie. Verzoeken die
    # op dat moment al onderweg waren (bijv. de status van elke seconde) hebben nog het oude
    # cookie en zouden hem anders uitloggen. Daarom telt voor precies die sessie (sid) het oude
    # stempel nog even mee: {sid: (gebruiker, oud stempel, nieuw stempel, tot wanneer)}.
    stamp_grace = {}
    if settings is None:
        settings = Settings(cfg.data_dir / "instellingen.json", cfg)
    if media is None:
        media = Media(cfg.data_dir, recorder)
    # Weergave-opties (kaders, HUD) voor als de pipeline ze zelf (nog) niet kent.
    view_options = {"kaders": True, "hud": True}

    # -- inloggen ------------------------------------------------------------

    def login_required(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if "user" not in session or not session_valid():
                session.clear()
                if request.path.startswith("/api/"):
                    return jsonify(fout="Niet ingelogd"), 401
                return redirect(url_for("login", next=request.path))
            if request.path.startswith("/api/") and request.method != "GET" \
                    and request.headers.get(API_HEADER) != "1":
                return jsonify(fout="Ongeldig verzoek"), 403
            return view(*args, **kwargs)
        return wrapper

    def session_valid() -> bool:
        """Bestaat de gebruiker nog, en is zijn wachtwoord niet veranderd sinds het inloggen?"""
        if "stempel" not in session:  # ingelogd met een oudere versie: stempel nu zetten
            stamp = users.stamp(session["user"])
            if stamp is None:
                return False  # gebruiker is verwijderd
            session["stempel"] = stamp
        current = current_stamp(session["user"], session.get("sid"), session["stempel"])
        if current is None:
            return False
        if current != session["stempel"]:
            session["stempel"] = current  # net zelf het wachtwoord gewijzigd: nieuw stempel
        return True

    def current_stamp(user, sid, stamp):
        """Het geldige stempel voor deze sessie, of None als hij niet meer mag (verwijderd of
        ander wachtwoord). Na een eigen wachtwoordwijziging telt het oude stempel nog even."""
        now_stamp = users.stamp(user)
        if now_stamp is None:
            return None  # gebruiker is verwijderd
        if stamp == now_stamp:
            return now_stamp
        grace = stamp_grace.get(sid) if sid else None
        if grace and grace[:3] == (user, stamp, now_stamp) and time.time() < grace[3]:
            return now_stamp
        return None

    def still_allowed():
        """Voor lange stromen (video): een functie die zegt of deze sessie nog mag kijken.

        Wordt een gebruiker verwijderd of zijn wachtwoord gewijzigd, dan stopt de stroom
        binnen STREAM_CHECK seconden (anders bleef het camerabeeld gewoon doorlopen)."""
        user, sid, stamp = session["user"], session.get("sid"), session.get("stempel")
        checked = time.monotonic()

        def check() -> bool:
            nonlocal checked, stamp
            if time.monotonic() - checked < STREAM_CHECK:
                return True
            checked = time.monotonic()
            stamp = current_stamp(user, sid, stamp)
            return stamp is not None
        return check

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        username = ""
        if request.method == "POST":
            ip = request.remote_addr or "?"
            username = request.form.get("gebruiker", "").strip().lower()[:64]
            if not limiter.attempt(ip):  # telt de poging meteen mee, ook bij veel tegelijk
                error = "Te veel mislukte pogingen. Wacht 5 minuten."
            elif users.verify(username, request.form.get("wachtwoord", "")):
                limiter.succeeded(ip)
                session.clear()
                session.permanent = True
                session["user"] = username
                session["stempel"] = users.stamp(username)
                session["sid"] = secrets.token_hex(8)
                return redirect(_safe_next(request.args.get("next", "/")))
            else:
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

    @app.get("/instellingen")
    @login_required
    def settings_page():
        return render_template("instellingen.html", user=session["user"], page="instellingen",
                               ai_mode=assistant.mode)

    @app.get("/media")
    @login_required
    def media_page():
        return render_template("media.html", user=session["user"], page="media")

    @app.get("/statistieken")
    @login_required
    def statistics_page():
        return render_template("statistieken.html", user=session["user"], page="statistieken")

    # -- livestream ----------------------------------------------------------

    @app.get("/video_feed")
    @login_required
    def video_feed():
        allowed = still_allowed()

        def frames():
            last_id, min_interval = -1, 1.0 / max(1, cfg.stream_fps)
            while allowed():
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
                    "bron": safe_source(pipeline.camera.source)},  # zonder token of wachtwoord
            fps=round(pipeline.fps, 1),
            objecten=[{"label": d.label, "naam": d.naam, "zekerheid": round(d.confidence, 2),
                       "kader": list(d.box)} for d in snap.detections],
            gezichten=[{"naam": f.name, "overeenkomst": round(f.similarity, 2)} for f in snap.faces],
            zoom=pipeline.zoom.status(),
            meldingen=alerts.since(after),
            ai=assistant.mode,
            objectherkenning=objects.description,
            prestaties=pipeline_stats(pipeline),
            systeem=system.status(),
            weergave=current_view(),
            wachters=activity.watches() if activity else [],
            ai_info=assistant.info(),
            opname=recording_status(),
        )

    def current_view() -> dict:
        options = getattr(pipeline, "options", None)
        if callable(options):
            options = options()
        return dict(options) if isinstance(options, dict) else dict(view_options)

    @app.post("/api/weergave")
    @login_required
    def set_view():
        data = _json_body()
        wanted = {key: data.get(key) for key in ("kaders", "hud")}
        for value in wanted.values():
            if value is not None and not isinstance(value, bool):
                return jsonify(fout="Gebruik true of false."), 400
        set_options = getattr(pipeline, "set_options", None)
        if callable(set_options):
            return jsonify(set_options(**wanted))
        view_options.update({k: v for k, v in wanted.items() if v is not None})
        return jsonify(view_options)

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
        """Vraag aan J.A.R.V.I.S.; wacht op het hele antwoord (de oude manier)."""
        question = str(_json_body().get("bericht", "")).strip()[:1000]
        if not question:
            return jsonify(fout="Lege vraag"), 400
        result = assistant.ask(session["user"], question)
        if result is None:
            return jsonify(fout=BUSY), 409
        if "fout" in result:
            return jsonify(result), 500
        return jsonify(result)

    @app.post("/api/chat/stream")
    @login_required
    def chat_stream():
        """Vraag aan J.A.R.V.I.S.; het antwoord komt woord voor woord binnen (server-sent events)."""
        question = str(_json_body().get("bericht", "")).strip()[:1000]
        if not question:
            return jsonify(fout="Lege vraag"), 400
        events = assistant.start(session["user"], question)
        if events is None:
            return jsonify(fout=BUSY), 409

        def generate():
            for name, data in events:
                if name == "ping":
                    yield ": nog bezig\n\n"  # commentaarregel: houdt de verbinding open
                else:
                    yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        # Haakt de browser af, dan stopt alleen deze stroom; J.A.R.V.I.S. maakt het
        # antwoord in zijn eigen thread af (het komt dan in de geschiedenis).
        return Response(stream_with_context(generate()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/chat/geschiedenis")
    @login_required
    def chat_history():
        return jsonify(assistant.history(session["user"]))

    @app.post("/api/chat/reset")
    @login_required
    def chat_reset():
        assistant.reset(session["user"])
        return jsonify(ok=True)

    # -- instellingen (Claude-sleutel, effort, internet zoeken) ----------------

    def settings_json():
        ai = settings.public()  # zonder de sleutel zelf
        ai["modus"] = assistant.mode
        ai["melding"] = assistant.problem
        return jsonify(ai=ai)

    @app.get("/api/instellingen")
    @login_required
    def get_settings():
        return settings_json()

    @app.post("/api/instellingen")
    @login_required
    def save_settings():
        try:
            ai = settings.update(_json_body())
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        except OSError:
            return jsonify(fout="De instellingen konden niet worden opgeslagen."), 500
        assistant.configure(api_key=ai["api_key"], model=ai["model"], effort=ai["effort"], web=ai["web"])
        return settings_json()

    # -- activiteit en wachters ------------------------------------------------

    @app.get("/api/activiteit")
    @login_required
    def get_activity():
        after = request.args.get("na", 0, type=int)
        return jsonify(gebeurtenissen=activity.since(after, limit=50) if activity else [])

    @app.get("/api/wachters")
    @login_required
    def get_watches():
        return jsonify(wachters=activity.watches() if activity else [])

    @app.delete("/api/wachters/<int:watch_id>")
    @login_required
    def delete_watch(watch_id):
        if activity is None or activity.cancel_watch(watch_id) is None:
            return jsonify(fout="Deze wachter bestaat niet (meer)."), 404
        return jsonify(ok=True)

    # -- opnemen en foto's ---------------------------------------------------

    def recording_status() -> dict:
        if recorder is None:
            return {"actief": False, "sinds": None, "bestand": None, "duur": 0.0,
                    "max_minuten": getattr(cfg, "opname_max_minuten", 10)}
        return recorder.status()

    @app.post("/api/opname")
    @login_required
    def recording():
        action = _json_body().get("actie")
        if action not in ("start", "stop"):
            return jsonify(fout='Gebruik "actie": "start" of "stop".'), 400
        if recorder is None:
            return jsonify(fout="Opnemen is niet beschikbaar."), 409
        if action == "stop":
            return jsonify(recorder.stop())  # wacht tot het bestand af is
        if recorder.active:
            return jsonify(recorder.status())  # loopt al: niets aan de hand
        try:
            return jsonify(recorder.start(user=session["user"]))
        except RecorderError as exc:
            return jsonify(fout=str(exc)), 409

    @app.post("/api/fotos")
    @login_required
    def take_photo():
        """Foto op de server: met kaders (wat je op de website ziet) of het schone camerabeeld."""
        with_boxes = _json_body().get("kaders", True)
        if not isinstance(with_boxes, bool):
            return jsonify(fout="Gebruik true of false voor kaders."), 400
        if pipeline.snapshot().frame is None:  # dan is het laatste beeld "Geen camerabeeld"
            return jsonify(fout="Er is geen camerabeeld om een foto van te maken."), 409
        try:
            if with_boxes:
                jpeg = pipeline.latest_jpeg()
                if jpeg is None:
                    return jsonify(fout="Er is geen camerabeeld om een foto van te maken."), 409
                path = save_photo(media.photos_dir, jpeg=jpeg)
            else:
                path = save_photo(media.photos_dir, image=clean_frame())
        except OSError as exc:
            log.error("Foto opslaan mislukte: %s", exc)
            return jsonify(fout="De foto kon niet worden opgeslagen."), 500
        return jsonify(naam=path.name, url=f"/api/media/fotos/{path.name}",
                       mini=f"/api/media/fotos/{path.name}/mini")

    def clean_frame():
        """Het schone camerabeeld op volle grootte (zonder kaders en zonder zoom)."""
        camera = getattr(pipeline, "camera", None)
        read = getattr(camera, "read", None)
        if camera is not None and camera.connected and callable(read):
            frame = read()[1]
            if frame is not None:
                return frame
        return pipeline.snapshot().frame

    # -- media (galerij) -------------------------------------------------------

    @app.get("/api/media")
    @login_required
    def media_list():
        return jsonify(media.listing())

    @app.get("/api/media/<soort>/<naam>")
    @login_required
    def media_file(soort, naam):
        path = media.path(soort, naam)  # alleen eigen namen, zonder mappen of ".."
        if path is None:
            return jsonify(fout="Dit bestand bestaat niet."), 404
        if soort == "opnames" and media.busy(naam):
            return jsonify(fout="Deze opname loopt nog. Stop eerst de opname."), 409
        return send_file(path, mimetype="image/jpeg" if soort == "fotos" else "video/x-msvideo",
                         as_attachment=request.args.get("download") == "1", download_name=naam, max_age=0)

    @app.get("/api/media/<soort>/<naam>/mini")
    @login_required
    def media_mini(soort, naam):
        path = media.path(soort, naam)
        if path is None:
            return jsonify(fout="Dit bestand bestaat niet."), 404
        try:
            jpeg = media.mini(soort, path)
        except MediaBusy as exc:
            return jsonify(fout=str(exc)), 409
        except (OSError, ValueError, struct.error):
            return jsonify(fout="Van dit bestand kan geen miniatuur worden gemaakt."), 404
        # Een naam hoort altijd bij hetzelfde bestand: de browser mag de miniatuur bewaren.
        return Response(jpeg, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/media/opnames/<naam>/afspelen")
    @login_required
    def media_play(naam):
        """Speel een opname af als MJPEG-stroom (net als /video_feed), op de echte snelheid."""
        path = media.path("opnames", naam)
        if path is None:
            return jsonify(fout="Dit bestand bestaat niet."), 404
        try:
            _, frames = media.play(path)
        except MediaBusy as exc:
            return jsonify(fout=str(exc)), 409
        except (OSError, ValueError, struct.error):
            return jsonify(fout="Deze opname kan niet worden afgespeeld."), 404

        allowed = still_allowed()

        def stream():
            for jpeg in frames:
                if not allowed():
                    break
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")

        return Response(stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.delete("/api/media/<soort>/<naam>")
    @login_required
    def media_delete(soort, naam):
        path = media.path(soort, naam)
        if path is None:
            return jsonify(fout="Dit bestand bestaat niet."), 404
        try:
            media.delete(soort, path)
        except MediaBusy as exc:
            return jsonify(fout=str(exc)), 409
        except OSError as exc:
            log.warning("Verwijderen van %s mislukte: %s", path, exc)
            return jsonify(fout="Het bestand kon niet worden verwijderd (wordt het nog afgespeeld?)."), 409
        return jsonify(ok=True)

    # -- statistiek en meldingen ---------------------------------------------------

    @app.get("/api/statistiek")
    @login_required
    def statistics():
        periode = request.args.get("periode", "uur")
        if periode not in ("uur", "dag"):
            return jsonify(fout="Kies periode=uur of periode=dag."), 400
        if stats is None:
            # 409 en niet 404: de website zegt bij 404 dat de software te oud is.
            return jsonify(fout="Statistiek staat uit (STATISTIEK=uit in .env)", uit=True), 409
        return jsonify(stats.report(periode))

    @app.get("/api/meldingen.csv")
    @login_required
    def alerts_csv():
        """Alle meldingen als csv-bestand, met een kopregel erboven.

        Zo dat het in een Nederlandse Excel meteen goed opent: puntkomma's tussen de kolommen
        (zoals Excel dat hier verwacht), aanhalingstekens om een veld met ; of " erin, en een
        "BOM" vooraan zodat é en ë goed overkomen (UTF-8).
        """
        path = getattr(alerts, "log_file", None)

        def generate():
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
            writer.writerow(["tijd", "soort", "bericht"])
            yield "\ufeff".encode() + buffer.getvalue().encode("utf-8")
            try:
                with open(path, encoding="utf-8", errors="replace", newline="") as f:
                    for row in csv.reader(f):  # data/meldingen.csv zelf blijft met komma's
                        buffer.seek(0)
                        buffer.truncate()
                        writer.writerow(row)
                        yield buffer.getvalue().encode("utf-8")
            except (OSError, TypeError, csv.Error):
                pass  # nog geen meldingen (alleen de kopregel) of een kapotte regel aan het eind

        return Response(generate(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="meldingen.csv"',
                                 "Cache-Control": "no-store"})

    # -- gebruikers ------------------------------------------------------------

    @app.get("/api/gebruikers")
    @login_required
    def list_users():
        return jsonify(gebruikers=[{"naam": name} for name in users.names()], ik=session["user"])

    @app.post("/api/gebruikers")
    @login_required
    def add_user():
        data = _json_body()
        name, password = data.get("naam"), data.get("wachtwoord")
        if not isinstance(name, str) or not isinstance(password, str):
            return jsonify(fout="Vul een gebruikersnaam en een wachtwoord in."), 400
        try:
            users.add(name, password, replace=False)  # een bestaande gebruiker overschrijven mag niet
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        log.info("Gebruiker '%s' toegevoegd door %s", name.strip().lower(), session["user"])
        return jsonify(ok=True)

    @app.delete("/api/gebruikers/<naam>")
    @login_required
    def delete_user(naam):
        naam = naam.strip().lower()
        if naam == session["user"]:
            return jsonify(fout="Je kunt jezelf niet verwijderen."), 400
        try:
            removed = users.remove(naam, keep_last=True)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        if not removed:
            return jsonify(fout="Deze gebruiker bestaat niet."), 404
        log.info("Gebruiker '%s' verwijderd door %s", naam, session["user"])
        return jsonify(ok=True)

    @app.post("/api/wachtwoord")
    @login_required
    def change_password():
        """Eigen wachtwoord wijzigen. Net als bij inloggen: na 5 foute pogingen 5 minuten wachten."""
        data = _json_body()
        old, new = data.get("oud"), data.get("nieuw")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return jsonify(fout="Vul je huidige en je nieuwe wachtwoord in."), 400
        try:
            check_password(new)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        ip = request.remote_addr or "?"
        if not limiter.attempt(ip):  # telt de poging meteen mee, ook bij veel tegelijk
            return jsonify(fout="Te veel mislukte pogingen. Wacht 5 minuten."), 429
        if not users.verify(session["user"], old):
            time.sleep(0.5)
            return jsonify(fout="Je huidige wachtwoord klopt niet."), 400
        limiter.succeeded(ip)
        try:
            users.change_password(session["user"], new)
        except ValueError as exc:
            return jsonify(fout=str(exc)), 400
        # Zelf ingelogd blijven, de rest is uitgelogd. Verzoeken van deze sessie die nog met
        # het oude cookie onderweg zijn, tellen nog even mee (zie stamp_grace).
        old_stamp, new_stamp = session.get("stempel"), users.stamp(session["user"])
        session["stempel"] = new_stamp
        session.setdefault("sid", secrets.token_hex(8))
        now = time.time()
        for sid in [k for k, v in stamp_grace.items() if v[3] < now]:
            stamp_grace.pop(sid, None)  # verlopen
        stamp_grace[session["sid"]] = (session["user"], old_stamp, new_stamp, now + STAMP_GRACE)
        log.info("Gebruiker '%s' heeft zijn wachtwoord gewijzigd", session["user"])
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


BUSY = "J.A.R.V.I.S. is nog bezig met je vorige vraag. Even geduld."


def _safe_next(target: str) -> str:
    """Waar je na het inloggen heen gaat: alleen een pad op deze website, anders "/".

    Strenger dan "begint met /": browsers negeren tabs en regeleinden in een adres en lezen
    een backslash als "/". Zo zou "/", tab, "/evil.com" toch naar een andere website gaan.
    """
    if not isinstance(target, str) or not target.startswith("/") or target.startswith("//") \
            or "\\" in target or any(ord(c) < 32 or ord(c) == 127 for c in target):
        return "/"
    parts = urlparse(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target


def _json_body() -> dict:
    """De JSON van het verzoek, of {} als het geen JSON-object is."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}

