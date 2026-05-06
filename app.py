import os
import re
from functools import wraps
from io import BytesIO
from datetime import datetime

import pandas as pd
import qrcode
from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "zmen_toto_tajne_heslo")

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

# Unicode font pro PDF výstupy s českou diakritikou.
PDF_FONT_NAME = "Helvetica"
try:
    PDF_FONT_PATH = os.environ.get("PDF_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", PDF_FONT_PATH))
    PDF_FONT_NAME = "DejaVuSans"
except Exception:
    # Lokálně na Windows nemusí cesta existovat; PDF se v tom případě vytvoří s implicitním fontem.
    PDF_FONT_NAME = "Helvetica"



# =========================
# MODELY
# =========================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")


class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.String(20), unique=True, nullable=False)
    manufacturer_id = db.Column(db.String(100), default="")
    name = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, default=0)
    unit = db.Column(db.String(50), default="")
    storage_position = db.Column(db.String(100), default="")
    price_without_vat = db.Column(db.Float, default=0)


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey("material.id"), nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)  # prijem / vydej
    quantity = db.Column(db.Float, nullable=False)
    order_number = db.Column(db.String(100), default="")
    username = db.Column(db.String(80), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    material = db.relationship("Material", backref="movements")


class JobOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="active")
    created_at = db.Column(db.DateTime, default=datetime.now)
    closed_at = db.Column(db.DateTime)


class IssueSlip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slip_number = db.Column(db.String(30), unique=True, nullable=False)
    order_number = db.Column(db.String(30), nullable=False)
    order_name = db.Column(db.String(255), default="")
    username = db.Column(db.String(80), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)


class IssueSlipItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    issue_slip_id = db.Column(db.Integer, db.ForeignKey("issue_slip.id"), nullable=False)
    material_id_db = db.Column(db.Integer)
    material_code = db.Column(db.String(30), default="")
    manufacturer_id = db.Column(db.String(100), default="")
    material_name = db.Column(db.String(255), default="")
    quantity = db.Column(db.Float, default=0)
    unit = db.Column(db.String(50), default="")
    storage_position = db.Column(db.String(100), default="")

    issue_slip = db.relationship("IssueSlip", backref="items")


class ReceiptSlip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(30), unique=True, nullable=False)
    source_type = db.Column(db.String(30), nullable=False, default="new_purchase")
    order_number = db.Column(db.String(30), default="")
    order_name = db.Column(db.String(255), default="")
    username = db.Column(db.String(80), default="")
    created_at = db.Column(db.DateTime, default=datetime.now)


class ReceiptSlipItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    receipt_slip_id = db.Column(db.Integer, db.ForeignKey("receipt_slip.id"), nullable=False)
    material_id_db = db.Column(db.Integer)
    material_code = db.Column(db.String(30), default="")
    manufacturer_id = db.Column(db.String(100), default="")
    material_name = db.Column(db.String(255), default="")
    quantity = db.Column(db.Float, default=0)
    unit = db.Column(db.String(50), default="")
    storage_position = db.Column(db.String(100), default="")

    receipt_slip = db.relationship("ReceiptSlip", backref="items")



class InventorySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="active")  # active / done
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)

    user = db.relationship("User", backref="inventory_sessions")


class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inventory_session_id = db.Column(db.Integer, db.ForeignKey("inventory_session.id"), nullable=False)
    material_id_db = db.Column(db.Integer, db.ForeignKey("material.id"), nullable=False)
    material_code = db.Column(db.String(30), default="")
    manufacturer_id = db.Column(db.String(100), default="")
    material_name = db.Column(db.String(255), default="")
    system_qty = db.Column(db.Float, default=0)
    real_qty = db.Column(db.Float, default=0)
    unit = db.Column(db.String(50), default="")
    storage_position = db.Column(db.String(100), default="")
    username = db.Column(db.String(80), default="")
    scanned_at = db.Column(db.DateTime, default=datetime.now)

    inventory_session = db.relationship("InventorySession", backref="items")
    material = db.relationship("Material")


# =========================
# AUTH
# =========================

def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.context_processor
def inject_user():
    user = current_user()
    active_inventory = None
    if user:
        active_inventory = InventorySession.query.filter_by(user_id=user.id, status="active").first()
    return {
        "current_user": user,
        "active_inventory": active_inventory,
    }


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user.role != "admin":
            flash("Nemáš oprávnění pro tuto akci.", "danger")
            return redirect(url_for("index"))
        return fn(*args, **kwargs)
    return wrapper


# =========================
# POMOCNÉ FUNKCE
# =========================

def clean_float(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).strip().replace(" ", "").replace(",", "."))
    except Exception:
        return 0.0


def generate_material_id():
    max_number = 0
    for material in Material.query.all():
        code = str(material.material_id or "").upper().strip()
        if code.startswith("JZ"):
            try:
                max_number = max(max_number, int(code.replace("JZ", "")))
            except Exception:
                pass
    return f"JZ{max_number + 1:05d}"



def generate_order_number():
    prefix = datetime.now().strftime("%Y%m%d")
    count = JobOrder.query.filter(JobOrder.order_number.startswith(prefix)).count() + 1
    return f"{prefix}{count:02d}"


def generate_issue_slip_number():
    prefix = "VYD" + datetime.now().strftime("%Y%m%d")
    count = IssueSlip.query.filter(IssueSlip.slip_number.startswith(prefix)).count() + 1
    return f"{prefix}{count:02d}"


def generate_receipt_slip_number():
    prefix = "PRI" + datetime.now().strftime("%Y%m%d")
    count = ReceiptSlip.query.filter(ReceiptSlip.receipt_number.startswith(prefix)).count() + 1
    return f"{prefix}{count:02d}"


def material_from_scan(value):
    value = (value or "").strip()
    if not value:
        return None

    # Podpora QR formátu MAT:JZ00001 i čistého JZ00001.
    if value.upper().startswith("MAT:"):
        value = value.split(":", 1)[1].strip()

    match = re.search(r"/(?:movement|qr)/(\d+)", value)
    if match:
        return db.session.get(Material, int(match.group(1)))

    if value.isdigit():
        material = db.session.get(Material, int(value))
        if material:
            return material

    return Material.query.filter_by(material_id=value).first()



def get_active_inventory(user_id):
    return InventorySession.query.filter_by(user_id=user_id, status="active").first()


def inventory_difference(item):
    return clean_float(item.real_qty) - clean_float(item.system_qty)


