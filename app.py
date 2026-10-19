import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
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
        if "customer_id" in session:
            # the account may be gone if the database was rebuilt with init_db.py
            db = get_db()
            exists = db.execute("SELECT 1 FROM customers WHERE customer_id = ?", (session["customer_id"],)).fetchone()
            db.close()
            if not exists:
                session.clear()
        if "customer_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def market_is_open(db):
    override = db.execute("SELECT override FROM market_schedule WHERE schedule_id = 1").fetchone()["override"]
    if override == "open":
        return True, "Open (administrator override)"
    if override == "closed":
        return False, "Closed (administrator override)"
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
    override = db.execute("SELECT override FROM market_schedule WHERE schedule_id = 1").fetchone()["override"]
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
                           status=status, is_open=is_open, week=week, override=override,
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


@app.route("/admin/market/override", methods=["POST"])
def set_override():
    choice = request.form.get("override")
    value = {"open": "open", "closed": "closed", "schedule": None}.get(choice, "invalid")
    if value == "invalid":
        flash("Choose open, closed, or follow the schedule.", "error")
    else:
        db = get_db()
        db.execute("UPDATE market_schedule SET override = ? WHERE schedule_id = 1", (value,))
        db.commit()
        db.close()
        flash({"open": "Market opened by administrator override.",
               "closed": "Market closed by administrator override.",
               "schedule": "Market now follows the regular schedule."}[choice], "success")
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



# ---------- Random stock price generator and market listing ----------
TICK_SECONDS = 3      # at most one price change every 3 seconds
MAX_STEP = 0.005      # each change moves a price up or down by at most 0.5%
_last_tick = 0.0


def latest_price(db, stock_id):
    return db.execute(
        "SELECT price FROM price_history WHERE stock_id = ? ORDER BY price_history_id DESC LIMIT 1",
        (stock_id,),
    ).fetchone()["price"]


def tick_prices(db):
    """While the market is open, move every stock's price a small random step."""
    global _last_tick
    is_open, _ = market_is_open(db)
    if not is_open or time.time() - _last_tick < TICK_SECONDS:
        return
    _last_tick = time.time()
    for s in db.execute("SELECT stock_id FROM stocks").fetchall():
        old = latest_price(db, s["stock_id"])
        step = random.uniform(-MAX_STEP, MAX_STEP)
        new = round(old * (1 + step), 2)
        if new == old:  # low-priced stocks: move at least one cent
            new = round(old + (0.01 if step >= 0 else -0.01), 2)
        new = max(0.01, new)
        db.execute("INSERT INTO price_history (stock_id, price) VALUES (?, ?)", (s["stock_id"], new))
    db.commit()


def market_rows(db):
    # start of today in local time, converted to UTC because SQLite stores UTC
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for s in db.execute("SELECT * FROM stocks ORDER BY ticker").fetchall():
        price = latest_price(db, s["stock_id"])
        today = [r["price"] for r in db.execute(
            "SELECT price FROM price_history WHERE stock_id = ? AND timestamp >= ? ORDER BY price_history_id",
            (s["stock_id"], start_utc),
        ).fetchall()] or [price]
        opening = today[0]
        rows.append({
            "ticker": s["ticker"],
            "company": s["company_name"],
            "price": price,
            "open": opening,
            "high": max(today),
            "low": min(today),
            "change": round(price - opening, 2),
            "change_pct": round((price - opening) / opening * 100, 2),
            "volume": s["volume"],
            "market_cap": round(s["volume"] * price, 2),
        })
    return rows


@app.route("/market")
def market():
    db = get_db()
    stocks = market_rows(db)
    db.close()
    return render_template("market.html", stocks=stocks, tick_seconds=TICK_SECONDS)


@app.route("/api/market")
def market_api():
    db = get_db()
    stocks = market_rows(db)
    is_open, status = market_is_open(db)
    db.close()
    return jsonify({"open": is_open, "status": status, "stocks": stocks})



# ---------- Buy and sell order engine ----------
EXECUTION_DELAY = 10  # seconds an order stays pending, so the customer can cancel it


def shares_available(db, stock_id):
    """Shares of a stock not yet owned by any customer (volume minus all holdings)."""
    volume = db.execute("SELECT volume FROM stocks WHERE stock_id = ?", (stock_id,)).fetchone()["volume"]
    held = db.execute("SELECT COALESCE(SUM(shares), 0) AS n FROM holdings WHERE stock_id = ?", (stock_id,)).fetchone()["n"]
    return volume - held


def shares_owned(db, customer_id, stock_id):
    row = db.execute(
        "SELECT shares FROM holdings WHERE customer_id = ? AND stock_id = ?", (customer_id, stock_id)
    ).fetchone()
    return row["shares"] if row else 0


