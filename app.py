import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
import sqlite3
import re
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone

def get_now():
    """Returns current time in IST (UTC+5:30)"""
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

class SQLite:
    def __init__(self, db_path):
        self.db_path = db_path

    @property
    def connection(self):
        if 'db_conn' not in g:
            g.db_conn = sqlite3.connect(self.db_path)
            g.db_conn.row_factory = sqlite3.Row
            # Enable foreign keys for SQLite
            g.db_conn.execute("PRAGMA foreign_keys = ON")
        return g.db_conn

    def teardown(self, exception):
        db_conn = g.pop('db_conn', None)
        if db_conn is not None:
            db_conn.close()

# --- DS (Data Structures) for Search ---
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end_of_word = False

class ShopTrie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        if not word: return
        node = self.root
        for char in word.lower():
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True

    def search_prefix(self, prefix):
        if not prefix: return True
        node = self.root
        for char in prefix.lower():
            if char not in node.children:
                return False
            node = node.children[char]
        return True

    def get_all_with_prefix(self, prefix):
        """Returns all words in the trie that start with prefix"""
        if not prefix: return []
        node = self.root
        for char in prefix.lower():
            if char not in node.children:
                return []
            node = node.children[char]
        
        results = []
        self._dfs(node, prefix.lower(), results)
        return results

    def _dfs(self, node, prefix, results):
        if node.is_end_of_word:
            results.append(prefix)
        for char, child_node in node.children.items():
            self._dfs(child_node, prefix + char, results)

# Initialize Search Index
search_index = ShopTrie()

def rebuild_search_index():
    global search_index
    search_index = ShopTrie()
    with app.app_context():
        # We can't use g here outside a request easily if not using app_context properly
        # but since this runs in a thread or on startup, we'll connect directly
        db_path = app.config['DATABASE']
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT area FROM shops")
        areas = cursor.fetchall()
        for area in areas:
            if area[0]:
                search_index.insert(area[0])
        conn.close()

app = Flask(__name__)

# Secret key for sessions
app.secret_key = 'your_secret_key'

# Database Configuration (SQLite)
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'profile_pics')
app.config['SHOP_UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'shop_pics')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

db = SQLite(app.config['DATABASE'])
app.teardown_appcontext(db.teardown)

@app.template_filter('datetimeformat')
def datetimeformat(value, format='%d %b, %H:%M'):
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            # SQLite format usually YYYY-MM-DD HH:MM:SS
            dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            return dt.strftime(format)
        except ValueError:
            return value
    return value.strftime(format)

# --- Helper Functions ---
def is_valid_phone(phone):
    """Simple regex to check for 10-15 digit phone numbers"""
    if not phone: return False
    return bool(re.match(r'^[0-9]{10,15}$', phone))

def get_db_cursor():
    return db.connection.cursor()

def is_logged_in():
    return 'loggedin' in session

def is_owner():
    return 'role' in session and session['role'] == 'shop_owner'

@app.context_processor
def inject_shop_status():
    unread_count = 0
    if is_logged_in():
        from flask import g
        cursor = get_db_cursor()
        cursor.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id = ? AND is_read = FALSE", (session['id'],))
        res = cursor.fetchone()
        unread_count = res['count'] if res else 0

    if is_logged_in() and is_owner():
        cursor = get_db_cursor()
        cursor.execute('SELECT id FROM shops WHERE owner_id = ?', (session['id'],))
        shop = cursor.fetchone()
        return {'owner_has_shop': shop is not None, 'owner_shop_id': shop['id'] if shop else None, 'unread_notifications': unread_count}
    return {'owner_has_shop': False, 'owner_shop_id': None, 'unread_notifications': unread_count}

def create_notification(user_id, title, message, appointment_id=None):
    cursor = get_db_cursor()
    now = get_now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO notifications (user_id, appointment_id, title, message, created_at) VALUES (?, ?, ?, ?, ?)',
                   (user_id, appointment_id, title, message, now))
    db.connection.commit()

# --- Routes ---