def build_inventory_pdf(inventory):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm
    )

    styles = getSampleStyleSheet()
    for style_name in ["Title", "Normal", "BodyText"]:
        styles[style_name].fontName = PDF_FONT_NAME

    title = f"Inventura č. {inventory.id}"
    worker = inventory.user.username if inventory.user else ""

    story = [
        Paragraph(title, styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Název: {inventory.name or ''}", styles["Normal"]),
        Paragraph(f"Pracovník: {worker}", styles["Normal"]),
        Paragraph(f"Stav: {'Dokončená' if inventory.status == 'done' else 'Aktivní'}", styles["Normal"]),
        Paragraph(f"Vytvořeno: {inventory.created_at.strftime('%d.%m.%Y %H:%M') if inventory.created_at else ''}", styles["Normal"]),
        Paragraph(f"Dokončeno: {inventory.finished_at.strftime('%d.%m.%Y %H:%M') if inventory.finished_at else ''}", styles["Normal"]),
        Spacer(1, 12)
    ]

    data = [["ID Materiálu", "ID výrobce", "Název", "Skladová pozice", "Systém", "Realita", "Rozdíl", "Mn.j."]]
    for item in inventory.items:
        diff = inventory_difference(item)
        data.append([
            item.material_code or "",
            item.manufacturer_id or "",
            Paragraph(item.material_name or "", styles["BodyText"]),
            item.storage_position or "",
            str(item.system_qty or 0),
            str(item.real_qty or 0),
            str(diff),
            item.unit or "",
        ])

    table = Table(data, colWidths=[24 * mm, 28 * mm, 50 * mm, 28 * mm, 18 * mm, 18 * mm, 18 * mm, 12 * mm], repeatRows=1)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT_NAME),
        ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_NAME),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    for row_index, item in enumerate(inventory.items, start=1):
        if abs(inventory_difference(item)) > 0.000001:
            table_style.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.Color(1, 0.92, 0.92)))

    table.setStyle(TableStyle(table_style))
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer

def build_issue_pdf(slip, include_qr=False, base_url=""):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm
    )
    styles = getSampleStyleSheet()
    for style_name in ["Title", "Normal", "BodyText"]:
        styles[style_name].fontName = PDF_FONT_NAME
    story = [
        Paragraph(f"Výdejka č. {slip.slip_number}", styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Zakázka: {slip.order_number} - {slip.order_name}", styles["Normal"]),
        Paragraph(f"Vydal: {slip.username}", styles["Normal"]),
        Paragraph(f"Datum: {slip.created_at.strftime('%d.%m.%Y %H:%M')}", styles["Normal"]),
        Spacer(1, 12)
    ]

    if include_qr:
        data = [["QR", "ID Materiálu", "ID výrobce", "Název", "Skladová pozice", "Množství", "Mn.j."]]
    else:
        data = [["ID Materiálu", "ID výrobce", "Název", "Skladová pozice", "Množství", "Mn.j."]]

    qr_buffers = []

    for item in slip.items:
        if include_qr:
            # QR ve výdejce obsahuje přímo ID materiálu, např. JZ00001.
            # Díky tomu jde QR použít univerzálně ve skladu i mimo konkrétní URL aplikace.
            qr_data = item.material_code or ""

            qr_img = qrcode.make(qr_data)
            qr_buffer = BytesIO()
            qr_img.save(qr_buffer, format="PNG")
            qr_buffer.seek(0)
            qr_buffers.append(qr_buffer)

            data.append([
                Image(qr_buffer, width=22 * mm, height=22 * mm),
                item.material_code or "",
                item.manufacturer_id or "",
                Paragraph(item.material_name or "", styles["BodyText"]),
                item.storage_position or "",
                str(item.quantity or 0),
                item.unit or ""
            ])
        else:
            data.append([
                item.material_code or "",
                item.manufacturer_id or "",
                item.material_name or "",
                item.storage_position or "",
                str(item.quantity or 0),
                item.unit or ""
            ])

    if include_qr:
        col_widths = [22 * mm, 24 * mm, 28 * mm, 55 * mm, 30 * mm, 22 * mm, 14 * mm]
    else:
        col_widths = [28 * mm, 32 * mm, 62 * mm, 35 * mm, 22 * mm, 16 * mm]

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT_NAME),
        ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_NAME),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
    ]))
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer


def build_receipt_pdf(slip):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )
    styles = getSampleStyleSheet()
    for style_name in ["Title", "Normal", "BodyText"]:
        styles[style_name].fontName = PDF_FONT_NAME

    if slip.source_type == "return_from_order":
        source_text = f"Vratka ze zakázky: {slip.order_number} - {slip.order_name}"
    else:
        source_text = "Nově pořízený materiál"

    story = [
        Paragraph(f"Příjemka č. {slip.receipt_number}", styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Typ příjmu: {source_text}", styles["Normal"]),
        Paragraph(f"Přijal: {slip.username}", styles["Normal"]),
        Paragraph(f"Datum: {slip.created_at.strftime('%d.%m.%Y %H:%M')}", styles["Normal"]),
        Spacer(1, 12)
    ]

    data = [["ID Materiálu", "ID výrobce", "Název", "Skladová pozice", "Množství", "Mn.j."]]
    for item in slip.items:
        data.append([
            item.material_code or "",
            item.manufacturer_id or "",
            item.material_name or "",
            item.storage_position or "",
            str(item.quantity or 0),
            item.unit or ""
        ])

    table = Table(data, colWidths=[28 * mm, 32 * mm, 62 * mm, 35 * mm, 22 * mm, 16 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT_NAME),
        ("FONTNAME", (0, 0), (-1, 0), PDF_FONT_NAME),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer


def create_initial_users():
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("admin123"),
            role="admin"
        ))

    if not User.query.filter_by(username="user").first():
        db.session.add(User(
            username="user",
            password_hash=generate_password_hash("user123"),
            role="user"
        ))

    db.session.commit()