def reject_order(db, order, reason):
    db.execute("UPDATE orders SET status = 'rejected', note = ? WHERE order_id = ?", (reason, order["order_id"]))


def execute_order(db, order):
    price = latest_price(db, order["stock_id"])
    qty = order["quantity"]
    total = round(price * qty, 2)
    account = db.execute(
        "SELECT * FROM cash_accounts WHERE customer_id = ?", (order["customer_id"],)
    ).fetchone()
    owned = shares_owned(db, order["customer_id"], order["stock_id"])

    if order["side"] == "buy":
        if total > round(account["balance"], 2):
            return reject_order(db, order, "Not enough cash at the execution price.")
        if qty > shares_available(db, order["stock_id"]):
            return reject_order(db, order, "Not enough shares available.")
        db.execute("UPDATE cash_accounts SET balance = ROUND(balance - ?, 2) WHERE account_id = ?",
                   (total, account["account_id"]))
        if owned:
            db.execute(
                "UPDATE holdings SET avg_cost = ROUND((shares * avg_cost + ?) / (shares + ?), 4), "
                "shares = shares + ? WHERE customer_id = ? AND stock_id = ?",
                (total, qty, qty, order["customer_id"], order["stock_id"]),
            )
        else:
            db.execute("INSERT INTO holdings (customer_id, stock_id, shares, avg_cost) VALUES (?, ?, ?, ?)",
                       (order["customer_id"], order["stock_id"], qty, price))
    else:
        if qty > owned:
            return reject_order(db, order, "You no longer own enough shares.")
        db.execute("UPDATE cash_accounts SET balance = ROUND(balance + ?, 2) WHERE account_id = ?",
                   (total, account["account_id"]))
        if qty == owned:
            db.execute("DELETE FROM holdings WHERE customer_id = ? AND stock_id = ?",
                       (order["customer_id"], order["stock_id"]))
        else:
            db.execute("UPDATE holdings SET shares = shares - ? WHERE customer_id = ? AND stock_id = ?",
                       (qty, order["customer_id"], order["stock_id"]))

    db.execute("UPDATE orders SET status = 'executed', price = ? WHERE order_id = ?", (price, order["order_id"]))
    db.execute("INSERT INTO transactions (account_id, type, amount, order_id) VALUES (?, ?, ?, ?)",
               (account["account_id"], order["side"], total, order["order_id"]))


def process_orders(db):
    """Execute pending orders whose cancel window has passed, but only while the market is open."""
    is_open, _ = market_is_open(db)
    if not is_open:
        return
    due = db.execute(
        "SELECT * FROM orders WHERE status = 'pending' AND created_at <= ? ORDER BY order_id",
        (time.time() - EXECUTION_DELAY,),
    ).fetchall()
    for order in due:
        execute_order(db, order)
    db.commit()


@app.before_request
def run_market():
    """Each request moves prices (at most every few seconds) and executes orders that are due."""
    if request.endpoint == "static":
        return
    db = get_db()
    tick_prices(db)
    process_orders(db)
    db.close()


def customer_orders(db, limit=10):
    return db.execute(
        "SELECT o.*, s.ticker FROM orders o JOIN stocks s ON s.stock_id = o.stock_id "
        "WHERE o.customer_id = ? ORDER BY o.order_id DESC LIMIT ?",
        (session["customer_id"], limit),
    ).fetchall()


def order_json(o):
    seconds_left = max(0, int(EXECUTION_DELAY - (time.time() - o["created_at"]))) if o["status"] == "pending" else 0
    return {
        "id": o["order_id"], "side": o["side"], "ticker": o["ticker"], "quantity": o["quantity"],
        "status": o["status"], "price": o["price"], "note": o["note"],
        "total": round(o["price"] * o["quantity"], 2) if o["price"] is not None else None,
        "time": nice_time(o["timestamp"]), "seconds_left": seconds_left,
    }


@app.route("/trade")
@login_required
def trade():
    db = get_db()
    stocks = market_rows(db)
    for s in stocks:
        row = db.execute("SELECT stock_id FROM stocks WHERE ticker = ?", (s["ticker"],)).fetchone()
        s["owned"] = shares_owned(db, session["customer_id"], row["stock_id"])
    balance = get_account(db)["balance"]
    orders = [order_json(o) for o in customer_orders(db)]
    is_open, status = market_is_open(db)
    db.close()
    return render_template("trade.html", stocks=stocks, balance=balance, orders=orders,
                           is_open=is_open, status=status, delay=EXECUTION_DELAY,
                           selected=request.args.get("ticker", ""))


