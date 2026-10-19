"""TradeVault end-to-end integration tests.

WARNING: this WIPES tradevault.db and rebuilds it. Run `python init_db.py`
afterwards to start clean before a demo.

Run: python integration_test.py
"""
import subprocess, sys
subprocess.run([sys.executable, "init_db.py"], check=True)

import re, sqlite3, time
import app as A

results = []
def ok(label, cond):
    results.append(cond); print(("PASS " if cond else "FAIL ") + label)
def flash_of(html):
    m = re.search(r'<div class="flash (\w+)"[^>]*>(.*?)</div>', html, re.S)
    return (m.group(1), m.group(2).strip()) if m else None
def q(sql, *a):
    db = sqlite3.connect("tradevault.db"); db.row_factory = sqlite3.Row
    r = db.execute(sql, a).fetchall(); db.close(); return r
def age_orders():
    db = sqlite3.connect("tradevault.db")
    db.execute("UPDATE orders SET created_at = created_at - 60 WHERE status = 'pending'"); db.commit(); db.close()
def balance(user):
    return q("SELECT balance FROM cash_accounts a JOIN customers c ON c.customer_id=a.customer_id WHERE c.username=?", user)[0]["balance"]

class Browser:
    """Follows redirects like a real browser, so flash messages are shown and cleared."""
    def __init__(self): self.c = A.app.test_client()
    def get(self, url, **kw): return self.c.get(url, follow_redirects=kw.get("follow_redirects", True))
    def post(self, url, data=None, **kw): return self.c.post(url, data=data, follow_redirects=True)
a = Browser(); b = Browser(); admin = Browser()

print("--- Accounts")
r = a.get("/trade", follow_redirects=False); ok("trade page requires login", r.status_code == 302 and "/login" in r.location)
a.post("/register", data={"full_name": "Alhasan Abdulrazzaq", "username": "al", "email": "al@example.com", "password": "tradevault1"})
b.post("/register", data={"full_name": "Second Customer", "username": "second", "email": "b@example.com", "password": "password1"})
ok("two customers registered with cash accounts", len(q("SELECT * FROM cash_accounts")) == 2)

print("--- Admin setup")
admin.post("/admin/stocks/create", data={"company_name": "Apple Inc.", "ticker": "AAPL", "volume": "100", "initial_price": "100.00"})
admin.post("/admin/stocks/create", data={"company_name": "Tesla Inc.", "ticker": "TSLA", "volume": "1000000", "initial_price": "50.00"})
ok("two stocks listed", len(q("SELECT * FROM stocks")) == 2)

print("--- Cash")
a.post("/cash/deposit", data={"amount": "100000"})
ok("deposit $100,000", balance("al") == 100000)

print("--- Market hours")
admin.post("/admin/market/hours", data={"open_time": "00:00", "close_time": "00:01"})
r = a.post("/trade/order", data={"ticker": "AAPL", "side": "buy", "quantity": "10"}, follow_redirects=True)
ok("closed market: order refused (process map 2.2)", flash_of(r.text) == ("error", "The market is closed. Orders can only be placed during market hours."))
ok("closed market: no order saved", len(q("SELECT * FROM orders")) == 0)
admin.post("/admin/market/hours", data={"open_time": "00:00", "close_time": "23:59"})

print("--- Buy, then cancel before execution")
r = a.post("/trade/order", data={"ticker": "AAPL", "side": "buy", "quantity": "10"}, follow_redirects=True)
ok("buy order placed as pending", flash_of(r.text)[0] == "success" and q("SELECT status FROM orders")[0]["status"] == "pending")
ok("pending order does not move cash", balance("al") == 100000)
oid = q("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 1")[0]["order_id"]
r = b.post(f"/trade/cancel/{oid}", follow_redirects=True)
ok("another customer cannot cancel it", flash_of(r.text) == ("error", "Order not found.") and q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "pending")
r = a.post(f"/trade/cancel/{oid}", follow_redirects=True)
ok("owner cancels before execution", flash_of(r.text) == ("success", "Order cancelled before it executed."))
age_orders(); a.get("/trade")
ok("cancelled order never executes", q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "cancelled" and balance("al") == 100000)