def ensure_db_columns():
    """Lehká migrace pro starší lokální DB."""
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    if "material" in tables:
        cols = [c["name"] for c in inspector.get_columns("material")]
        if "manufacturer_id" not in cols:
            db.session.execute(text("ALTER TABLE material ADD COLUMN manufacturer_id VARCHAR(100) DEFAULT ''"))
        if "unit" not in cols:
            db.session.execute(text("ALTER TABLE material ADD COLUMN unit VARCHAR(50) DEFAULT ''"))
        if "storage_position" not in cols:
            db.session.execute(text("ALTER TABLE material ADD COLUMN storage_position VARCHAR(100) DEFAULT ''"))
        if "price_without_vat" not in cols:
            db.session.execute(text("ALTER TABLE material ADD COLUMN price_without_vat FLOAT DEFAULT 0"))

    if "stock_movement" in tables:
        cols = [c["name"] for c in inspector.get_columns("stock_movement")]
        if "order_number" not in cols:
            db.session.execute(text("ALTER TABLE stock_movement ADD COLUMN order_number VARCHAR(100) DEFAULT ''"))
        if "username" not in cols:
            db.session.execute(text("ALTER TABLE stock_movement ADD COLUMN username VARCHAR(80) DEFAULT ''"))

    if "job_order" in tables:
        cols = [c["name"] for c in inspector.get_columns("job_order")]
        if "status" not in cols:
            db.session.execute(text("ALTER TABLE job_order ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))
        if "closed_at" not in cols:
            db.session.execute(text("ALTER TABLE job_order ADD COLUMN closed_at TIMESTAMP"))

    if "issue_slip_item" in tables:
        cols = [c["name"] for c in inspector.get_columns("issue_slip_item")]
        if "storage_position" not in cols:
            db.session.execute(text("ALTER TABLE issue_slip_item ADD COLUMN storage_position VARCHAR(100) DEFAULT ''"))

    if "receipt_slip_item" in tables:
        cols = [c["name"] for c in inspector.get_columns("receipt_slip_item")]
        if "storage_position" not in cols:
            db.session.execute(text("ALTER TABLE receipt_slip_item ADD COLUMN storage_position VARCHAR(100) DEFAULT ''"))

    if "inventory_item" in tables:
        cols = [c["name"] for c in inspector.get_columns("inventory_item")]
        if "storage_position" not in cols:
            db.session.execute(text("ALTER TABLE inventory_item ADD COLUMN storage_position VARCHAR(100) DEFAULT ''"))

    db.session.commit()


def normalize_column_name(col):
    text = str(col).strip().lower()
    replacements = {
        "id materialu": "id materiálu",
        "material id": "id materiálu",
        "id vyrobce": "id výrobce",
        "vyrobce": "id výrobce",
        "výrobce": "id výrobce",
        "nazev": "název",
        "nazev materialu": "název",
        "název materiálu": "název",
        "mnozstvi": "množství",
        "mj": "mn.j.",
        "m.j.": "mn.j.",
        "mn.j": "mn.j.",
        "mn. j.": "mn.j.",
        "jednotka": "mn.j.",
        "pozice": "skladová pozice",
        "skladova pozice": "skladová pozice",
        "skladove misto": "skladová pozice",
        "skladové místo": "skladová pozice",
        "umisteni": "skladová pozice",
        "umístění": "skladová pozice",
        "lokace": "skladová pozice",
        "cena": "cena bez dph",
        "pořizovací cena bez dph": "cena bez dph",
        "porizovaci cena bez dph": "cena bez dph",
    }
    return replacements.get(text, text)


def normalize_dataframe_columns(df):
    df = df.rename(columns={c: normalize_column_name(c) for c in df.columns})
    return df.rename(columns={
        "id materiálu": "ID Materiálu",
        "id výrobce": "ID výrobce",
        "název": "Název",
        "množství": "Množství",
        "mn.j.": "Mn.j.",
        "skladová pozice": "Skladová pozice",
        "cena bez dph": "Cena bez DPH",
    })


def read_excel_smart(file):
    filename = file.filename.lower()
    if filename.endswith(".xlsx"):
        return pd.read_excel(file, engine="openpyxl")
    if filename.endswith(".xls"):
        return pd.read_excel(file, engine="xlrd")
    raise ValueError("Nepodporovaný formát souboru. Použij .xlsx nebo .xls.")


# =========================
# ROUTES
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash("Přihlášení proběhlo úspěšně.", "success")
            return redirect(url_for("index"))

        flash("Špatné jméno nebo heslo.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Byl jsi odhlášen.", "info")
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
        item = Material(
            material_id=generate_material_id(),
            manufacturer_id=request.form.get("manufacturer_id") or "",
            name=request.form.get("name") or "",
            quantity=clean_float(request.form.get("quantity")),
            unit=request.form.get("unit") or "",
            storage_position=request.form.get("storage_position") or "",
            price_without_vat=clean_float(request.form.get("price_without_vat")),
        )
        db.session.add(item)
        db.session.commit()
        flash("Materiál byl přidán.", "success")
        return redirect(url_for("index"))

    return render_template("form.html", item=None, material=None, title="Přidat materiál")


# kompatibilita se starší šablonou
@app.route("/add-item", methods=["GET", "POST"])
@login_required
@admin_required
def add_item():
    return add_material()


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
        material.storage_position = request.form.get("storage_position") or ""
        material.price_without_vat = clean_float(request.form.get("price_without_vat"))
        db.session.commit()
        flash("Materiál byl upraven.", "success")
        return redirect(url_for("index"))

    return render_template("form.html", item=material, material=material, title="Upravit materiál")


@app.route("/delete/<int:item_id>", methods=["POST"])
@login_required
@admin_required
def delete_item(item_id):
    material = Material.query.get_or_404(item_id)
    StockMovement.query.filter_by(material_id=material.id).delete()
    db.session.delete(material)
    db.session.commit()
    flash("Materiál byl smazán.", "info")
    return redirect(url_for("index"))


@app.route("/card/<int:item_id>")
@login_required
def stock_card(item_id):
    material = Material.query.get_or_404(item_id)
    movements = StockMovement.query.filter_by(material_id=material.id).order_by(StockMovement.created_at.desc()).all()
    return render_template("card.html", material=material, item=material, movements=movements)


@app.route("/movement/<int:item_id>", methods=["GET", "POST"])
@login_required
def movement(item_id):
    material = Material.query.get_or_404(item_id)
    user = current_user()

    if request.method == "POST":
        movement_type = request.form.get("movement_type") or request.form.get("type")
        quantity = clean_float(request.form.get("quantity"))
        issued_to = request.form.get("issued_to") or "internal"
        order_number = ""

        if movement_type == "in":
            movement_type = "prijem"
        if movement_type == "out":
            movement_type = "vydej"

        if quantity <= 0:
            flash("Množství musí být větší než 0.", "danger")
            return redirect(url_for("movement", item_id=item_id))

        if movement_type == "prijem":
            material.quantity += quantity

        elif movement_type == "vydej":
            if issued_to == "internal":
                order_number = "Vlastní spotřeba"
            elif issued_to == "existing":
                order_number = request.form.get("order_number") or ""
            elif issued_to == "new":
                order_name = request.form.get("new_order_name") or "Nová zakázka"
                order_number = generate_order_number()
                db.session.add(JobOrder(order_number=order_number, name=order_name, status="active"))

            if quantity > material.quantity:
                flash("Nelze vyskladnit více, než je skladem.", "danger")
                return redirect(url_for("movement", item_id=item_id))
            material.quantity -= quantity
        else:
            flash("Neplatný typ pohybu.", "danger")
            return redirect(url_for("movement", item_id=item_id))

        db.session.add(StockMovement(
            material_id=material.id,
            movement_type=movement_type,
            quantity=quantity,
            order_number=order_number,
            username=user.username if user else ""
        ))
        db.session.commit()
        flash("Pohyb byl uložen.", "success")
        return redirect(url_for("stock_card", item_id=item_id))

    orders = JobOrder.query.filter_by(status="active").order_by(JobOrder.created_at.desc()).all()
    return render_template("move.html", item=material, material=material, orders=orders)

@app.route("/api/material_lookup")
@login_required
def material_lookup():
    q = request.args.get("q", "")

    material = material_from_scan(q)

    if not material:
        return {"found": False}

    return {
        "found": True,
        "name": material.name,
        "material_id": material.material_id,
        "storage_position": material.storage_position or ""
    }


@app.route("/move/<int:item_id>", methods=["GET", "POST"])
@login_required
def move_item(item_id):
    return movement(item_id)


@app.route("/qr/<int:item_id>")
@login_required
def qr_code(item_id):
    material = Material.query.get_or_404(item_id)
    qr_url = request.host_url.rstrip("/") + url_for("movement", item_id=material.id)
    img = qrcode.make(qr_url)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


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
        "Skladová pozice": m.storage_position or "",
        "Cena bez DPH": m.price_without_vat or 0,
    } for m in Material.query.order_by(Material.material_id.asc()).all()]

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data).to_excel(writer, index=False, sheet_name="Sklad")
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="sklad_export.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/template")
@login_required
@admin_required
def import_template():
    df = pd.DataFrame(columns=["ID Materiálu", "ID výrobce", "Název", "Množství", "Mn.j.", "Skladová pozice", "Cena bez DPH"])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sklad")
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="sablona_importu.xlsx")


