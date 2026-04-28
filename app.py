import os
from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from io import BytesIO
from datetime import datetime
import pandas as pd
import qrcode


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tajneheslo123")


# =========================
# DB: Railway PostgreSQL / lokálně SQLite
# =========================

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL.replace("postgres://", "postgresql://")
else:
    DB_DIR = os.path.join(os.path.expanduser("~"), "SkladAppData")
    os.makedirs(DB_DIR, exist_ok=True)
    DB_PATH = os.path.join(DB_DIR, "sklad.db").replace("\\", "/")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# =========================
# MODELY
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True)
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), default="user")


class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.String(20), unique=True)
    manufacturer_id = db.Column(db.String(100))
    name = db.Column(db.String(255))
    quantity = db.Column(db.Float, default=0)
    unit = db.Column(db.String(50))
    price_without_vat = db.Column(db.Float, default=0)


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey("material.id"))
    movement_type = db.Column(db.String(20))
    quantity = db.Column(db.Float)
    username = db.Column(db.String(80))
    order_number = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)


# =========================
# AUTH
# =========================

def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*a, **k)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **k):
        user = current_user()
        if not user or user.role != "admin":
            flash("Nemáš oprávnění", "danger")
            return redirect(url_for("index"))
        return fn(*a, **k)
    return wrapper


# =========================
# HELPERS
# =========================

def clean_float(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def generate_material_id():
    last = Material.query.order_by(Material.id.desc()).first()
    if not last:
        return "JZ00001"

    try:
        num = int(str(last.material_id).replace("JZ", ""))
    except Exception:
        num = last.id

    return f"JZ{num + 1:05d}"


def read_excel(file):
    name = file.filename.lower()

    if name.endswith(".xlsx"):
        return pd.read_excel(file, engine="openpyxl")

    if name.endswith(".xls"):
        return pd.read_excel(file, engine="xlrd")

    raise Exception("Použij .xls nebo .xlsx")


def create_initial_users():
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("admin"),
            role="admin"
        ))

    if not User.query.filter_by(username="user").first():
        db.session.add(User(
            username="user",
            password_hash=generate_password_hash("user"),
            role="user"
        ))

    db.session.commit()


# =========================
# ROUTES
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()

        if u and check_password_hash(u.password_hash, request.form["password"]):
            session["user_id"] = u.id
            return redirect(url_for("index"))

        flash("Špatné přihlášení")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    query = Material.query

    if q:
        query = query.filter(
            Material.material_id.contains(q) |
            Material.manufacturer_id.contains(q) |
            Material.name.contains(q)
        )

    materials = query.order_by(Material.material_id.asc()).all()
    return render_template("index.html", materials=materials, items=materials, q=q)


@app.route("/add", methods=["GET", "POST"])
@login_required
@admin_required
def add_material():
    if request.method == "POST":
        m = Material(
            material_id=generate_material_id(),
            manufacturer_id=request.form.get("manufacturer_id") or "",
            name=request.form.get("name") or "",
            quantity=clean_float(request.form.get("quantity")),
            unit=request.form.get("unit") or "",
            price_without_vat=clean_float(request.form.get("price"))
        )
        db.session.add(m)
        db.session.commit()
        return redirect(url_for("index"))

    return render_template("form.html")


@app.route("/card/<int:item_id>")
@login_required
def stock_card(item_id):
    material = Material.query.get_or_404(item_id)
    movements = StockMovement.query.filter_by(
        material_id=material.id
    ).order_by(StockMovement.created_at.desc()).all()

    return render_template(
        "card.html",
        material=material,
        item=material,
        movements=movements
    )


