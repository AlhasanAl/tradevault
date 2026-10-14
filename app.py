import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "dev"  # needed for on-screen messages; change before deploying

CUSTOMER_ID = 1  # test customer until login is built


def get_db():
    conn = sqlite3.connect("tradevault.db")
    conn.row_factory = sqlite3.Row
    return conn


def get_account(db):
    return db.execute(
        "SELECT * FROM cash_accounts WHERE customer_id = ?", (CUSTOMER_ID,)
    ).fetchone()


@app.route("/")
def home():
    return "TradeVault is running"


@app.route("/cash")
def cash():
    db = get_db()
    account = get_account(db)
    db.close()
    return render_template("cash.html", balance=account["balance"])


@app.route("/cash/<action>", methods=["POST"])
def update_cash(action):
    try:
        amount = round(float(request.form["amount"]), 2)
    except ValueError:
        flash("Enter a valid amount.")
        return redirect(url_for("cash"))

    if amount <= 0:
        flash("Amount must be greater than zero.")
        return redirect(url_for("cash"))

    db = get_db()
    account = get_account(db)

    if action == "withdraw" and amount > round(account["balance"], 2):
        flash("Insufficient funds. You can withdraw up to your cash balance.")
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
        flash(f"{action.capitalize()} of ${amount:.2f} successful.")

    db.close()
    return redirect(url_for("cash"))


# ---------- Market schedule (admin) ----------
from datetime import datetime


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


@app.route("/admin/market")
def admin_market():
    db = get_db()
    sched = db.execute(
        "SELECT open_time, close_time FROM market_schedule WHERE schedule_id = 1"
    ).fetchone()
    holidays = db.execute("SELECT holiday_date FROM holidays ORDER BY holiday_date").fetchall()
    is_open, status = market_is_open(db)
    db.close()
    return render_template("admin_market.html", sched=sched, holidays=holidays,
                           status=status, is_open=is_open)


@app.route("/admin/market/hours", methods=["POST"])
def update_hours():
    open_time = request.form["open_time"]
    close_time = request.form["close_time"]
    if open_time >= close_time:
        flash("Invalid schedule: open time must be before close time.")
    else:
        db = get_db()
        db.execute(
            "UPDATE market_schedule SET open_time = ?, close_time = ? WHERE schedule_id = 1",
            (open_time, close_time),
        )
        db.commit()
        db.close()
        flash(f"Market hours set to {open_time} - {close_time}.")
    return redirect(url_for("admin_market"))


@app.route("/admin/market/holidays", methods=["POST"])
def add_holiday():
    holiday_date = request.form["holiday_date"]
    db = get_db()
    try:
        datetime.strptime(holiday_date, "%Y-%m-%d")
        db.execute("INSERT INTO holidays (holiday_date) VALUES (?)", (holiday_date,))
        db.commit()
        flash(f"{holiday_date} added as a market holiday.")
    except ValueError:
        flash("Invalid schedule: enter a valid date.")
    except sqlite3.IntegrityError:
        flash("That date is already a holiday.")
    db.close()
    return redirect(url_for("admin_market"))
