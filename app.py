from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from datetime import datetime, date
from pathlib import Path
from io import BytesIO
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
import sqlite3
import os
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("EASY_BANK_SECRET_KEY", "easy-bank-dev-secret-change-this")

BANK_NAME = "Easy Bank"
BRANCH_NAME = "Lincoln Branch"
TAGLINE = "NZ only Lincoln based community bank"
FOOTER_TEXT = "Easy Bank New Zealand"
CONTACT_PHONE_DISPLAY = "+64 022 106 0569"
CONTACT_PHONE_WHATSAPP = "+64221060569"
BRANCH_MANAGER = "A N M Fajle Rabbi"

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "easy_bank.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads" / "customers"
LOGO_PATH = BASE_DIR / "static" / "images" / "easy_bank_logo.png"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(conn, table_name, column_name, column_sql):
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def init_db():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            address TEXT NOT NULL,
            date_of_birth TEXT NOT NULL,
            occupation TEXT NOT NULL,
            id_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            sex TEXT NOT NULL,
            nationality TEXT NOT NULL,
            photo_path TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_number TEXT NOT NULL UNIQUE,
            customer_id TEXT NOT NULL UNIQUE,
            customer_name TEXT NOT NULL,
            account_type TEXT NOT NULL DEFAULT 'Savings',
            balance REAL NOT NULL DEFAULT 0,
            opened_at TEXT NOT NULL,
            opened_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL UNIQUE,
            account_number TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            transaction_date TEXT,
            from_account_number TEXT,
            to_account_number TEXT,
            comment TEXT,
            balance_after REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enquiry_id INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            enquiry TEXT NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS manager_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Migration support for old Easy Bank database files
    add_column_if_missing(conn, "accounts", "opened_date", "opened_date TEXT")
    add_column_if_missing(conn, "transactions", "transaction_date", "transaction_date TEXT")
    add_column_if_missing(conn, "transactions", "from_account_number", "from_account_number TEXT")
    add_column_if_missing(conn, "transactions", "to_account_number", "to_account_number TEXT")
    add_column_if_missing(conn, "transactions", "comment", "comment TEXT")
    add_column_if_missing(conn, "transactions", "balance_after", "balance_after REAL")
    add_column_if_missing(conn, "customers", "username", "username TEXT")
    add_column_if_missing(conn, "customers", "password_hash", "password_hash TEXT")
    add_column_if_missing(conn, "enquiries", "is_read", "is_read INTEGER NOT NULL DEFAULT 0")

    manager = conn.execute("SELECT * FROM manager_settings WHERE id=1").fetchone()
    if manager is None:
        conn.execute(
            "INSERT INTO manager_settings (id, username, password_hash, updated_at) VALUES (1, ?, ?, ?)",
            ("manager", generate_password_hash("EasyBank@2026!"), display_datetime())
        )

    today_iso = date.today().isoformat()
    conn.execute("UPDATE accounts SET opened_date=? WHERE opened_date IS NULL OR opened_date=''", (today_iso,))
    conn.execute("UPDATE transactions SET transaction_date=? WHERE transaction_date IS NULL OR transaction_date=''", (today_iso,))
    conn.commit()
    conn.close()


def generate_customer_id():
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM customers ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    next_number = 1 if row is None else row["id"] + 1
    return f"EB-C{next_number:04d}"


def generate_account_number():
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM accounts ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    next_number = 1 if row is None else row["id"] + 1
    return f"EB-SA{next_number:04d}"


def next_transaction_id(conn=None):
    close_after = False
    if conn is None:
        conn = get_db_connection()
        close_after = True
    row = conn.execute("SELECT transaction_id FROM transactions ORDER BY transaction_id DESC LIMIT 1").fetchone()
    if close_after:
        conn.close()
    return 1 if row is None else row["transaction_id"] + 1


def next_enquiry_id():
    conn = get_db_connection()
    row = conn.execute("SELECT enquiry_id FROM enquiries ORDER BY enquiry_id DESC LIMIT 1").fetchone()
    conn.close()
    return 1 if row is None else row["enquiry_id"] + 1


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def display_datetime():
    return datetime.now().strftime("%d %b %Y %I:%M %p")


def today_iso():
    return date.today().isoformat()

def is_manager_logged_in():
    return session.get("manager_logged_in") is True


def is_customer_logged_in():
    return session.get("customer_logged_in") is True


def get_logged_customer_id():
    return session.get("customer_id")