@app.route('/')
def index():
    cursor = get_db_cursor()
    
    # Live Social Proof Stats
    cursor.execute('SELECT COUNT(*) as count FROM shops')
    active_shops = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM appointments WHERE appointment_date >= date('now', '-1 month')")
    monthly_bookings = cursor.fetchone()['count']
    
    cursor.execute('SELECT AVG(rating) as avg_rating FROM reviews')
    res = cursor.fetchone()
    avg_rating = round(res['avg_rating'], 1) if res['avg_rating'] else 5.0
    
    cursor.execute('SELECT COUNT(*) as total, COUNT(CASE WHEN status IN ("confirmed", "completed") THEN 1 END) as reliable FROM appointments')
    res_rel = cursor.fetchone()
    reliability = round((res_rel['reliable'] / res_rel['total']) * 100) if res_rel['total'] > 0 else 100

    stats = {
        'active_shops': active_shops,
        'monthly_bookings': monthly_bookings,
        'avg_rating': avg_rating,
        'reliability': reliability
    }

    return render_template('index.html', stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    msg = ''
    if request.method == 'POST' and 'email' in request.form and 'password' in request.form:
        email = request.form['email']
        password = request.form['password']
        
        cursor = get_db_cursor()
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        account = cursor.fetchone()
        
        if account and check_password_hash(account['password'], password):
            session['loggedin'] = True
            session['id'] = account['id']
            session['email'] = account['email']
            session['role'] = account['role']
            session['name'] = account['name']
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            msg = 'Incorrect email or password!'
            
    return render_template('login.html', msg=msg)

@app.route('/register', methods=['GET', 'POST'])
def register():
    msg = ''
    if request.method == 'POST':
        name = request.form.get('name')
        password = request.form.get('password')
        email = request.form.get('email')
        role = request.form.get('role') # 'customer' or 'shop_owner'
        phone_number = request.form.get('phone_number')
        gender = request.form.get('gender')
        area = request.form.get('area')
        
        if not role:
            role = 'customer'

        cursor = get_db_cursor()
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        account = cursor.fetchone()
        
        if account:
            msg = 'Account already exists!'
        elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
            msg = 'Invalid email address!'
        elif not name or not password or not email or not phone_number:
            msg = 'Please fill out the form!'
        elif not is_valid_phone(phone_number):
            msg = 'Phone number must be between 10 and 15 digits!'
        elif role not in ['customer', 'shop_owner']:
            msg = 'Invalid account role!'
        elif len(name) > 100:
            msg = 'Name must be less than 100 characters!'
        elif len(password) < 8:
            msg = 'Password must be at least 8 characters long!'
        else:
            hashed_password = generate_password_hash(password)
            now = get_now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('INSERT INTO users (name, email, password, role, phone_number, gender, area, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', 
                           (name, email, hashed_password, role, phone_number, gender, area, now))
            db.connection.commit()
            msg = 'You have successfully registered!'
            return redirect(url_for('login'))
            
    return render_template('register.html', msg=msg)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        return redirect(url_for('login'))
    
    cursor = get_db_cursor()
    user_id = session['id']
    
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        gender = request.form['gender']
        area = request.form['area']
        
        # Validation
        if not name:
            flash('Name is required!', 'danger')
        elif len(name) > 100:
            flash('Name must be less than 100 characters!', 'danger')
        elif phone and not is_valid_phone(phone):
            flash('Phone number must be 10-15 digits!', 'danger')
        elif area and len(area) > 100:
            flash('Area must be less than 100 characters!', 'danger')
        else:
            # Handle Profile Pic Upload
            if 'profile_pic' in request.files:
                file = request.files['profile_pic']
                if file and file.filename != '':
                    if allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        import uuid
                        unique_filename = f"{uuid.uuid4()}_{filename}"
                        try:
                            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                            cursor.execute('UPDATE users SET profile_pic = ? WHERE id = ?', (unique_filename, user_id))
                        except Exception as e:
                            flash(f'Error saving profile picture: {str(e)}', 'danger')
                    else:
                        flash('Invalid profile picture type! Please upload an image (png, jpg, jpeg, gif).', 'danger')
            
            cursor.execute('UPDATE users SET name = ?, phone_number = ?, gender = ?, area = ? WHERE id = ?', 
                           (name, phone, gender, area, user_id))
            db.connection.commit()
            session['name'] = name # Update session name
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))
        
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    return render_template('profile.html', user=user)

# --- Owner Routes ---

