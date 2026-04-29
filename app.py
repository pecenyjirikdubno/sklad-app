import os
import re
import zipfile
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
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "tajneheslo123")

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


class JobOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(255))
    status = db.Column(db.String(20), default="active")
    created_at = db.Column(db.DateTime, default=datetime.now)
    closed_at = db.Column(db.DateTime)


class BackupLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), unique=True)
    username = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.now)


class IssueSlip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slip_number = db.Column(db.String(30), unique=True)
    order_number = db.Column(db.String(30))
    order_name = db.Column(db.String(255))
    username = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.now)


class IssueSlipItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    issue_slip_id = db.Column(db.Integer, db.ForeignKey("issue_slip.id"))
    material_id_db = db.Column(db.Integer)
    material_code = db.Column(db.String(30))
    manufacturer_id = db.Column(db.String(100))
    material_name = db.Column(db.String(255))
    quantity = db.Column(db.Float)
    unit = db.Column(db.String(50))
    issue_slip = db.relationship("IssueSlip", backref="items")


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
            flash("Nemáš oprávnění.", "danger")
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
        return float(str(value).replace(",", ".").replace(" ", ""))
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


def generate_order_number():
    prefix = datetime.now().strftime("%Y%m%d")
    count = JobOrder.query.filter(JobOrder.order_number.startswith(prefix)).count() + 1
    return f"{prefix}{count:02d}"


def generate_issue_slip_number():
    prefix = "VYD" + datetime.now().strftime("%Y%m%d")
    count = IssueSlip.query.filter(IssueSlip.slip_number.startswith(prefix)).count() + 1
    return f"{prefix}{count:02d}"


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


