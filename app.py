from werkzeug.security import generate_password_hash, check_password_hash

import os
import csv
import re
import requests
import psycopg2
from datetime import datetime, date
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_from_directory, jsonify, flash, Response
)
from werkzeug.utils import secure_filename
from functools import wraps

import cloudinary
import cloudinary.uploader

import io
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Image as RLImage,
    PageBreak,
)

# -----------------------------------------------------------------------------#
# CONFIG FLASK
# -----------------------------------------------------------------------------#
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Dossier d'upload local (fallback si Cloudinary n'est pas configuré)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# -----------------------------------------------------------------------------#
# CONFIG CLOUDINARY (pour les photos de tickets)
# -----------------------------------------------------------------------------#
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL")
if CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)


def upload_receipt(file):
    """
    Upload du justificatif.
    - Si Cloudinary est configuré : upload dans le cloud et on stocke l'URL.
    - Sinon : stockage en local dans /uploads, on stocke le nom de fichier.
    On retourne une 'receipt_path' (URL ou nom de fichier).
    """
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


# -----------------------------------------------------------------------------#
# CONFIG BASE DE DONNÉES (PostgreSQL)
# -----------------------------------------------------------------------------#
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
    conn = get_db()
    cur = conn.cursor()

    # Table users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        );
    """)

    # Table expenses (avec status + validation + HT/TVA + payment_method + comment_text)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_email TEXT NOT NULL,
            amount NUMERIC(10,2) NOT NULL,
            amount_ht NUMERIC(10,2),
            tva_amount NUMERIC(10,2),
            date DATE NOT NULL,
            label TEXT NOT NULL,
            chantier TEXT NOT NULL,
            payment_method TEXT,
            comment_text TEXT,
            receipt_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            validated_by TEXT,
            validated_at TIMESTAMPTZ,
            created_at TIMESTAMP NOT NULL
        );
    """)

    # Ajout des colonnes HT / TVA / moyen de paiement / commentaire si base déjà existante
    cur.execute("""
        ALTER TABLE expenses
        ADD COLUMN IF NOT EXISTS amount_ht NUMERIC(10,2);
    """)
    cur.execute("""
        ALTER TABLE expenses
        ADD COLUMN IF NOT EXISTS tva_amount NUMERIC(10,2);
    """)
    cur.execute("""
        ALTER TABLE expenses
        ADD COLUMN IF NOT EXISTS payment_method TEXT;
    """)
    cur.execute("""
        ALTER TABLE expenses
        ADD COLUMN IF NOT EXISTS comment_text TEXT;
    """)

    conn.commit()
    conn.close()


# IMPORTANT : on initialise la DB au chargement du module
init_db()


def sync_users_from_csv():
    """
    Lit users.csv et synchronise dans la table users.
    - crée les utilisateurs manquants
    - met à jour prénom/nom si besoin
    - NE réécrit PAS les mots de passe existants
    """
    csv_path = os.path.join(BASE_DIR, "users.csv")
    if not os.path.exists(csv_path):
        print("users.csv introuvable, pas de synchro utilisateurs.")
        return

    conn = get_db()
    cur = conn.cursor()

    with open(csv_path, encoding="latin-1") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            email = (row.get("email") or "").strip().lower()
            pwd = (row.get("password") or "").strip()
            first_name = (row.get("Prenom") or "").strip()
            last_name = (row.get("Nom") or "").strip()

            if not email or not pwd:
                continue

            # Vérifier si l'utilisateur existe déjà
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing = cur.fetchone()

            if existing:
                # Mise à jour prénom/nom, mais on ne touche pas au mot de passe
                cur.execute(
                    """
                    UPDATE users
                    SET first_name = %s, last_name = %s
                    WHERE email = %s
                    """,
                    (first_name, last_name, email)
                )
            else:
                # Création avec mot de passe hashé
                password_hash = generate_password_hash(pwd)
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, first_name, last_name)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (email, password_hash, first_name, last_name)
                )

    conn.commit()
    conn.close()
    print("Synchronisation des utilisateurs depuis users.csv terminée.")