@app.route('/owner/dashboard')
def owner_dashboard():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
    
    cursor = get_db_cursor()
    # Get Owner's Shop
    cursor.execute('SELECT * FROM shops WHERE owner_id = ?', (session['id'],))
    shop = cursor.fetchone()
    
    services = []
    appointments = []
    reviews = []
    
    if shop:
        # Get Services
        cursor.execute('SELECT * FROM services WHERE shop_id = ?', (shop['id'],))
        services = cursor.fetchall()
        
        # Get Appointments (with joins for user details, multiple services, and total duration)
        cursor.execute('''
            SELECT a.*, u.name as user_name, GROUP_CONCAT(s.name, ', ') as services_list, p.amount, p.status as payment_status
            FROM appointments a 
            JOIN users u ON a.user_id = u.id 
            LEFT JOIN appointment_services asrv ON a.id = asrv.appointment_id
            LEFT JOIN services s ON asrv.service_id = s.id
            LEFT JOIN payments p ON a.id = p.appointment_id
            WHERE a.shop_id = ?
            GROUP BY a.id
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        ''', (shop['id'],))
        appointments = cursor.fetchall()

        # Get Reviews for this shop
        cursor.execute('''
            SELECT r.*, u.name as user_name 
            FROM reviews r
            JOIN users u ON r.user_id = u.id
            WHERE r.shop_id = ?
            ORDER BY r.created_at DESC
        ''', (shop['id'],))
        reviews = cursor.fetchall()

    return render_template('owner_dashboard.html', shop=shop, services=services, appointments=appointments, reviews=reviews)

