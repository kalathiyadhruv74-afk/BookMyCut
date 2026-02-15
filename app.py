import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

from flask import Flask, render_template, request, redirect, url_for, session, flash, g
import pymysql
import pymysql.cursors

# Use pymysql as MySQLdb
pymysql.install_as_MySQLdb()
import MySQLdb.cursors
import re
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

class MySQL:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        app.teardown_appcontext(self.teardown)

    @property
    def connection(self):
        if 'db_conn' not in g:
            g.db_conn = pymysql.connect(
                host=self.app.config['MYSQL_HOST'],
                user=self.app.config['MYSQL_USER'],
                password=self.app.config['MYSQL_PASSWORD'],
                database=self.app.config['MYSQL_DB'],
                charset=self.app.config.get('MYSQL_CHARSET', 'utf8mb4'),
                cursorclass=pymysql.cursors.DictCursor
            )
        return g.db_conn

    def teardown(self, exception):
        db_conn = g.pop('db_conn', None)
        if db_conn is not None:
            db_conn.close()

app = Flask(__name__)

# Secret key for sessions
app.secret_key = 'your_secret_key'

# Database Configuration (XAMPP Default)
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = '' # Default XAMPP password is empty
app.config['MYSQL_DB'] = 'bookmycut_db'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'profile_pics')
app.config['SHOP_UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'shop_pics')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['MYSQL_CHARSET'] = 'utf8mb4'
app.config['MYSQL_COLLATION'] = 'utf8mb4_unicode_ci'

mysql = MySQL(app)

# --- Helper Functions ---
def get_db_cursor():
    return mysql.connection.cursor(MySQLdb.cursors.DictCursor)

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
        cursor.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id = %s AND is_read = FALSE", (session['id'],))
        res = cursor.fetchone()
        unread_count = res['count'] if res else 0

    if is_logged_in() and is_owner():
        cursor = get_db_cursor()
        cursor.execute('SELECT id FROM shops WHERE owner_id = %s', (session['id'],))
        shop = cursor.fetchone()
        return {'owner_has_shop': shop is not None, 'owner_shop_id': shop['id'] if shop else None, 'unread_notifications': unread_count}
    return {'owner_has_shop': False, 'owner_shop_id': None, 'unread_notifications': unread_count}

def create_notification(user_id, title, message, appointment_id=None):
    cursor = get_db_cursor()
    cursor.execute('INSERT INTO notifications (user_id, appointment_id, title, message) VALUES (%s, %s, %s, %s)',
                   (user_id, appointment_id, title, message))
    mysql.connection.commit()

# --- Routes ---

@app.route('/')
def index():
    cursor = get_db_cursor()
    
    # Live Social Proof Stats
    cursor.execute('SELECT COUNT(*) as count FROM shops')
    active_shops = cursor.fetchone()['count']
    
    cursor.execute('SELECT COUNT(*) as count FROM appointments WHERE appointment_date >= DATE_SUB(CURDATE(), INTERVAL 1 MONTH)')
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
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
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
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
        account = cursor.fetchone()
        
        if account:
            msg = 'Account already exists!'
        elif not re.match(r'[^@]+@[^@]+\.[^@]+', email):
            msg = 'Invalid email address!'
        elif not name or not password or not email:
            msg = 'Please fill out the form!'
        elif len(name) > 100:
            msg = 'Name must be less than 100 characters!'
        elif len(password) < 8:
            msg = 'Password must be at least 8 characters long!'
        else:
            hashed_password = generate_password_hash(password)
            cursor.execute('INSERT INTO users (name, email, password, role, phone_number, gender, area) VALUES (%s, %s, %s, %s, %s, %s, %s)', 
                           (name, email, hashed_password, role, phone_number, gender, area))
            mysql.connection.commit()
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
        elif phone and not re.match(r'^[0-9]{10,15}$', phone):
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
                            cursor.execute('UPDATE users SET profile_pic = %s WHERE id = %s', (unique_filename, user_id))
                        except Exception as e:
                            flash(f'Error saving profile picture: {str(e)}', 'danger')
                    else:
                        flash('Invalid profile picture type! Please upload an image (png, jpg, jpeg, gif).', 'danger')
            
            cursor.execute('UPDATE users SET name = %s, phone_number = %s, gender = %s, area = %s WHERE id = %s', 
                           (name, phone, gender, area, user_id))
            mysql.connection.commit()
            session['name'] = name # Update session name
            flash('Profile updated successfully!', 'success')
            return redirect(url_for('profile'))
        
    cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()
    return render_template('profile.html', user=user)

# --- Owner Routes ---

@app.route('/owner/dashboard')
def owner_dashboard():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
    
    cursor = get_db_cursor()
    # Get Owner's Shop
    cursor.execute('SELECT * FROM shops WHERE owner_id = %s', (session['id'],))
    shop = cursor.fetchone()
    
    services = []
    appointments = []
    reviews = []
    
    if shop:
        # Get Services
        cursor.execute('SELECT * FROM services WHERE shop_id = %s', (shop['id'],))
        services = cursor.fetchall()
        
        # Get Appointments (with joins for user details, multiple services, and total duration)
        cursor.execute('''
            SELECT a.*, u.name as user_name, GROUP_CONCAT(s.name SEPARATOR ', ') as services_list, p.amount, p.status as payment_status
            FROM appointments a 
            JOIN users u ON a.user_id = u.id 
            LEFT JOIN appointment_services asrv ON a.id = asrv.appointment_id
            LEFT JOIN services s ON asrv.service_id = s.id
            LEFT JOIN payments p ON a.id = p.appointment_id
            WHERE a.shop_id = %s
            GROUP BY a.id
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
        ''', (shop['id'],))
        appointments = cursor.fetchall()

        # Get Reviews for this shop
        cursor.execute('''
            SELECT r.*, u.name as user_name 
            FROM reviews r
            JOIN users u ON r.user_id = u.id
            WHERE r.shop_id = %s
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
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
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
        else:
            cursor = get_db_cursor()
            cursor.execute('INSERT INTO shops (owner_id, name, area, address, description, contact_number, shop_image) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                           (session['id'], name, area, address, description, contact, shop_image_name))
            mysql.connection.commit()
            flash('Shop created successfully!', 'success')
            return redirect(url_for('owner_dashboard'))
        
    return render_template('add_shop.html')

@app.route('/owner/edit_shop', methods=['GET', 'POST'])
def edit_shop():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT * FROM shops WHERE owner_id = %s', (session['id'],))
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
        shop_image_name = shop.get('shop_image') # Keep existing by default
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
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
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
        else:
            cursor.execute('UPDATE shops SET name = %s, area = %s, address = %s, description = %s, contact_number = %s, shop_image = %s WHERE owner_id = %s',
                           (name, area, address, description, contact, shop_image_name, session['id']))
            mysql.connection.commit()
            flash('Shop details updated successfully!', 'success')
            return redirect(url_for('owner_dashboard'))
            
    return render_template('edit_shop.html', shop=shop)

@app.route('/owner/add_service', methods=['GET', 'POST'])
def add_service():
    if not is_logged_in() or not is_owner():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT id FROM shops WHERE owner_id = %s', (session['id'],))
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
            cursor.execute('INSERT INTO services (shop_id, name, description, price, duration_minutes) VALUES (%s, %s, %s, %s, %s)',
                           (shop['id'], name, description, price, duration))
            mysql.connection.commit()
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
        SELECT a.*, GROUP_CONCAT(s.name SEPARATOR ', ') as services_list, sh.name as shop_name, sh.area
        FROM appointments a
        LEFT JOIN appointment_services asrv ON a.id = asrv.appointment_id
        LEFT JOIN services s ON asrv.service_id = s.id
        JOIN shops sh ON a.shop_id = sh.id
        WHERE a.user_id = %s
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
        WHERE n.user_id = %s 
        ORDER BY n.created_at DESC
    ''', (session['id'],))
    notifications = cursor.fetchall()
    
    # Mark all as read when visiting inbox
    cursor.execute('UPDATE notifications SET is_read = TRUE WHERE user_id = %s', (session['id'],))
    mysql.connection.commit()
    
    return render_template('inbox.html', notifications=notifications)

@app.route('/shops')
def list_shops():
    cursor = get_db_cursor()
    area_filter = request.args.get('area')
    
    query = 'SELECT * FROM shops'
    params = ()
    
    if area_filter:
        query += ' WHERE area LIKE %s'
        params = ('%' + area_filter + '%',)
        
    cursor.execute(query, params)
    shops = cursor.fetchall()
    
    # Optional: Get average rating for each shop (could be optimized)
    for shop in shops:
         cursor.execute('SELECT AVG(rating) as avg_rating FROM reviews WHERE shop_id = %s', (shop['id'],))
         res = cursor.fetchone()
         shop['rating'] = round(res['avg_rating'], 1) if res['avg_rating'] else 'New'

    return render_template('shops.html', shops=shops)

@app.route('/shop/<int:shop_id>')
def shop_details(shop_id):
    cursor = get_db_cursor()
    
    cursor.execute('SELECT * FROM shops WHERE id = %s', (shop_id,))
    shop = cursor.fetchone()
    
    cursor.execute('SELECT * FROM services WHERE shop_id = %s', (shop_id,))
    services = cursor.fetchall()
    
    cursor.execute('''
        SELECT r.*, u.name as user_name 
        FROM reviews r
        JOIN users u ON r.user_id = u.id
        WHERE r.shop_id = %s
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
    cursor.execute('SELECT * FROM shops WHERE id = %s', (shop_id,))
    shop = cursor.fetchone()
    
    # Fetch selected services
    format_strings = ','.join(['%s'] * len(service_ids))
    cursor.execute(f'SELECT * FROM services WHERE id IN ({format_strings}) AND shop_id = %s', (*service_ids, shop_id))
    selected_services = cursor.fetchall()
    
    if not selected_services:
        flash('Selected services not found.', 'danger')
        return redirect(url_for('shop_details', shop_id=shop_id))

    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    # Generate Slots (Helper Logic)
    slots_data = []
    start_hour = 9 # 9 AM
    end_hour = 20  # 8 PM
    
    # Fetch existing appointments for the shop on the selected date
    cursor.execute('SELECT appointment_time, total_duration FROM appointments WHERE shop_id = %s AND appointment_date = %s AND status != "cancelled"', 
                   (shop_id, selected_date))
    existing_appointments = cursor.fetchall()
    
    # Convert existing appointments to time ranges
    booked_ranges = []
    for appt in existing_appointments:
        # Convert TIME object to datetime for easier calculation
        start_time = datetime.combine(datetime.today(), (datetime.min + appt['appointment_time']).time())
        end_time = start_time + timedelta(minutes=int(appt['total_duration']))
        booked_ranges.append((start_time, end_time))

    for hour in range(start_hour, end_hour):
        for minute in [0, 30]:
            slot_time_str = f"{hour:02d}:{minute:02d}"
            slot_time_dt = datetime.combine(datetime.today(), datetime.strptime(slot_time_str, "%H:%M").time())
            
            is_booked = False
            for b_start, b_end in booked_ranges:
                # A slot is booked if its start time falls within an existing appointment range
                # OR if the existing appointment starts during this slot (less common for 30min slots but safer)
                if b_start <= slot_time_dt < b_end:
                    is_booked = True
                    break
            
            slots_data.append({
                'time': slot_time_str,
                'is_available': not is_booked
            })
    
    return render_template('book.html', shop=shop, services=selected_services, slots=slots_data, selected_date=selected_date, now=datetime.now().strftime('%Y-%m-%d'))

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

    # Validation: Ensure date is not in the past
    booking_date = datetime.strptime(date, f'%Y-%m-%d').date()
    if booking_date < datetime.now().date():
        flash('You cannot book an appointment in the past!', 'danger')
        return redirect(url_for('book', shop_id=shop_id, service_ids=service_ids))

    cursor = get_db_cursor()
    
    # Calculate total price and duration
    format_strings = ','.join(['%s'] * len(service_ids))
    cursor.execute(f'SELECT SUM(price) as total_price, SUM(duration_minutes) as total_duration FROM services WHERE id IN ({format_strings})', (*service_ids,))
    booking_info = cursor.fetchone()
    amount = booking_info['total_price']
    total_duration = booking_info['total_duration']

    # --- Double Check Availability ---
    requested_start = datetime.combine(datetime.today(), datetime.strptime(time, "%H:%M").time())
    requested_end = requested_start + timedelta(minutes=int(total_duration))
    
    cursor.execute('SELECT appointment_time, total_duration FROM appointments WHERE shop_id = %s AND appointment_date = %s AND status != "cancelled"', 
                   (shop_id, date))
    existing = cursor.fetchall()
    
    for appt in existing:
        e_start = datetime.combine(datetime.today(), (datetime.min + appt['appointment_time']).time())
        e_end = e_start + timedelta(minutes=int(appt['total_duration']))
        
        # Overlap check: (Start1 < End2) AND (End1 > Start2)
        if requested_start < e_end and requested_end > e_start:
            flash('The selected time slot is no longer available for the full duration of your services. Please choose another time.', 'danger')
            return redirect(url_for('book_confirm', shop_id=shop_id, service_ids=service_ids))
    # --- End Check ---

    # Insert Appointment with total_price
    cursor.execute('INSERT INTO appointments (user_id, shop_id, appointment_date, appointment_time, total_duration, total_price, status, payment_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                   (session['id'], shop_id, date, time, total_duration, amount, 'pending', 'unpaid'))
    appointment_id = cursor.lastrowid

    # Insert into appointment_services
    for s_id in service_ids:
        cursor.execute('INSERT INTO appointment_services (appointment_id, service_id) VALUES (%s, %s)', (appointment_id, s_id))
    
    mysql.connection.commit()
    
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
        
        # Insert payment record
        cursor.execute('INSERT INTO payments (appointment_id, amount, payment_method, status) VALUES (%s, %s, %s, %s)',
                       (appointment_id, actual_amount, payment_method, 'completed'))
        
        # Determine new payment status and update booking status if it's the initial payment
        if is_final_passed == '1' or payment_plan == 'full':
            cursor.execute('UPDATE appointments SET payment_status = "paid" WHERE id = %s', (appointment_id,))
        else:
            cursor.execute('UPDATE appointments SET payment_status = "partially_paid" WHERE id = %s', (appointment_id,))
            
        # Only move from pending to confirmed during the initial payment phase
        cursor.execute('UPDATE appointments SET status = "confirmed" WHERE id = %s AND status = "pending"', (appointment_id,))
            
        mysql.connection.commit()
        
        # Get Shop Owner ID and Customer/Shop Name for notifications
        cursor.execute('''
            SELECT s.owner_id, s.name as shop_name, u.name as customer_name, a.appointment_date, a.appointment_time 
            FROM appointments a 
            JOIN shops s ON a.shop_id = s.id 
            JOIN users u ON a.user_id = u.id 
            WHERE a.id = %s
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
    cursor.execute('SELECT * FROM appointments WHERE id = %s', (appointment_id,))
    appt = cursor.fetchone()
    
    if not appt:
        flash('Appointment not found.', 'danger')
        return redirect(url_for('index'))
        
    # Check ownership
    is_owner = False
    if session.get('role') == 'shop_owner':
        cursor.execute('SELECT * FROM shops WHERE id = %s AND owner_id = %s', (appt['shop_id'], session['id']))
        if cursor.fetchone():
            is_owner = True
            
    if appt['user_id'] != session['id'] and not is_owner:
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('index'))
        
    cursor.execute('UPDATE appointments SET status = "cancelled" WHERE id = %s', (appointment_id,))
    mysql.connection.commit()
    
    # Get details for cross-notification
    cursor.execute('''
        SELECT a.user_id, s.owner_id, s.name as shop_name, u.name as customer_name, a.appointment_date 
        FROM appointments a 
        JOIN shops s ON a.shop_id = s.id 
        JOIN users u ON a.user_id = u.id 
        WHERE a.id = %s
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
        WHERE a.id = %s AND s.owner_id = %s
    ''', (appointment_id, session['id']))
    
    if not cursor.fetchone():
        flash('Unauthorized action.', 'danger')
        return redirect(url_for('owner_dashboard'))
        
    cursor.execute('UPDATE appointments SET status = "completed" WHERE id = %s', (appointment_id,))
    mysql.connection.commit()
    
    # Get user_id for the appointment to notify them
    cursor.execute('SELECT user_id FROM appointments WHERE id = %s', (appointment_id,))
    appt = cursor.fetchone()
    create_notification(appt['user_id'], "Service Completed", "Your grooming session is complete. We hope you enjoyed the service!", appointment_id)
    
    flash('Appointment marked as completed.', 'success')
    return redirect(url_for('owner_dashboard'))

@app.route('/pay_remaining/<int:appointment_id>')
def pay_remaining(appointment_id):
    if not is_logged_in():
        return redirect(url_for('login'))
        
    cursor = get_db_cursor()
    cursor.execute('SELECT * FROM appointments WHERE id = %s AND user_id = %s', (appointment_id, session['id']))
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
        cursor.execute('INSERT INTO reviews (user_id, shop_id, rating, comment) VALUES (%s, %s, %s, %s)',
                       (session['id'], shop_id, rating, comment))
        mysql.connection.commit()
        flash('Review submitted!', 'success')
    
    return redirect(url_for('shop_details', shop_id=shop_id))

if __name__ == '__main__':
    app.run(debug=True)