# synchro au chargement
sync_users_from_csv()

# -----------------------------------------------------------------------------#
# AUTH / ROLES
# -----------------------------------------------------------------------------#
ADMIN_EMAILS = {
    "mirona.orian@batirenov.info",
    "launay.jeremy@batirenov.info",
}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def is_admin():
    return session.get("user_email") in ADMIN_EMAILS


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_email" not in session or not is_admin():
            flash("Accès réservé à l'administration.", "danger")
            return redirect(url_for("expenses"))
        return f(*args, **kwargs)
    return wrapper


def get_user_by_email(email: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, email, password_hash, first_name, last_name
        FROM users
        WHERE email = %s AND is_active = TRUE
        """,
        (email.lower(),)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "email": row[1],
        "password_hash": row[2],
        "first_name": row[3] or "",
        "last_name": row[4] or "",
    }


# -----------------------------------------------------------------------------#
# ROUTES AUTH
# -----------------------------------------------------------------------------#
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        user = get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session["user_email"] = user["email"]
            full_name = f'{user["first_name"]} {user["last_name"]}'.strip()
            session["user_name"] = full_name or user["email"]
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


# -----------------------------------------------------------------------------#
# ROUTES FICHIERS UPLOAD (fallback local)
# -----------------------------------------------------------------------------#
@app.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    # ⚠️ Pour une sécurité stricte, on pourrait vérifier ici que le fichier
    # correspond bien à une note appartenant à l'utilisateur ou à un admin.
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# -----------------------------------------------------------------------------#
# ROUTE PRINCIPALE : NOTES DE FRAIS
# -----------------------------------------------------------------------------#
@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    conn = get_db()
    cur = conn.cursor()
    current_user = session["user_email"]

    if request.method == "POST":
        # Récupération des champs du formulaire
        amount = request.form.get("amount")
        amount_ht_str = request.form.get("amount_ht") or ""
        tva_amount_str = request.form.get("tva_amount") or ""
        date_str = request.form.get("date")
        label = request.form.get("label")
        chantier = request.form.get("chantier")

        # Nouveaux champs optionnels
        payment_method = (request.form.get("payment_method") or "").strip() or None
        comment_text = (request.form.get("comment_text") or "").strip() or None

        # Validation basique
        if not all([amount, date_str, label, chantier]):
            flash("Tous les champs marqués * sont obligatoires.", "danger")
        else:
            try:
                amount_val = float(amount.replace(",", "."))
                datetime.strptime(date_str, "%Y-%m-%d")
                amount_ht_val = float(amount_ht_str.replace(",", ".")) if amount_ht_str else None
                tva_amount_val = float(tva_amount_str.replace(",", ".")) if tva_amount_str else None
            except ValueError:
                flash("Montants ou date invalides.", "danger")
                conn.close()
                return redirect(url_for("expenses"))

            # Gestion du fichier justificatif (Cloudinary ou local)
            file = request.files.get("receipt")
            receipt_path = upload_receipt(file) if file and file.filename else None

            # Status = pending par défaut (défini aussi en base)
            cur.execute(
                """
                INSERT INTO expenses
                    (user_email, amount, amount_ht, tva_amount,
                     date, label, chantier, payment_method, comment_text,
                     receipt_path, created_at)
                VALUES
                    (%s, %s, %s, %s,
                     %s, %s, %s, %s, %s,
                     %s, %s)
                """,
                (
                    current_user,
                    amount_val,
                    amount_ht_val,
                    tva_amount_val,
                    date_str,
                    label,
                    chantier,
                    payment_method,
                    comment_text,
                    receipt_path,
                    datetime.utcnow(),
                )
            )
            conn.commit()
            flash("Note de frais ajoutée avec succès ✅", "success")

        conn.close()
        return redirect(url_for("expenses"))

    # ----------- PARTIE LECTURE / AFFICHAGE -----------#
    if is_admin():
        # Admin : voit toutes les notes
        cur.execute(
            """
            SELECT id, user_email, amount, amount_ht, tva_amount,
                   date, label, chantier, payment_method, comment_text,
                   receipt_path, created_at, status, validated_by, validated_at
            FROM expenses
            ORDER BY date DESC, id DESC
            """
        )
    else:
        # Utilisateur normal : ne voit que ses propres notes
        cur.execute(
            """
            SELECT id, user_email, amount, amount_ht, tva_amount,
                   date, label, chantier, payment_method, comment_text,
                   receipt_path, created_at, status, validated_by, validated_at
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
            "amount_ht": float(r[3]) if r[3] is not None else None,
            "tva_amount": float(r[4]) if r[4] is not None else None,
            "date": r[5].strftime("%Y-%m-%d"),
            "label": r[6],
            "chantier": r[7],
            "payment_method": r[8],
            "comment_text": r[9],
            "receipt_path": r[10],
            "created_at": r[11].isoformat(),
            "status": r[12],
            "validated_by": r[13],
            "validated_at": r[14].isoformat() if r[14] else None,
        })

    return render_template(
        "expenses.html",
        expenses=expenses_data,
        user_name=session.get("user_name"),
        user_email=current_user,
        is_admin=is_admin(),
    )