def ensure_order_columns():
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()

    if "job_order" not in table_names:
        return

    columns = [c["name"] for c in inspector.get_columns("job_order")]

    if "status" not in columns:
        db.session.execute(text("ALTER TABLE job_order ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))

    if "closed_at" not in columns:
        db.session.execute(text("ALTER TABLE job_order ADD COLUMN closed_at TIMESTAMP"))

    db.session.commit()


def check_monthly_backup_notice(user):
    if not user or user.role != "admin":
        return

    period = datetime.now().strftime("%Y-%m")

    if not BackupLog.query.filter_by(period=period).first():
        db.session.add(BackupLog(period=period, username=user.username))
        db.session.commit()
        flash("První přihlášení admina v novém měsíci: doporučujeme stáhnout měsíční zálohu dat.", "warning")


def material_from_scan(value):
    value = (value or "").strip()

    if not value:
        return None

    match = re.search(r"/(?:movement|qr)/(\d+)", value)
    if match:
        return db.session.get(Material, int(match.group(1)))

    if value.isdigit():
        material = db.session.get(Material, int(value))
        if material:
            return material

    return Material.query.filter_by(material_id=value).first()


def export_backup_zip():
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = BytesIO()

    tables = {
        "materials.xlsx": pd.DataFrame([{
            "ID Materiálu": m.material_id,
            "ID výrobce": m.manufacturer_id or "",
            "Název": m.name or "",
            "Množství": m.quantity or 0,
            "Mn.j.": m.unit or "",
            "Cena bez DPH": m.price_without_vat or 0
        } for m in Material.query.order_by(Material.material_id.asc()).all()]),

        "movements.xlsx": pd.DataFrame([{
            "Datum": m.created_at,
            "Typ": m.movement_type,
            "ID materiálu DB": m.material_id,
            "Množství": m.quantity,
            "Uživatel": m.username,
            "Zakázka": m.order_number
        } for m in StockMovement.query.order_by(StockMovement.created_at.desc()).all()]),

        "orders.xlsx": pd.DataFrame([{
            "Číslo zakázky": o.order_number,
            "Název": o.name,
            "Stav": o.status or "active",
            "Vytvořeno": o.created_at,
            "Ukončeno": o.closed_at
        } for o in JobOrder.query.order_by(JobOrder.created_at.desc()).all()]),

        "issue_slips.xlsx": pd.DataFrame([{
            "Výdejka": s.slip_number,
            "Zakázka": s.order_number,
            "Název zakázky": s.order_name,
            "Uživatel": s.username,
            "Vytvořeno": s.created_at
        } for s in IssueSlip.query.order_by(IssueSlip.created_at.desc()).all()]),

        "issue_slip_items.xlsx": pd.DataFrame([{
            "Výdejka": i.issue_slip.slip_number if i.issue_slip else "",
            "ID Materiálu": i.material_code,
            "ID výrobce": i.manufacturer_id,
            "Název": i.material_name,
            "Množství": i.quantity,
            "Mn.j.": i.unit
        } for i in IssueSlipItem.query.all()])
    }

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for filename, df in tables.items():
            xlsx_buffer = BytesIO()
            with pd.ExcelWriter(xlsx_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False)
            xlsx_buffer.seek(0)
            z.writestr(filename, xlsx_buffer.read())

        z.writestr(
            "backup_info.txt",
            f"Záloha skladové aplikace\nVytvořeno: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
        )

    zip_buffer.seek(0)
    return zip_buffer, f"sklad_backup_{now}.zip"


def build_issue_pdf(slip):
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
    story = [
        Paragraph(f"Výdejka č. {slip.slip_number}", styles["Title"]),
        Spacer(1, 8),
        Paragraph(f"Zakázka: {slip.order_number} - {slip.order_name}", styles["Normal"]),
        Paragraph(f"Vydal: {slip.username}", styles["Normal"]),
        Paragraph(f"Datum: {slip.created_at.strftime('%d.%m.%Y %H:%M')}", styles["Normal"]),
        Spacer(1, 12)
    ]

    data = [["ID Materiálu", "ID výrobce", "Název", "Množství", "Mn.j."]]

    for item in slip.items:
        data.append([
            item.material_code or "",
            item.manufacturer_id or "",
            item.material_name or "",
            str(item.quantity or 0),
            item.unit or ""
        ])

    table = Table(data, colWidths=[30 * mm, 35 * mm, 70 * mm, 25 * mm, 20 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP")
    ]))

    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer


# =========================
# ROUTES
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()

        if u and check_password_hash(u.password_hash, request.form["password"]):
            session["user_id"] = u.id
            check_monthly_backup_notice(u)
            return redirect(url_for("index"))

        flash("Špatné přihlášení", "danger")

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
        db.session.add(Material(
            material_id=generate_material_id(),
            manufacturer_id=request.form.get("manufacturer_id") or "",
            name=request.form.get("name") or "",
            quantity=clean_float(request.form.get("quantity")),
            unit=request.form.get("unit") or "",
            price_without_vat=clean_float(request.form.get("price"))
        ))
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

    return render_template("card.html", material=material, item=material, movements=movements)


@app.route("/movement/<int:item_id>", methods=["GET", "POST"])
@login_required
def movement(item_id):
    m = Material.query.get_or_404(item_id)

    if request.method == "POST":
        qty = clean_float(request.form.get("quantity"))
        typ = request.form.get("type")
        issued_to = request.form.get("issued_to")
        order_number = ""

        if qty <= 0:
            flash("Množství musí být větší než 0", "danger")
            return redirect(url_for("movement", item_id=item_id))

        if typ == "in":
            m.quantity += qty

        elif typ == "out":
            if issued_to == "internal":
                order_number = "Vlastní spotřeba"

            elif issued_to == "existing":
                order_number = request.form.get("order_number") or ""

            elif issued_to == "new":
                order_name = request.form.get("new_order_name") or "Nová zakázka"
                order_number = generate_order_number()
                db.session.add(JobOrder(
                    order_number=order_number,
                    name=order_name,
                    status="active"
                ))

            if qty > m.quantity:
                flash("Nedostatek na skladě", "danger")
                return redirect(url_for("movement", item_id=item_id))

            m.quantity -= qty

        else:
            flash("Neplatný typ pohybu", "danger")
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

    orders = JobOrder.query.filter_by(status="active").order_by(JobOrder.created_at.desc()).all()
    return render_template("movement.html", material=m, item=m, orders=orders)


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

    buf = BytesIO()
    pd.DataFrame(data).to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)

    return send_file(
        buf,
        download_name="export.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/backup")