@app.route("/import", methods=["GET", "POST"])
@login_required
@admin_required
def import_excel():
    if request.method == "GET":
        return render_template("import.html")

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Nebyl vybrán žádný soubor.", "danger")
        return redirect(url_for("index"))

    try:
        df = normalize_dataframe_columns(read_excel_smart(file))
    except Exception as e:
        flash(str(e), "danger")
        return redirect(url_for("index"))

    required_columns = ["ID výrobce", "Název", "Množství", "Mn.j.", "Cena bez DPH"]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        flash("V Excelu chybí sloupce: " + ", ".join(missing), "danger")
        return redirect(url_for("index"))

    if "ID Materiálu" not in df.columns:
        df["ID Materiálu"] = ""

    imported = updated = skipped = 0
    for _, row in df.iterrows():
        name = "" if pd.isna(row.get("Název")) else str(row.get("Název")).strip()
        if not name:
            skipped += 1
            continue

        material_id = "" if pd.isna(row.get("ID Materiálu")) else str(row.get("ID Materiálu")).strip()
        if not material_id:
            material_id = generate_material_id()

        existing = Material.query.filter_by(material_id=material_id).first()
        values = {
            "manufacturer_id": "" if pd.isna(row.get("ID výrobce")) else str(row.get("ID výrobce")).strip(),
            "name": name,
            "quantity": clean_float(row.get("Množství")),
            "unit": "" if pd.isna(row.get("Mn.j.")) else str(row.get("Mn.j.")).strip(),
            "storage_position": "" if pd.isna(row.get("Skladová pozice")) else str(row.get("Skladová pozice")).strip(),
            "price_without_vat": clean_float(row.get("Cena bez DPH")),
        }

        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
        else:
            db.session.add(Material(material_id=material_id, **values))
            imported += 1

    db.session.commit()
    flash(f"Import dokončen. Přidáno: {imported}, aktualizováno: {updated}, přeskočeno: {skipped}.", "success")
    return redirect(url_for("index"))



# =========================
# ZAKÁZKY - ADMIN
# =========================

@app.route("/orders")
@login_required
@admin_required
def orders():
    orders = JobOrder.query.order_by(JobOrder.created_at.desc()).all()
    return render_template("orders.html", orders=orders)


@app.route("/orders/new", methods=["GET", "POST"])
@login_required
@admin_required
def order_new():
    if request.method == "POST":
        name = request.form.get("name") or "Nová zakázka"
        order = JobOrder(order_number=generate_order_number(), name=name, status="active")
        db.session.add(order)
        db.session.commit()
        flash(f"Zakázka {order.order_number} byla založena.", "success")
        return redirect(url_for("orders"))

    return render_template("order_form.html")


@app.route("/orders/<int:order_id>/close", methods=["POST"])
@login_required
@admin_required
def order_close(order_id):
    order = JobOrder.query.get_or_404(order_id)
    if order.status == "closed":
        flash("Zakázka už je ukončená.", "warning")
        return redirect(url_for("orders"))

    order.status = "closed"
    order.closed_at = datetime.now()
    db.session.commit()
    flash(f"Zakázka {order.order_number} byla ukončena.", "success")
    return redirect(url_for("orders"))


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
@admin_required
def order_delete(order_id):
    order = JobOrder.query.get_or_404(order_id)

    used = StockMovement.query.filter_by(order_number=order.order_number).first()
    issue = IssueSlip.query.filter_by(order_number=order.order_number).first()
    if used or issue:
        flash("Zakázku nelze smazat, protože už má pohyby nebo výdejky. Můžeš ji pouze ukončit.", "danger")
        return redirect(url_for("orders"))

    db.session.delete(order)
    db.session.commit()
    flash("Zakázka byla smazána.", "success")
    return redirect(url_for("orders"))


# =========================
# VÝDEJKA DO ZAKÁZKY
# =========================


@app.route("/issues")
@login_required
@admin_required
def issue_slip_list():
    selected_order = request.args.get("order_number", "").strip()

    query = IssueSlip.query

    if selected_order:
        query = query.filter_by(order_number=selected_order)

    slips = query.order_by(IssueSlip.created_at.desc()).all()
    orders = JobOrder.query.order_by(JobOrder.created_at.desc()).all()

    return render_template(
        "issue_list.html",
        slips=slips,
        orders=orders,
        selected_order=selected_order
    )