# -----------------------------------------------------------------------------#
# API JSON pour le tableau (utilisée par main.js pour filtrer/tri côté client)
# -----------------------------------------------------------------------------#
@app.route("/api/expenses")
@login_required
def api_expenses():
    conn = get_db()
    cur = conn.cursor()
    current_user = session["user_email"]

    if is_admin():
        cur.execute(
            """
            SELECT id, user_email, amount, amount_ht, tva_amount,
                   date, label, chantier, payment_method, comment_text,
                   receipt_path, created_at, status, validated_by, validated_at
            FROM expenses
            ORDER BY date DESC, id DESC
            """
        )
    else:
        cur.execute(
            """
            SELECT id, user_email, amount, amount_ht, tva_amount,
                   date, label, chantier, payment_method, comment_text,
                   receipt_path, created_at, status, validated_by, validated_at
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
            "amount_ht": float(r[3]) if r[3] is not None else None,
            "tva_amount": float(r[4]) if r[4] is not None else None,
            "date": r[5].strftime("%Y-%m-%d"),
            "label": r[6],
            "chantier": r[7],
            "payment_method": r[8],
            "comment_text": r[9],
            "receipt_path": r[10],
            "created_at": r[11].isoformat(),
            "status": r[12],
            "validated_by": r[13],
            "validated_at": r[14].isoformat() if r[14] else None,
        })
    return jsonify(data)

# -----------------------------------------------------------------------------#
# OCR : Scan d'un ticket pour pré-remplir la note (TTC / HT / TVA)
# -----------------------------------------------------------------------------#

def parse_amounts_ttc_ht_tva(text: str):
    """
    Essaie d'extraire TTC, HT et TVA à partir du texte entier du ticket.

    Stratégie:
      - on extrait tous les montants avec 2 décimales (10,90 / 10.90)
      - TTC = plus grand montant
      - HT  = montant associé à un libellé H.T / HT si présent,
              sinon le plus grand montant strictement inférieur au TTC
      - TVA = montant associé à un libellé TVA si présent,
              sinon TTC - HT
    On renvoie des chaînes '10.90' ou None.
    """
    if not text:
        return {"ttc": None, "ht": None, "tva": None}

    # Normalisation basique : on remplace les virgules par des points
    cleaned = text.replace("\u00a0", " ").replace(",", ".")
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]

    # 1) Récupérer tous les montants du ticket
    all_values = []
    for line in lines:
        for m in re.findall(r"\d+\.\d{2}", line):
            try:
                all_values.append(float(m))
            except ValueError:
                pass

    if not all_values:
        return {"ttc": None, "ht": None, "tva": None}

    # 2) TTC = plus grand montant
    ttc_val = max(all_values)

    # 3) HT : d'abord via un libellé H.T / HT / H T
    ht_val = None
    m_ht = re.search(r"(?:H\.?T\.?|H T|HT)[^0-9]*(\d+\.\d{2})", cleaned, re.IGNORECASE)
    if m_ht:
        try:
            ht_val = float(m_ht.group(1))
        except ValueError:
            ht_val = None

    # Sinon : plus grand montant strictement inférieur au TTC
    if ht_val is None:
        smaller = [v for v in all_values if v < ttc_val - 1e-6]
        if smaller:
            ht_val = max(smaller)

    # 4) TVA : d'abord via un libellé TVA
    tva_val = None
    m_tva = re.search(r"TVA[^0-9]*(\d+\.\d{2})", cleaned, re.IGNORECASE)
    if m_tva:
        try:
            tva_val = float(m_tva.group(1))
        except ValueError:
            tva_val = None

    # Sinon : on calcule TVA = TTC - HT si possible
    if tva_val is None and ht_val is not None:
        tva_val = round(ttc_val - ht_val, 2)

    # 5) On renvoie des chaînes "10.90" ou None
    def fmt(x):
        return f"{x:.2f}" if x is not None else None

    return {
        "ttc": fmt(ttc_val),
        "ht": fmt(ht_val),
        "tva": fmt(tva_val),
    }


def extract_date(text: str):
    """
    Cherche une date JJ/MM/AAAA ou AAAA-MM-JJ.
    Retourne une date ISO (YYYY-MM-DD) ou None.
    """
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

    # On compresse/redimensionne l'image pour rester < 1 Mo
    try:
        img = PILImage.open(file.stream)

        max_width = 1200
        if img.width > max_width:
            ratio = max_width / float(img.width)
            new_height = int(float(img.height) * ratio)
            img = img.resize((max_width, new_height))

        buf = io.BytesIO()
        img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=60)
        buf.seek(0)

        files = {"file": ("ticket.jpg", buf, "image/jpeg")}
        resp = requests.post(
            ocr_url,
            files=files,
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

    # Si l'API indique une erreur
    if data.get("IsErroredOnProcessing"):
        msg_list = data.get("ErrorMessage") or []
        if isinstance(msg_list, list):
            msg = " ".join(msg_list)
        else:
            msg = str(msg_list)
        return jsonify({"error": f"OCR: {msg}"}), 500

    parsed_results = data.get("ParsedResults")
    if not parsed_results:
        return jsonify({"error": "OCR n'a pas réussi à lire le ticket."}), 500

    text = " ".join(r.get("ParsedText", "") for r in parsed_results) or ""

    # Logs debug dans Render
    print("=== OCR RAW TEXT ===")
    print(text)
    print("====================")

    # --- Montants TTC / HT / TVA
    amounts = parse_amounts_ttc_ht_tva(text)
    amount = amounts["ttc"]          # TTC pour le champ principal
    amount_ht = amounts["ht"]
    tva_amount = amounts["tva"]

    # --- Date & libellé
    date_str = extract_date(text)
    label_guess = text.strip().replace("\n", " ")[:80] if text else ""

    # Si vraiment rien d'exploitable
    if not amount and not amount_ht and not tva_amount and not date_str:
        return jsonify({
            "error": "Le ticket a été lu mais aucun montant ou date n'ont été détectés.",
            "raw_text": text,
        }), 500

    return jsonify({
        "amount": amount,          # TTC
        "amount_ht": amount_ht,    # HT (peut être None)
        "tva_amount": tva_amount,  # TVA (peut être None)
        "date": date_str,
        "label": label_guess,
        "raw_text": text,
    })

# -----------------------------------------------------------------------------#
# GÉNÉRATION DU RÉCAP MENSUEL + ENVOI MAIL / EXPORT
# -----------------------------------------------------------------------------#

def generate_monthly_report(year: int, month: int, approved_only: bool = True):
    """
    Récupère les notes de frais pour un mois donné.

    approved_only = True  -> uniquement les notes avec status = 'approved'
    approved_only = False -> toutes les notes, peu importe le statut
    """
    conn = get_db()
    cur = conn.cursor()

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    query = """
        SELECT user_email, amount, amount_ht, tva_amount,
               date, label, chantier, payment_method, comment_text,
               receipt_path, status
        FROM expenses
        WHERE date >= %s AND date < %s
    """
    params = [start, end]

    if approved_only:
        query += " AND status = 'approved'"

    query += " ORDER BY date ASC"

    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "user_email": r[0],
            "amount": float(r[1]),
            "amount_ht": float(r[2]) if r[2] is not None else None,
            "tva_amount": float(r[3]) if r[3] is not None else None,
            "date": r[4].strftime("%Y-%m-%d"),
            "label": r[5],
            "chantier": r[6],
            "payment_method": r[7],
            "comment_text": r[8],
            "receipt_path": r[9],
            "status": r[10],
        })
    return result


def format_report_csv(rows):
    import io
    import csv as csv_module
    output = io.StringIO()
    writer = csv_module.writer(output, delimiter=";")
    writer.writerow([
        "Date", "Montant TTC", "Montant HT", "TVA",
        "Libellé", "Chantier", "Utilisateur",
        "Moyen de paiement", "Commentaire",
        "Statut", "Justificatif"
    ])
    for r in rows:
        writer.writerow([
            r["date"],
            r["amount"],
            r["amount_ht"] if r["amount_ht"] is not None else "",
            r["tva_amount"] if r["tva_amount"] is not None else "",
            r["label"],
            r["chantier"],
            r["user_email"],
            r.get("payment_method") or "",
            r.get("comment_text") or "",
            r.get("status", ""),
            r["receipt_path"] or "",
        ])
    return output.getvalue()


def generate_pdf_report(rows):
    """Génère un PDF avec un tableau récapitulatif puis les justificatifs en plein format."""
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
    elements = []

    headers = [
        "Date",
        "Montant TTC",
        "Montant HT",
        "TVA",
        "Libellé",
        "Chantier",
        "Moyen de paiement",
        "Commentaire",
        "Utilisateur",
        "Statut",
    ]

    def fmt_amount(value):
        if value is None:
            return ""
        try:
            return f"{float(value):.2f} €"
        except (TypeError, ValueError):
            return ""

    table_data = [headers]
    for r in rows:
        table_data.append([
            r.get("date", ""),
            fmt_amount(r.get("amount")),
            fmt_amount(r.get("amount_ht")),
            fmt_amount(r.get("tva_amount")),
            r.get("label", ""),
            r.get("chantier", ""),
            r.get("payment_method") or "",
            r.get("comment_text") or "",
            r.get("user_email", ""),
            r.get("status", ""),
        ])

    col_widths = [
        45,
        45,
        45,
        35,
        55,
        40,
        40,
        60,
        50,
        36,
    ]

    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (3, -1), "RIGHT"),
    ]))

    elements.append(table)

    max_width = A4[0] - 40 * mm
    max_height = A4[1] - 40 * mm

    for r in rows:
        receipt_path = r.get("receipt_path")
        if not receipt_path:
            continue

        elements.append(PageBreak())

        try:
            if receipt_path.startswith("http"):
                resp = requests.get(receipt_path, timeout=30)
                resp.raise_for_status()
                img_bytes = io.BytesIO(resp.content)
            else:
                local_path = os.path.join(app.config["UPLOAD_FOLDER"], receipt_path)
                if not os.path.exists(local_path):
                    continue
                with open(local_path, "rb") as f:
                    img_bytes = io.BytesIO(f.read())

            img_bytes.seek(0)
            pil_img = PILImage.open(img_bytes)
            width, height = pil_img.size
            ratio = min(max_width / float(width), max_height / float(height), 1)
            new_width = width * ratio
            new_height = height * ratio

            img_bytes.seek(0)
            elements.append(RLImage(img_bytes, width=new_width, height=new_height))
        except Exception:
            continue

    doc.build(elements)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


@app.route("/admin/export_last_month")
def admin_export_last_month():
    """
    Export CSV du mois précédent, utilisé par le scenario Make (webhook).
    On n'exporte que les notes APPROUVÉES pour la compta.
    """
    # --- petite sécurité avec un "secret" dans l'URL ---
    api_key = request.args.get("key", "")
    if api_key != os.environ.get("EXPORT_API_KEY", "dev-secret"):
        return "Non autorisé", 403

    # On calcule le mois précédent
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1

    # On génère les données -> uniquement approved
    rows = generate_monthly_report(year, month, approved_only=True)
    csv_content = format_report_csv(rows)

    filename = f"notes-de-frais-{year}-{month:02d}.csv"
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


def send_report_email(year: int, month: int):
    """
    Envoi du récap par mail (si SMTP dispo).
    On n'envoie que les notes APPROUVÉES.
    """
    import smtplib
    from email.message import EmailMessage

    rows = generate_monthly_report(year, month, approved_only=True)
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

    print(f"[MAIL] Envoi du récap {year}-{month:02d} via {host}:{port}", flush=True)

    try:
        # timeout court pour éviter que le worker bloque trop longtemps
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        print("[MAIL] Envoi OK", flush=True)
    except Exception as e:
        # log bien visible dans Render
        print(f"[MAIL] ERREUR SMTP : {e!r}", flush=True)
        # on relance l'erreur pour provoquer un 500 (mais avec un log clair)
        raise


@app.route("/admin/send_report_now")
@admin_required
def admin_send_report_now():
    """Route pour tester manuellement l'envoi du rapport (mois précédent)."""
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    send_report_email(year, month)
    return "OK"


def cli_send_report_cron():
    """Fonction appelée éventuellement par un cron Render (python app.py send_report_cron)."""
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    send_report_email(year, month)


# -----------------------------------------------------------------------------#
# EXPORT CSV MANUEL POUR ADMIN
# -----------------------------------------------------------------------------#
@app.route("/admin/export")
@admin_required
def admin_export():
    """
    Export CSV des notes de frais VALIDÉES pour un mois donné.
    GET :
      - year
      - month
    Ex: /admin/export?year=2025&month=11
    """
    try:
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))
    except (TypeError, ValueError):
        return "Paramètres year et month invalides", 400

    rows = generate_monthly_report(year, month, approved_only=True)
    csv_content = format_report_csv(rows)

    filename = f"notes-de-frais-{year}-{month:02d}.csv"
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@app.route("/admin/export_pdf")
@admin_required
def admin_export_pdf():
    """Export PDF des notes de frais (tous statuts) pour un mois donné."""
    try:
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))
    except (TypeError, ValueError):
        return "Paramètres year et month invalides", 400

    if month < 1 or month > 12:
        return "Paramètre month invalide", 400

    rows = generate_monthly_report(year, month, approved_only=False)
    pdf_bytes = generate_pdf_report(rows)

    filename = f"notes-de-frais-{year}-{month:02d}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