@login_required
@admin_required
def backup_download():
    zip_buffer, filename = export_backup_zip()

    return send_file(
        zip_buffer,
        download_name=filename,
        as_attachment=True,
        mimetype="application/zip"
    )


# =========================
# ZAKÁZKY
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

        order = JobOrder(
            order_number=generate_order_number(),
            name=name,
            status="active"
        )

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
# VÝDEJKY
# =========================

@app.route("/issue", methods=["GET", "POST"])
@login_required
def issue_slip_new():
    if request.method == "POST":
        order_number = request.form.get("order_number") or ""
        order = JobOrder.query.filter_by(
            order_number=order_number,
            status="active"
        ).first()

        if not order:
            flash("Vyber existující aktivní zakázku", "danger")
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
    items = session.get("issue_items", [])

    if not order_number:
        flash("Nejprve vyber zakázku", "warning")
        return redirect(url_for("issue_slip_new"))

    if request.method == "POST":
        material = material_from_scan(request.form.get("scan_value"))
        qty = clean_float(request.form.get("quantity"))

        if not material:
            flash("Materiál nebyl nalezen", "danger")
            return redirect(url_for("issue_slip_edit"))

        if qty <= 0:
            flash("Množství musí být větší než 0", "danger")
            return redirect(url_for("issue_slip_edit"))

        if qty > material.quantity:
            flash(f"Nedostatek na skladě: {material.material_id} má skladem {material.quantity} {material.unit}", "danger")
            return redirect(url_for("issue_slip_edit"))

        items.append({
            "material_db_id": material.id,
            "material_code": material.material_id,
            "manufacturer_id": material.manufacturer_id or "",
            "name": material.name or "",
            "quantity": qty,
            "unit": material.unit or ""
        })

        session["issue_items"] = items
        flash("Položka přidána do výdejky", "success")
        return redirect(url_for("issue_slip_edit"))

    return render_template(
        "issue_edit.html",
        order_number=order_number,
        order_name=order_name,
        items=items
    )


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
        flash("Výdejka nemá zakázku nebo položky", "danger")
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
        qty = clean_float(item["quantity"])

        material.quantity -= qty

        db.session.add(StockMovement(
            material_id=material.id,
            movement_type="out",
            quantity=qty,
            username=current_user().username,
            order_number=order_number
        ))

        db.session.add(IssueSlipItem(
            issue_slip_id=slip.id,
            material_id_db=material.id,
            material_code=material.material_id,
            manufacturer_id=material.manufacturer_id or "",
            material_name=material.name or "",
            quantity=qty,
            unit=material.unit or ""
        ))

    db.session.commit()

    session.pop("issue_order_number", None)
    session.pop("issue_order_name", None)
    session.pop("issue_items", None)

    flash(f"Výdejka {slip.slip_number} vytvořena", "success")
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

    return send_file(
        build_issue_pdf(slip),
        download_name=f"{slip.slip_number}.pdf",
        as_attachment=True,
        mimetype="application/pdf"
    )


@app.route("/import", methods=["GET", "POST"])
@login_required
@admin_required
def import_excel():
    if request.method == "GET":
        return render_template("import.html")

    file = request.files.get("file")

    if not file or file.filename == "":
        flash("Vyber soubor", "danger")
        return redirect(url_for("index"))

    try:
        df = read_excel(file)
    except Exception as e:
        flash(str(e), "danger")
        return redirect(url_for("index"))

    imported = 0
    updated = 0
    skipped = 0

    for _, row in df.iterrows():
        name = str(row.get("Název", "") or "").strip()

        if not name:
            skipped += 1
            continue

        material_id = str(row.get("ID Materiálu", "") or "").strip() or generate_material_id()
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
    flash(f"Import hotov. Přidáno: {imported}, aktualizováno: {updated}, přeskočeno: {skipped}", "success")
    return redirect(url_for("index"))


# =========================
# INIT
# =========================

with app.app_context():
    db.create_all()
    ensure_order_columns()
    create_initial_users()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
