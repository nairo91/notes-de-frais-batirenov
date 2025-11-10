import os
import csv
import re
import requests
import psycopg2
from datetime import datetime, date
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_from_directory, jsonify, flash
)
from werkzeug.utils import secure_filename
from functools import wraps

import cloudinary
import cloudinary.uploader

# -----------------------------------------------------------------------------
# CONFIG FLASK
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Dossier d'upload local (fallback si Cloudinary n'est pas configuré)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# -----------------------------------------------------------------------------
# CONFIG CLOUDINARY (pour les photos de tickets)
# -----------------------------------------------------------------------------
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

def upload_receipt(file):
    """Upload du justificatif. Si Cloudinary est configuré, on stocke dans le cloud,
    sinon en local dans /uploads. On retourne une 'receipt_path' (URL ou nom de fichier)."""
    if not file or not file.filename:
        return None

    if CLOUDINARY_URL:
        # Upload sur Cloudinary
        result = cloudinary.uploader.upload(file, folder="notes-frais-batirenov")
        return result.get("secure_url")
    else:
        # Fallback local
        filename = secure_filename(file.filename)
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(save_path)
        return filename

# -----------------------------------------------------------------------------
# CONFIG BASE DE DONNÉES (PostgreSQL)
# -----------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode=os.environ.get("DB_SSLMODE", "require")
    )
    return conn

def init_db():
    """Crée la table expenses si elle n'existe pas."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_email TEXT NOT NULL,
            amount NUMERIC(10,2) NOT NULL,
            date DATE NOT NULL,
            label TEXT NOT NULL,
            chantier TEXT NOT NULL,
            receipt_path TEXT,
            created_at TIMESTAMP NOT NULL
        );
    """)
    conn.commit()
    conn.close()

# IMPORTANT : on initialise la DB au chargement du module
init_db()

# -----------------------------------------------------------------------------
# AUTH : CHARGEMENT DES UTILISATEURS VIA CSV
# -----------------------------------------------------------------------------
USERS = {}  # email -> {password, nom, prenom}

ADMIN_EMAILS = {
    "mirona.orian@batirenov.info",
    "launay.jeremy@batirenov.info",
}