@app.route("/trade/order", methods=["POST"])
@login_required
def place_order():
    side = request.form.get("side")
    ticker = request.form.get("ticker", "").strip().upper()
    try:
        qty = int(request.form.get("quantity", ""))
    except ValueError:
        qty = 0

    db = get_db()
    stock = db.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()
    is_open, _ = market_is_open(db)
    error = None
    if side not in ("buy", "sell") or stock is None:
        error = "Choose a stock and whether to buy or sell."
    elif qty <= 0:
        error = "Quantity must be a whole number greater than zero."
    elif not is_open:
        error = "The market is closed. Orders can only be placed during market hours."
    else:
        price = latest_price(db, stock["stock_id"])
        if side == "buy":
            estimate = round(price * qty, 2)
            if estimate > round(get_account(db)["balance"], 2):
                error = f"Insufficient cash. {qty} {ticker} costs about ${estimate:,.2f} at the current price."
            elif qty > shares_available(db, stock["stock_id"]):
                error = f"Only {shares_available(db, stock['stock_id']):,} shares of {ticker} are available."
        else:
            pending_sells = db.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS n FROM orders WHERE customer_id = ? AND stock_id = ? "
                "AND side = 'sell' AND status = 'pending'",
                (session["customer_id"], stock["stock_id"]),
            ).fetchone()["n"]
            can_sell = shares_owned(db, session["customer_id"], stock["stock_id"]) - pending_sells
            if qty > can_sell:
                error = f"You have {can_sell} shares of {ticker} available to sell."

    if error:
        flash(error, "error")
    else:
        db.execute(
            "INSERT INTO orders (customer_id, stock_id, side, quantity, created_at) VALUES (?, ?, ?, ?, ?)",
            (session["customer_id"], stock["stock_id"], side, qty, time.time()),
        )
        db.commit()
        flash(f"{side.capitalize()} order for {qty} {ticker} placed. It executes at the market price "
              f"in about {EXECUTION_DELAY} seconds unless you cancel it.", "success")
    db.close()
    return redirect(url_for("trade", ticker=ticker))


@app.route("/trade/cancel/<int:order_id>", methods=["POST"])
@login_required
def cancel_order(order_id):
    db = get_db()
    order = db.execute(
        "SELECT status FROM orders WHERE order_id = ? AND customer_id = ?", (order_id, session["customer_id"])
    ).fetchone()
    if order is None:
        flash("Order not found.", "error")
    elif order["status"] != "pending":
        flash(f"That order was already {order['status']} and can no longer be cancelled.", "error")
    else:
        db.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,))
        db.commit()
        flash("Order cancelled before it executed.", "success")
    db.close()
    return redirect(url_for("trade"))


@app.route("/api/orders")
@login_required
def orders_api():
    db = get_db()
    balance = get_account(db)["balance"]
    orders = [order_json(o) for o in customer_orders(db)]
    stocks = [{"ticker": s["ticker"], "price": latest_price(db, s["stock_id"]),
               "owned": shares_owned(db, session["customer_id"], s["stock_id"])}
              for s in db.execute("SELECT stock_id, ticker FROM stocks").fetchall()]
    db.close()
    return jsonify({"balance": balance, "orders": orders, "stocks": stocks})


# ---------- Portfolio and transaction history ----------
@app.route("/portfolio")
@login_required
def portfolio():
    db = get_db()
    balance = get_account(db)["balance"]
    holdings = []
    for h in db.execute(
        "SELECT h.*, s.ticker, s.company_name FROM holdings h JOIN stocks s ON s.stock_id = h.stock_id "
        "WHERE h.customer_id = ? ORDER BY s.ticker", (session["customer_id"],)
    ).fetchall():
        price = latest_price(db, h["stock_id"])
        value = round(price * h["shares"], 2)
        cost = round(h["avg_cost"] * h["shares"], 2)
        holdings.append({"ticker": h["ticker"], "company": h["company_name"], "shares": h["shares"],
                         "avg_cost": h["avg_cost"], "price": price, "value": value,
                         "gain": round(value - cost, 2)})
    history = db.execute(
        "SELECT t.type, t.amount, t.timestamp, o.quantity, o.price, s.ticker "
        "FROM transactions t JOIN cash_accounts a ON a.account_id = t.account_id "
        "LEFT JOIN orders o ON o.order_id = t.order_id LEFT JOIN stocks s ON s.stock_id = o.stock_id "
        "WHERE a.customer_id = ? ORDER BY t.transaction_id DESC",
        (session["customer_id"],),
    ).fetchall()
    db.close()
    stock_value = round(sum(h["value"] for h in holdings), 2)
    return render_template("portfolio.html", balance=balance, holdings=holdings, history=history,
                           stock_value=stock_value, total=round(balance + stock_value, 2))