@app.route("/admin/export_all_now")
@admin_required
def admin_export_all_now():
    """
    Export CSV de TOUTES les notes de frais (tous statuts, toutes dates).
    Accessible uniquement pour les admins, via un bouton dans l'interface.
    """
    import io
    import csv as csv_module

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            date,
            amount,
            amount_ht,
            tva_amount,
            label,
            chantier,
            payment_method,
            comment_text,
            user_email,
            receipt_path,
            status,
            validated_by,
            validated_at
        FROM expenses
        ORDER BY date ASC, id ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv_module.writer(output, delimiter=";")

    # En-têtes du CSV
    writer.writerow([
        "Date",
        "Montant TTC",
        "Montant HT",
        "TVA",
        "Libellé",
        "Chantier",
        "Moyen de paiement",
        "Commentaire",
        "Utilisateur",
        "Justificatif",
        "Statut",
        "Validé par",
        "Date de validation"
    ])

    for r in rows:
        date_val = r[0].strftime("%Y-%m-%d") if r[0] else ""
        amount = float(r[1]) if r[1] is not None else ""
        amount_ht = float(r[2]) if r[2] is not None else ""
        tva_amount = float(r[3]) if r[3] is not None else ""
        label = r[4] or ""
        chantier = r[5] or ""
        payment_method = r[6] or ""
        comment_text = r[7] or ""
        user_email = r[8] or ""
        receipt_path = r[9] or ""
        status = r[10] or ""
        validated_by = r[11] or ""
        validated_at = r[12].strftime("%Y-%m-%d %H:%M:%S") if r[12] else ""

        writer.writerow([
            date_val,
            amount,
            amount_ht,
            tva_amount,
            label,
            chantier,
            payment_method,
            comment_text,
            user_email,
            receipt_path,
            status,
            validated_by,
            validated_at
        ])

    csv_content = output.getvalue()
    filename = f"notes-de-frais-ALL-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"

    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@app.route("/admin/export_pdf_all_now")
