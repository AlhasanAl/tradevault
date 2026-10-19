import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "dev"  # needed for on-screen messages; change before deploying

def get_db():
    conn = sqlite3.connect("tradevault.db")
    conn.row_factory = sqlite3.Row
    return conn


def get_account(db):
    return db.execute(
        "SELECT * FROM cash_accounts WHERE customer_id = ?", (session["customer_id"],)
    ).fetchone()


def login_required(view):
    def wrapped(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in to view your cash account.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def market_is_open(db):
    now = datetime.now()
    if now.weekday() >= 5:
        return False, "Closed: weekend"
    today = now.strftime("%Y-%m-%d")
    if db.execute("SELECT 1 FROM holidays WHERE holiday_date = ?", (today,)).fetchone():
        return False, "Closed: holiday"
    sched = db.execute(
        "SELECT open_time, close_time FROM market_schedule WHERE schedule_id = 1"
    ).fetchone()
    current = now.strftime("%H:%M")
    if sched["open_time"] <= current < sched["close_time"]:
        return True, "Open"
    return False, "Closed: outside market hours"


@app.context_processor
def inject_globals():
    db = get_db()
    is_open, _ = market_is_open(db)
    customer = None
    if "customer_id" in session:
        customer = db.execute(
            "SELECT full_name FROM customers WHERE customer_id = ?", (session["customer_id"],)
        ).fetchone()
    db.close()
    if customer is None:
        return {"market_open": is_open, "customer_name": None, "customer_initials": ""}
    name = customer["full_name"]
    initials = "".join(part[0] for part in name.split()[:2]).upper()
    return {"market_open": is_open, "customer_name": name, "customer_initials": initials}


@app.template_filter("nice_date")
def nice_date(value):
    return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%a, %b %d, %Y")


@app.template_filter("clock")
def clock(value):
    return datetime.strptime(value, "%H:%M").strftime("%I:%M %p").lstrip("0")


@app.template_filter("nice_time")
def nice_time(value):
    utc = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone().strftime("%b %d, %I:%M %p")  # SQLite stores UTC; show local time


@app.route("/")
def home():
    return redirect(url_for("cash") if "customer_id" in session else url_for("login"))


# ---------- Create account and log in (customer) ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip().lower()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if not full_name or not username or not email:
            flash("Full name, username, and email are required.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        else:
            db = get_db()
            if db.execute("SELECT 1 FROM customers WHERE username = ?", (username,)).fetchone():
                flash("That username is already taken.", "error")
            elif db.execute("SELECT 1 FROM customers WHERE email = ?", (email,)).fetchone():
                flash("An account with that email already exists.", "error")
            else:
                cur = db.execute(
                    "INSERT INTO customers (full_name, username, email, password_hash) VALUES (?, ?, ?, ?)",
                    (full_name, username, email, generate_password_hash(password)),
                )
                db.execute("INSERT INTO cash_accounts (customer_id, balance) VALUES (?, 0)", (cur.lastrowid,))
                db.commit()
                db.close()
                session["customer_id"] = cur.lastrowid
                flash(f"Welcome to TradeVault, {full_name}. Your cash account is ready.", "success")
                return redirect(url_for("cash"))
            db.close()
        return render_template("register.html", form=request.form)
    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        db = get_db()
        customer = db.execute("SELECT * FROM customers WHERE username = ?", (username,)).fetchone()
        db.close()
        if customer and check_password_hash(customer["password_hash"], request.form["password"]):
            session["customer_id"] = customer["customer_id"]
            return redirect(url_for("cash"))
        flash("Incorrect username or password.", "error")
        return render_template("login.html", username=username)
    return render_template("login.html", username="")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------- Cash account (customer) ----------
@app.route("/cash")
@login_required
def cash():
    db = get_db()
    account = get_account(db)
    activity = db.execute(
        "SELECT type, amount, timestamp FROM transactions WHERE account_id = ? "
        "ORDER BY transaction_id DESC LIMIT 5",
        (account["account_id"],),
    ).fetchall()
    db.close()
    return render_template("cash.html", balance=account["balance"], activity=activity)


@app.route("/cash/<action>", methods=["POST"])
@login_required
def update_cash(action):
    try:
        amount = round(float(request.form["amount"]), 2)
    except ValueError:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("cash"))

    if amount <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("cash"))

    db = get_db()
    account = get_account(db)

    if action == "withdraw" and amount > round(account["balance"], 2):
        flash("Insufficient funds. You can withdraw up to your cash balance.", "error")
    elif action in ("deposit", "withdraw"):
        change = amount if action == "deposit" else -amount
        db.execute(
            "UPDATE cash_accounts SET balance = ROUND(balance + ?, 2) WHERE account_id = ?",
            (change, account["account_id"]),
        )
        db.execute(
            "INSERT INTO transactions (account_id, type, amount) VALUES (?, ?, ?)",
            (account["account_id"], action, amount),
        )
        db.commit()
        flash(f"{action.capitalize()} of ${amount:,.2f} successful.", "success")

    db.close()
    return redirect(url_for("cash"))


