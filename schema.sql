DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS cash_accounts;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name   TEXT NOT NULL,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE
);

CREATE TABLE cash_accounts (
    account_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL UNIQUE REFERENCES customers(customer_id),
    balance     REAL NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

CREATE TABLE transactions (
    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL REFERENCES cash_accounts(account_id),
    type           TEXT NOT NULL,
    amount         REAL NOT NULL,
    timestamp      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO customers (full_name, username, email)
VALUES ('Test Customer', 'testuser', 'test@example.com');
INSERT INTO cash_accounts (customer_id, balance) VALUES (1, 0);