print("--- Buy executes at market price")
a.post("/trade/order", data={"ticker": "AAPL", "side": "buy", "quantity": "10"})
oid = q("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 1")[0]["order_id"]
a.get("/trade")
ok("not executed during the cancel window", q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "pending")
age_orders(); a.get("/trade")
o = q("SELECT * FROM orders WHERE order_id=?", oid)[0]
ok("executed after the window", o["status"] == "executed" and o["price"] is not None)
cost = round(o["price"] * 10, 2)
ok("cash reduced by shares x execution price", abs(balance("al") - (100000 - cost)) < 0.005)
h = q("SELECT * FROM holdings")[0]
ok("portfolio holds 10 AAPL at the execution price", h["shares"] == 10 and abs(h["avg_cost"] - o["price"]) < 0.0001)
t = q("SELECT * FROM transactions WHERE order_id=?", oid)[0]
ok("buy recorded in transaction history", t["type"] == "buy" and abs(t["amount"] - cost) < 0.005)
r = a.post(f"/trade/cancel/{oid}", follow_redirects=True)
ok("executed order cannot be cancelled", flash_of(r.text) == ("error", "That order was already executed and can no longer be cancelled."))

print("--- Sell rules")
r = a.post("/trade/order", data={"ticker": "AAPL", "side": "sell", "quantity": "15"}, follow_redirects=True)
ok("cannot sell more than owned", flash_of(r.text) == ("error", "You have 10 shares of AAPL available to sell."))
a.post("/trade/order", data={"ticker": "AAPL", "side": "sell", "quantity": "4"})
r = a.post("/trade/order", data={"ticker": "AAPL", "side": "sell", "quantity": "7"}, follow_redirects=True)
ok("pending sells count against shares (no double-selling)", flash_of(r.text) == ("error", "You have 6 shares of AAPL available to sell."))
before = balance("al")
age_orders(); a.get("/trade")
o = q("SELECT * FROM orders WHERE side='sell' AND status='executed'")[0]
ok("sell proceeds go to the cash account", abs(balance("al") - (before + round(o["price"] * 4, 2))) < 0.005)
ok("holding reduced to 6", q("SELECT shares FROM holdings")[0]["shares"] == 6)

print("--- Buy rules")
r = b.post("/trade/order", data={"ticker": "TSLA", "side": "buy", "quantity": "1"}, follow_redirects=True)
ok("buy with no cash refused", flash_of(r.text)[0] == "error" and "Insufficient cash" in flash_of(r.text)[1])
r = a.post("/trade/order", data={"ticker": "AAPL", "side": "buy", "quantity": "95"}, follow_redirects=True)
ok("cannot buy more than available shares (volume 100, 6 owned)", flash_of(r.text) == ("error", "Only 94 shares of AAPL are available."))
for bad in ("0", "-3", "abc", "2.5"):
    r = a.post("/trade/order", data={"ticker": "AAPL", "side": "buy", "quantity": bad}, follow_redirects=True)
    ok(f"invalid quantity '{bad}' refused", flash_of(r.text)[0] == "error")
r = a.post("/trade/order", data={"ticker": "ZZZZ", "side": "buy", "quantity": "1"}, follow_redirects=True)
ok("unknown stock refused", flash_of(r.text)[0] == "error")

print("--- Rejected at execution time")
a.post("/trade/order", data={"ticker": "TSLA", "side": "buy", "quantity": "100"})
oid = q("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 1")[0]["order_id"]
a.post("/cash/withdraw", data={"amount": f"{balance('al'):.2f}"})
age_orders(); a.get("/trade")
o = q("SELECT * FROM orders WHERE order_id=?", oid)[0]
ok("order rejected when cash is gone at execution", o["status"] == "rejected" and o["note"] == "Not enough cash at the execution price.")
ok("rejected order changes nothing", balance("al") == 0 and len(q("SELECT * FROM holdings WHERE stock_id=2")) == 0)

print("--- Pending orders wait for market hours")
a.post("/cash/deposit", data={"amount": "1000"})
a.post("/trade/order", data={"ticker": "TSLA", "side": "buy", "quantity": "2"})
oid = q("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 1")[0]["order_id"]
admin.post("/admin/market/hours", data={"open_time": "00:00", "close_time": "00:01"})
age_orders(); a.get("/portfolio")
ok("no execution while market is closed", q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "pending")
admin.post("/admin/market/hours", data={"open_time": "00:00", "close_time": "23:59"})
a.get("/portfolio")
ok("executes once the market reopens", q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "executed")

print("--- Sell everything")
a.post("/trade/order", data={"ticker": "AAPL", "side": "sell", "quantity": "6"})
age_orders(); a.get("/trade")
ok("selling all shares removes the holding", len(q("SELECT * FROM holdings WHERE stock_id=1")) == 0)

