import sqlite3
import os

def init_db():
    db_path = 'database.db'
    schema_path = 'database_sqlite.sql'
    
    # Try to remove existing database for a clean start
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            print(f"Removed existing {db_path}")
        except PermissionError:
            print(f"Warning: Could not remove {db_path} (file in use). Attempting to drop tables instead.")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            # Get all table names
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            for table in tables:
                if table[0] != 'sqlite_sequence':
                    cursor.execute(f"DROP TABLE IF EXISTS {table[0]};")
            conn.commit()
            conn.close()

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
