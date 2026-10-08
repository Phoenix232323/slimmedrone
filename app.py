"""Start het grondstation van de SlimmeDrone.

    python app.py

Open daarna in de browser het adres dat hieronder in de terminal verschijnt.
Instellingen staan in .env (zie .env.example).
"""
import logging
import socket
import sys

from slimmedrone.alerts import AlertManager
from slimmedrone.assistant import Assistant
from slimmedrone.auth import UserStore
from slimmedrone.camera import Camera
from slimmedrone.config import load_config
from slimmedrone.faces import FaceEngine
from slimmedrone.objects import ObjectDetector
from slimmedrone.pipeline import Pipeline
from slimmedrone.web import create_app


def local_ip() -> str:
    """Het IP-adres van deze computer in het (wifi-)netwerk."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # er wordt niets verstuurd
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    cfg = load_config()

    users = UserStore(cfg.users_file)
    if not users.names():
        print("\nEr zijn nog geen gebruikers. Maak er eerst een aan:\n"
              "    python beheer.py gebruiker-toevoegen <naam>\n")
        sys.exit(1)

    # Eerst de objectherkenning, dan pas de camera starten: kiest hij de AI Camera
    # (IMX500), dan moet het netwerk in de camera staan voordat de camera begint.
    camera = Camera(cfg.camera_source, cfg.camera_width, cfg.camera_height, cfg.camera_fps)
    objects = ObjectDetector(cfg.object_backend, cfg.models_dir, cfg.yolo_path, cfg.object_confidence,
                             camera=camera, imx500_model=cfg.imx500_model)
    camera.start()
    faces = FaceEngine(cfg.models_dir, cfg.faces_dir, cfg.face_detect_threshold, cfg.face_match_threshold)
    alerts = AlertManager(cfg.alert_cooldown, cfg.data_dir / "meldingen.csv")
    pipeline = Pipeline(cfg, camera, objects, faces, alerts).start()
    assistant = Assistant(cfg, pipeline)
    app = create_app(cfg, pipeline, faces, objects, alerts, assistant, users)

    print("\n  SlimmeDrone grondstation draait!")
    print(f"  Op deze computer:     http://localhost:{cfg.port}")
    if cfg.host == "0.0.0.0":
        print(f"  Vanaf laptop/tablet:  http://{local_ip()}:{cfg.port}")
    print("  Stoppen: Ctrl+C\n")
    app.run(host=cfg.host, port=cfg.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
