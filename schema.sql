DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS cash_accounts;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE cash_accounts (
    account_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(customer_id),
    balance     REAL NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE transactions (
    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL REFERENCES cash_accounts(account_id),
    type           TEXT NOT NULL,                     -- deposit, withdraw, buy, sell
    amount         REAL NOT NULL,
    order_id       INTEGER REFERENCES orders(order_id),  -- set for buy and sell
    timestamp      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS market_schedule;
DROP TABLE IF EXISTS holidays;

CREATE TABLE market_schedule (
    schedule_id INTEGER PRIMARY KEY,
    open_time   TEXT NOT NULL,
    close_time  TEXT NOT NULL,
    override    TEXT CHECK (override IN ('open', 'closed'))  -- NULL = follow the schedule
);

CREATE TABLE holidays (
    holiday_date TEXT PRIMARY KEY
);

INSERT INTO market_schedule (schedule_id, open_time, close_time) VALUES (1, '09:30', '16:00');

DROP TABLE IF EXISTS price_history;
DROP TABLE IF EXISTS stocks;

CREATE TABLE stocks (
    stock_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name  TEXT NOT NULL,
    ticker        TEXT NOT NULL UNIQUE,
    volume        INTEGER NOT NULL CHECK (volume > 0),
    initial_price REAL NOT NULL CHECK (initial_price > 0)
);

CREATE TABLE price_history (
    price_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id  INTEGER NOT NULL REFERENCES stocks(stock_id),
    price     REAL NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS holdings;

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    stock_id    INTEGER NOT NULL REFERENCES stocks(stock_id),
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending, executed, cancelled, rejected
    price       REAL,                              -- market price when executed
    note        TEXT,                              -- reason when rejected
    created_at  REAL NOT NULL,                     -- seconds since epoch, for the cancel window
    timestamp   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE holdings (
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    stock_id    INTEGER NOT NULL REFERENCES stocks(stock_id),
    shares      INTEGER NOT NULL CHECK (shares > 0),
    avg_cost    REAL NOT NULL,
    PRIMARY KEY (customer_id, stock_id)
);
