import sqlite3

connection = sqlite3.connect(r"D:\JobLogDemo\sitelog.db")
tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables", tables)
if "mailbox" in tables:
    rows = list(connection.execute("SELECT host, email, CASE WHEN password IS NULL OR password = '' THEN 0 ELSE 1 END FROM mailbox"))
    print("mailbox_has_password", rows)
