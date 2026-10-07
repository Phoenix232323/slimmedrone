"""Beheer van de SlimmeDrone vanaf de terminal.

    python beheer.py gebruiker-toevoegen johan     (vraagt om een wachtwoord)
    python beheer.py gebruiker-verwijderen johan
    python beheer.py gebruikers
    python beheer.py gezicht-toevoegen "Johan" foto1.jpg foto2.jpg
    python beheer.py modellen                       (alle AI-modellen alvast downloaden)
"""
import argparse
import getpass
import logging
import sys

import cv2

from slimmedrone.auth import UserStore
from slimmedrone.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Beheer van de SlimmeDrone")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("gebruiker-toevoegen", help="gebruiker maken of wachtwoord wijzigen")
    add.add_argument("naam")
    remove = sub.add_parser("gebruiker-verwijderen", help="gebruiker verwijderen")
    remove.add_argument("naam")
    sub.add_parser("gebruikers", help="alle gebruikers tonen")
    face = sub.add_parser("gezicht-toevoegen", help="gezicht toevoegen uit foto's")
    face.add_argument("naam")
    face.add_argument("fotos", nargs="+")
    sub.add_parser("modellen", help="AI-modellen downloaden")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    users = UserStore(cfg.users_file)

    if args.command == "gebruiker-toevoegen":
        password = getpass.getpass(f"Wachtwoord voor {args.naam}: ")
        if password != getpass.getpass("Nog een keer: "):
            sys.exit("De wachtwoorden zijn niet hetzelfde.")
        try:
            users.add(args.naam, password)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Gebruiker '{args.naam.lower()}' opgeslagen.")

    elif args.command == "gebruiker-verwijderen":
        print("Verwijderd." if users.remove(args.naam) else "Die gebruiker bestaat niet.")

    elif args.command == "gebruikers":
        print("\n".join(users.names()) or "Nog geen gebruikers.")

    elif args.command == "gezicht-toevoegen":
        from slimmedrone.faces import FaceEngine

        faces = FaceEngine(cfg.models_dir, cfg.faces_dir, cfg.face_detect_threshold, cfg.face_match_threshold)
        for path in args.fotos:
            try:
                faces.enroll(args.naam, cv2.imread(path))
                print(f"OK    {path}")
            except ValueError as exc:
                print(f"FOUT  {path}: {exc}")

    elif args.command == "modellen":
        from slimmedrone.faces import FaceEngine
        from slimmedrone.objects import ObjectDetector

        FaceEngine(cfg.models_dir, cfg.faces_dir)
        detector = ObjectDetector(cfg.object_backend, cfg.models_dir, cfg.yolo_path)
        print(f"Klaar. Objectherkenning: {detector.description}")


if __name__ == "__main__":
    main()