@app.route('/owner/add_shop', methods=['GET', 'POST'])
def add_shop():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        name = request.form['name']
        area = request.form['area']
        address = request.form['address']
        description = request.form['description']
        contact = request.form['contact']
        
        # Handle Shop Image
        shop_image_name = None
        if 'shop_image' in request.files:
            file = request.files['shop_image']
            if file and file.filename != '':
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    timestamp = get_now().strftime('%Y%m%d%H%M%S')
                    filename = f"{timestamp}_{filename}"
                    try:
                        file.save(os.path.join(app.config['SHOP_UPLOAD_FOLDER'], filename))
                        shop_image_name = filename
                    except Exception as e:
                        flash(f'Error saving image: {str(e)}', 'danger')
                else:
                    flash('Invalid file type! Please upload an image (png, jpg, jpeg, gif).', 'danger')
        
        # Validation
        if not name or not area or not contact or not address:
            flash('Please fill out all required fields!', 'danger')
        elif len(name) > 100:
            flash('Shop name must be less than 100 characters!', 'danger')
        elif not is_valid_phone(contact):
            flash('Contact number must be 10-15 digits!', 'danger')
        else:
            cursor = get_db_cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('INSERT INTO shops (owner_id, name, area, address, description, contact_number, shop_image, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                           (session['id'], name, area, address, description, contact, shop_image_name, now))
            db.connection.commit()
            flash('Shop created successfully!', 'success')
            return redirect(url_for('owner_dashboard'))
        
    return render_template('add_shop.html')

@app.route('/owner/edit_shop', methods=['GET', 'POST'])
def edit_shop():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT * FROM shops WHERE owner_id = ?', (session['id'],))
    shop = cursor.fetchone()

    if not shop:
        flash('Shop profile not found!', 'danger')
        return redirect(url_for('owner_dashboard'))

    if request.method == 'POST':
        name = request.form['name']
        area = request.form['area']
        address = request.form['address']
        description = request.form['description']
        contact = request.form['contact']
        
        # Handle Shop Image
        shop_image_name = shop['shop_image'] # Keep existing by default
        if 'shop_image' in request.files:
            file = request.files['shop_image']
            if file and file.filename != '':
                if allowed_file(file.filename):
                    # Delete old file if it exists
                    if shop_image_name:
                        old_path = os.path.join(app.config['SHOP_UPLOAD_FOLDER'], shop_image_name)
                        if os.path.exists(old_path):
                            try:
                                os.remove(old_path)
                            except:
                                pass
                    
                    filename = secure_filename(file.filename)
                    timestamp = get_now().strftime('%Y%m%d%H%M%S')
                    filename = f"{timestamp}_{filename}"
                    try:
                        file.save(os.path.join(app.config['SHOP_UPLOAD_FOLDER'], filename))
                        shop_image_name = filename
                    except Exception as e:
                        flash(f'Error saving image: {str(e)}', 'danger')
                else:
                    flash('Invalid file type! Please upload an image (png, jpg, jpeg, gif).', 'danger')
        
        # Validation
        if not name or not area or not contact or not address:
            flash('Please fill out all required fields!', 'danger')
        elif not is_valid_phone(contact):
            flash('Contact number must be 10-15 digits!', 'danger')
        else:
            cursor.execute('UPDATE shops SET name = ?, area = ?, address = ?, description = ?, contact_number = ?, shop_image = ? WHERE owner_id = ?',
                           (name, area, address, description, contact, shop_image_name, session['id']))
            db.connection.commit()
            flash('Shop details updated successfully!', 'success')
            return redirect(url_for('owner_dashboard'))
            
    return render_template('edit_shop.html', shop=shop)

@app.route('/owner/add_service', methods=['GET', 'POST'])
def add_service():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT id FROM shops WHERE owner_id = ?', (session['id'],))
    shop = cursor.fetchone()
    
    if not shop:
        flash('Please create a shop first!', 'warning')
        return redirect(url_for('add_shop'))

    if request.method == 'POST':
        name = request.form['name']
        price = request.form['price']
        duration = request.form['duration']
        description = request.form['description']
        
        # Validation
        if not name or not price or not duration:
            flash('Please fill out all required fields!', 'danger')
        elif len(name) > 100:
            flash('Service name must be less than 100 characters!', 'danger')
        elif float(price) <= 0:
            flash('Price must be greater than 0!', 'danger')
        elif int(duration) <= 0:
            flash('Duration must be greater than 0!', 'danger')
        elif description and len(description) > 500:
            flash('Description must be less than 500 characters!', 'danger')
        else:
            cursor.execute('INSERT INTO services (shop_id, name, description, price, duration_minutes) VALUES (?, ?, ?, ?, ?)',
                           (shop['id'], name, description, price, duration))
            db.connection.commit()
            flash('Service added successfully!', 'success')
            return redirect(url_for('owner_dashboard'))
        
    return render_template('add_service.html')

# --- Customer Routes ---

@app.route('/dashboard')
def customer_dashboard():
    if not is_logged_in() or session['role'] != 'customer':
        return redirect(url_for('login'))

    cursor = get_db_cursor()
    # Get user's appointments
    cursor.execute('''
        SELECT a.*, GROUP_CONCAT(s.name, ', ') as services_list, sh.name as shop_name, sh.area
        FROM appointments a
        LEFT JOIN appointment_services asrv ON a.id = asrv.appointment_id
        LEFT JOIN services s ON asrv.service_id = s.id
        JOIN shops sh ON a.shop_id = sh.id
        WHERE a.user_id = ?
        GROUP BY a.id
        ORDER BY a.appointment_date DESC
    ''', (session['id'],))
    appointments = cursor.fetchall()
    
    return render_template('customer_dashboard.html', appointments=appointments)

@app.route('/inbox')
def inbox():
    if not is_logged_in():
        return redirect(url_for('login'))
    
    cursor = get_db_cursor()
    cursor.execute('''
        SELECT n.*, a.appointment_date, a.appointment_time, s.name as shop_name 
        FROM notifications n
        LEFT JOIN appointments a ON n.appointment_id = a.id
        LEFT JOIN shops s ON a.shop_id = s.id
        WHERE n.user_id = ? 
        ORDER BY n.created_at DESC
    ''', (session['id'],))
    notifications = cursor.fetchall()
    
    # Mark all as read when visiting inbox
    cursor.execute('UPDATE notifications SET is_read = TRUE WHERE user_id = ?', (session['id'],))
    db.connection.commit()
    
    return render_template('inbox.html', notifications=notifications)

@app.route('/shops')
def list_shops():
    cursor = get_db_cursor()
    area_filter = request.args.get('area', '').strip()
    
    # Get all unique areas for the datalist (autocomplete)
    cursor.execute('SELECT DISTINCT area FROM shops WHERE area IS NOT NULL')
    all_areas = [row['area'] for row in cursor.fetchall()]
    
    # Optionally rebuild index if new areas are found (simple version)
    # In a real app, you'd do this on shop creation
    for area in all_areas:
        search_index.insert(area)

    query = 'SELECT * FROM shops'
    params = ()
    
    if area_filter:
        # Use Trie to check if the prefix exists
        if search_index.search_prefix(area_filter):
            # If it's a valid prefix, we find all full area names that match
            matching_areas = search_index.get_all_with_prefix(area_filter)
            if matching_areas:
                placeholders = ', '.join(['?'] * len(matching_areas))
                query += f' WHERE area IN ({placeholders})'
                params = tuple(matching_areas)
            else:
                # No full words found for this prefix
                query += ' WHERE 1=0' 
        else:
            # Prefix doesn't exist in our DS
            query += ' WHERE 1=0'
        
    cursor.execute(query, params)
    shops = [dict(row) for row in cursor.fetchall()]
    
    # Get average rating for each shop
    for shop in shops:
         cursor.execute('SELECT AVG(rating) as avg_rating FROM reviews WHERE shop_id = ?', (shop['id'],))
         res = cursor.fetchone()
         shop['rating'] = round(res['avg_rating'], 1) if res['avg_rating'] else 'New'

    return render_template('shops.html', shops=shops, all_areas=all_areas, area_filter=area_filter)

@app.route('/shop/<int:shop_id>')
def shop_details(shop_id):
    cursor = get_db_cursor()
    
    cursor.execute('SELECT * FROM shops WHERE id = ?', (shop_id,))
    shop = cursor.fetchone()
    
    cursor.execute('SELECT * FROM services WHERE shop_id = ?', (shop_id,))
    services = cursor.fetchall()
    
    cursor.execute('''
        SELECT r.*, u.name as user_name 
        FROM reviews r
        JOIN users u ON r.user_id = u.id
        WHERE r.shop_id = ?
        ORDER BY r.created_at DESC
    ''', (shop_id,))
    reviews = cursor.fetchall()
    
    return render_template('shop_details.html', shop=shop, services=services, reviews=reviews)

@app.route('/book', methods=['GET'])
def book_confirm():
    if not is_logged_in():
        flash('Please login to book.', 'info')
        return redirect(url_for('login'))
        
    if session.get('role') != 'customer':
        flash('Only customers can book appointments!', 'warning')
        return redirect(url_for('index'))
        
    shop_id = request.args.get('shop_id')
    service_ids = request.args.getlist('service_ids')
    
    if not shop_id or not service_ids:
        flash('Please select at least one service.', 'warning')
        return redirect(url_for('list_shops'))

    cursor = get_db_cursor()
    cursor.execute('SELECT * FROM shops WHERE id = ?', (shop_id,))
    shop = cursor.fetchone()
    
    # Fetch selected services
    format_strings = ','.join(['?'] * len(service_ids))
    cursor.execute(f'SELECT * FROM services WHERE id IN ({format_strings}) AND shop_id = ?', (*service_ids, shop_id))
    selected_services = cursor.fetchall()
    
    if not selected_services:
        flash('Selected services not found.', 'danger')
        return redirect(url_for('shop_details', shop_id=shop_id))

    selected_date = request.args.get('date', get_now().strftime('%Y-%m-%d'))
    
    # Generate Slots (Helper Logic)
    slots_data = []
    start_hour = 9 # 9 AM
    end_hour = 20  # 8 PM
    
    # Fetch existing appointments for the shop on the selected date
    cursor.execute('SELECT appointment_time, total_duration FROM appointments WHERE shop_id = ? AND appointment_date = ? AND status != "cancelled"', 
                   (shop_id, selected_date))
    existing_appointments = cursor.fetchall()
    
    current_now = get_now()
    current_date = current_now.date()
    current_tz = current_now.tzinfo
    
    # Convert existing appointments to time ranges
    booked_ranges = []
    for appt in existing_appointments:
        # SQLite returns string 'HH:MM:SS' or 'HH:MM'
        appt_time = appt['appointment_time']
        if isinstance(appt_time, str):
            try:
                time_obj = datetime.strptime(appt_time, "%H:%M:%S").time()
            except ValueError:
                time_obj = datetime.strptime(appt_time, "%H:%M").time()
            
            # Use current_date to ensure "today" is IST
            start_time = datetime.combine(current_date, time_obj).replace(tzinfo=current_tz)
        else:
            # Fallback for timedelta
            start_time = datetime.combine(current_date, (datetime.min + appt_time).time()).replace(tzinfo=current_tz)
            
        end_time = start_time + timedelta(minutes=int(appt['total_duration']))
        booked_ranges.append((start_time, end_time))

    today_str = current_now.strftime('%Y-%m-%d')
    is_today = selected_date == today_str

    for hour in range(start_hour, end_hour):
        for minute in [0, 30]:
            slot_time_str = f"{hour:02d}:{minute:02d}"
            # Time objects for comparison
            slot_time = datetime.strptime(slot_time_str, "%H:%M").time()
            # Combine with IST today's date and add TZ info
            slot_time_dt = datetime.combine(current_date, slot_time).replace(tzinfo=current_tz)
            
            is_booked = False
            for b_start, b_end in booked_ranges:
                # A slot is booked if its start time falls within an existing appointment range
                if b_start <= slot_time_dt < b_end:
                    is_booked = True
                    break
            
            # Disable slot if it's already passed today
            is_past = is_today and slot_time_dt < current_now

            slots_data.append({
                'time': slot_time_str,
                'is_available': not is_booked and not is_past
            })
    
    return render_template('book.html', shop=shop, services=selected_services, slots=slots_data, selected_date=selected_date, now=current_now.strftime('%Y-%m-%d'))

@app.route('/process_booking', methods=['POST'])
def process_booking():
    if not is_logged_in():
        return redirect(url_for('login'))
        
    if session.get('role') != 'customer':
        flash('Only customers can book appointments!', 'warning')
        return redirect(url_for('index'))
        
    service_ids = request.form.getlist('service_ids')
    shop_id = request.form['shop_id']
    date = request.form['date']
    time = request.form['time']
    
    if not service_ids:
        flash('No services selected!', 'danger')
        return redirect(url_for('shop_details', shop_id=shop_id))

    # Validation: Ensure date/time is not in the past
    booking_date = datetime.strptime(date, '%Y-%m-%d').date()
    current_now = get_now()
    
    if booking_date < current_now.date():
        flash('You cannot book an appointment in the past!', 'danger')
        return redirect(url_for('book_confirm', shop_id=shop_id, service_ids=service_ids))
    
    if booking_date == current_now.date():
        booking_time = datetime.strptime(time, "%H:%M").time()
        booking_dt = datetime.combine(booking_date, booking_time).replace(tzinfo=current_now.tzinfo)
        if booking_dt < current_now:
            flash('This time slot has already passed!', 'danger')
            return redirect(url_for('book_confirm', shop_id=shop_id, service_ids=service_ids))

    cursor = get_db_cursor()
    
    # Calculate total price and duration
    format_strings = ','.join(['?'] * len(service_ids))
    cursor.execute(f'SELECT SUM(price) as total_price, SUM(duration_minutes) as total_duration FROM services WHERE id IN ({format_strings})', (*service_ids,))
    booking_info = cursor.fetchone()
    amount = booking_info['total_price']
    total_duration = booking_info['total_duration']

    # --- Double Check Availability ---
    current_date = current_now.date()
    current_tz = current_now.tzinfo
    
    requested_start = datetime.combine(current_date, datetime.strptime(time, "%H:%M").time()).replace(tzinfo=current_tz)
    requested_end = requested_start + timedelta(minutes=int(total_duration))
    
    cursor.execute('SELECT appointment_time, total_duration FROM appointments WHERE shop_id = ? AND appointment_date = ? AND status != "cancelled"', 
                   (shop_id, date))
    existing = cursor.fetchall()
    
    for appt in existing:
        # SQLite returns string 'HH:MM:SS' or 'HH:MM'
        if isinstance(appt['appointment_time'], str):
            try:
                time_obj = datetime.strptime(appt['appointment_time'], "%H:%M:%S").time()
            except ValueError:
                time_obj = datetime.strptime(appt['appointment_time'], "%H:%M").time()
            e_start = datetime.combine(current_date, time_obj).replace(tzinfo=current_tz)
        else:
            e_start = datetime.combine(current_date, (datetime.min + appt['appointment_time']).time()).replace(tzinfo=current_tz)
            
        e_end = e_start + timedelta(minutes=int(appt['total_duration']))
        
        # Overlap check: (Start1 < End2) AND (End1 > Start2)
        if requested_start < e_end and requested_end > e_start:
            flash('The selected time slot is no longer available for the full duration of your services. Please choose another time.', 'danger')
            return redirect(url_for('book_confirm', shop_id=shop_id, service_ids=service_ids))
    # --- End Check ---

    # Insert Appointment with total_price
    now = get_now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT INTO appointments (user_id, shop_id, appointment_date, appointment_time, total_duration, total_price, status, payment_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (session['id'], shop_id, date, time, total_duration, amount, 'pending', 'unpaid', now))
    appointment_id = cursor.lastrowid

    # Insert into appointment_services
    for s_id in service_ids:
        cursor.execute('INSERT INTO appointment_services (appointment_id, service_id) VALUES (?, ?)', (appointment_id, s_id))
    
    db.connection.commit()
    
    # Redirect to Payment with FULL amount (choice will be made in template)
    create_notification(session['id'], "Booking Initiated", f"Your appointment for {date} at {time} has been initiated. Please complete the payment to confirm.", appointment_id)
    return redirect(url_for('payment', appointment_id=appointment_id, amount=float(amount)))

@app.route('/payment/<int:appointment_id>/<float:amount>', methods=['GET', 'POST'])
def payment(appointment_id, amount):
    if not is_logged_in():
        return redirect(url_for('login'))
    
    # Check if this is a follow-up payment (final balance)
    # If is_final is explicitly passed in URL, respect it
    is_final_passed = request.args.get('is_final', None)
    
    if request.method == 'POST':
        payment_method = request.form.get('payment_method', 'Card')
        payment_plan = request.form.get('payment_plan', 'half') # 'half' or 'full'
        actual_amount = float(request.form.get('amount', amount))
        
        cursor = get_db_cursor()
        
        # --- Amount Logic Verification ---
        # Re-fetch appointment to get total_price from server-side
        cursor.execute('SELECT total_price FROM appointments WHERE id = ?', (appointment_id,))
        appt_data = cursor.fetchone()
        if not appt_data:
            flash('Invalid appointment session.', 'danger')
            return redirect(url_for('customer_dashboard'))
            
        expected_full = float(appt_data['total_price'])
        expected_half = expected_full / 2
        
        # Verification based on plan
        is_val_passed = (is_final_passed == '1')
        if is_val_passed:
            # Paying the remaining half
            if abs(actual_amount - expected_half) > 0.01:
                flash('Payment amount mismatch detected.', 'danger')
                return redirect(url_for('customer_dashboard'))
        elif payment_plan == 'full':
            if abs(actual_amount - expected_full) > 0.01:
                flash('Payment amount mismatch detected.', 'danger')
                return redirect(url_for('customer_dashboard'))
        elif payment_plan == 'half':
            if abs(actual_amount - expected_half) > 0.01:
                flash('Payment amount mismatch detected.', 'danger')
                return redirect(url_for('customer_dashboard'))
        # --- End Verification ---
        
        # Insert payment record
        now = get_now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('INSERT INTO payments (appointment_id, amount, payment_method, status, transaction_date) VALUES (?, ?, ?, ?, ?)',
                       (appointment_id, actual_amount, payment_method, 'completed', now))
        
        # Determine new payment status and update booking status if it's the initial payment
        if is_final_passed == '1' or payment_plan == 'full':
            cursor.execute('UPDATE appointments SET payment_status = "paid" WHERE id = ?', (appointment_id,))
        else:
            cursor.execute('UPDATE appointments SET payment_status = "partially_paid" WHERE id = ?', (appointment_id,))
            
        # Only move from pending to confirmed during the initial payment phase
        cursor.execute('UPDATE appointments SET status = "confirmed" WHERE id = ? AND status = "pending"', (appointment_id,))
            
        db.connection.commit()
        
        # Get Shop Owner ID and Customer/Shop Name for notifications
        cursor.execute('''
            SELECT s.owner_id, s.name as shop_name, u.name as customer_name, a.appointment_date, a.appointment_time 
            FROM appointments a 
            JOIN shops s ON a.shop_id = s.id 
            JOIN users u ON a.user_id = u.id 
            WHERE a.id = ?
        ''', (appointment_id,))
        details = cursor.fetchone()
        
        # Notify Customer
        create_notification(session['id'], "Payment Successful", f"Payment of ₹{actual_amount} successful for your session at {details['shop_name']}. Your appointment is now confirmed.", appointment_id)
        
        # Notify Shop Owner
        create_notification(details['owner_id'], "New Booking Confirmed", f"New confirmed booking from {details['customer_name']} for {details['appointment_date']} at {details['appointment_time']}. Payment of ₹{actual_amount} received.", appointment_id)
        
        flash(f'Payment of ₹{actual_amount} successful via {payment_method}!', 'success')
        return redirect(url_for('customer_dashboard'))
        
    return render_template('payment.html', appointment_id=appointment_id, total_amount=amount, is_final=(is_final_passed == '1'))

@app.route('/cancel_appointment/<int:appointment_id>', methods=['POST'])
def cancel_appointment(appointment_id):
    if not is_logged_in():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    # Check if appointment belongs to user or if user is the shop owner
    cursor.execute('SELECT * FROM appointments WHERE id = ?', (appointment_id,))
    appt = cursor.fetchone()
    
    if not appt:
        flash('Appointment not found.', 'danger')
        return redirect(url_for('index'))
        
    # Check ownership
    is_owner = False
    if session.get('role') == 'shop_owner':
        cursor.execute('SELECT * FROM shops WHERE id = ? AND owner_id = ?', (appt['shop_id'], session['id']))
        if cursor.fetchone():
            is_owner = True
            
    if appt['user_id'] != session['id'] and not is_owner:
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('index'))
        
    cursor.execute('UPDATE appointments SET status = "cancelled" WHERE id = ?', (appointment_id,))
    db.connection.commit()
    
    # Get details for cross-notification
    cursor.execute('''
        SELECT a.user_id, s.owner_id, s.name as shop_name, u.name as customer_name, a.appointment_date 
        FROM appointments a 
        JOIN shops s ON a.shop_id = s.id 
        JOIN users u ON a.user_id = u.id 
        WHERE a.id = ?
    ''', (appointment_id,))
    details = cursor.fetchone()

    if session['role'] == 'customer':
        # Notify Owner
        create_notification(details['owner_id'], "Appointment Cancelled", f"Customer {details['customer_name']} has cancelled their appointment for {details['appointment_date']}.", appointment_id)
        flash('Appointment cancelled successfully.', 'info')
    else:
        # Notify Customer
        create_notification(details['user_id'], "Appointment Cancelled", f"The shop {details['shop_name']} has cancelled your appointment for {details['appointment_date']}.", appointment_id)
        flash('Appointment cancelled successfully.', 'info')
    return redirect(request.referrer or url_for('index'))

