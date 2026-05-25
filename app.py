from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from datetime import datetime, date
from pathlib import Path
from io import BytesIO
from werkzeug.utils import secure_filename
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import sqlite3

app = Flask(__name__)
app.secret_key = "easy-bank-dev-secret"

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

    # Migration support for old Easy Bank database files
    add_column_if_missing(conn, "accounts", "opened_date", "opened_date TEXT")
    add_column_if_missing(conn, "transactions", "transaction_date", "transaction_date TEXT")
    add_column_if_missing(conn, "transactions", "from_account_number", "from_account_number TEXT")
    add_column_if_missing(conn, "transactions", "to_account_number", "to_account_number TEXT")
    add_column_if_missing(conn, "transactions", "comment", "comment TEXT")
    add_column_if_missing(conn, "transactions", "balance_after", "balance_after REAL")

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


def create_pdf_response(title, subtitle, table_data, filename):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=35, bottomMargin=25)
    styles = getSampleStyleSheet()
    elements = []
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
    return dict(
        bank_name=BANK_NAME,
        branch_name=BRANCH_NAME,
        tagline=TAGLINE,
        footer_text=FOOTER_TEXT,
        contact_phone_display=CONTACT_PHONE_DISPLAY,
        contact_phone_whatsapp=CONTACT_PHONE_WHATSAPP,
        branch_manager=BRANCH_MANAGER,
        today_date=today_iso(),
    )


@app.route("/")
def home():
    conn = get_db_connection()
    total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    total_accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    total_transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    total_balance = conn.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]
    conn.close()
    return render_template("home.html", total_customers=total_customers, total_accounts=total_accounts, total_transactions=total_transactions, total_balance=total_balance)


@app.route("/customers")
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
        status = request.form.get("status", "Active")
        sex = request.form.get("sex", "").strip()
        nationality = request.form.get("nationality", "").strip()
        agree = request.form.get("agree")
        required_fields = {"Full name": full_name, "Phone": phone, "Email": email, "Address": address, "Date of birth": date_of_birth, "Occupation": occupation, "ID number": id_number, "Sex": sex, "Nationality": nationality, "Status": status}
        missing_fields = [label for label, value in required_fields.items() if not value]
        if missing_fields:
            flash("Please complete all required fields: " + ", ".join(missing_fields) + ".", "danger")
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
        duplicate = conn.execute("SELECT customer_id FROM customers WHERE lower(phone)=lower(?) OR lower(email)=lower(?) OR lower(id_number)=lower(?)", (phone, email, id_number)).fetchone()
        if duplicate:
            conn.close()
            flash("Phone, email, or ID number is already registered.", "danger")
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
            INSERT INTO customers (customer_id, full_name, phone, email, address, date_of_birth, occupation, id_number, status, sex, nationality, photo_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (customer_id, full_name, phone, email, address, date_of_birth, occupation, id_number, status, sex, nationality, photo_path, display_datetime()))
        conn.commit()
        conn.close()
        flash(f"Customer added successfully. Customer ID: {customer_id}", "success")
        return redirect(url_for("customers_page"))
    return render_template("add_customer.html")


@app.route("/accounts")
def accounts_page():
    conn = get_db_connection()
    accounts = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("accounts.html", accounts=accounts)


@app.route("/accounts/open", methods=["GET", "POST"])
def open_account():
    if request.method == "POST":
        customer_id = request.form.get("customer_id", "").strip().upper()
        conn = get_db_connection()
        customer = conn.execute("SELECT * FROM customers WHERE upper(customer_id)=?", (customer_id,)).fetchone()
        if customer is None:
            conn.close()
            flash("Customer ID not found. Please enter a valid Customer ID, for example EB-C0001.", "danger")
            return redirect(url_for("open_account"))
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
def transactions_page():
    conn = get_db_connection()
    if request.method == "POST":
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
def transfer_page():
    conn = get_db_connection()
    if request.method == "POST":
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


@app.route("/reports")
def reports_page():
    return render_template("reports.html")


@app.route("/reports/account-statement", methods=["GET", "POST"])
def account_statement_report():
    conn = get_db_connection()
    accounts = conn.execute("SELECT * FROM accounts ORDER BY account_number").fetchall()
    if request.method == "POST":
        account_number = request.form.get("account_number", "").strip()
        start_date = request.form.get("start_date", "").strip()
        if not account_number or not start_date:
            conn.close()
            flash("Please select account number and start date.", "danger")
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
        account = conn.execute("SELECT * FROM accounts WHERE account_number=?", (account_number,)).fetchone()
        transactions = conn.execute("""
            SELECT * FROM transactions
            WHERE account_number=? AND transaction_date BETWEEN ? AND ?
            ORDER BY id ASC
        """, (account_number, start_date, today_iso())).fetchall()
        conn.close()
        rows = [["Date", "Type", "Amount", "From", "To", "Balance After", "Comment"]]
        for t in transactions:
            rows.append([t["created_at"], t["transaction_type"], f"${t['amount']:.2f}", t["from_account_number"] or "-", t["to_account_number"] or "-", f"${(t['balance_after'] or 0):.2f}", t["comment"] or "-"])
        subtitle = f"Account: {account_number} | Customer: {account['customer_name'] if account else '-'} | Period: {start_date} to {today_iso()} | Current Balance: ${account['balance'] if account else 0:.2f}"
        return create_pdf_response("Account Statement", subtitle, rows, f"statement_{account_number}_{today_iso()}.pdf")
    conn.close()
    return render_template("account_statement.html", accounts=accounts)


@app.route("/reports/management", methods=["GET", "POST"])
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


init_db()

if __name__ == "__main__":
    app.run(debug=True)
