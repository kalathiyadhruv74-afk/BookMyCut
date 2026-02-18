import sqlite3
import os
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta, timezone

def get_now():
    """Returns current time in IST (UTC+5:30)"""
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

def add_test_data():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        now_str = get_now().strftime('%Y-%m-%d %H:%M:%S')

        # 1. Add Customer Account
        customer_email = "customer@example.com"
        cursor.execute("SELECT id FROM users WHERE email = ?", (customer_email,))
        if not cursor.fetchone():
            hashed_pwd = generate_password_hash("password123")
            cursor.execute('''
                INSERT INTO users (name, email, password, role, phone_number, gender, area, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', ("John Doe", customer_email, hashed_pwd, "customer", "1234567890", "Male", "Downtown", now_str))
            print(f"Customer {customer_email} added.")
        else:
            print(f"Customer {customer_email} already exists.")

        # 2. Add Owner Account
        owner_email = "owner@example.com"
        cursor.execute("SELECT id FROM users WHERE email = ?", (owner_email,))
        owner = cursor.fetchone()
        if not owner:
            hashed_pwd = generate_password_hash("password123")
            cursor.execute('''
                INSERT INTO users (name, email, password, role, phone_number, gender, area, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', ("Jane Smith", owner_email, hashed_pwd, "shop_owner", "9876543210", "Female", "Uptown", now_str))
            owner_id = cursor.lastrowid
            print(f"Owner {owner_email} added.")
        else:
            owner_id = owner['id']
            print(f"Owner {owner_email} already exists.")

        # 3. Add Shop for Owner
        cursor.execute("SELECT id FROM shops WHERE owner_id = ?", (owner_id,))
        shop = cursor.fetchone()
        if not shop:
            cursor.execute('''
                INSERT INTO shops (owner_id, name, area, address, description, contact_number, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (owner_id, "Jane's Barber Shop", "Uptown", "123 Main St, Uptown", "A premium grooming experience.", "9876543210", now_str))
            shop_id = cursor.lastrowid
            print(f"Shop 'Jane's Barber Shop' added.")
        else:
            shop_id = shop['id']
            print(f"Shop for owner already exists.")

        # 4. Add Services for Shop
        services = [
            ("Haircut", 25.0, 30, "Classic haircut and style."),
            ("Beard Trim", 15.0, 20, "Precision beard trimming."),
            ("Shave", 20.0, 25, "Hot towel shave.")
        ]
        
        for name, price, duration, desc in services:
            cursor.execute("SELECT id FROM services WHERE shop_id = ? AND name = ?", (shop_id, name))
            if not cursor.fetchone():
                cursor.execute('''
                    INSERT INTO services (shop_id, name, description, price, duration_minutes)
                    VALUES (?, ?, ?, ?, ?)
                ''', (shop_id, name, desc, price, duration))
                print(f"Service '{name}' added.")
            else:
                print(f"Service '{name}' already exists.")

        conn.commit()
        print("Test data population complete.")

    except Exception as e:
        conn.rollback()
        print(f"An error occurred: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_test_data()