@admin_required
def admin_export_pdf_all_now():
    """Export PDF de toutes les notes de frais (tous statuts, toutes dates)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            date,
            amount,
            amount_ht,
            tva_amount,
            label,
            chantier,
            payment_method,
            comment_text,
            user_email,
            receipt_path,
            status
        FROM expenses
        ORDER BY date ASC, id ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    formatted_rows = []
    for r in rows:
        formatted_rows.append({
            "date": r[0].strftime("%Y-%m-%d") if r[0] else "",
            "amount": float(r[1]) if r[1] is not None else None,
            "amount_ht": float(r[2]) if r[2] is not None else None,
            "tva_amount": float(r[3]) if r[3] is not None else None,
            "label": r[4] or "",
            "chantier": r[5] or "",
            "payment_method": r[6],
            "comment_text": r[7],
            "user_email": r[8] or "",
            "receipt_path": r[9],
            "status": r[10] or "",
        })

    pdf_bytes = generate_pdf_report(formatted_rows)
    filename = f"notes-de-frais-ALL-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


# -----------------------------------------------------------------------------#
# ROUTES ADMIN : VALIDATION / REFUS DES NOTES
# -----------------------------------------------------------------------------#
@app.route("/admin/expenses/<int:expense_id>/approve", methods=["POST"])
@admin_required
def approve_expense(expense_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE expenses
        SET status = 'approved',
            validated_by = %s,
            validated_at = NOW()
        WHERE id = %s
        """,
        (session["user_email"], expense_id)
    )
    conn.commit()
    conn.close()
    flash("Note de frais validée.", "success")
    return redirect(url_for("expenses"))


@app.route("/admin/expenses/<int:expense_id>/reject", methods=["POST"])
@admin_required
def reject_expense(expense_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE expenses
        SET status = 'rejected',
            validated_by = %s,
            validated_at = NOW()
        WHERE id = %s
        """,
        (session["user_email"], expense_id)
    )
    conn.commit()
    conn.close()
    flash("Note de frais refusée.", "warning")
    return redirect(url_for("expenses"))


# -----------------------------------------------------------------------------#
# MAIN
# -----------------------------------------------------------------------------#
if __name__ == "__main__":
    import sys
    init_db()
    sync_users_from_csv()
    if len(sys.argv) > 1 and sys.argv[1] == "send_report_cron":
        cli_send_report_cron()
    else:
        app.run(debug=True, host="0.0.0.0", port=5000)
