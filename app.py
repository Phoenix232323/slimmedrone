"""Start het grondstation van de SlimmeDrone.

    python app.py

Open daarna in de browser het adres dat hieronder in de terminal verschijnt.
Instellingen staan in .env (zie .env.example).
"""
import datetime
import ipaddress
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import time

from slimmedrone.activity import ActivityTracker
from slimmedrone.alerts import AlertManager
from slimmedrone.assistant import Assistant
from slimmedrone.auth import UserStore
from slimmedrone.camera import Camera
from slimmedrone.config import load_config
from slimmedrone.faces import FaceEngine
from slimmedrone.objects import ObjectDetector
from slimmedrone.pipeline import Pipeline
from slimmedrone.settings import Settings
from slimmedrone.web import create_app

log = logging.getLogger("slimmedrone")
CERT_DAYS = 825  # langer geldig accepteren iPhones/Macs niet


def local_ip() -> str:
    """Het IP-adres van deze computer in het (wifi-)netwerk."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # er wordt niets verstuurd
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


# -- https ---------------------------------------------------------------------
# Browsers staan de microfoon alleen toe via https (of op localhost). Met HTTPS=aan
# in .env maken we één keer een eigen ("self-signed") certificaat in data/tls.
# De browser waarschuwt daar de eerste keer voor; dat is normaal.

def https_files(cfg):
    """Geeft (certificaat, sleutel) voor https, of None als het niet lukt."""
    folder = cfg.data_dir / "tls"
    cert, key = folder / "cert.pem", folder / "key.pem"
    host = re.sub(r"[^A-Za-z0-9.-]", "", socket.gethostname()) or "slimmedrone"
    names = sorted({"localhost", host, f"{host}.local"})
    ips = sorted({"127.0.0.1", local_ip()})
    label = folder / "adressen.txt"  # voor welke adressen het huidige certificaat is
    wanted = " ".join(names + ips)
    if cert.exists() and key.exists() and label.exists() and label.read_text().strip() == wanted \
            and time.time() - cert.stat().st_mtime < (CERT_DAYS - 30) * 86400:
        return cert, key
    folder.mkdir(parents=True, exist_ok=True)
    for make in (_cert_with_cryptography, _cert_with_openssl):
        try:
            make(cert, key, names, ips)
            os.chmod(key, 0o600)  # de sleutel is geheim
            label.write_text(wanted)
            log.info("Nieuw https-certificaat gemaakt in %s (voor %s)", folder, ", ".join(names + ips))
            return cert, key
        except Exception as exc:
            log.warning("Certificaat maken met %s lukte niet: %s", make.__doc__, exc)
    log.error("Kon geen https-certificaat maken. Installeer het pakket 'cryptography' "
              "(pip install cryptography) of het programma openssl. De website draait nu via http.")
    return None


class _HandshakeInThread(ssl.SSLContext):
    """https waarbij één trage verbinding de rest van de website niet laat wachten.

    Normaal doet de webserver de https-"handdruk" meteen bij het aannemen van een
    verbinding, in zijn ene hoofdlus. Opent een apparaat een verbinding en stuurt het
    daarna niets, dan wacht de hele website (voor iedereen). Daarom doen we de
    handdruk pas bij het eerste lezen, in de eigen thread van die verbinding.
    """

    def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True, **kwargs):
        return super().wrap_socket(sock, server_side=server_side, do_handshake_on_connect=False, **kwargs)


def https_context(cert, key):
    context = _HandshakeInThread(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    return context


def _cert_with_cryptography(cert, key, names, ips):
    """het Python-pakket cryptography"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SlimmeDrone")])
    now = datetime.datetime.now(datetime.timezone.utc)
    alt_names = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(private_key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _cert_with_openssl(cert, key, names, ips):
    """het programma openssl"""
    alt_names = ",".join([f"DNS:{n}" for n in names] + [f"IP:{i}" for i in ips])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                    "-days", str(CERT_DAYS), "-subj", "/CN=SlimmeDrone",
                    "-keyout", str(key), "-out", str(cert),
                    "-addext", f"subjectAltName={alt_names}",
                    "-addext", "extendedKeyUsage=serverAuth"],
                   check=True, capture_output=True, timeout=120)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    for name in ("httpx", "httpx2"):  # niet elke vraag aan Claude als aparte regel loggen
        logging.getLogger(name).setLevel(logging.WARNING)
    cfg = load_config()

    users = UserStore(cfg.users_file)
    if not users.names():
        print("\nEr zijn nog geen gebruikers. Maak er eerst een aan:\n"
              "    python beheer.py gebruiker-toevoegen <naam>\n")
        sys.exit(1)

    camera = Camera(cfg.camera_source, cfg.camera_width, cfg.camera_height).start()
    objects = ObjectDetector(cfg.object_backend, cfg.models_dir, cfg.yolo_path, cfg.object_confidence)
    faces = FaceEngine(cfg.models_dir, cfg.faces_dir, cfg.face_detect_threshold, cfg.face_match_threshold)
    alerts = AlertManager(cfg.alert_cooldown, cfg.data_dir / "meldingen.csv")
    pipeline = Pipeline(cfg, camera, objects, faces, alerts).start()

    # J.A.R.V.I.S. en wat erbij hoort: instellingen van de website, de activiteit
    # (wat verscheen/verdween, wachters) en eventueel https voor de microfoon.
    settings = Settings(cfg.data_dir / "instellingen.json", cfg)
    activity = ActivityTracker(pipeline, alerts).start()
    assistant = Assistant(cfg, pipeline, alerts=alerts, faces=faces, objects=objects,
                          activity=activity, settings=settings)
    app = create_app(cfg, pipeline, faces, objects, alerts, assistant, users,
                     settings=settings, activity=activity)
    tls = https_files(cfg) if cfg.https else None
    if tls:
        app.config["SESSION_COOKIE_SECURE"] = True  # inlogcookie alleen via https
    scheme = "https" if tls else "http"

    print("\n  SlimmeDrone grondstation draait!")
    print(f"  Op deze computer:     {scheme}://localhost:{cfg.port}")
    if cfg.host == "0.0.0.0":
        print(f"  Vanaf laptop/tablet:  {scheme}://{local_ip()}:{cfg.port}")
    if tls:
        print("  (De browser waarschuwt de eerste keer voor het eigen certificaat:\n"
              "   kies 'Geavanceerd' en dan 'Doorgaan'. Daarna werkt ook de microfoon.)")
    print("  Stoppen: Ctrl+C\n")
    app.run(host=cfg.host, port=cfg.port, threaded=True, debug=False, use_reloader=False,
            ssl_context=https_context(*tls) if tls else None)


if __name__ == "__main__":
    main()