# ---------- Market schedule (admin) ----------
@app.route("/admin/market")
def admin_market():
    db = get_db()
    sched = db.execute(
        "SELECT open_time, close_time FROM market_schedule WHERE schedule_id = 1"
    ).fetchone()
    holidays = db.execute("SELECT holiday_date FROM holidays ORDER BY holiday_date").fetchall()
    is_open, status = market_is_open(db)
    holiday_dates = {h["holiday_date"] for h in holidays}
    db.close()

    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    week = []
    for i in range(7):
        day = monday + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        week.append({
            "name": day.strftime("%a"),
            "date": day.strftime("%b %d"),
            "trading": i < 5 and key not in holiday_dates,
            "holiday": key in holiday_dates,
            "today": i == now.weekday(),
        })
    return render_template("admin_market.html", sched=sched, holidays=holidays,
                           status=status, is_open=is_open, week=week,
                           now=now.strftime("%I:%M %p").lstrip("0"))


@app.route("/admin/market/hours", methods=["POST"])
def update_hours():
    open_time = request.form["open_time"]
    close_time = request.form["close_time"]
    if open_time >= close_time:
        flash("Invalid schedule: open time must be before close time.", "error")
    else:
        db = get_db()
        db.execute(
            "UPDATE market_schedule SET open_time = ?, close_time = ? WHERE schedule_id = 1",
            (open_time, close_time),
        )
        db.commit()
        db.close()
        flash(f"Market hours set to {clock(open_time)} - {clock(close_time)}.", "success")
    return redirect(url_for("admin_market"))


@app.route("/admin/market/holidays", methods=["POST"])
def add_holiday():
    holiday_date = request.form["holiday_date"]
    db = get_db()
    try:
        datetime.strptime(holiday_date, "%Y-%m-%d")
        db.execute("INSERT INTO holidays (holiday_date) VALUES (?)", (holiday_date,))
        db.commit()
        flash(f"{nice_date(holiday_date)} added as a market holiday.", "success")
    except ValueError:
        flash("Invalid schedule: enter a valid date.", "error")
    except sqlite3.IntegrityError:
        flash("That date is already a holiday.", "error")
    db.close()
    return redirect(url_for("admin_market"))


# ---------- Create stock (admin) ----------
@app.route("/admin/stocks")
def admin_stocks():
    db = get_db()
    stocks = db.execute("SELECT * FROM stocks ORDER BY ticker").fetchall()
    db.close()
    total_cap = sum(s["volume"] * s["initial_price"] for s in stocks)
    return render_template("admin_stocks.html", stocks=stocks, total_cap=total_cap)


@app.route("/admin/stocks/create", methods=["POST"])
def create_stock():
    company = request.form["company_name"].strip()
    ticker = request.form["ticker"].strip().upper()
    try:
        volume = int(request.form["volume"])
        price = round(float(request.form["initial_price"]), 2)
    except ValueError:
        flash("Volume must be a whole number and price must be a number.", "error")
        return redirect(url_for("admin_stocks"))
    if not company or not ticker or volume <= 0 or price <= 0:
        flash("All fields are required. Volume and price must be greater than zero.", "error")
        return redirect(url_for("admin_stocks"))

    db = get_db()
    if db.execute("SELECT 1 FROM stocks WHERE ticker = ?", (ticker,)).fetchone():
        flash(f"Ticker {ticker} already exists.", "error")
    else:
        cur = db.execute(
            "INSERT INTO stocks (company_name, ticker, volume, initial_price) VALUES (?, ?, ?, ?)",
            (company, ticker, volume, price),
        )
        db.execute(
            "INSERT INTO price_history (stock_id, price) VALUES (?, ?)",
            (cur.lastrowid, price),
        )
        db.commit()
        flash(f"{ticker} created at ${price:,.2f}.", "success")
    db.close()
    return redirect(url_for("admin_stocks"))