@app.route('/complete_appointment/<int:appointment_id>', methods=['POST'])
def complete_appointment(appointment_id):
    if not is_logged_in() or session.get('role') != 'shop_owner':
        flash('Unauthorized.', 'danger')
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    # Check if this owner owns the shop for this appointment
    cursor.execute('''
        SELECT a.* FROM appointments a 
        JOIN shops s ON a.shop_id = s.id 
        WHERE a.id = ? AND s.owner_id = ?
    ''', (appointment_id, session['id']))
    
    if not cursor.fetchone():
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('owner_dashboard'))
        
    cursor.execute('UPDATE appointments SET status = "completed" WHERE id = ?', (appointment_id,))
    db.connection.commit()
    
    # Get user_id for the appointment to notify them
    cursor.execute('SELECT user_id FROM appointments WHERE id = ?', (appointment_id,))
    appt = cursor.fetchone()
    create_notification(appt['user_id'], "Service Completed", "Your grooming session is complete. We hope you enjoyed the service!", appointment_id)
    
    flash('Appointment marked as completed.', 'success')
    return redirect(url_for('owner_dashboard'))

@app.route('/pay_remaining/<int:appointment_id>')
def pay_remaining(appointment_id):
    if not is_logged_in():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT * FROM appointments WHERE id = ? AND user_id = ?', (appointment_id, session['id']))
    appt = cursor.fetchone()
    
    if not appt:
        flash('Appointment not found.', 'danger')
        return redirect(url_for('customer_dashboard'))
        
    if appt['payment_status'] != 'partially_paid':
        flash('No remaining balance for this appointment.', 'info')
        return redirect(url_for('customer_dashboard'))
        
    remaining_balance = float(appt['total_price']) / 2
    return redirect(url_for('payment', appointment_id=appointment_id, amount=remaining_balance, is_final=1))

@app.route('/add_review', methods=['POST'])
def add_review():
    if not is_logged_in():
        return redirect(url_for('login'))
    
    if session.get('role') != 'customer':
        flash('Only customers can write reviews!', 'warning')
        return redirect(url_for('index'))
        
    shop_id = request.form['shop_id']
    rating = request.form['rating']
    comment = request.form['comment']
    
    # Validation
    if not rating or not comment:
        flash('Please provide both a rating and a comment!', 'danger')
    elif int(rating) < 1 or int(rating) > 5:
        flash('Rating must be between 1 and 5!', 'danger')
    elif len(comment) > 500:
        flash('Comment must be less than 500 characters!', 'danger')
    else:
        cursor = get_db_cursor()
        now = get_now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('INSERT INTO reviews (user_id, shop_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)',
                       (session['id'], shop_id, rating, comment, now))
        db.connection.commit()
        flash('Review submitted!', 'success')
    
    return redirect(url_for('shop_details', shop_id=shop_id))

if __name__ == '__main__':
    app.run(debug=True)
