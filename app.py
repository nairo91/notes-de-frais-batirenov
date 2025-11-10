import os
import csv
import psycopg2
from datetime import datetime, date
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_from_directory, jsonify, flash
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-env")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Uploads
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Database (PostgreSQL via DATABASE_URL env var)
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(DATABASE_URL, sslmode=os.environ.get("DB_SSLMODE", "require"))
    return conn

def init_db():
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

init_db()
# Auth via CSV
USERS = {}  # email -> {password, nom, prenom}

def load_users_from_csv():
    csv_path = os.path.join(BASE_DIR, "users.csv")
    if not os.path.exists(csv_path):
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

from functools import wraps

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = USERS.get(email)
        if user and user["password"] == password:
            session["user_email"] = email
            session["user_name"] = f'{user["prenom"]} {user["nom"]}'.strip() or email
            return redirect(url_for("expenses"))
        else:
            flash("Email ou mot de passe incorrect", "error")
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

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        amount = request.form.get("amount")
        date_str = request.form.get("date")
        label = request.form.get("label")
        chantier = request.form.get("chantier")

        if not all([amount, date_str, label, chantier]):
            flash("Tous les champs sont obligatoires", "error")
        else:
            try:
                amount_val = float(amount)
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                flash("Montant ou date invalide", "error")
                return redirect(url_for("expenses"))

            file = request.files.get("receipt")
            receipt_path = None
            if file and file.filename:
                filename = secure_filename(file.filename)
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                file.save(save_path)
                receipt_path = filename

            cur.execute(
                "INSERT INTO expenses (user_email, amount, date, label, chantier, receipt_path, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    session["user_email"],
                    amount_val,
                    date_str,
                    label,
                    chantier,
                    receipt_path,
                    datetime.utcnow(),
                )
            )
            conn.commit()
            flash("Note de frais ajoutée", "success")

        return redirect(url_for("expenses"))

    cur.execute(
        "SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at "
        "FROM expenses ORDER BY date DESC"
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

    return render_template("expenses.html", expenses=expenses_data, user_name=session.get("user_name"))

@app.route("/api/expenses")
@login_required
def api_expenses():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, user_email, amount, date, label, chantier, receipt_path, created_at FROM expenses"
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

def generate_monthly_report(year: int, month: int):
    conn = get_db()
    cur = conn.cursor()

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    cur.execute(
        "SELECT user_email, amount, date, label, chantier, receipt_path "
        "FROM expenses WHERE date >= %s AND date < %s ORDER BY date ASC",
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
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    init_db()
    send_report_email(year, month)
    return "OK"

def cli_send_report_cron():
    today = date.today()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    init_db()
    send_report_email(year, month)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "send_report_cron":
        cli_send_report_cron()
    else:
        init_db()
        app.run(debug=True, host="0.0.0.0", port=5000)