@app.route("/issue", methods=["GET", "POST"])
@login_required
def issue_slip_new():
    if request.method == "POST":
        order_number = request.form.get("order_number") or ""
        order = JobOrder.query.filter_by(order_number=order_number, status="active").first()
        if not order:
            flash("Vyber existující aktivní zakázku.", "danger")
            return redirect(url_for("issue_slip_new"))

        session["issue_order_number"] = order.order_number
        session["issue_order_name"] = order.name
        session["issue_items"] = []
        return redirect(url_for("issue_slip_edit"))

    orders = JobOrder.query.filter_by(status="active").order_by(JobOrder.created_at.desc()).all()
    return render_template("issue_new.html", orders=orders)


@app.route("/issue/edit", methods=["GET", "POST"])
@login_required
def issue_slip_edit():
    order_number = session.get("issue_order_number")
    order_name = session.get("issue_order_name")
    items = list(session.get("issue_items", []))

    if not order_number:
        flash("Nejprve vyber zakázku.", "warning")
        return redirect(url_for("issue_slip_new"))

    if request.method == "POST":
        material = material_from_scan(request.form.get("scan_value"))
        quantity = clean_float(request.form.get("quantity"))

        if not material:
            flash("Materiál nebyl nalezen.", "danger")
            return redirect(url_for("issue_slip_edit"))

        if quantity <= 0:
            flash("Množství musí být větší než 0.", "danger")
            return redirect(url_for("issue_slip_edit"))

        already_in_slip = sum(
            clean_float(item.get("quantity"))
            for item in items
            if item.get("material_db_id") == material.id
        )

        if already_in_slip + quantity > material.quantity:
            flash(
                f"Nedostatek na skladě: {material.material_id} má skladem {material.quantity} {material.unit}, "
                f"ve výdejce už je {already_in_slip} {material.unit}.",
                "danger"
            )
            return redirect(url_for("issue_slip_edit"))

        existing_item = next((item for item in items if item.get("material_db_id") == material.id), None)

        if existing_item:
            existing_item["quantity"] = clean_float(existing_item.get("quantity")) + quantity
        else:
            items.append({
                "material_db_id": material.id,
                "material_code": material.material_id,
                "manufacturer_id": material.manufacturer_id or "",
                "name": material.name or "",
                "quantity": quantity,
                "unit": material.unit or "",
                "storage_position": material.storage_position or ""
            })

        session["issue_items"] = items
        session.modified = True

        flash("Položka přidána do výdejky.", "success")
        return redirect(url_for("issue_slip_edit"))

    return render_template("issue_edit.html", order_number=order_number, order_name=order_name, items=items)


@app.route("/api/issue/add", methods=["POST"])
@login_required
def api_issue_add():
    order_number = session.get("issue_order_number")
    if not order_number:
        return {"ok": False, "message": "Nejprve vyber zakázku."}, 400

    material = material_from_scan(request.form.get("scan_value"))
    quantity = clean_float(request.form.get("quantity"))
    items = list(session.get("issue_items", []))

    if not material:
        return {"ok": False, "message": "Materiál nebyl nalezen."}, 404

    if quantity <= 0:
        return {"ok": False, "message": "Množství musí být větší než 0."}, 400

    already_in_slip = sum(
        clean_float(item.get("quantity"))
        for item in items
        if item.get("material_db_id") == material.id
    )

    if already_in_slip + quantity > material.quantity:
        return {
            "ok": False,
            "message": (
                f"Nedostatek na skladě: {material.material_id} má skladem {material.quantity} {material.unit}, "
                f"ve výdejce už je {already_in_slip} {material.unit}."
            )
        }, 400

    existing_item = next((item for item in items if item.get("material_db_id") == material.id), None)

    if existing_item:
        existing_item["quantity"] = clean_float(existing_item.get("quantity")) + quantity
    else:
        items.append({
            "material_db_id": material.id,
            "material_code": material.material_id,
            "manufacturer_id": material.manufacturer_id or "",
            "name": material.name or "",
            "quantity": quantity,
            "unit": material.unit or "",
            "storage_position": material.storage_position or ""
        })

    session["issue_items"] = items
    session.modified = True

    return {
        "ok": True,
        "message": "Položka přidána do výdejky.",
        "items": items,
        "material": {
            "material_id": material.material_id,
            "name": material.name,
            "quantity": material.quantity,
            "unit": material.unit
        }
    }



@app.route("/issue/remove/<int:index>", methods=["POST"])
@login_required
def issue_slip_remove(index):
    items = session.get("issue_items", [])
    if 0 <= index < len(items):
        items.pop(index)
        session["issue_items"] = items
    return redirect(url_for("issue_slip_edit"))


@app.route("/issue/confirm", methods=["POST"])
@login_required
def issue_slip_confirm():
    order_number = session.get("issue_order_number")
    order_name = session.get("issue_order_name")
    items = session.get("issue_items", [])

    if not order_number or not items:
        flash("Výdejka nemá zakázku nebo položky.", "danger")
        return redirect(url_for("issue_slip_new"))

    order = JobOrder.query.filter_by(order_number=order_number, status="active").first()
    if not order:
        flash("Zakázka už není aktivní.", "danger")
        return redirect(url_for("issue_slip_new"))

    for item in items:
        material = db.session.get(Material, item["material_db_id"])
        if not material or item["quantity"] > material.quantity:
            flash(f"Nedostatek nebo chybějící materiál {item['material_code']}", "danger")
            return redirect(url_for("issue_slip_edit"))

    slip = IssueSlip(
        slip_number=generate_issue_slip_number(),
        order_number=order_number,
        order_name=order_name,
        username=current_user().username
    )
    db.session.add(slip)
    db.session.flush()

    for item in items:
        material = db.session.get(Material, item["material_db_id"])
        quantity = clean_float(item["quantity"])
        material.quantity -= quantity

        db.session.add(StockMovement(
            material_id=material.id,
            movement_type="vydej",
            quantity=quantity,
            username=current_user().username,
            order_number=order_number
        ))

        db.session.add(IssueSlipItem(
            issue_slip_id=slip.id,
            material_id_db=material.id,
            material_code=material.material_id,
            manufacturer_id=material.manufacturer_id or "",
            material_name=material.name or "",
            quantity=quantity,
            unit=material.unit or "",
            storage_position=material.storage_position or ""
        ))

    db.session.commit()
    session.pop("issue_order_number", None)
    session.pop("issue_order_name", None)
    session.pop("issue_items", None)

    flash(f"Výdejka {slip.slip_number} vytvořena.", "success")
    return redirect(url_for("issue_slip_detail", slip_id=slip.id))