def customer_login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not is_customer_logged_in():
            flash("Please login to your customer account first.", "warning")
            return redirect(url_for("customer_login_page", next=request.path))
        return view_function(*args, **kwargs)
    return wrapped_view


def manager_login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not is_manager_logged_in():
            flash("Manager login required for this action.", "warning")
            return redirect(url_for("login_page", next=request.path))
        return view_function(*args, **kwargs)
    return wrapped_view



def create_pdf_response(title, subtitle, table_data, filename):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=35, bottomMargin=25)
    styles = getSampleStyleSheet()
    elements = []
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=70, height=70)
        elements.append(logo)
        elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>{BANK_NAME}</b>", styles["Title"]))
    elements.append(Paragraph(f"{BRANCH_NAME} | {TAGLINE}", styles["Normal"]))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"<b>{title}</b>", styles["Heading2"]))
    elements.append(Paragraph(subtitle, styles["Normal"]))
    elements.append(Spacer(1, 12))

    if table_data and len(table_data) > 1:
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No records found for this report.", styles["Normal"]))

    elements.append(Spacer(1, 18))
    elements.append(Paragraph(f"Generated on: {display_datetime()}", styles["Normal"]))
    elements.append(Paragraph(f"Branch Manager: {BRANCH_MANAGER}", styles["Normal"]))
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/pdf")


@app.context_processor
def inject_bank_details():
    unread_count = 0
    if is_manager_logged_in():
        try:
            conn = get_db_connection()
            unread_count = conn.execute("SELECT COUNT(*) FROM enquiries WHERE is_read=0").fetchone()[0]
            conn.close()
        except Exception:
            pass
    return dict(
        bank_name=BANK_NAME,
        branch_name=BRANCH_NAME,
        tagline=TAGLINE,
        footer_text=FOOTER_TEXT,
        contact_phone_display=CONTACT_PHONE_DISPLAY,
        contact_phone_whatsapp=CONTACT_PHONE_WHATSAPP,
        branch_manager=BRANCH_MANAGER,
        today_date=today_iso(),
        manager_logged_in=is_manager_logged_in(),
        customer_logged_in=is_customer_logged_in(),
        logged_customer_id=get_logged_customer_id(),
        logged_customer_name=session.get("customer_name"),
        unread_inbox_count=unread_count,
    )



@app.route("/login", methods=["GET", "POST"])
def login_page():
    if is_manager_logged_in():
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_page = request.form.get("next") or url_for("home")

        conn = get_db_connection()
        manager = conn.execute("SELECT * FROM manager_settings WHERE id=1").fetchone()
        conn.close()
        if manager and username == manager["username"] and check_password_hash(manager["password_hash"], password):
            session.clear()
            session["manager_logged_in"] = True
            session["manager_username"] = username
            flash("Manager login successful.", "success")
            return redirect(next_page)

        flash("Invalid manager username or password.", "danger")

    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout")
def logout_page():
    session.clear()
    flash("You have logged out successfully.", "success")
    return redirect(url_for("home"))