@app.route("/movement/<int:item_id>", methods=["GET", "POST"])
@login_required
def movement(item_id):
    m = Material.query.get_or_404(item_id)

    if request.method == "POST":
        qty = clean_float(request.form.get("quantity"))
        typ = request.form.get("type")
        order_number = request.form.get("order_number") or ""

        if qty <= 0:
            flash("Množství musí být větší než 0")
            return redirect(url_for("movement", item_id=item_id))

        if typ == "in":
            m.quantity += qty
        elif typ == "out":
            if qty > m.quantity:
                flash("Nedostatek na skladě")
                return redirect(url_for("movement", item_id=item_id))
            m.quantity -= qty
        else:
            flash("Neplatný typ pohybu")
            return redirect(url_for("movement", item_id=item_id))

        db.session.add(StockMovement(
            material_id=m.id,
            movement_type=typ,
            quantity=qty,
            username=current_user().username,
            order_number=order_number
        ))

        db.session.commit()
        return redirect(url_for("stock_card", item_id=item_id))

    return render_template("movement.html", material=m, item=m)


@app.route("/move/<int:item_id>", methods=["GET", "POST"])
@login_required
def move_item(item_id):
    return movement(item_id)


@app.route("/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
@admin_required
def edit_item(item_id):
    material = Material.query.get_or_404(item_id)

    if request.method == "POST":
        material.manufacturer_id = request.form.get("manufacturer_id") or ""
        material.name = request.form.get("name") or ""
        material.quantity = clean_float(request.form.get("quantity"))
        material.unit = request.form.get("unit") or ""
        material.price_without_vat = clean_float(request.form.get("price"))

        db.session.commit()
        return redirect(url_for("index"))

    return render_template("form.html", material=material, item=material)


@app.route("/delete/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def delete_item(item_id):
    material = Material.query.get_or_404(item_id)

    StockMovement.query.filter_by(material_id=material.id).delete()
    db.session.delete(material)
    db.session.commit()

    return redirect(url_for("index"))


@app.route("/qr/<int:item_id>")
@login_required
def qr_code(item_id):
    url = request.host_url.rstrip("/") + url_for("movement", item_id=item_id)

    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return send_file(buf, mimetype="image/png")


@app.route("/export")
@login_required
@admin_required
def export_excel():
    data = [{
        "ID Materiálu": m.material_id,
        "ID výrobce": m.manufacturer_id or "",
        "Název": m.name or "",
        "Množství": m.quantity or 0,
        "Mn.j.": m.unit or "",
        "Cena bez DPH": m.price_without_vat or 0
    } for m in Material.query.order_by(Material.material_id.asc()).all()]

    df = pd.DataFrame(data)

    buf = BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)

    return send_file(
        buf,
        download_name="export.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/import", methods=["GET", "POST"])
@login_required
@admin_required
def import_excel():
    if request.method == "GET":
        return """
        <h2>Import Excel</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".xls,.xlsx" required>
            <button type="submit">Importovat</button>
        </form>
        <p><a href="/">Zpět</a></p>
        """

    file = request.files.get("file")

    if not file or file.filename == "":
        flash("Vyber soubor")
        return redirect(url_for("index"))

    try:
        df = read_excel(file)
    except Exception as e:
        flash(str(e))
        return redirect(url_for("index"))

    imported = 0
    updated = 0
    skipped = 0

    for _, row in df.iterrows():
        name = str(row.get("Název", "") or "").strip()

        if not name:
            skipped += 1
            continue

        material_id = str(row.get("ID Materiálu", "") or "").strip()

        if not material_id:
            material_id = generate_material_id()

        existing = Material.query.filter_by(material_id=material_id).first()

        if existing:
            existing.manufacturer_id = str(row.get("ID výrobce", "") or "").strip()
            existing.name = name
            existing.quantity = clean_float(row.get("Množství"))
            existing.unit = str(row.get("Mn.j.", "") or "").strip()
            existing.price_without_vat = clean_float(row.get("Cena bez DPH"))
            updated += 1
        else:
            db.session.add(Material(
                material_id=material_id,
                manufacturer_id=str(row.get("ID výrobce", "") or "").strip(),
                name=name,
                quantity=clean_float(row.get("Množství")),
                unit=str(row.get("Mn.j.", "") or "").strip(),
                price_without_vat=clean_float(row.get("Cena bez DPH"))
            ))
            imported += 1

    db.session.commit()
    flash(f"Import hotov. Přidáno: {imported}, aktualizováno: {updated}, přeskočeno: {skipped}")
    return redirect(url_for("index"))


# =========================
# INIT
# =========================

with app.app_context():
    db.create_all()
    create_initial_users()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)