@app.route("/issue/<int:slip_id>")
@login_required
def issue_slip_detail(slip_id):
    slip = IssueSlip.query.get_or_404(slip_id)
    return render_template("issue_detail.html", slip=slip)


@app.route("/issue/<int:slip_id>/pdf")
@login_required
def issue_slip_pdf(slip_id):
    slip = IssueSlip.query.get_or_404(slip_id)
    pdf = build_issue_pdf(slip, include_qr=False, base_url=request.host_url)
    return send_file(pdf, download_name=f"{slip.slip_number}.pdf", as_attachment=True, mimetype="application/pdf")


@app.route("/issue/<int:slip_id>/pdf_qr")
@login_required
def issue_slip_pdf_qr(slip_id):
    slip = IssueSlip.query.get_or_404(slip_id)
    pdf = build_issue_pdf(slip, include_qr=True, base_url=request.host_url)
    return send_file(pdf, download_name=f"{slip.slip_number}_QR.pdf", as_attachment=True, mimetype="application/pdf")


# =========================
# PŘÍJEMKA NA SKLAD
# =========================

@app.route("/receipts")
@login_required
@admin_required
def receipt_slip_list():
    selected_order = request.args.get("order_number", "").strip()
    selected_source = request.args.get("source_type", "").strip()

    query = ReceiptSlip.query
    if selected_order:
        query = query.filter_by(order_number=selected_order)
    if selected_source:
        query = query.filter_by(source_type=selected_source)

    receipts = query.order_by(ReceiptSlip.created_at.desc()).all()
    orders = JobOrder.query.order_by(JobOrder.created_at.desc()).all()

    return render_template(
        "receipt_list.html",
        receipts=receipts,
        orders=orders,
        selected_order=selected_order,
        selected_source=selected_source
    )


@app.route("/receipt", methods=["GET", "POST"])
@login_required
def receipt_slip_new():
    if request.method == "POST":
        source_type = request.form.get("source_type") or "new_purchase"
        order_number = ""
        order_name = ""

        if source_type == "return_from_order":
            order_number = request.form.get("order_number") or ""
            order = JobOrder.query.filter_by(order_number=order_number).first()
            if not order:
                flash("Vyber existující zakázku pro vratku.", "danger")
                return redirect(url_for("receipt_slip_new"))
            order_name = order.name
        else:
            source_type = "new_purchase"

        session["receipt_source_type"] = source_type
        session["receipt_order_number"] = order_number
        session["receipt_order_name"] = order_name
        session["receipt_items"] = []
        session.modified = True
        return redirect(url_for("receipt_slip_edit"))

    orders = JobOrder.query.order_by(JobOrder.created_at.desc()).all()
    return render_template("receipt_new.html", orders=orders)


@app.route("/receipt/edit", methods=["GET", "POST"])
@login_required
def receipt_slip_edit():
    source_type = session.get("receipt_source_type")
    order_number = session.get("receipt_order_number", "")
    order_name = session.get("receipt_order_name", "")
    items = list(session.get("receipt_items", []))

    if not source_type:
        flash("Nejprve zvol typ příjmu.", "warning")
        return redirect(url_for("receipt_slip_new"))

    if request.method == "POST":
        material = material_from_scan(request.form.get("scan_value"))
        quantity = clean_float(request.form.get("quantity"))

        if not material:
            flash("Materiál nebyl nalezen.", "danger")
            return redirect(url_for("receipt_slip_edit"))

        if quantity <= 0:
            flash("Množství musí být větší než 0.", "danger")
            return redirect(url_for("receipt_slip_edit"))

        existing_item = next((item for item in items if item.get("material_db_id") == material.id), None)
        if existing_item:
            existing_item["quantity"] = clean_float(existing_item.get("quantity")) + quantity
        else:
            items.append({
                "material_db_id": material.id,
                "material_code": material.material_id,
                "manufacturer_id": material.manufacturer_id or "",
                "name": material.name or "",
                "quantity": quantity,
                "unit": material.unit or "",
                "storage_position": material.storage_position or ""
            })

        session["receipt_items"] = items
        session.modified = True

        flash("Položka přidána do příjemky.", "success")
        return redirect(url_for("receipt_slip_edit"))

    return render_template(
        "receipt_edit.html",
        source_type=source_type,
        order_number=order_number,
        order_name=order_name,
        items=items
    )


@app.route("/api/receipt/add", methods=["POST"])
@login_required
def api_receipt_add():
    source_type = session.get("receipt_source_type")
    if not source_type:
        return {"ok": False, "message": "Nejprve zvol typ příjmu."}, 400

    material = material_from_scan(request.form.get("scan_value"))
    quantity = clean_float(request.form.get("quantity"))
    items = list(session.get("receipt_items", []))

    if not material:
        return {"ok": False, "message": "Materiál nebyl nalezen."}, 404

    if quantity <= 0:
        return {"ok": False, "message": "Množství musí být větší než 0."}, 400

    existing_item = next((item for item in items if item.get("material_db_id") == material.id), None)
    if existing_item:
        existing_item["quantity"] = clean_float(existing_item.get("quantity")) + quantity
    else:
        items.append({
            "material_db_id": material.id,
            "material_code": material.material_id,
            "manufacturer_id": material.manufacturer_id or "",
            "name": material.name or "",
            "quantity": quantity,
            "unit": material.unit or "",
            "storage_position": material.storage_position or ""
        })

    session["receipt_items"] = items
    session.modified = True

    return {
        "ok": True,
        "message": "Položka přidána do příjemky.",
        "items": items,
        "material": {
            "material_id": material.material_id,
            "name": material.name,
            "quantity": material.quantity,
            "unit": material.unit
        }
    }


@app.route("/receipt/remove/<int:index>", methods=["POST"])
@login_required
def receipt_slip_remove(index):
    items = list(session.get("receipt_items", []))
    if 0 <= index < len(items):
        items.pop(index)
        session["receipt_items"] = items
        session.modified = True
    return redirect(url_for("receipt_slip_edit"))