print("--- Pages and API")
r = a.get("/portfolio")
ok("portfolio page loads with holdings and history", r.status_code == 200 and "TSLA" in r.text and "Transaction history" in r.text)
cash_now = balance("al"); val = sum(round(A.latest_price(A.get_db(), x["stock_id"]) * x["shares"], 2) for x in q("SELECT * FROM holdings WHERE customer_id=1"))
ok("portfolio total = cash + stock value", f"${cash_now + val:,.2f}" in r.text)
r = a.get("/cash")
ok("cash activity labels stock purchases and sales", "Stock purchase" in r.text and "Stock sale" in r.text)
d = b.get("/api/orders").get_json()
ok("customers only see their own orders", d["orders"] == [])
ok("market page and API work", a.get("/market").status_code == 200 and a.get("/api/market").get_json()["open"] is True)
ok("admin pages work", admin.get("/admin/stocks").status_code == 200 and admin.get("/admin/market").status_code == 200)
ok("trade page loads", a.get("/trade").status_code == 200)

print("--- Administrator override (simulated Sunday night)")
from datetime import datetime as _dt
class SundayNight(_dt):
    @classmethod
    def now(cls, tz=None):
        return _dt(2026, 10, 18, 22, 0)
real_dt = A.datetime
A.datetime = SundayNight
admin.post("/admin/market/hours", data={"open_time": "00:00", "close_time": "23:59"})
admin.post("/admin/market/override", data={"override": "schedule"})
d = a.get("/api/market").get_json()
ok("Sunday: closed even with all-day hours", d["open"] is False and d["status"] == "Closed: weekend")
r = a.post("/trade/order", data={"ticker": "TSLA", "side": "buy", "quantity": "1"})
ok("Sunday: orders refused", flash_of(r.text)[0] == "error" and "market is closed" in flash_of(r.text)[1])
r = admin.post("/admin/market/override", data={"override": "open"})
ok("override open message", flash_of(r.text) == ("success", "Market opened by administrator override."))
d = a.get("/api/market").get_json()
ok("Sunday + override: market open", d["open"] is True and d["status"] == "Open (administrator override)")
A._last_tick = 0
p0 = A.latest_price(A.get_db(), 2); A._last_tick = 0; a.get("/api/market"); A._last_tick = 0; a.get("/api/market")
ok("Sunday + override: price history grows", len(q("SELECT * FROM price_history WHERE stock_id=2")) > 3)
a.post("/cash/deposit", data={"amount": "1000"})
a.post("/trade/order", data={"ticker": "TSLA", "side": "buy", "quantity": "1"})
oid = q("SELECT order_id FROM orders ORDER BY order_id DESC LIMIT 1")[0]["order_id"]
age_orders(); a.get("/trade")
ok("Sunday + override: order executes", q("SELECT status FROM orders WHERE order_id=?", oid)[0]["status"] == "executed")
admin.post("/admin/market/override", data={"override": "closed"})
ok("override closed: market closed", a.get("/api/market").get_json()["status"] == "Closed (administrator override)")
r = admin.post("/admin/market/override", data={"override": "bogus"})
ok("invalid override refused", flash_of(r.text)[0] == "error")
admin.post("/admin/market/override", data={"override": "schedule"})
ok("follow schedule: back to weekend rule", a.get("/api/market").get_json()["status"] == "Closed: weekend")
A.datetime = real_dt
ok("market schedule page shows override controls", "Open now" in admin.get("/admin/market").text)

print("--- Database rebuilt while logged in")
stale = Browser()
stale.post("/register", data={"full_name": "Stale User", "username": "stale", "email": "s@example.com", "password": "password1"})
import subprocess as _sp, sys as _sys
_sp.run([_sys.executable, "init_db.py"], check=True, capture_output=True)
r = stale.get("/cash", follow_redirects=False)
ok("deleted account: redirected to login instead of crashing", r.status_code == 302 and "/login" in r.location)
r = stale.get("/cash")
ok("deleted account: login page explains", r.status_code == 200 and flash_of(r.text) == ("error", "Please log in to continue."))
ok("deleted account: header shows Log in, not a stale name", "Log out" not in r.text)
for path in ("/trade", "/portfolio", "/api/orders"):
    ok(f"deleted account: {path} does not crash", stale.get(path, follow_redirects=False).status_code == 302)

print(f"\n{sum(results)}/{len(results)} passed")
