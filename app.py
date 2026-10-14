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