@app.route("/receipt/confirm", methods=["POST"])
@login_required
def receipt_slip_confirm():
    source_type = session.get("receipt_source_type")
    order_number = session.get("receipt_order_number", "")
    order_name = session.get("receipt_order_name", "")
    items = list(session.get("receipt_items", []))

    if not source_type or not items:
        flash("Příjemka nemá typ příjmu nebo položky.", "danger")
        return redirect(url_for("receipt_slip_new"))

    if source_type == "return_from_order":
        order = JobOrder.query.filter_by(order_number=order_number).first()
        if not order:
            flash("Zakázka pro vratku neexistuje.", "danger")
            return redirect(url_for("receipt_slip_new"))
        order_name = order.name
    else:
        source_type = "new_purchase"
        order_number = ""
        order_name = ""

    for item in items:
        material = db.session.get(Material, item["material_db_id"])
        if not material:
            flash(f"Chybějící materiál {item['material_code']}", "danger")
            return redirect(url_for("receipt_slip_edit"))

    slip = ReceiptSlip(
        receipt_number=generate_receipt_slip_number(),
        source_type=source_type,
        order_number=order_number,
        order_name=order_name,
        username=current_user().username
    )
    db.session.add(slip)
    db.session.flush()

    movement_order_number = order_number if source_type == "return_from_order" else "Nově pořízený materiál"

    for item in items:
        material = db.session.get(Material, item["material_db_id"])
        quantity = clean_float(item["quantity"])
        material.quantity += quantity

        db.session.add(StockMovement(
            material_id=material.id,
            movement_type="prijem",
            quantity=quantity,
            username=current_user().username,
            order_number=movement_order_number
        ))

        db.session.add(ReceiptSlipItem(
            receipt_slip_id=slip.id,
            material_id_db=material.id,
            material_code=material.material_id,
            manufacturer_id=material.manufacturer_id or "",
            material_name=material.name or "",
            quantity=quantity,
            unit=material.unit or "",
            storage_position=material.storage_position or ""
        ))

    db.session.commit()

    session.pop("receipt_source_type", None)
    session.pop("receipt_order_number", None)
    session.pop("receipt_order_name", None)
    session.pop("receipt_items", None)
    session.modified = True

    flash(f"Příjemka {slip.receipt_number} vytvořena.", "success")
    return redirect(url_for("receipt_slip_detail", receipt_id=slip.id))


@app.route("/receipt/<int:receipt_id>")
@login_required
def receipt_slip_detail(receipt_id):
    slip = ReceiptSlip.query.get_or_404(receipt_id)
    return render_template("receipt_detail.html", slip=slip)


@app.route("/receipt/<int:receipt_id>/pdf")
@login_required
def receipt_slip_pdf(receipt_id):
    slip = ReceiptSlip.query.get_or_404(receipt_id)
    pdf = build_receipt_pdf(slip)
    return send_file(pdf, download_name=f"{slip.receipt_number}.pdf", as_attachment=True, mimetype="application/pdf")





# =========================
# INVENTURA
# =========================

@app.route("/inventories")
@login_required
@admin_required
def inventory_list():
    status = request.args.get("status", "").strip()
    query = InventorySession.query

    if status:
        query = query.filter_by(status=status)

    inventories = query.order_by(InventorySession.created_at.desc()).all()
    return render_template("inventory_list.html", inventories=inventories, selected_status=status)


@app.route("/inventories/start", methods=["GET", "POST"])
@login_required
@admin_required
def inventory_start():
    if request.method == "POST":
        user_id = request.form.get("user_id")
        user = db.session.get(User, int(user_id)) if user_id and str(user_id).isdigit() else None

        if not user:
            flash("Vyber platného uživatele.", "danger")
            return redirect(url_for("inventory_start"))

        existing = get_active_inventory(user.id)
        if existing:
            flash(f"Uživatel {user.username} už má aktivní inventuru.", "warning")
            return redirect(url_for("inventory_list"))

        inventory = InventorySession(
            name=request.form.get("name") or f"Inventura {datetime.now().strftime('%d.%m.%Y')}",
            user_id=user.id,
            status="active"
        )
        db.session.add(inventory)
        db.session.commit()

        flash(f"Inventura byla zpřístupněna uživateli {user.username}.", "success")
        return redirect(url_for("inventory_list"))

    users = User.query.order_by(User.username.asc()).all()
    return render_template("inventory_start.html", users=users)


@app.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory_user():
    user = current_user()
    inventory = get_active_inventory(user.id)

    if not inventory:
        flash("Nemáš aktivní inventuru.", "warning")
        return redirect(url_for("index"))

    if request.method == "POST":
        material = material_from_scan(request.form.get("scan_value"))
        real_qty = clean_float(request.form.get("real_qty"))

        if not material:
            flash("Materiál nebyl nalezen.", "danger")
            return redirect(url_for("inventory_user"))

        storage_position = (request.form.get("storage_position") or material.storage_position or "").strip()

        existing = InventoryItem.query.filter_by(
            inventory_session_id=inventory.id,
            material_id_db=material.id
        ).first()

        if existing:
            existing.real_qty = real_qty
            existing.system_qty = material.quantity or 0
            existing.storage_position = storage_position
            existing.scanned_at = datetime.now()
        else:
            db.session.add(InventoryItem(
                inventory_session_id=inventory.id,
                material_id_db=material.id,
                material_code=material.material_id or "",
                manufacturer_id=material.manufacturer_id or "",
                material_name=material.name or "",
                system_qty=material.quantity or 0,
                real_qty=real_qty,
                unit=material.unit or "",
                storage_position=storage_position,
                username=user.username
            ))

        db.session.commit()
        flash("Položka inventury byla uložena.", "success")
        return redirect(url_for("inventory_user"))

    items = InventoryItem.query.filter_by(inventory_session_id=inventory.id).order_by(InventoryItem.scanned_at.desc()).all()
    return render_template("inventory_user.html", inventory=inventory, items=items)


@app.route("/api/inventory/add", methods=["POST"])
@login_required
def api_inventory_add():
    user = current_user()
    inventory = get_active_inventory(user.id)

    if not inventory:
        return {"ok": False, "message": "Nemáš aktivní inventuru."}, 403

    material = material_from_scan(request.form.get("scan_value"))
    real_qty = clean_float(request.form.get("real_qty"))
    storage_position = (request.form.get("storage_position") or (material.storage_position if material else "") or "").strip()

    if not material:
        return {"ok": False, "message": "Materiál nebyl nalezen."}, 404

    existing = InventoryItem.query.filter_by(
        inventory_session_id=inventory.id,
        material_id_db=material.id
    ).first()

    if existing:
        existing.real_qty = real_qty
        existing.system_qty = material.quantity or 0
        existing.storage_position = storage_position
        existing.scanned_at = datetime.now()
    else:
        existing = InventoryItem(
            inventory_session_id=inventory.id,
            material_id_db=material.id,
            material_code=material.material_id or "",
            manufacturer_id=material.manufacturer_id or "",
            material_name=material.name or "",
            system_qty=material.quantity or 0,
            real_qty=real_qty,
            unit=material.unit or "",
            storage_position=storage_position,
            username=user.username
        )
        db.session.add(existing)

    db.session.commit()

    items = InventoryItem.query.filter_by(inventory_session_id=inventory.id).order_by(InventoryItem.scanned_at.desc()).all()
    return {
        "ok": True,
        "message": "Položka inventury byla uložena.",
        "items": [{
            "material_code": item.material_code,
            "manufacturer_id": item.manufacturer_id,
            "material_name": item.material_name,
            "storage_position": item.storage_position,
            "system_qty": item.system_qty,
            "real_qty": item.real_qty,
            "difference": inventory_difference(item),
            "unit": item.unit,
        } for item in items]
    }


