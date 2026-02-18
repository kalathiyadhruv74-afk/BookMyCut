import sqlite3
import os

def init_db():
    db_path = os.environ.get('DATABASE_PATH', 'database.db')
    schema_path = 'database_sqlite.sql'
    
    # Connect to SQLite database
    conn = sqlite3.connect(db_path)
    print(f"Connected to {db_path}")

    # Read schema file
    with open(schema_path, 'r') as f:
        schema_sql = f.read()

    # Execute schema
    try:
        conn.executescript(schema_sql)
        print("Schema executed successfully")
    except sqlite3.Error as e:
        print(f"An error occurred: {e}")
    finally:
        conn.close()
        print("Connection closed")

if __name__ == '__main__':
    init_db()