@app.route("/customer-login", methods=["GET", "POST"])
def customer_login_page():
    if is_customer_logged_in():
        return redirect(url_for("customer_dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_page = request.form.get("next") or url_for("customer_dashboard")
        conn = get_db_connection()
        customer = conn.execute("SELECT * FROM customers WHERE lower(username)=lower(?)", (username,)).fetchone()
        conn.close()
        if customer and customer["password_hash"] and check_password_hash(customer["password_hash"], password):
            session.clear()
            session["customer_logged_in"] = True
            session["customer_id"] = customer["customer_id"]
            session["customer_name"] = customer["full_name"]
            flash("Customer login successful.", "success")
            return redirect(next_page)
        flash("Invalid customer username or password.", "danger")
    return render_template("customer_login.html", next=request.args.get("next", ""))


@app.route("/my-dashboard")
@customer_login_required
def customer_dashboard():
    conn = get_db_connection()
    customer = conn.execute("SELECT * FROM customers WHERE customer_id=?", (get_logged_customer_id(),)).fetchone()
    account = conn.execute("SELECT * FROM accounts WHERE customer_id=?", (get_logged_customer_id(),)).fetchone()
    transactions = []
    if account:
        transactions = conn.execute("SELECT * FROM transactions WHERE account_number=? ORDER BY id DESC LIMIT 20", (account["account_number"],)).fetchall()
    conn.close()
    return render_template("customer_dashboard.html", customer=customer, account=account, transactions=transactions)


@app.route("/manager/settings", methods=["GET", "POST"])
@manager_login_required
def manager_settings_page():
    conn = get_db_connection()
    manager = conn.execute("SELECT * FROM manager_settings WHERE id=1").fetchone()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_username = request.form.get("new_username", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not manager or not check_password_hash(manager["password_hash"], current_password):
            flash("Current manager password is incorrect.", "danger")
        elif not new_username or not new_password:
            flash("New username and new password are required.", "danger")
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_password != confirm_password:
            flash("New password and confirm password do not match.", "danger")
        else:
            conn.execute("UPDATE manager_settings SET username=?, password_hash=?, updated_at=? WHERE id=1", (new_username, generate_password_hash(new_password), display_datetime()))
            conn.commit()
            session["manager_username"] = new_username
            flash("Manager login details updated successfully.", "success")
            manager = conn.execute("SELECT * FROM manager_settings WHERE id=1").fetchone()
    conn.close()
    return render_template("manager_settings.html", manager=manager)

@app.route("/")
def home():
    conn = get_db_connection()
    if is_manager_logged_in():
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        total_accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        total_transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        total_balance = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]
    elif is_customer_logged_in():
        account = conn.execute("SELECT * FROM accounts WHERE customer_id=?", (get_logged_customer_id(),)).fetchone()
        total_customers = 1
        total_accounts = 1 if account else 0
        total_transactions = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_number=?", (account["account_number"],)).fetchone()[0] if account else 0
        total_balance = account["balance"] if account else 0
    else:
        total_customers = total_accounts = total_transactions = 0
        total_balance = 0
    conn.close()
    return render_template("home.html", total_customers=total_customers, total_accounts=total_accounts, total_transactions=total_transactions, total_balance=total_balance)


@app.route("/customers")
@manager_login_required
def customers_page():
    conn = get_db_connection()
    customers = conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("customers.html", customers=customers)


@app.route("/customers/add", methods=["GET", "POST"])
def add_customer():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip().lower()
        address = request.form.get("address", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        occupation = request.form.get("occupation", "").strip()
        id_number = request.form.get("id_number", "").strip()
        status = "Inactive"
        sex = request.form.get("sex", "").strip()
        nationality = request.form.get("nationality", "").strip()
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        agree = request.form.get("agree")
        required_fields = {"Full name": full_name, "Phone": phone, "Email": email, "Address": address, "Date of birth": date_of_birth, "Occupation": occupation, "ID number": id_number, "Sex": sex, "Nationality": nationality, "Username": username, "Password": password}
        missing_fields = [label for label, value in required_fields.items() if not value]
        if missing_fields:
            flash("Please complete all required fields: " + ", ".join(missing_fields) + ".", "danger")
            return render_template("add_customer.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("add_customer.html")
        if password != confirm_password:
            flash("Password and confirm password do not match.", "danger")
            return render_template("add_customer.html")
        try:
            dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        except ValueError:
            flash("Please enter a valid date of birth.", "danger")
            return render_template("add_customer.html")
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            flash("Customer must be 18 years or older to open an Easy Bank profile.", "danger")
            return render_template("add_customer.html")
        if not agree:
            flash("Please tick the agreement box before saving the customer.", "danger")
            return render_template("add_customer.html")
        conn = get_db_connection()
        duplicate = conn.execute("SELECT customer_id FROM customers WHERE lower(phone)=lower(?) OR lower(email)=lower(?) OR lower(id_number)=lower(?) OR lower(username)=lower(?)", (phone, email, id_number, username)).fetchone()
        if duplicate:
            conn.close()
            flash("Phone, email, ID number, or username is already registered.", "danger")
            return render_template("add_customer.html")
        customer_id = generate_customer_id()
        photo_path = None
        photo = request.files.get("photo")
        if photo and photo.filename:
            if not allowed_file(photo.filename):
                conn.close()
                flash("Photo must be PNG, JPG, JPEG, GIF, or WEBP.", "danger")
                return render_template("add_customer.html")
            safe_name = secure_filename(photo.filename)
            extension = safe_name.rsplit(".", 1)[1].lower()
            stored_filename = f"{customer_id}.{extension}"
            photo.save(UPLOAD_FOLDER / stored_filename)
            photo_path = f"uploads/customers/{stored_filename}"
        conn.execute("""
            INSERT INTO customers (customer_id, full_name, phone, email, address, date_of_birth, occupation, id_number, status, sex, nationality, photo_path, created_at, username, password_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (customer_id, full_name, phone, email, address, date_of_birth, occupation, id_number, status, sex, nationality, photo_path, display_datetime(), username, generate_password_hash(password)))
        conn.commit()
        conn.close()
        flash(f"Customer registration submitted successfully. Your Customer ID is {customer_id}. Status is Inactive until manager approval.", "success")
        if is_manager_logged_in():
            return redirect(url_for("customers_page"))
        return redirect(url_for("customer_login_page"))
    return render_template("add_customer.html")


@app.route("/customers/<customer_id>/activate", methods=["POST"])
@manager_login_required
def activate_customer(customer_id):
    conn = get_db_connection()
    customer = conn.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    if customer is None:
        flash("Customer not found.", "danger")
    else:
        conn.execute("UPDATE customers SET status='Active' WHERE customer_id=?", (customer_id,))
        conn.commit()
        flash(f"Customer {customer_id} is now Active.", "success")
    conn.close()
    return redirect(url_for("customers_page"))


@app.route("/customers/<customer_id>/deactivate", methods=["POST"])
@manager_login_required
def deactivate_customer(customer_id):
    conn = get_db_connection()
    customer = conn.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    if customer is None:
        flash("Customer not found.", "danger")
    else:
        conn.execute("UPDATE customers SET status='Inactive' WHERE customer_id=?", (customer_id,))
        conn.commit()
        flash(f"Customer {customer_id} is now Inactive.", "success")
    conn.close()
    return redirect(url_for("customers_page"))


@app.route("/accounts")
@manager_login_required
def accounts_page():
    conn = get_db_connection()
    accounts = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("accounts.html", accounts=accounts)


@app.route("/accounts/open", methods=["GET", "POST"])
@manager_login_required
def open_account():
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip().upper()
        conn = get_db_connection()
        customer = conn.execute("SELECT * FROM customers WHERE upper(customer_id)=?", (customer_id,)).fetchone()
        if customer is None:
            conn.close()
            flash("Customer ID not found. Please enter a valid Customer ID, for example EB-C0001.", "danger")
            return redirect(url_for("open_account"))
        if customer["status"] != "Active":
            conn.close()
            flash("This customer is Inactive. Manager must activate the customer before opening an account.", "warning")
            return redirect(url_for("customers_page"))
        existing_account = conn.execute("SELECT * FROM accounts WHERE upper(customer_id)=?", (customer_id,)).fetchone()
        if existing_account:
            conn.close()
            flash(f"This customer already has a Savings Account: {existing_account['account_number']}", "warning")
            return redirect(url_for("accounts_page"))
        account_number = generate_account_number()
        conn.execute("""
            INSERT INTO accounts (account_number, customer_id, customer_name, account_type, balance, opened_at, opened_date)
            VALUES (?, ?, ?, 'Savings', 0, ?, ?)
        """, (account_number, customer["customer_id"], customer["full_name"], display_datetime(), today_iso()))
        conn.commit()
        conn.close()
        flash(f"Savings Account {account_number} opened successfully for {customer['full_name']}.", "success")
        return redirect(url_for("accounts_page"))
    return render_template("open_account.html")


@app.route("/transactions", methods=["GET", "POST"])
@manager_login_required
def transactions_page():
    conn = get_db_connection()
    if request.method == "POST":
        if not is_manager_logged_in():
            conn.close()
            flash("Manager login required for deposit or withdraw.", "warning")
            return redirect(url_for("login_page", next=url_for("transactions_page")))
        account_number = request.form.get("account_number", "").strip()
        transaction_type = request.form.get("transaction_type", "").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0
        account = conn.execute("SELECT * FROM accounts WHERE account_number=?", (account_number,)).fetchone()
        if account is None or amount <= 0:
            conn.close()
            flash("Please select a valid account and amount.", "danger")
            return redirect(url_for("transactions_page"))
        if transaction_type == "Withdraw" and account["balance"] < amount:
            conn.close()
            flash("Insufficient balance for withdrawal.", "danger")
            return redirect(url_for("transactions_page"))
        new_balance = account["balance"] + amount if transaction_type == "Deposit" else account["balance"] - amount
        conn.execute("UPDATE accounts SET balance=? WHERE account_number=?", (new_balance, account_number))
        conn.execute("""
            INSERT INTO transactions (transaction_id, account_number, transaction_type, amount, created_at, transaction_date, balance_after)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (next_transaction_id(conn), account_number, transaction_type, amount, display_datetime(), today_iso(), new_balance))
        conn.commit()
        conn.close()
        flash("Transaction completed successfully.", "success")
        return redirect(url_for("transactions_page"))
    accounts = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
    transactions = conn.execute("SELECT * FROM transactions ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("transactions.html", accounts=accounts, transactions=transactions)


@app.route("/transfer", methods=["GET", "POST"])
@manager_login_required
def transfer_page():
    conn = get_db_connection()
    if request.method == "POST":
        if not is_manager_logged_in():
            conn.close()
            flash("Manager login required for account transfer.", "warning")
            return redirect(url_for("login_page", next=url_for("transfer_page")))
        from_account = request.form.get("from_account", "").strip()
        to_account = request.form.get("to_account", "").strip()
        comment = request.form.get("comment", "").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0
        if not from_account or not to_account or from_account == to_account or amount <= 0:
            conn.close()
            flash("Please select two different accounts and enter a valid amount.", "danger")
            return redirect(url_for("transfer_page"))
        sender = conn.execute("SELECT * FROM accounts WHERE account_number=?", (from_account,)).fetchone()
        receiver = conn.execute("SELECT * FROM accounts WHERE account_number=?", (to_account,)).fetchone()
        if sender is None or receiver is None:
            conn.close()
            flash("One or both account numbers are invalid.", "danger")
            return redirect(url_for("transfer_page"))
        if sender["balance"] < amount:
            conn.close()
            flash("Insufficient balance in the sender account.", "danger")
            return redirect(url_for("transfer_page"))
        sender_new_balance = sender["balance"] - amount
        receiver_new_balance = receiver["balance"] + amount
        conn.execute("UPDATE accounts SET balance=? WHERE account_number=?", (sender_new_balance, from_account))
        conn.execute("UPDATE accounts SET balance=? WHERE account_number=?", (receiver_new_balance, to_account))
        txn_id = next_transaction_id(conn)
        now_text = display_datetime()
        today_text = today_iso()
        conn.execute("""
            INSERT INTO transactions (transaction_id, account_number, transaction_type, amount, created_at, transaction_date, from_account_number, to_account_number, comment, balance_after)
            VALUES (?, ?, 'Transfer Out', ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id, from_account, amount, now_text, today_text, from_account, to_account, comment, sender_new_balance))
        conn.execute("""
            INSERT INTO transactions (transaction_id, account_number, transaction_type, amount, created_at, transaction_date, from_account_number, to_account_number, comment, balance_after)
            VALUES (?, ?, 'Transfer In', ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id + 1, to_account, amount, now_text, today_text, from_account, to_account, comment, receiver_new_balance))
        conn.commit()
        conn.close()
        flash(f"Transfer completed successfully from {from_account} to {to_account}.", "success")
        return redirect(url_for("transfer_page"))
    accounts = conn.execute("SELECT * FROM accounts ORDER BY account_number").fetchall()
    recent_transfers = conn.execute("""
        SELECT * FROM transactions
        WHERE transaction_type IN ('Transfer Out', 'Transfer In')
        ORDER BY id DESC LIMIT 20
    """).fetchall()
    conn.close()
    return render_template("transfer.html", accounts=accounts, recent_transfers=recent_transfers)


@app.route("/my-transfer", methods=["GET", "POST"])
@customer_login_required
def customer_transfer_page():
    conn = get_db_connection()
    customer_id = get_logged_customer_id()
    customer = conn.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    my_account = conn.execute("SELECT * FROM accounts WHERE customer_id=?", (customer_id,)).fetchone()

    if not my_account:
        conn.close()
        flash("You do not have a savings account yet. Please contact the branch manager.", "warning")
        return redirect(url_for("customer_dashboard"))

    if customer["status"] != "Active":
        conn.close()
        flash("Your account is currently inactive. Please contact the branch manager to activate your account.", "warning")
        return redirect(url_for("customer_dashboard"))

    if request.method == "POST":
        to_account_number = request.form.get("to_account", "").strip()
        comment = request.form.get("comment", "").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0

        from_account_number = my_account["account_number"]

        if not to_account_number or from_account_number == to_account_number or amount <= 0:
            conn.close()
            flash("Please enter a valid recipient account number and a positive amount.", "danger")
            return redirect(url_for("customer_transfer_page"))

        receiver = conn.execute("SELECT * FROM accounts WHERE account_number=?", (to_account_number,)).fetchone()
        if receiver is None:
            conn.close()
            flash("Recipient account number not found. Please check and try again.", "danger")
            return redirect(url_for("customer_transfer_page"))

        sender = conn.execute("SELECT * FROM accounts WHERE account_number=?", (from_account_number,)).fetchone()
        if sender["balance"] < amount:
            conn.close()
            flash("Insufficient funds in your account.", "danger")
            return redirect(url_for("customer_transfer_page"))

        sender_new_balance = sender["balance"] - amount
        receiver_new_balance = receiver["balance"] + amount
        conn.execute("UPDATE accounts SET balance=? WHERE account_number=?", (sender_new_balance, from_account_number))
        conn.execute("UPDATE accounts SET balance=? WHERE account_number=?", (receiver_new_balance, to_account_number))
        txn_id = next_transaction_id(conn)
        now_text = display_datetime()
        today_text = today_iso()
        conn.execute("""
            INSERT INTO transactions (transaction_id, account_number, transaction_type, amount, created_at, transaction_date, from_account_number, to_account_number, comment, balance_after)
            VALUES (?, ?, 'Transfer Out', ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id, from_account_number, amount, now_text, today_text, from_account_number, to_account_number, comment, sender_new_balance))
        conn.execute("""
            INSERT INTO transactions (transaction_id, account_number, transaction_type, amount, created_at, transaction_date, from_account_number, to_account_number, comment, balance_after)
            VALUES (?, ?, 'Transfer In', ?, ?, ?, ?, ?, ?, ?)
        """, (txn_id + 1, to_account_number, amount, now_text, today_text, from_account_number, to_account_number, comment, receiver_new_balance))
        conn.commit()
        conn.close()
        flash(f"Transfer of ${amount:.2f} to account {to_account_number} completed successfully.", "success")
        return redirect(url_for("customer_transfer_page"))

    recent_transfers = conn.execute("""
        SELECT * FROM transactions
        WHERE account_number=? AND transaction_type IN ('Transfer Out', 'Transfer In')
        ORDER BY id DESC LIMIT 20
    """, (my_account["account_number"],)).fetchall()
    conn.close()
    return render_template("customer_transfer.html", my_account=my_account, recent_transfers=recent_transfers)


@app.route("/reports")
def reports_page():
    return render_template("reports.html")


@app.route("/reports/account-statement", methods=["GET", "POST"])
def account_statement_report():
    if not is_manager_logged_in() and not is_customer_logged_in():
        flash("Please login as customer or manager to download an account statement.", "warning")
        return redirect(url_for("customer_login_page", next=url_for("account_statement_report")))
    conn = get_db_connection()
    if is_manager_logged_in():
        accounts = conn.execute("SELECT * FROM accounts ORDER BY account_number").fetchall()
    else:
        accounts = conn.execute("SELECT * FROM accounts WHERE customer_id=? ORDER BY account_number", (get_logged_customer_id(),)).fetchall()
    if request.method == "POST":
        if is_manager_logged_in():
            account_number = request.form.get("account_number", "").strip()
        else:
            account = conn.execute("SELECT * FROM accounts WHERE customer_id=?", (get_logged_customer_id(),)).fetchone()
            account_number = account["account_number"] if account else ""
        start_date = request.form.get("start_date", "").strip()
        if not account_number or not start_date:
            conn.close()
            flash("Please select account number and start date. If you do not have an account yet, contact the branch manager.", "danger")
            return redirect(url_for("account_statement_report"))
        try:
            selected_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            conn.close()
            flash("Please enter a valid start date.", "danger")
            return redirect(url_for("account_statement_report"))
        if selected_start > date.today():
            conn.close()
            flash("Start date cannot be after today.", "danger")
            return redirect(url_for("account_statement_report"))
        if is_customer_logged_in() and not is_manager_logged_in():
            account = conn.execute("SELECT * FROM accounts WHERE account_number=? AND customer_id=?", (account_number, get_logged_customer_id())).fetchone()
        else:
            account = conn.execute("SELECT * FROM accounts WHERE account_number=?", (account_number,)).fetchone()
        if account is None:
            conn.close()
            flash("Account not found or you do not have permission to view it.", "danger")
            return redirect(url_for("account_statement_report"))
        transactions = conn.execute("""
            SELECT * FROM transactions
            WHERE account_number=? AND transaction_date BETWEEN ? AND ?
            ORDER BY id ASC
        """, (account_number, start_date, today_iso())).fetchall()
        conn.close()
        rows = [["Date", "Type", "Amount", "From", "To", "Balance After", "Comment"]]
        for t in transactions:
            rows.append([t["created_at"], t["transaction_type"], f"${t['amount']:.2f}", t["from_account_number"] or "-", t["to_account_number"] or "-", f"${(t['balance_after'] or 0):.2f}", t["comment"] or "-"])
        subtitle = f"Account: {account_number} | Customer: {account['customer_name']} | Period: {start_date} to {today_iso()} | Current Balance: ${account['balance']:.2f}"
        return create_pdf_response("Account Statement", subtitle, rows, f"statement_{account_number}_{today_iso()}.pdf")
    conn.close()
    return render_template("account_statement.html", accounts=accounts)


@app.route("/reports/management", methods=["GET", "POST"])
@manager_login_required
def management_report():
    if request.method == "POST":
        report_type = request.form.get("report_type", "all")
        conn = get_db_connection()
        if report_type == "today":
            accounts = conn.execute("SELECT * FROM accounts WHERE opened_date=? ORDER BY id DESC", (today_iso(),)).fetchall()
            title = "Accounts Opened Today"
            subtitle = f"Report Date: {today_iso()}"
            filename = f"accounts_opened_today_{today_iso()}.pdf"
        else:
            accounts = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
            title = "All Savings Accounts Report"
            subtitle = f"All accounts up to: {today_iso()}"
            filename = f"all_accounts_{today_iso()}.pdf"
        conn.close()
        rows = [["Account No.", "Customer ID", "Customer Name", "Type", "Balance", "Opened Date"]]
        for a in accounts:
            rows.append([a["account_number"], a["customer_id"], a["customer_name"], a["account_type"], f"${a['balance']:.2f}", a["opened_date"] or a["opened_at"]])
        return create_pdf_response(title, subtitle, rows, filename)
    return render_template("management_report.html")


@app.route("/contact", methods=["GET", "POST"])
def contact_page():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        enquiry = request.form.get("enquiry", "").strip()
        comment = request.form.get("comment", "").strip()
        if not name or not phone or not email or not enquiry:
            flash("Please complete name, phone, email, and your enquiry.", "danger")
            return render_template("contact.html")
        conn = get_db_connection()
        conn.execute("INSERT INTO enquiries (enquiry_id, name, phone, email, enquiry, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (next_enquiry_id(), name, phone, email, enquiry, comment, display_datetime()))
        conn.commit()
        conn.close()
        flash("Thank you. Your enquiry has been submitted successfully.", "success")
        return redirect(url_for("contact_page"))
    return render_template("contact.html")


@app.route("/manager/inbox")
@manager_login_required
def manager_inbox():
    conn = get_db_connection()
    enquiries = conn.execute("SELECT * FROM enquiries ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("manager_inbox.html", enquiries=enquiries)


@app.route("/manager/inbox/<int:enquiry_id>/read", methods=["POST"])
@manager_login_required
def mark_enquiry_read(enquiry_id):
    conn = get_db_connection()
    conn.execute("UPDATE enquiries SET is_read=1 WHERE enquiry_id=?", (enquiry_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("manager_inbox"))


@app.route("/manager/inbox/<int:enquiry_id>/unread", methods=["POST"])
@manager_login_required
def mark_enquiry_unread(enquiry_id):
    conn = get_db_connection()
    conn.execute("UPDATE enquiries SET is_read=0 WHERE enquiry_id=?", (enquiry_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("manager_inbox"))


@app.route("/manager/inbox/mark-all-read", methods=["POST"])
@manager_login_required
def mark_all_read():
    conn = get_db_connection()
    conn.execute("UPDATE enquiries SET is_read=1")
    conn.commit()
    conn.close()
    flash("All messages marked as read.", "success")
    return redirect(url_for("manager_inbox"))


@app.route("/manager/inbox/<int:enquiry_id>/delete", methods=["POST"])
@manager_login_required
def delete_enquiry(enquiry_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM enquiries WHERE enquiry_id=?", (enquiry_id,))
    conn.commit()
    conn.close()
    flash("Message deleted.", "success")
    return redirect(url_for("manager_inbox"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)