@app.route("/inventory/finish", methods=["POST"])
@login_required
def inventory_finish():
    user = current_user()
    inventory = get_active_inventory(user.id)

    if not inventory:
        flash("Nemáš aktivní inventuru.", "warning")
        return redirect(url_for("index"))

    inventory.status = "done"
    inventory.finished_at = datetime.now()
    db.session.commit()

    flash("Inventura byla dokončena.", "success")
    return redirect(url_for("index"))


@app.route("/inventories/<int:inventory_id>")
@login_required
@admin_required
def inventory_detail(inventory_id):
    inventory = InventorySession.query.get_or_404(inventory_id)
    items = InventoryItem.query.filter_by(inventory_session_id=inventory.id).order_by(InventoryItem.material_code.asc()).all()
    return render_template("inventory_detail.html", inventory=inventory, items=items)


@app.route("/inventories/<int:inventory_id>/pdf")
@login_required
@admin_required
def inventory_pdf(inventory_id):
    inventory = InventorySession.query.get_or_404(inventory_id)
    pdf = build_inventory_pdf(inventory)
    return send_file(pdf, download_name=f"inventura_{inventory.id}.pdf", as_attachment=True, mimetype="application/pdf")


@app.route("/inventories/<int:inventory_id>/export")
@login_required
@admin_required
def inventory_export(inventory_id):
    inventory = InventorySession.query.get_or_404(inventory_id)

    data = []
    for item in inventory.items:
        data.append({
            "ID Materiálu": item.material_code,
            "ID výrobce": item.manufacturer_id,
            "Název": item.material_name,
            "Skladová pozice": item.storage_position,
            "Systémový stav": item.system_qty,
            "Reálný stav": item.real_qty,
            "Rozdíl": inventory_difference(item),
            "Mn.j.": item.unit,
            "Uživatel": inventory.user.username if inventory.user else "",
            "Datum skenu": item.scanned_at.strftime("%d.%m.%Y %H:%M") if item.scanned_at else "",
        })

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data).to_excel(writer, index=False, sheet_name="Inventura")
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"inventura_{inventory.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/inventories/<int:inventory_id>/close", methods=["POST"])
@login_required
@admin_required
def inventory_close(inventory_id):
    inventory = InventorySession.query.get_or_404(inventory_id)
    inventory.status = "done"
    inventory.finished_at = inventory.finished_at or datetime.now()
    db.session.commit()
    flash("Inventura byla uzavřena administrátorem.", "success")
    return redirect(url_for("inventory_detail", inventory_id=inventory.id))


# =========================
# SPRÁVA UŽIVATELŮ - ADMIN
# =========================

@app.route("/users")
@login_required
@admin_required
def users():
    users = User.query.order_by(User.username.asc()).all()
    return render_template("users.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
@login_required
@admin_required
def user_new():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")

        if not username or not password:
            flash("Vyplň uživatelské jméno i heslo.", "danger")
            return redirect(url_for("user_new"))

        if role not in ["admin", "user"]:
            role = "user"

        if User.query.filter_by(username=username).first():
            flash("Uživatel s tímto jménem už existuje.", "danger")
            return redirect(url_for("user_new"))

        db.session.add(User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role
        ))
        db.session.commit()
        flash("Uživatel byl vytvořen.", "success")
        return redirect(url_for("users"))

    return render_template("user_form.html", user=None)


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def user_edit(user_id):
    edited_user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        new_role = request.form.get("role", "user")

        if not username:
            flash("Uživatelské jméno nesmí být prázdné.", "danger")
            return redirect(url_for("user_edit", user_id=user_id))

        duplicate = User.query.filter(User.username == username, User.id != edited_user.id).first()
        if duplicate:
            flash("Uživatel s tímto jménem už existuje.", "danger")
            return redirect(url_for("user_edit", user_id=user_id))

        if new_role not in ["admin", "user"]:
            new_role = "user"

        if edited_user.role == "admin" and new_role != "admin":
            admin_count = User.query.filter_by(role="admin").count()
            if admin_count <= 1:
                flash("Nelze odebrat roli poslednímu adminovi.", "danger")
                return redirect(url_for("users"))

        edited_user.username = username
        edited_user.role = new_role
        db.session.commit()
        flash("Uživatel byl upraven.", "success")
        return redirect(url_for("users"))

    return render_template("user_form.html", user=edited_user)


@app.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@admin_required
def user_password(user_id):
    edited_user = User.query.get_or_404(user_id)

    if request.method == "POST":
        password = request.form.get("password", "")
        if not password:
            flash("Zadej nové heslo.", "danger")
            return redirect(url_for("user_password", user_id=user_id))

        edited_user.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Heslo bylo změněno.", "success")
        return redirect(url_for("users"))

    return render_template("user_password.html", user=edited_user)

@app.route("/scan")
@login_required
def qr_scan():
    next_url = request.args.get("next") or url_for("issue_slip_edit")
    return render_template("scan.html", next_url=next_url)

@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def user_delete(user_id):
    edited_user = User.query.get_or_404(user_id)
    logged_user = current_user()

    if edited_user.id == logged_user.id:
        flash("Nemůžeš smazat sám sebe.", "danger")
        return redirect(url_for("users"))

    if edited_user.role == "admin":
        admin_count = User.query.filter_by(role="admin").count()
        if admin_count <= 1:
            flash("Nelze smazat posledního admina.", "danger")
            return redirect(url_for("users"))

    db.session.delete(edited_user)
    db.session.commit()
    flash("Uživatel byl smazán.", "success")
    return redirect(url_for("users"))


# =========================
# START
# =========================

with app.app_context():
    db.create_all()
    ensure_db_columns()
    create_initial_users()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