def load_users_from_csv():
    csv_path = os.path.join(BASE_DIR, "users.csv")
    if not os.path.exists(csv_path):
        print("⚠️ users.csv introuvable : pas de login possible.")
        return
    with open(csv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            email = (row.get("email") or "").strip().lower()
            pwd = (row.get("password") or "").strip()
            if email and pwd:
                USERS[email] = {
                    "password": pwd,
                    "nom": (row.get("Nom") or "").strip(),
                    "prenom": (row.get("Prenom") or "").strip(),
                }

load_users_from_csv()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def is_admin():
    return session.get("user_email") in ADMIN_EMAILS

# -----------------------------------------------------------------------------
# ROUTES AUTH
# -----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = USERS.get(email)
        if user and user["password"] == password:
            session["user_email"] = email
            session["user_name"] = (
                f'{user["prenom"]} {user["nom"]}'.strip() or email
            )
            return redirect(url_for("expenses"))
        else:
            flash("Email ou mot de passe incorrect", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    if "user_email" in session:
        return redirect(url_for("expenses"))
    return redirect(url_for("login"))

# -----------------------------------------------------------------------------
# ROUTES FICHIERS UPLOAD (fallback local)
# -----------------------------------------------------------------------------
@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    # ⚠️ Pour une sécurité stricte, on pourrait vérifier ici que le fichier
    # correspond bien à une note appartenant à l'utilisateur ou à un admin.
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# -----------------------------------------------------------------------------
# ROUTE PRINCIPALE : NOTES DE FRAIS
# -----------------------------------------------------------------------------
@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    conn = get_db()
    cur = conn.cursor()

    current_user = session["user_email"]

    if request.method == "POST":
        # Récupération des champs du formulaire
        amount = request.form.get("amount")
        date_str = request.form.get("date")
        label = request.form.get("label")
        chantier = request.form.get("chantier")

        # Validation basique
        if not all([amount, date_str, label, chantier]):
            flash("Tous les champs marqués * sont obligatoires.", "danger")
        else:
            try:
                amount_val = float(amount)
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                flash("Montant ou date invalide.", "danger")
                conn.close()
                return redirect(url_for("expenses"))

            # Gestion du fichier justificatif (Cloudinary ou local)
            file = request.files.get("receipt")
            receipt_path = upload_receipt(file) if file and file.filename else None

            cur.execute(
                """
                INSERT INTO expenses
                    (user_email, amount, date, label, chantier, receipt_path, created_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    current_user,
                    amount_val,
                    date_str,
                    label,
                    chantier,
                    receipt_path,
                    datetime.utcnow(),
                )
            )
            conn.commit()
            flash("Note de frais ajoutée avec succès ✅", "success")

        conn.close()
        return redirect(url_for("expenses"))

    # ----------- PARTIE LECTURE / AFFICHAGE -----------
    if is_admin():
        # Admin : voit toutes les notes
        cur.execute(
            """
            SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at
            FROM expenses
            ORDER BY date DESC, id DESC
            """
        )
    else:
        # Utilisateur normal : ne voit que ses propres notes
        cur.execute(
            """
            SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at
            FROM expenses
            WHERE user_email = %s
            ORDER BY date DESC, id DESC
            """,
            (current_user,)
        )

    rows = cur.fetchall()
    conn.close()

    expenses_data = []
    for r in rows:
        expenses_data.append({
            "id": r[0],
            "user_email": r[1],
            "amount": float(r[2]),
            "date": r[3].strftime("%Y-%m-%d"),
            "label": r[4],
            "chantier": r[5],
            "receipt_path": r[6],
            "created_at": r[7].isoformat(),
        })

    return render_template(
        "expenses.html",
        expenses=expenses_data,
        user_name=session.get("user_name"),
        user_email=current_user,
        is_admin=is_admin(),
    )

# -----------------------------------------------------------------------------
# API JSON pour le tableau (utilisée par main.js pour filtrer/tri côté client)
# -----------------------------------------------------------------------------
@app.route("/api/expenses")
@login_required
def api_expenses():
    conn = get_db()
    cur = conn.cursor()
    current_user = session["user_email"]

    if is_admin():
        cur.execute(
            """
            SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at
            FROM expenses
            ORDER BY date DESC, id DESC
            """
        )
    else:
        cur.execute(
            """
            SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at
            FROM expenses
            WHERE user_email = %s
            ORDER BY date DESC, id DESC
            """,
            (current_user,)
        )

    rows = cur.fetchall()
    conn.close()

    data = []
    for r in rows:
        data.append({
            "id": r[0],
            "user_email": r[1],
            "amount": float(r[2]),
            "date": r[3].strftime("%Y-%m-%d"),
            "label": r[4],
            "chantier": r[5],
            "receipt_path": r[6],
            "created_at": r[7].isoformat(),
        })
    return jsonify(data)

# -----------------------------------------------------------------------------
# OCR : Scan d'un ticket pour pré-remplir la note
# -----------------------------------------------------------------------------
def extract_amount(text: str):
    if not text:
        return None
    # remplace les virgules par des points pour simplifier
    cleaned = text.replace(",", ".")
    # cherche des montants avec 2 décimales
    matches = re.findall(r"(\d+\.\d{2})", cleaned)
    if matches:
        return matches[-1]
    # à défaut, cherche un entier
    matches = re.findall(r"(\d+)", cleaned)
    if matches:
        return matches[-1]
    return None

def extract_date(text: str):
    if not text:
        return None
    # format français JJ/MM/AAAA
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            return d.isoformat()
        except ValueError:
            pass
    # format ISO AAAA-MM-JJ
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    return None

@app.route("/api/scan_receipt", methods=["POST"])
@login_required
def scan_receipt():
    file = request.files.get("receipt")
    if not file:
        return jsonify({"error": "Aucun fichier reçu"}), 400

    ocr_api_key = os.environ.get("OCRSPACE_API_KEY")
    ocr_url = os.environ.get("OCRSPACE_URL", "https://api.ocr.space/parse/image")

    if not ocr_api_key:
        return jsonify({"error": "OCR non configuré (OCRSPACE_API_KEY manquant)"}), 500

    try:
        resp = requests.post(
            ocr_url,
            files={"file": (file.filename, file.stream, file.mimetype)},
            data={
                "apikey": ocr_api_key,
                "language": "fre",
                "OCREngine": 2,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return jsonify({"error": f"Erreur OCR: {e}"}), 500

    text = ""
    if data.get("ParsedResults"):
        text = " ".join(r.get("ParsedText", "") for r in data["ParsedResults"])

    amount = extract_amount(text)
    date_str = extract_date(text)
    label_guess = (text or "").strip().replace("\n", " ")[:80]

    return jsonify({
        "amount": amount,
        "date": date_str,
        "label": label_guess,
        "raw_text": text,
    })

# -----------------------------------------------------------------------------
# GÉNÉRATION DU RÉCAP MENSUEL + ENVOI MAIL
# (tient compte de toutes les notes, pour la compta)
# -----------------------------------------------------------------------------
def generate_monthly_report(year: int, month: int):
    conn = get_db()
    cur = conn.cursor()

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    cur.execute(
        """
        SELECT user_email, amount, date, label, chantier, receipt_path
        FROM expenses
        WHERE date >= %s AND date < %s
        ORDER BY date ASC
        """,
        (start, end)
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "user_email": r[0],
            "amount": float(r[1]),
            "date": r[2].strftime("%Y-%m-%d"),
            "label": r[3],
            "chantier": r[4],
            "receipt_path": r[5],
        })
    return result

def format_report_csv(rows):
    import io
    import csv as csv_module
    output = io.StringIO()
    writer = csv_module.writer(output, delimiter=";")
    writer.writerow(["Date", "Montant", "Libellé", "Chantier", "Utilisateur", "Justificatif"])
    for r in rows:
        writer.writerow([
            r["date"],
            r["amount"],
            r["label"],
            r["chantier"],
            r["user_email"],
            r["receipt_path"] or "",
        ])
    return output.getvalue()

def send_report_email(year: int, month: int):
    import smtplib
    from email.message import EmailMessage

    rows = generate_monthly_report(year, month)
    csv_content = format_report_csv(rows)

    msg = EmailMessage()
    msg["Subject"] = f"Récap notes de frais {year}-{month:02d}"
    msg["From"] = os.environ.get("SMTP_FROM", "no-reply@batirenov.info")
    msg["To"] = "compta@batirenov.info"
    msg.set_content(
        f"Bonjour,\n\n"
        f"Veuillez trouver ci-joint le récapitulatif des notes de frais pour {year}-{month:02d}.\n\n"
        f"Cordialement,\n"
        f"L'application notes de frais BATI RENOV"
    )

    msg.add_attachment(
        csv_content.encode("utf-8"),
        maintype="text",
        subtype="csv",
        filename=f"notes-de-frais-{year}-{month:02d}.csv"
    )

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    if not host:
        raise RuntimeError("SMTP_HOST is not configured")

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)

@app.route("/admin/send_report_now")
def admin_send_report_now():
    """Route pour tester manuellement l'envoi du rapport."""
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    send_report_email(year, month)
    return "OK"

def cli_send_report_cron():
    """Fonction appelée par le cron Render (python app.py send_report_cron)."""
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    send_report_email(year, month)

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "send_report_cron":
        cli_send_report_cron()
    else:
        app.run(debug=True, host="0.0.0.0", port=5000)
