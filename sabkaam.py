from flask import Flask, request, redirect, session, render_template_string, flash, abort, send_file, Response
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import os
import secrets
from werkzeug.utils import secure_filename
from markupsafe import escape

app = Flask(__name__)
app.secret_key = os.environ.get("SABKAAM_SECRET", secrets.token_hex(32))
DB = "sabkaam.db"
ADMIN_USERNAME = "Qasimzulfiqar123"
ADMIN_PASSWORD = "qasim@123"
COMMISSION_RATE = 0.05
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_provider_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DOC_EXTENSIONS = IMAGE_EXTENSIONS | {"pdf"}

# Customer-facing manual fund transfer instructions supplied by the owner.
FUND_ACCOUNTS = [
    {"name": "Zulfiqar Ali", "number": "03061689726", "methods": "JazzCash / Easypaisa"},
    {"name": "Qasim Zulfiqar", "number": "03025697747", "methods": "JazzCash / Easypaisa"},
    {"name": "SabKaam", "number": "03039659631", "methods": "JazzCash"},
]

CATEGORIES = [
    "Plumber", "Electrician", "Carpenter", "AC Technician", "Delivery",
    "Ride Service", "Cleaning", "Graphic Design", "Website Development",
    "Software Development", "Data Entry", "Excel Services", "Home Tutor",
    "Accountant", "Painter", "Construction", "Other"
]

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS service_categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        status TEXT NOT NULL DEFAULT 'Approved',
        submitted_by INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        phone TEXT,
        city TEXT,
        role TEXT NOT NULL,
        approved INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS job_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        city TEXT NOT NULL,
        area TEXT,
        budget REAL,
        status TEXT DEFAULT 'Open',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS offers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        provider_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        eta TEXT,
        message TEXT,
        status TEXT DEFAULT 'Pending',
        created_at TEXT NOT NULL,
        UNIQUE(request_id, provider_id)
    );

    CREATE TABLE IF NOT EXISTS marketplace_bookings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        offer_id INTEGER NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL,
        provider_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        commission REAL NOT NULL,
        status TEXT DEFAULT 'Accepted',
        customer_confirmed INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS provider_profiles(
        user_id INTEGER PRIMARY KEY,
        father_name TEXT,
        cnic_number TEXT,
        date_of_birth TEXT,
        gender TEXT,
        full_address TEXT,
        province TEXT,
        postal_code TEXT,
        emergency_contact TEXT,
        experience_years TEXT,
        about TEXT,
        profile_photo TEXT,
        cnic_front TEXT,
        cnic_back TEXT,
        verification_status TEXT NOT NULL DEFAULT 'Incomplete',
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS provider_services(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        UNIQUE(provider_id, category)
    );
    CREATE TABLE IF NOT EXISTS admin_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS commission_payments(id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL, provider_id INTEGER NOT NULL, amount REAL NOT NULL, method TEXT, reference TEXT, status TEXT NOT NULL DEFAULT 'Pending', paid_at TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS disputes(id INTEGER PRIMARY KEY AUTOINCREMENT, booking_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open', admin_note TEXT, created_at TEXT NOT NULL, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS website_content(content_key TEXT PRIMARY KEY, content_value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS admin_audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        link TEXT,
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """)
    # Safe marketplace payment ledger. Real JazzCash processing remains disabled.
    con.executescript("""
    CREATE TABLE IF NOT EXISTS payment_ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL,
        provider_id INTEGER NOT NULL,
        gross_amount REAL NOT NULL,
        commission_rate REAL NOT NULL,
        commission_amount REAL NOT NULL,
        gateway_fee REAL NOT NULL DEFAULT 0,
        provider_net REAL NOT NULL,
        gateway TEXT NOT NULL DEFAULT 'JazzCash',
        mode TEXT NOT NULL DEFAULT 'SANDBOX',
        payment_status TEXT NOT NULL DEFAULT 'Pending',
        gateway_reference TEXT,
        created_at TEXT NOT NULL,
        paid_at TEXT
    );
    CREATE TABLE IF NOT EXISTS provider_payouts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL UNIQUE,
        provider_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Awaiting confirmation',
        payout_reference TEXT,
        created_at TEXT NOT NULL,
        released_at TEXT
    );
    CREATE TABLE IF NOT EXISTS payment_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER,
        event_type TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS fund_submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        sender_name TEXT NOT NULL,
        sender_phone TEXT NOT NULL,
        payment_method TEXT NOT NULL,
        recipient_number TEXT NOT NULL,
        transaction_reference TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending verification',
        created_at TEXT NOT NULL,
        verified_at TEXT
    );
    """)
    # Additive migrations preserve existing bookings and records.
    payout_cols={r['name'] for r in con.execute("PRAGMA table_info(provider_payouts)").fetchall()}
    if 'method' not in payout_cols: con.execute("ALTER TABLE provider_payouts ADD COLUMN method TEXT")
    if 'recipient_account' not in payout_cols: con.execute("ALTER TABLE provider_payouts ADD COLUMN recipient_account TEXT")
    booking_cols={r['name'] for r in con.execute("PRAGMA table_info(marketplace_bookings)").fetchall()}
    if 'payment_status' not in booking_cols:
        con.execute("ALTER TABLE marketplace_bookings ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'Not Required'")
    # Set the requested 5% rate exactly once; later admin changes are preserved.
    commission_migration=con.execute("SELECT setting_value FROM admin_settings WHERE setting_key='commission_rate_v2_migration'").fetchone()
    if not commission_migration:
        con.execute("INSERT INTO admin_settings(setting_key,setting_value) VALUES('commission_rate','5') ON CONFLICT(setting_key) DO UPDATE SET setting_value='5'")
        con.execute("INSERT INTO admin_settings(setting_key,setting_value) VALUES('commission_rate_v2_migration','done')")
    else:
        con.execute("INSERT OR IGNORE INTO admin_settings(setting_key,setting_value) VALUES('commission_rate','5')")
    con.execute("INSERT OR IGNORE INTO admin_settings(setting_key,setting_value) VALUES('payment_mode','SANDBOX')")
    con.execute("INSERT OR IGNORE INTO admin_settings(setting_key,setting_value) VALUES('jazzcash_live_enabled','0')")
    for key,value in [('site_name','SabKaam'),('tagline','Har Service, Ek Hi Jagah!'),('support_email','support@sabkaam.pk'),('support_phone','')]:
        con.execute("INSERT OR IGNORE INTO website_content(content_key,content_value) VALUES(?,?)",(key,value))
    for category_name in CATEGORIES:
        con.execute("INSERT OR IGNORE INTO service_categories(name,status,created_at) VALUES(?,?,?)",
                    (category_name, "Approved", datetime.now().isoformat(timespec="minutes")))
    # One-time Super Admin credential migration. Existing admin account is reused,
    # so its user ID and related records remain unchanged.
    migrated = con.execute(
        "SELECT setting_value FROM admin_settings WHERE setting_key='super_admin_credentials_v2'"
    ).fetchone()
    admin = con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    if not migrated:
        if admin:
            con.execute(
                "UPDATE users SET name=?, email=?, password=?, approved=1 WHERE id=?",
                (ADMIN_USERNAME, ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), admin["id"])
            )
        else:
            con.execute(
                """INSERT INTO users(name,email,password,role,approved)
                   VALUES(?,?,?,?,1)""",
                (ADMIN_USERNAME, ADMIN_USERNAME,
                 generate_password_hash(ADMIN_PASSWORD), "admin")
            )
        con.execute(
            "INSERT INTO admin_settings(setting_key,setting_value) VALUES('super_admin_credentials_v2','done')"
        )
    elif not admin:
        con.execute(
            """INSERT INTO users(name,email,password,role,approved)
               VALUES(?,?,?,?,1)""",
            (ADMIN_USERNAME, ADMIN_USERNAME,
             generate_password_hash(ADMIN_PASSWORD), "admin")
        )
    con.commit()
    con.close()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("uid"):
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

def current_user():
    if not session.get("uid"):
        return None
    con = db()
    user = con.execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    con.close()
    return user

def approved_categories():
    con = db()
    rows = con.execute("SELECT name FROM service_categories WHERE status='Approved' ORDER BY name COLLATE NOCASE").fetchall()
    con.close()
    return [r["name"] for r in rows]

def admin_required():
    u = current_user()
    if not u or u["role"] != "admin":
        abort(403)
    return u

BASE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SabKaam | Har Service, Ek Hi Jagah</title>
<style>
:root{--navy:#10233f;--blue:#246bfe;--cyan:#20c7b5;--ink:#17243a;--muted:#718096;--bg:#f4f7fb;--line:#e7edf5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,Segoe UI,Arial,sans-serif}
header{background:var(--navy);color:white;padding:16px 5%;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.brand{font-size:23px;font-weight:800;letter-spacing:-.5px}.brand span{color:#45ddca}
nav{display:flex;gap:18px;flex-wrap:wrap}nav a{color:#eaf1ff;text-decoration:none;font-size:14px;font-weight:600}
main{max-width:1100px;margin:30px auto;padding:0 18px 50px}
.hero{background:linear-gradient(120deg,#10233f,#174b82 65%,#167f88);color:white;border-radius:24px;padding:42px 36px;display:grid;grid-template-columns:1.4fr .8fr;gap:20px;align-items:center;box-shadow:0 15px 35px #142d4a22}
.hero h1{font-size:clamp(30px,5vw,48px);line-height:1.08;margin:10px 0 14px}.hero p{color:#d8e7f8;line-height:1.7}.hero-art{font-size:90px;text-align:center;filter:drop-shadow(0 10px 15px #001b3c55)}
.eyebrow{color:#9df5e7;font-size:12px;font-weight:800;letter-spacing:2px;text-transform:uppercase}
.btn,button{border:0;border-radius:10px;background:var(--blue);color:white;padding:12px 17px;text-decoration:none;display:inline-block;font-weight:700;cursor:pointer}
.btn.alt{background:white;color:var(--navy)}.btn.dark{background:var(--navy)}.btn.small{padding:8px 12px;font-size:13px}
.card{background:white;border:1px solid var(--line);border-radius:17px;padding:22px;margin:15px 0;box-shadow:0 5px 18px #17385a08}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.stat{background:white;padding:18px;border-radius:15px;border:1px solid var(--line)}.stat strong{font-size:27px;display:block;color:var(--navy)}
h1,h2,h3{color:var(--navy)}.hero h1{color:white}h2{margin-top:0}.muted{color:var(--muted);font-size:14px}.pill{display:inline-block;padding:5px 10px;border-radius:30px;background:#eaf1ff;color:#2458aa;font-size:12px;font-weight:700}
input,select,textarea{width:100%;padding:13px;margin:7px 0 14px;border:1px solid #dce4ef;border-radius:10px;background:#fff;font:inherit}
label{font-size:13px;font-weight:700;color:#34445d}textarea{min-height:105px;resize:vertical}
form.inline{display:inline} .offer{border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0;background:#fbfdff}
.offer-price{font-size:25px;font-weight:800;color:#126b59}
.notice{padding:13px 16px;background:#e9f8f4;border:1px solid #c8eee3;border-radius:12px;margin:12px 0}
footer{text-align:center;color:#7a8799;padding:25px;font-size:13px}

/* Premium post-login dashboard styling */
body:has(.dashboard-shell){background:linear-gradient(135deg,#f3f7fc 0%,#edf3fb 100%)}
.dashboard-shell{display:grid;grid-template-columns:250px minmax(0,1fr);gap:22px;align-items:start}
.dash-side{background:linear-gradient(160deg,#10233f,#174b82);color:#fff;border-radius:22px;padding:25px 20px;min-height:440px;box-shadow:0 15px 35px #10233f20;position:sticky;top:18px}
.dash-logo{font-size:22px;font-weight:900;margin-bottom:8px}.dash-side .muted{color:#c6d8ed;line-height:1.6}
.dash-nav{display:grid;gap:9px;margin-top:28px}.dash-nav a{color:#eaf3ff;text-decoration:none;padding:12px 13px;border-radius:11px;font-weight:650;font-size:14px;background:#ffffff0b}
.dash-nav a:hover{background:#ffffff20}.dash-main{min-width:0}
.dash-top{display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap;margin:2px 0 20px}
.dash-top h1{font-size:29px;margin:0 0 5px}.dash-top p{margin:0;color:var(--muted)}
.dash-banner{background:linear-gradient(110deg,#176b83,#246bfe);color:#fff;border-radius:20px;padding:24px 27px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;gap:18px;box-shadow:0 12px 28px #246bfe25}
.dash-banner h2{color:#fff;margin:0 0 7px}.dash-banner p{color:#e1efff;margin:0;line-height:1.6}.dash-banner .art{font-size:54px}
.dash-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px;margin-bottom:20px}
.dash-stat{background:#fff;border:1px solid #e5ecf5;border-radius:17px;padding:18px;box-shadow:0 8px 24px #18385c08}
.dash-stat .symbol{font-size:23px}.dash-stat strong{display:block;font-size:27px;color:#10233f;margin:8px 0 3px}.dash-stat span{font-size:13px;color:#718096}
.dash-section{background:#fff;border:1px solid #e5ecf5;border-radius:19px;padding:22px;margin:0 0 17px;box-shadow:0 8px 24px #18385c08}
.dash-section h2{font-size:19px;margin:0 0 15px}.dash-section h3{font-size:16px}
.offer{border:1px solid #e4ebf4;border-radius:15px;padding:17px;margin:12px 0;background:#fbfdff}
.offer:hover{border-color:#bfd3f5;box-shadow:0 6px 18px #1d4f8810}
.offer-price{font-size:24px;font-weight:850;color:#087e69}
.pill{padding:6px 11px}.btn{box-shadow:0 5px 12px #246bfe20;transition:transform .15s,box-shadow .15s}.btn:hover,button:hover{transform:translateY(-1px);box-shadow:0 8px 18px #246bfe30}
.empty-state{text-align:center;padding:25px 12px;color:#718096}.empty-state .symbol{font-size:36px;margin-bottom:8px}
.auth-wrap{max-width:900px;margin:30px auto;display:grid;grid-template-columns:1fr 1fr;background:#fff;border:1px solid #e5ecf5;border-radius:24px;overflow:hidden;box-shadow:0 18px 50px #10233f12}
.auth-art{background:linear-gradient(145deg,#10233f,#176b83 70%,#20a99d);color:#fff;padding:38px;display:flex;flex-direction:column;justify-content:center}
.auth-art h2{color:#fff;font-size:30px;line-height:1.15}.auth-art p{color:#d8e9f6;line-height:1.7}.auth-art .big{font-size:65px;margin-bottom:20px}
.auth-form{padding:35px}.auth-form h2{font-size:25px;margin-bottom:6px}.auth-form button{width:100%;padding:14px}
@media(max-width:800px){.dashboard-shell{grid-template-columns:1fr}.dash-side{position:static;min-height:0}.dash-nav{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:17px}.dash-stats{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.hero{grid-template-columns:1fr;padding:28px 22px}.hero-art{font-size:60px}header{align-items:flex-start}nav{gap:12px}.auth-wrap{grid-template-columns:1fr}.auth-art{padding:25px}.auth-form{padding:25px}.dash-stats{grid-template-columns:1fr}.dash-banner{padding:20px}.dash-banner .art{font-size:38px}}

/* SabKaam professional messenger */
.chat-wrap{max-width:960px;margin:20px auto;background:#fff;border:1px solid #e4ebf4;border-radius:22px;overflow:hidden;box-shadow:0 18px 55px #10233f12}
.chat-head{padding:20px 24px;background:linear-gradient(115deg,#10233f,#176b83);color:#fff;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.chat-head h1{color:#fff;margin:0 0 6px;font-size:22px}.chat-head p{margin:0;color:#d9e9f7;font-size:13px;line-height:1.6}
.chat-head .chat-person{display:flex;align-items:center;gap:12px}.chat-avatar{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:#ffffff20;border:1px solid #ffffff40;font-size:21px}
.chat-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 22px;background:#f8fafd;border-bottom:1px solid #e8eef6;flex-wrap:wrap}
.chat-toolbar .muted{font-size:12px}.chat-room{height:460px;overflow-y:auto;padding:22px;background:radial-gradient(circle at top right,#eaf5ff 0,transparent 36%),#f4f7fb;scroll-behavior:smooth}
.chat-date{text-align:center;margin:0 auto 18px;font-size:11px;color:#7b8ba1;background:#fff;border:1px solid #e5ebf3;border-radius:30px;padding:6px 12px;width:max-content}
.chat-row{display:flex;margin:12px 0;align-items:flex-end;gap:8px}.chat-row.mine{justify-content:flex-end}.chat-mini-avatar{width:28px;height:28px;flex:0 0 28px;border-radius:50%;display:grid;place-items:center;background:#e3ebf7;color:#23466f;font-size:12px;font-weight:800}
.chat-bubble{max-width:min(76%,590px);padding:12px 15px;border-radius:17px 17px 17px 5px;background:#fff;border:1px solid #e4ebf4;box-shadow:0 4px 12px #17385a08;overflow-wrap:anywhere}
.chat-row.mine .chat-bubble{background:linear-gradient(135deg,#246bfe,#1e58d8);color:#fff;border:0;border-radius:17px 17px 5px 17px}
.chat-sender{font-size:11px;font-weight:800;color:#246bfe;margin-bottom:5px}.chat-row.mine .chat-sender{color:#dce8ff}.chat-message{white-space:pre-wrap;line-height:1.55;font-size:14px}.chat-time{font-size:10px;color:#8492a6;text-align:right;margin-top:7px}.chat-row.mine .chat-time{color:#d9e6ff}
.chat-empty{text-align:center;padding:80px 15px;color:#718096}.chat-empty .symbol{font-size:42px;margin-bottom:12px}
.chat-compose{padding:16px 20px 18px;background:#fff;border-top:1px solid #e8eef6}.chat-compose label{display:block;margin-bottom:4px}.chat-compose .compose-row{display:flex;align-items:flex-end;gap:10px}.chat-compose textarea{min-height:52px;max-height:130px;resize:vertical;margin:0;border-radius:14px;background:#f8fafd}.chat-compose button{height:49px;min-width:115px;border-radius:13px}.chat-hints{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}.chat-hint{font-size:11px;color:#61748e;background:#f1f5fa;border:1px solid #e3eaf3;border-radius:20px;padding:5px 9px}
@media(max-width:650px){.chat-wrap{margin:10px -4px;border-radius:16px}.chat-head{padding:17px}.chat-head h1{font-size:19px}.chat-room{height:55vh;min-height:330px;padding:14px 10px}.chat-bubble{max-width:84%;padding:10px 12px}.chat-compose{padding:12px}.chat-compose .compose-row{gap:7px}.chat-compose button{min-width:88px;padding:10px}.chat-toolbar{padding:10px 14px}}

</style>
</head>
<body>
<header><div class="brand">✦ Sab<span>Kaam</span></div>
<nav><a href="/">Home</a><a href="/post-request">Post a Job</a><a href="/requests">Find Requests</a>
{% if user %}<a href="/dashboard">Dashboard</a>{% if user.role=='provider' %}<a href="/provider/profile">My Profile</a>{% endif %}<a href="/logout">Logout</a>
{% else %}<a href="/login">Login</a><a href="/register">Join</a>{% endif %}</nav></header>
<main>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="notice">{{m}}</div>{% endfor %}{% endwith %}
{{ body|safe }}
</main><footer>SabKaam · Har Service, Ek Hi Jagah! · Pakistan</footer>
</body></html>
"""

def page(body, **data):
    # Insert the page-specific template before rendering so its Jinja variables work.
    template = BASE.replace("{{ body|safe }}", body)
    return render_template_string(template, user=current_user(), **data)

@app.route("/")
def home():
    categories = approved_categories()
    category_cards = "".join(
        f'<div class="stat"><span style="font-size:24px">{"🔧" if c in ["Plumber","Electrician","AC Technician"] else "🏠" if c in ["Cleaning","Carpenter","Painter"] else "💻" if "Design" in c or "Development" in c or "Excel" in c else "⭐"}</span><h3>{escape(c)}</h3></div>'
        for c in categories
    )
    return page("""
    <section class="hero">
      <div><div class="eyebrow">Your city. Your choice. Your price.</div>
      <h1>Kaam post karein.<br>Behtareen offer chunein.</h1>
      <p>Apni zaroorat post karein aur approved providers se price offers hasil karein.</p>
      <a class="btn alt" href="/post-request">Post Your Requirement</a>
      <a class="btn" href="/register">Join as Provider</a></div>
      <div class="hero-art">🛠️ 🧰 ⚡</div>
    </section>
    <div class="card"><h2>SabKaam kaise kaam karta hai?</h2><div class="grid">
       <div><div style="font-size:32px">📝</div><h3>1. Request post karein</h3><p class="muted">Service, city aur kaam ki details likhein.</p></div>
       <div><div style="font-size:32px">💬</div><h3>2. Offers hasil karein</h3><p class="muted">Providers apni price aur estimated time bhejein.</p></div>
       <div><div style="font-size:32px">🤝</div><h3>3. Apna provider chunein</h3><p class="muted">Offers compare karein aur apni pasand select karein.</p></div>
      </div></div>
    <div class="card"><h2>Popular categories</h2><div class="grid">
    """ + category_cards + """
    </div></div>
    """)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        role=request.form.get("role","customer")
        phone=request.form.get("phone","").strip()
        city=request.form.get("city","").strip()
        selected_services=request.form.getlist("services")
        if not name or not email or len(password)<8 or not city:
            flash("Name, email, city aur kam az kam 8-character password zaroori hain.")
        elif role not in ("customer","provider"):
            flash("Invalid account type.")
        else:
            con=db()
            try:
                con.execute("""INSERT INTO users(name,email,password,phone,city,role,approved)
                VALUES(?,?,?,?,?,?,?)""",
                (name,email,generate_password_hash(password),phone,city,role,0 if role=="provider" else 1))
                if role=="provider":
                    valid_services=[c for c in approved_categories() if c in selected_services]
                    for service in valid_services:
                        con.execute("INSERT OR IGNORE INTO provider_services(provider_id,category) VALUES(?,?)",
                                    (con.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone()[0],service))
                con.commit()
                flash("Account ban gaya. Provider account admin approval ke baad active hoga.")
                return redirect("/login")
            except sqlite3.IntegrityError:
                flash("Ye email pehle se registered hai.")
            finally:
                con.close()
    return page("""
    <div class="auth-wrap"><section class="auth-art"><div class="big">🛠️</div><h2>Join the SabKaam marketplace</h2>
    <p>Customer ban kar apna kaam post karein, ya service provider ban kar naye customers hasil karein.</p><p><b>Simple. Smart. Local.</b></p></section>
    <section class="auth-form"><h2>Create your account</h2><p class="muted">Start using SabKaam in a few easy steps.</p><form method="post">
    <label>Full name</label><input name="name" placeholder="Your full name" required>
    <label>Email address</label><input name="email" type="email" placeholder="name@example.com" required>
    <label>Phone number</label><input name="phone" placeholder="03XXXXXXXXX">
    <label>City</label><input name="city" placeholder="e.g. Lahore" required>
    <label>Services you provide (providers only — Ctrl ke sath multiple select karein)</label><select name="services" multiple size="6">{% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}</select>
    <label>Password (minimum 8 characters)</label><input name="password" type="password" minlength="8" required>
    <label>Choose account type</label><select name="role"><option value="customer">I need a service (Customer)</option><option value="provider">I provide services (Provider)</option></select>
    <button>Create SabKaam Account →</button></form><p class="muted">Already registered? <a href="/login">Login here</a></p></section></div>""")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        con=db()
        login_id=request.form.get("email", "").strip().lower()
        u=con.execute("SELECT * FROM users WHERE lower(email)=?",(login_id,)).fetchone()
        con.close()
        if u and check_password_hash(u["password"],request.form.get("password","")):
            if u["role"]=="provider" and not u["approved"]:
                flash("Provider account admin approval ka intezar kar raha hai.")
                return redirect("/login")
            session["uid"]=u["id"]
            return redirect("/dashboard")
        flash("Email ya password ghalat hai.")
    return page("""<div class="auth-wrap"><section class="auth-art"><div class="big">✦</div><h2>Welcome back to SabKaam</h2>
    <p>Apni service requests, provider offers aur bookings ek professional dashboard se manage karein.</p><p><b>Har Service, Ek Hi Jagah!</b></p></section>
    <section class="auth-form"><h2>Login to your account</h2><p class="muted">Continue your SabKaam journey.</p><form method="post">
    <label>Username or email</label><input name="email" type="text" placeholder="Enter username or email" autocomplete="username" required>
    <label>Password</label><input name="password" type="password" placeholder="Enter your password" autocomplete="current-password" required>
    <button>Login to Dashboard →</button></form><p class="muted">New to SabKaam? <a href="/register">Create an account</a></p></section></div>""")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/post-request", methods=["GET","POST"])
@login_required
def post_request():
    u=current_user()
    if not u or u["role"]!="customer":
        abort(403)
    if request.method=="POST":
        title=request.form.get("title","").strip()
        category=request.form.get("category","")
        description=request.form.get("description","").strip()
        city=request.form.get("city","").strip()
        area=request.form.get("area","").strip()
        budget_raw=request.form.get("budget","").strip()
        try:
            budget=float(budget_raw) if budget_raw else None
            if budget is not None and budget<=0: raise ValueError()
        except ValueError:
            flash("Budget valid amount mein likhein ya blank chhor dein.")
            return redirect("/post-request")
        if not title or category not in approved_categories() or not description or not city:
            flash("Title, category, details aur city zaroori hain.")
            return redirect("/post-request")
        con=db()
        cur=con.execute("""INSERT INTO job_requests(customer_id,title,category,description,city,area,budget,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",(u["id"],title,category,description,city,area,budget,datetime.now().isoformat(timespec="minutes")))
        request_id=cur.lastrowid
        matching=con.execute("""SELECT DISTINCT u.id FROM users u JOIN provider_services ps ON ps.provider_id=u.id
            WHERE u.role='provider' AND u.approved=1 AND LOWER(TRIM(u.city))=LOWER(TRIM(?))
            AND LOWER(TRIM(ps.category))=LOWER(TRIM(?))""",(city,category)).fetchall()
        now=datetime.now().isoformat(timespec="minutes")
        for provider in matching:
            con.execute("""INSERT INTO notifications(user_id,title,body,link,created_at)
                VALUES(?,?,?,?,?)""",(provider["id"],"New matching customer job",f"{category}: {title} · {city}. Customer ne naya kaam post kiya hai.",f"/make-offer/{request_id}",now))
        con.commit()
        con.close()
        flash("Aapki request publish ho gayi. Ab providers offers bhej sakte hain.")
        return redirect("/dashboard")
    return page("""
    <div class="card"><h2>Post your service request</h2><p class="muted">Provider apni price offer karega. Fixed price dena zaroori nahi.</p>
    <form method="post"><label>What service do you need?</label><input name="title" placeholder="e.g. Ghar ka ceiling fan repair" required>
    <label>Category</label><select name="category">{% for c in categories %}<option>{{c}}</option>{% endfor %}</select>
    <label>Work details</label><textarea name="description" placeholder="Kaam ki details, quantity ya issue likhein..." required></textarea>
    <label>City</label><input name="city" value="{{city}}" required>
    <label>Area / locality</label><input name="area" placeholder="e.g. Johar Town">
    <label>Optional budget (PKR)</label><input name="budget" type="number" min="1" placeholder="Agar budget maloom ho">
    <button>Publish Request</button></form></div>""",categories=approved_categories(),city=u["city"] or "")

@app.route("/requests")
@login_required
def requests_list():
    u=current_user()
    if not u or u["role"] not in ("provider","admin"):
        abort(403)
    con=db()
    if u["role"]=="admin":
        rows=con.execute("SELECT r.*,u.name customer_name FROM job_requests r JOIN users u ON u.id=r.customer_id WHERE r.status='Open' ORDER BY r.id DESC").fetchall()
    else:
        rows=con.execute("""SELECT r.*,u.name customer_name FROM job_requests r JOIN users u ON u.id=r.customer_id
        JOIN provider_services ps ON ps.provider_id=? AND LOWER(TRIM(ps.category))=LOWER(TRIM(r.category))
        WHERE r.status='Open' AND LOWER(TRIM(r.city))=LOWER(TRIM(?)) ORDER BY r.id DESC""",(u["id"],u["city"])).fetchall()
    con.close()
    return page("""
    <div class="card"><h2>Open customer requests</h2><p class="muted">Apne shehar ki requests dekhein aur apni offer bhejein.</p></div>
    {% for r in rows %}<div class="card"><span class="pill">{{r.category}}</span><h3>{{r.title}}</h3>
    <p>{{r.description}}</p><p class="muted">📍 {{r.area or 'Area not specified'}}, {{r.city}} · Customer: {{r.customer_name}}</p>
    {% if r.budget %}<p>Customer budget: Rs. {{'%.0f'|format(r.budget)}}</p>{% endif %}
    <a class="btn" href="/make-offer/{{r.id}}">Send Price Offer</a></div>
    {% else %}<div class="card">Is waqt aapke shehar mein koi open request nahi.</div>{% endfor %}
    """,rows=rows)

@app.route("/make-offer/<int:rid>", methods=["GET","POST"])
@login_required
def make_offer(rid):
    u=current_user()
    if not u or u["role"]!="provider" or not u["approved"]:
        abort(403)
    con=db()
    profile=con.execute("SELECT * FROM provider_profiles WHERE user_id=?",(u["id"],)).fetchone()
    if not profile or not all(profile[k] for k in ("father_name","cnic_number","date_of_birth","full_address","profile_photo","cnic_front","cnic_back")):
        con.close()
        flash("Customer ko offer bhejne se pehle apni provider profile aur required documents complete karein.")
        return redirect("/provider/profile")
    r=con.execute("""SELECT r.* FROM job_requests r JOIN provider_services ps ON ps.provider_id=?
        AND LOWER(TRIM(ps.category))=LOWER(TRIM(r.category))
        WHERE r.id=? AND r.status='Open' AND LOWER(TRIM(r.city))=LOWER(TRIM(?))""",(u["id"],rid,u["city"])).fetchone()
    if not r:
        con.close()
        abort(404)
    if request.method=="POST":
        try:
            amount=float(request.form.get("amount","0"))
            if amount<=0: raise ValueError()
        except ValueError:
            con.close()
            flash("Apni offer ki valid price enter karein.")
            return redirect(f"/make-offer/{rid}")
        eta=request.form.get("eta","").strip()
        message=request.form.get("message","").strip()
        try:
            con.execute("""INSERT INTO offers(request_id,provider_id,amount,eta,message,created_at)
            VALUES(?,?,?,?,?,?)""",(rid,u["id"],amount,eta,message,datetime.now().isoformat(timespec="minutes")))
            customer=con.execute("SELECT customer_id,title FROM job_requests WHERE id=?",(rid,)).fetchone()
            con.execute("""INSERT INTO notifications(user_id,title,body,link,created_at)
                VALUES(?,?,?,?,?)""",(customer["customer_id"],"New provider offer received",f"{u['name']} ne {customer['title']} ke liye Rs. {amount:,.0f} ki offer bheji hai.","/dashboard",datetime.now().isoformat(timespec="minutes")))
            con.commit()
            flash("Aapki price offer customer ko bhej di gayi.")
        except sqlite3.IntegrityError:
            flash("Aap is request par pehle hi offer bhej chuke hain.")
        con.close()
        return redirect("/dashboard")
    con.close()
    return page("""
    <div class="card"><span class="pill">{{r.category}}</span><h2>{{r.title}}</h2><p>{{r.description}}</p>
    <p class="muted">{{r.area or ''}} {{r.city}}</p>
    <form method="post"><label>Your price offer (PKR)</label><input name="amount" type="number" min="1" step="1" required>
    <label>Estimated completion time</label><input name="eta" placeholder="e.g. 2 hours / tomorrow">
    <label>Message to customer</label><textarea name="message" placeholder="Apne experience ya kaam ke bare mein batayein"></textarea>
    <button>Send Offer</button></form></div>""",r=r)

@app.route("/choose-offer/<int:oid>", methods=["POST"])
@login_required
def choose_offer(oid):
    u=current_user()
    if not u or u["role"]!="customer":
        abort(403)
    con=db()
    offer=con.execute("""SELECT o.*,r.customer_id,r.status request_status FROM offers o
    JOIN job_requests r ON r.id=o.request_id WHERE o.id=?""",(oid,)).fetchone()
    if not offer or offer["customer_id"]!=u["id"] or offer["request_status"]!="Open" or offer["status"]!="Pending":
        con.close()
        abort(404)
    rate_row=con.execute("SELECT setting_value FROM admin_settings WHERE setting_key='commission_rate'").fetchone()
    commission_rate=float(rate_row['setting_value'])/100 if rate_row else COMMISSION_RATE
    commission=round(offer['amount']*commission_rate,2)
    con.execute("UPDATE offers SET status='Not selected' WHERE request_id=?",(offer["request_id"],))
    con.execute("UPDATE offers SET status='Selected' WHERE id=?",(oid,))
    con.execute("UPDATE job_requests SET status='Assigned' WHERE id=?",(offer["request_id"],))
    now=datetime.now().isoformat(timespec="minutes")
    cur=con.execute("""INSERT INTO marketplace_bookings(request_id,offer_id,customer_id,provider_id,amount,commission,created_at,payment_status)
    VALUES(?,?,?,?,?,?,?,'Pending')""",(offer["request_id"],oid,u["id"],offer["provider_id"],offer["amount"],commission,now))
    bid=cur.lastrowid
    con.execute("""INSERT INTO payment_ledger(booking_id,customer_id,provider_id,gross_amount,commission_rate,commission_amount,gateway_fee,provider_net,gateway,mode,payment_status,created_at)
    VALUES(?,?,?,?,?,?,0,?,'JazzCash','SANDBOX','Pending',?)""",(bid,u['id'],offer['provider_id'],offer['amount'],commission_rate*100,commission,round(offer['amount']-commission,2),now))
    con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(bid,'FUND_REQUEST_CREATED','Manual wallet transfer instructions shown; payment is not verified until admin checks wallet statement.',now))
    con.commit(); con.close()
    flash("Offer select ho gayi. Ab Fund section mein payment instructions aur transaction details submit karein.")
    return redirect(f"/fund/{bid}")

@app.route("/fund/<int:bid>", methods=["GET", "POST"])
@login_required
def fund_booking(bid):
    u=current_user()
    if not u or u['role']!='customer': abort(403)
    con=db()
    b=con.execute("""SELECT b.*,r.title,p.name provider_name FROM marketplace_bookings b
        JOIN job_requests r ON r.id=b.request_id JOIN users p ON p.id=b.provider_id
        WHERE b.id=? AND b.customer_id=?""",(bid,u['id'])).fetchone()
    if not b: con.close(); abort(404)
    led=con.execute("SELECT * FROM payment_ledger WHERE booking_id=?",(bid,)).fetchone()
    submitted=con.execute("SELECT * FROM fund_submissions WHERE booking_id=?",(bid,)).fetchone()
    if request.method=='POST':
        if submitted or b['payment_status'] not in ('Pending','Deposit submitted'):
            con.close(); flash('Is booking ke liye payment details pehle submit ho chuki hain ya payment verified hai.'); return redirect(f'/fund/{bid}')
        sender_name=request.form.get('sender_name','').strip()[:100]
        sender_phone=request.form.get('sender_phone','').strip()[:30]
        method=request.form.get('payment_method','')
        recipient=request.form.get('recipient_number','')
        reference=request.form.get('transaction_reference','').strip()[:100]
        valid_recipients={'03061689726':('JazzCash','Easypaisa'),'03025697747':('JazzCash','Easypaisa'),'03039659631':('JazzCash',)}
        if not sender_name or not sender_phone or method not in ('JazzCash','Easypaisa') or recipient not in valid_recipients or (recipient in valid_recipients and method not in valid_recipients[recipient]) or not reference:
            con.close(); flash('Sender name, mobile, payment method, recipient aur transaction ID zaroori hain.'); return redirect(f'/fund/{bid}')
        now=datetime.now().isoformat(timespec='minutes')
        con.execute("""INSERT INTO fund_submissions(booking_id,customer_id,amount,sender_name,sender_phone,payment_method,recipient_number,transaction_reference,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",(bid,u['id'],b['amount'],sender_name,sender_phone,method,recipient,reference,now))
        con.execute("UPDATE marketplace_bookings SET payment_status='Deposit submitted' WHERE id=?",(bid,))
        con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(bid,'MANUAL_DEPOSIT_SUBMITTED',f'{method} transfer reported by customer. Ref: {reference}. Awaiting admin verification.',now))
        admin=con.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if admin: con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)",(admin['id'],'Customer fund deposit submitted',f'Booking #{bid}: customer ne {method} transfer reference submit ki hai. Wallet statement se verify karein.','/admin/funds',now))
        con.commit(); con.close(); flash('Payment details submit ho gayi hain. Admin wallet statement se verify karega; verification se pehle provider kaam start nahi kar sakta.'); return redirect(f'/fund/{bid}')
    con.close()
    html='''<div class="dash-section"><span class="pill">MANUAL TRANSFER · ADMIN VERIFICATION REQUIRED</span><h2>SabKaam Fund Section</h2><p>Booking #{{b.id}} · {{b.title}} · Provider: {{b.provider_name}}</p>
    <div class="dash-stats"><div class="dash-stat"><strong>Rs. {{'%.2f'|format(b.amount)}}</strong><span>Customer payment</span></div><div class="dash-stat"><strong>Rs. {{'%.2f'|format(led.commission_amount)}}</strong><span>SabKaam commission (5%)</span></div><div class="dash-stat"><strong>Rs. {{'%.2f'|format(led.provider_net)}}</strong><span>Provider share (95%)</span></div></div>
    <p><b>Payment status:</b> {{b.payment_status}}</p>
    <div class="offer"><h3>Payment transfer numbers</h3><p class="muted">Payment bhejne se pehle recipient ka naam aur number wallet app mein dobara verify karein. Sirf isi booking ki exact amount bhejein.</p>
    {% for a in accounts %}<div class="offer"><b>{{a.name}}</b><p>{{a.number}} · {{a.methods}}</p></div>{% endfor %}</div>
    {% if submitted %}<div class="offer"><b>Deposit details submitted</b><p>{{submitted.payment_method}} · {{submitted.recipient_number}} · Ref: {{submitted.transaction_reference}}</p><span class="pill">{{submitted.status}}</span><p class="muted">Admin payment ko apne wallet/bank statement mein verify karega. Sirf screenshot ya customer-submitted reference proof nahi hota.</p></div>
    {% elif b.payment_status=='Pending' %}<div class="offer"><h3>Transfer details submit karein</h3><form method="post"><label>Sender account holder name</label><input name="sender_name" required maxlength="100"><label>Sender mobile number</label><input name="sender_phone" required placeholder="03XXXXXXXXX"><label>Payment method</label><select name="payment_method"><option>JazzCash</option><option>Easypaisa</option></select><label>Payment kis number par bheji?</label><select name="recipient_number" required>{% for a in accounts %}<option value="{{a.number}}">{{a.name}} — {{a.number}} ({{a.methods}})</option>{% endfor %}</select><label>Transaction ID / TID</label><input name="transaction_reference" required maxlength="100" placeholder="Wallet receipt ka transaction reference"><button>Submit payment details for verification</button></form></div>{% endif %}
    <p class="muted">Important: Ye manual payment instruction page hai, escrow ya licensed payment gateway nahi. Admin actual wallet statement mein amount receive hone ki tasdeeq ke baad hi payment verify kare. Personal wallet numbers par public funds collect karne se pehle JazzCash/Easypaisa aur legal/tax adviser se marketplace collection aur provider payout ki written approval lein.</p></div>'''
    return page(html,b=b,led=led,submitted=submitted,accounts=FUND_ACCOUNTS)

@app.route('/admin/funds')
@login_required
def admin_funds():
    admin_required(); con=db()
    deposits=con.execute("""SELECT f.*,c.name customer_name,b.provider_id,p.name provider_name,b.amount booking_amount
        FROM fund_submissions f JOIN users c ON c.id=f.customer_id
        JOIN marketplace_bookings b ON b.id=f.booking_id JOIN users p ON p.id=b.provider_id
        ORDER BY CASE f.status WHEN 'Pending verification' THEN 0 ELSE 1 END,f.id DESC""").fetchall()
    payouts=con.execute("""SELECT pp.*,u.name provider_name,b.amount gross_amount FROM provider_payouts pp
        JOIN users u ON u.id=pp.provider_id JOIN marketplace_bookings b ON b.id=pp.booking_id ORDER BY pp.id DESC""").fetchall()
    con.close()
    html='''<div class="dash-section"><span class="pill">MANUAL WALLET CHECK · NO AUTOMATIC TRANSFERS</span><h2>Incoming customer deposits</h2><p>Har transaction ko apne JazzCash/Easypaisa wallet statement mein verify karein. Customer ka diya hua TID akela proof nahi hai.</p>
    {% for d in deposits %}<div class="offer"><b>Booking #{{d.booking_id}} · Rs. {{'%.2f'|format(d.amount)}}</b> <span class="pill">{{d.status}}</span><p>Customer: {{d.customer_name}} · Provider: {{d.provider_name}}</p><p>{{d.payment_method}} → {{d.recipient_number}} · Sender {{d.sender_name}} ({{d.sender_phone}})</p><p>Transaction ID: <b>{{d.transaction_reference}}</b> · Submitted {{d.created_at}}</p>{% if d.status=='Pending verification' %}<form method="post" action="/admin/fund/{{d.id}}/verify"><button>Verify funds received in wallet</button></form>{% endif %}</div>{% else %}<p>No customer deposits submitted.</p>{% endfor %}</div>
    <div class="dash-section"><h2>Provider payouts</h2><p>Customer completion confirmation ke baad provider ka 95% share yahan manual payout ke liye ready hota hai.</p>{% for p in payouts %}<div class="offer"><b>Booking #{{p.booking_id}} · {{p.provider_name}}</b> · Rs. {{'%.2f'|format(p.amount)}} <span class="pill">{{p.status}}</span>{% if p.status=='Ready for manual payout' %}<form method="post" action="/admin/fund-payout/{{p.id}}/paid"><label>Transfer method</label><select name="method"><option>JazzCash</option><option>Easypaisa</option><option>Bank Transfer</option></select><label>Provider recipient account/number</label><input name="recipient_account" required placeholder="Provider ka verified wallet/IBAN"><label>Transfer transaction reference</label><input name="reference" required><button>Confirm provider payout sent</button></form>{% elif p.status=='Paid' %}<p>Paid via {{p.method}} · {{p.recipient_account}} · Ref {{p.payout_reference}}</p>{% endif %}</div>{% else %}<p>No provider payouts yet.</p>{% endfor %}</div>'''
    return admin_page('Fund Management',html,deposits=deposits,payouts=payouts)

@app.route('/admin/fund/<int:fid>/verify',methods=['POST'])
@login_required
def admin_verify_fund(fid):
    admin_required(); con=db(); f=con.execute("SELECT * FROM fund_submissions WHERE id=?",(fid,)).fetchone()
    if not f or f['status']!='Pending verification': con.close(); flash('Deposit missing ya pehle verify ho chuki hai.'); return redirect('/admin/funds')
    now=datetime.now().isoformat(timespec='minutes')
    con.execute("UPDATE fund_submissions SET status='Verified',verified_at=? WHERE id=?",(now,fid))
    con.execute("UPDATE payment_ledger SET payment_status='Paid (Manual Verified)',gateway='Manual wallet transfer',mode='MANUAL',paid_at=? WHERE booking_id=?",(now,f['booking_id']))
    con.execute("UPDATE marketplace_bookings SET payment_status='Paid (Manual Verified)' WHERE id=?",(f['booking_id'],))
    con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(f['booking_id'],'MANUAL_FUNDS_VERIFIED',f"Admin verified {f['amount']} PKR in recipient wallet. Ref {f['transaction_reference']}.",now))
    b=con.execute("SELECT customer_id,provider_id FROM marketplace_bookings WHERE id=?",(f['booking_id'],)).fetchone()
    for uid in (b['customer_id'],b['provider_id']): con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)",(uid,'Booking payment verified',f"Booking #{f['booking_id']} payment admin ne verify kar di hai.",f"/inbox/{f['booking_id']}",now))
    con.commit(); con.close(); admin_log('Customer funds manually verified',f"Booking #{f['booking_id']}; Rs {f['amount']}"); flash('Funds verified. Ab provider job start kar sakta hai.'); return redirect('/admin/funds')

@app.route('/admin/fund-payout/<int:pid>/paid',methods=['POST'])
@login_required
def admin_fund_payout_paid(pid):
    admin_required(); method=request.form.get('method','').strip(); recipient=request.form.get('recipient_account','').strip(); reference=request.form.get('reference','').strip()
    if method not in ('JazzCash','Easypaisa','Bank Transfer') or not recipient or not reference: flash('Payout method, recipient aur transaction reference required hain.'); return redirect('/admin/funds')
    con=db(); p=con.execute("SELECT * FROM provider_payouts WHERE id=?",(pid,)).fetchone()
    if not p or p['status']!='Ready for manual payout': con.close(); flash('Payout ready nahi ya pehle paid mark ho chuki hai.'); return redirect('/admin/funds')
    now=datetime.now().isoformat(timespec='minutes')
    con.execute("UPDATE provider_payouts SET status='Paid',method=?,recipient_account=?,payout_reference=?,released_at=? WHERE id=?",(method,recipient,reference,now,pid))
    con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(p['booking_id'],'PROVIDER_PAYOUT_MARKED_PAID',f'Admin recorded payout by {method} to {recipient}; reference {reference}.',now))
    con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)",(p['provider_id'],'Provider payout sent',f"Booking #{p['booking_id']} ka payout admin ne paid mark kiya hai. Ref: {reference}",f"/inbox/{p['booking_id']}",now))
    con.commit(); con.close(); admin_log('Provider payout marked paid',f"Payout #{pid}, ref {reference}"); flash('Provider payout paid record ho gayi. Transfer receipt apne records mein rakhein.'); return redirect('/admin/funds')

@app.route("/inbox/<int:bid>", methods=["GET", "POST"])
@login_required
def inbox(bid):
    u=current_user()
    con=db()
    booking=con.execute("""SELECT b.*,r.title,c.name customer_name,p.name provider_name
        FROM marketplace_bookings b JOIN job_requests r ON r.id=b.request_id
        JOIN users c ON c.id=b.customer_id JOIN users p ON p.id=b.provider_id
        WHERE b.id=?""",(bid,)).fetchone()
    if not booking or u["id"] not in (booking["customer_id"],booking["provider_id"]):
        con.close(); abort(403)
    if request.method=="POST":
        message=request.form.get("message","").strip()
        if message and len(message)<=2000:
            con.execute("INSERT INTO chat_messages(booking_id,sender_id,message,created_at) VALUES(?,?,?,?)",
                (bid,u["id"],message,datetime.now().isoformat(timespec="minutes")))
            con.commit()
        con.close()
        return redirect(f"/inbox/{bid}")
    messages=con.execute("""SELECT m.*,u.name sender_name FROM chat_messages m
        JOIN users u ON u.id=m.sender_id WHERE m.booking_id=? ORDER BY m.id""",(bid,)).fetchall()
    con.close()
    return page("""
    <section class="chat-wrap">
      <div class="chat-head">
        <div class="chat-person"><div class="chat-avatar">💬</div><div>
          <h1>{{booking.title}}</h1>
          <p>SabKaam secure booking conversation · Booking #{{booking.id}}</p>
        </div></div>
        <a class="btn alt small" href="/dashboard">← Dashboard</a>
      </div>
      <div class="chat-toolbar">
        <div><b>{{booking.customer_name}}</b> <span class="muted">(Customer)</span> <span class="muted">↔</span> <b>{{booking.provider_name}}</b> <span class="muted">(Provider)</span></div>
        <div><span class="pill">{{booking.status}}</span> <b style="color:#087e69;margin-left:7px">Rs. {{'%.0f'|format(booking.amount)}}</b></div>
      </div>
      <div class="chat-toolbar" style="background:#fff;border-top:0">
        <span class="muted">Discuss work details, material, schedule and final price here.</span>
        <span><a class="muted" href="/inbox/{{booking.id}}">↻ Refresh messages</a> · <a class="muted" href="/report-dispute/{{booking.id}}">Report a problem</a></span>
      </div>
      <div class="chat-room" id="chatRoom">
        <div class="chat-date">Booking conversation</div>
        {% for m in messages %}
          <div class="chat-row {% if m.sender_id==u.id %}mine{% endif %}">
            {% if m.sender_id!=u.id %}<div class="chat-mini-avatar">{{m.sender_name[:1]|upper}}</div>{% endif %}
            <div class="chat-bubble">
              <div class="chat-sender">{% if m.sender_id==u.id %}You{% else %}{{m.sender_name}}{% endif %}</div>
              <div class="chat-message">{{m.message}}</div>
              <div class="chat-time">{{m.created_at}}</div>
            </div>
            {% if m.sender_id==u.id %}<div class="chat-mini-avatar">{{u.name[:1]|upper}}</div>{% endif %}
          </div>
        {% else %}
          <div class="chat-empty"><div class="symbol">👋</div><b>Conversation shuru karein</b><p class="muted">Pehla message bhejein aur kaam ki details clear karein.</p></div>
        {% endfor %}
      </div>
      <form method="post" class="chat-compose">
        <label for="chatMessage">Your message</label>
        <div class="compose-row">
          <textarea id="chatMessage" name="message" maxlength="2000" required placeholder="Message likhein... (kaam, location, timing, material ya price)"></textarea>
          <button type="submit">Send ➤</button>
        </div>
        <div class="chat-hints"><span class="chat-hint">📋 Work details</span><span class="chat-hint">🕒 Schedule</span><span class="chat-hint">💰 Price discussion</span><span class="chat-hint">📍 Location</span></div>
      </form>
    </section>
    <script>
      (function(){var room=document.getElementById('chatRoom');if(room){room.scrollTop=room.scrollHeight;}var box=document.getElementById('chatMessage');if(box){box.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(box.value.trim()){box.form.requestSubmit();}}});}})();
    </script>
    """,booking=booking,messages=messages,u=u)


@app.route("/job-status/<int:bid>/<status>", methods=["POST"])
@login_required
def job_status(bid,status):
    u=current_user()
    if not u or u["role"]!="provider" or status not in ("In Progress","Completed"):
        abort(403)
    con=db()
    b=con.execute("SELECT * FROM marketplace_bookings WHERE id=? AND provider_id=?",(bid,u["id"])).fetchone()
    if b and b['payment_status'] in ('Not Required','Paid (Sandbox)','Paid','Paid (Manual Verified)') and ((status=="In Progress" and b["status"]=="Accepted") or (status=="Completed" and b["status"]=="In Progress")):
        con.execute("UPDATE marketplace_bookings SET status=? WHERE id=?",(status,bid))
        con.commit()
    con.close()
    return redirect("/dashboard")

@app.route("/confirm-job/<int:bid>", methods=["POST"])
@login_required
def confirm_job(bid):
    u=current_user()
    if not u or u["role"]!="customer":
        abort(403)
    con=db()
    b=con.execute("SELECT * FROM marketplace_bookings WHERE id=? AND customer_id=? AND status='Completed'",(bid,u['id'])).fetchone()
    if b and b['payment_status'] in ('Not Required','Paid (Sandbox)','Paid','Paid (Manual Verified)'):
        now=datetime.now().isoformat(timespec='minutes')
        con.execute("UPDATE marketplace_bookings SET customer_confirmed=1 WHERE id=?",(bid,))
        row=con.execute("SELECT provider_net FROM payment_ledger WHERE booking_id=?",(bid,)).fetchone()
        if row:
            con.execute("INSERT OR IGNORE INTO provider_payouts(booking_id,provider_id,amount,status,created_at) VALUES(?,?,?,'Ready for manual payout',?)",(bid,b['provider_id'],row['provider_net'],now))
        con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(bid,'CUSTOMER_CONFIRMED','Customer confirmed completion; provider payout is ready for admin manual transfer. No automatic wallet transfer.',now))
    con.commit()
    con.close()
    flash("Aapki completion confirmation record ho gayi. Provider ki payout admin ke manual transfer ke liye ready hai.")
    return redirect("/dashboard")

@app.route("/dashboard")
@login_required
def dashboard():
    u=current_user()
    con=db()
    if u["role"]=="admin":
        pending=con.execute("SELECT * FROM users WHERE role='provider' AND approved=0 ORDER BY id DESC").fetchall()
        bookings=con.execute("""SELECT b.*,c.name customer_name,p.name provider_name,r.title
        FROM marketplace_bookings b JOIN users c ON c.id=b.customer_id
        JOIN users p ON p.id=b.provider_id JOIN job_requests r ON r.id=b.request_id ORDER BY b.id DESC""").fetchall()
        earnings=con.execute("SELECT COALESCE(SUM(commission),0) FROM marketplace_bookings WHERE status='Completed' AND customer_confirmed=1").fetchone()[0]
        total_requests=con.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0]
        category_count=con.execute("SELECT COUNT(*) FROM service_categories WHERE status='Approved'").fetchone()[0]
        booking_count=con.execute("SELECT COUNT(*) FROM marketplace_bookings").fetchone()[0]
        con.close()
        return page("""
        <div class="dashboard-shell">
          <aside class="dash-side"><div class="dash-logo">✦ SabKaam</div><p class="muted">Super Admin Control Center</p>
            <div class="dash-nav"><a href="/dashboard">▦ Overview</a><a href="/admin/manage">⚙ Manage Marketplace</a><a href="/requests">▤ Customer Requests</a><a href="/">⌂ View Website</a><a href="/logout">↪ Logout</a></div>
          </aside>
          <section class="dash-main">
            <div class="dash-top"><div><h1>Admin Dashboard</h1><p>Welcome back, SabKaam Admin. Manage your marketplace.</p></div><span class="pill">● Administrator</span></div>
            <div class="dash-banner"><div><h2>Marketplace overview</h2><p>Providers, customer requests aur completed bookings ko yahan manage karein.</p></div><div class="art">🛡️</div></div>
            <div class="dash-stats">
              <div class="dash-stat"><div class="symbol">💰</div><strong>Rs. {{'%.0f'|format(earnings)}}</strong><span>Recorded commission</span></div>
              <div class="dash-stat"><div class="symbol">📝</div><strong>{{total_requests}}</strong><span>Total customer requests</span></div>
              <div class="dash-stat"><div class="symbol">👥</div><strong>{{pending|length}}</strong><span>Pending providers</span></div>
              <div class="dash-stat"><div class="symbol">🧩</div><strong>{{category_count}}</strong><span>Approved categories</span></div>
              <div class="dash-stat"><div class="symbol">📦</div><strong>{{booking_count}}</strong><span>Total bookings</span></div>
            </div>
            <div class="dash-section"><h2>⚙ Marketplace controls</h2><p class="muted">Categories, users, requests, bookings aur commission ko ek jagah manage karein.</p><a class="btn" href="/admin/manage">Open Admin Management Center →</a></div>
            <div class="dash-section"><h2>👤 Provider approval requests</h2>
            {% for p in pending %}<div class="offer"><b>{{p.name}}</b><p class="muted">{{p.city}} · {{p.phone or 'No phone'}} · {{p.email}}</p>
              <form class="inline" method="post" action="/approve/{{p.id}}"><button class="small">✓ Approve</button></form>
              <form class="inline" method="post" action="/reject/{{p.id}}"><button class="small" style="background:#c0392b">Reject</button></form></div>
            {% else %}<div class="empty-state"><div class="symbol">✅</div>No pending provider approvals.</div>{% endfor %}</div>
            <div class="dash-section"><h2>📦 Marketplace bookings</h2>
            {% for b in bookings %}<div class="offer"><b>#{{b.id}} · {{b.title}}</b><p class="muted">{{b.customer_name}} → {{b.provider_name}}</p>
              <b>Rs. {{b.amount}}</b> · <span class="pill">{{b.status}}</span> · Commission Rs. {{b.commission}} · Customer confirmed: {{b.customer_confirmed}}</div>
            {% else %}<div class="empty-state"><div class="symbol">📭</div>No bookings yet.</div>{% endfor %}</div>
          </section>
        </div>
        """,pending=pending,bookings=bookings,earnings=earnings,total_requests=total_requests,category_count=category_count,booking_count=booking_count)

    if u["role"]=="provider":
        offers=con.execute("""SELECT o.*,r.title,r.city,r.area,r.status request_status
        FROM offers o JOIN job_requests r ON r.id=o.request_id WHERE o.provider_id=? ORDER BY o.id DESC""",(u["id"],)).fetchall()
        jobs=con.execute("""SELECT b.*,r.title,c.name customer_name,c.phone customer_phone
        FROM marketplace_bookings b JOIN job_requests r ON r.id=b.request_id
        JOIN users c ON c.id=b.customer_id WHERE b.provider_id=? ORDER BY b.id DESC""",(u["id"],)).fetchall()
        provider_services=con.execute("SELECT category FROM provider_services WHERE provider_id=? ORDER BY category",(u["id"],)).fetchall()
        notifications=con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30",(u["id"],)).fetchall()
        unread_count=con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(u["id"],)).fetchone()[0]
        con.close()
        return page("""
        <div class="dashboard-shell">
          <aside class="dash-side"><div class="dash-logo">✦ SabKaam</div><p class="muted">Provider Workspace</p>
            <div class="dash-nav"><a href="/dashboard">▦ My Dashboard</a><a href="/provider/profile">👤 Complete Profile</a><a href="/provider/services">🧰 My Service Fields</a><a href="/requests">🔎 Matching Customer Jobs</a><a href="/">⌂ View Website</a><a href="/logout">↪ Logout</a></div>
          </aside>
          <section class="dash-main">
            <div class="dash-top"><div><h1>Provider Dashboard</h1><p>Assalam-o-Alaikum, {{u.name}}. Aaj naye customers dhoondein.</p></div><span class="pill">✓ Approved Provider</span></div>
            <div class="dash-banner"><div><h2>Apna agla customer hasil karein!</h2><p>Open requests dekhein, apni price bhejein aur customer ka kaam hasil karein.</p><a class="btn alt" href="/requests">Browse Customer Requests →</a></div><div class="art">🧰</div></div>
            <div class="dash-section"><h2>🔔 Job notifications <span class="pill">{{unread_count}} new</span></h2>
              <p class="muted">Sirf aapke selected service field aur city ke matching customer jobs yahan notify hote hain.</p>
              {% for n in notifications %}<div class="offer"><b>{{n.title}}</b> {% if not n.is_read %}<span class="pill">New</span>{% endif %}<p>{{n.body}}</p><a class="btn small" href="{{n.link}}">View matching job / Send offer →</a></div>{% else %}<div class="empty-state">Abhi koi matching job notification nahi.</div>{% endfor %}
            </div>
            <div class="dash-section"><h2>👤 Provider identity & profile</h2><p class="muted">Apni profile, profile photo aur CNIC details complete karein. Admin verification ke baad trust badge mil sakta hai.</p><a class="btn small" href="/provider/profile">Complete / Update My Profile →</a></div>
            <div class="dash-section"><h2>🧰 My service fields</h2><p class="muted">{{provider_services|length}} selected service categories. Aap apni skills update kar sakte hain.</p><p>{% for ps in provider_services %}<span class="pill">{{ps.category}}</span> {% else %}<b>No service field selected yet.</b>{% endfor %}</p><a class="btn small" href="/provider/services">Manage My Services</a></div>
            <div class="dash-stats">
              <div class="dash-stat"><div class="symbol">📨</div><strong>{{offers|length}}</strong><span>Your submitted offers</span></div>
              <div class="dash-stat"><div class="symbol">🤝</div><strong>{{jobs|length}}</strong><span>Selected jobs</span></div>
              <div class="dash-stat"><div class="symbol">🏙️</div><strong>{{u.city}}</strong><span>Your service city</span></div>
            </div>
            <div class="dash-section"><h2>➕ Suggest a new service category</h2>
              <p class="muted">Agar aapki service ki category list mein nahi hai to admin ko approval ke liye bhejein.</p>
              <form method="post" action="/suggest-category"><label>New category name</label><input name="name" maxlength="50" placeholder="e.g. Solar Panel Technician" required><button>Send Category Suggestion</button></form>
            </div>
            <div class="dash-section"><h2>📨 Your price offers</h2>
            {% for o in offers %}<div class="offer"><span class="pill">{{o.status}}</span><h3>{{o.title}}</h3><p>Offer: <span class="offer-price">Rs. {{o.amount}}</span> · {{o.eta or 'Time not specified'}}</p>
              <p class="muted">{{o.city}} · {{o.message or 'No message added'}}</p></div>
            {% else %}<div class="empty-state"><div class="symbol">📭</div>Abhi aapne koi offer nahi bheji. Customer requests browse karein.</div>{% endfor %}</div>
            <div class="dash-section"><h2>🛠️ Your selected jobs</h2>
            {% for b in jobs %}<div class="offer"><span class="pill">{{b.status}}</span><h3>{{b.title}}</h3><p>Customer: {{b.customer_name}} · {{b.customer_phone or 'No phone'}}</p>
              <b class="offer-price">Rs. {{b.amount}}</b><p><a class="btn small" href="/inbox/{{b.id}}">💬 Open Inbox / Chat</a></p>
              {% if b.status=='Accepted' %}<form method="post" action="/job-status/{{b.id}}/In%20Progress"><button>▶ Start Job</button></form>
              {% elif b.status=='In Progress' %}<form method="post" action="/job-status/{{b.id}}/Completed"><button>✓ Mark Completed</button></form>{% endif %}</div>
            {% else %}<div class="empty-state"><div class="symbol">🧭</div>Abhi koi selected job nahi. Customer requests par offer bhejein.</div>{% endfor %}</div>
          </section>
        </div>
        """,u=u,offers=offers,jobs=jobs,provider_services=provider_services,notifications=notifications,unread_count=unread_count)

    requests=con.execute("SELECT * FROM job_requests WHERE customer_id=? ORDER BY id DESC",(u["id"],)).fetchall()
    bookings=con.execute("""SELECT b.*,r.title,p.name provider_name,p.phone provider_phone
    FROM marketplace_bookings b JOIN job_requests r ON r.id=b.request_id
    JOIN users p ON p.id=b.provider_id WHERE b.customer_id=? ORDER BY b.id DESC""",(u["id"],)).fetchall()
    customer_notifications=con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 20",(u["id"],)).fetchall()
    con.close()
    return page("""
    <div class="dashboard-shell">
      <aside class="dash-side"><div class="dash-logo">✦ SabKaam</div><p class="muted">Customer Workspace</p>
        <div class="dash-nav"><a href="/dashboard">▦ My Dashboard</a><a href="/post-request">＋ Post a New Job</a><a href="/">⌂ View Website</a><a href="/logout">↪ Logout</a></div>
      </aside>
      <section class="dash-main">
        <div class="dash-top"><div><h1>Customer Dashboard</h1><p>Welcome, {{u.name}}. Apne kaam aur offers yahan manage karein.</p></div><span class="pill">● Customer Account</span></div>
        <div class="dash-banner"><div><h2>Apna kaam post karein, offers compare karein!</h2><p>Multiple providers se price hasil karein aur apni pasand ka expert select karein.</p><a class="btn alt" href="/post-request">＋ Post a Service Request</a></div><div class="art">🤝</div></div>
        <div class="dash-stats">
          <div class="dash-stat"><div class="symbol">📝</div><strong>{{requests|length}}</strong><span>Your posted requests</span></div>
          <div class="dash-stat"><div class="symbol">💬</div><strong>{{all_offers|length}}</strong><span>Offers received</span></div>
          <div class="dash-stat"><div class="symbol">📦</div><strong>{{bookings|length}}</strong><span>Your bookings</span></div>
        </div>
        <div class="dash-section"><h2>🔔 Notifications</h2>{% for n in customer_notifications %}<div class="offer"><b>{{n.title}}</b><p>{{n.body}}</p><a href="{{n.link}}">View dashboard →</a></div>{% else %}<p class="muted">Abhi koi notification nahi.</p>{% endfor %}</div>
        <div class="dash-section"><h2>📝 Your requests & provider offers</h2>
        {% for r in requests %}<div class="offer"><span class="pill">{{r.status}}</span><h3>{{r.title}}</h3><p class="muted">{{r.category}} · {{r.city}} · {{r.area or ''}}</p><p>{{r.description}}</p>
          <h3>Provider price offers</h3>
          {% for o in all_offers if o.request_id==r.id %}<div class="offer"><b>{{o.provider_name}}</b> <span class="offer-price">Rs. {{o.amount}}</span>
            <p class="muted">{{o.eta or 'Time not specified'}} · {{o.message or ''}}</p>
            {% if r.status=='Open' and o.status=='Pending' %}<form method="post" action="/choose-offer/{{o.id}}"><button>✓ Choose this provider</button></form>{% else %}<span class="pill">{{o.status}}</span>{% endif %}
            {% if o.status=='Selected' %}{% for b in bookings if b.offer_id==o.id %}<a class="btn small" href="/inbox/{{b.id}}">💬 Open Inbox / Chat</a> {% if b.payment_status=='Pending' %}<a class="btn small" href="/fund/{{b.id}}">💰 Fund booking</a>{% endif %}{% endfor %}{% endif %}
          </div>{% else %}<p class="muted">Abhi koi offer nahi aayi. Providers ki offers ka intezar karein.</p>{% endfor %}
        </div>{% else %}<div class="empty-state"><div class="symbol">📝</div>Aapne abhi koi request post nahi ki.<br><a class="btn" href="/post-request">Post Your First Job</a></div>{% endfor %}</div>
        <div class="dash-section"><h2>📦 Your bookings</h2>
        {% for b in bookings %}<div class="offer"><span class="pill">{{b.status}}</span><h3>{{b.title}}</h3><p>{{b.provider_name}} · Rs. {{b.amount}}</p>
          <p class="muted">Provider phone: {{b.provider_phone or 'Not provided'}}</p>
          <p><a class="btn small" href="/inbox/{{b.id}}">💬 Open Inbox / Chat</a></p>
          {% if b.payment_status in ('Pending','Deposit submitted') %}<p><a class="btn small" href="/fund/{{b.id}}">💰 Open Fund Section / Payment Status</a> <span class="pill">{{b.payment_status}}</span></p>{% elif b.payment_status=='Paid (Manual Verified)' %}<p><span class="pill">Payment verified by SabKaam admin</span></p>{% endif %}
          {% if b.status=='Completed' and not b.customer_confirmed %}<form method="post" action="/confirm-job/{{b.id}}"><button>✓ Confirm work completed</button></form>
          {% elif b.customer_confirmed %}<span class="pill">Completion confirmed</span>{% endif %}</div>
        {% else %}<div class="empty-state"><div class="symbol">📭</div>Abhi koi confirmed booking nahi.</div>{% endfor %}</div>
      </section>
    </div>
    """,u=u,requests=requests,bookings=bookings,all_offers=all_offers_for_customer(u["id"]),customer_notifications=customer_notifications)

def all_offers_for_customer(customer_id):
    con=db()
    rows=con.execute("""SELECT o.*,r.customer_id,u.name provider_name
    FROM offers o JOIN job_requests r ON r.id=o.request_id
    JOIN users u ON u.id=o.provider_id WHERE r.customer_id=? ORDER BY o.amount ASC""",(customer_id,)).fetchall()
    con.close()
    return rows



def _profile_complete(profile):
    required = ("father_name", "cnic_number", "date_of_birth", "full_address", "profile_photo", "cnic_front", "cnic_back")
    return bool(profile and all(profile[k] for k in required))

@app.route("/provider/profile", methods=["GET", "POST"])
@login_required
def provider_profile():
    u = current_user()
    if not u or u["role"] != "provider":
        abort(403)
    con = db()
    existing = con.execute("SELECT * FROM provider_profiles WHERE user_id=?", (u["id"],)).fetchone()
    if request.method == "POST":
        fields = {
            "father_name": request.form.get("father_name", "").strip(),
            "cnic_number": request.form.get("cnic_number", "").strip(),
            "date_of_birth": request.form.get("date_of_birth", "").strip(),
            "gender": request.form.get("gender", "").strip(),
            "full_address": request.form.get("full_address", "").strip(),
            "province": request.form.get("province", "").strip(),
            "postal_code": request.form.get("postal_code", "").strip(),
            "emergency_contact": request.form.get("emergency_contact", "").strip(),
            "experience_years": request.form.get("experience_years", "").strip(),
            "about": request.form.get("about", "").strip()[:1000],
        }
        if not fields["father_name"] or not fields["cnic_number"] or not fields["date_of_birth"] or not fields["full_address"]:
            con.close(); flash("Father name, CNIC, date of birth aur complete address zaroor fill karein."); return redirect("/provider/profile")
        import re
        if not re.fullmatch(r"\d{5}-\d{7}-\d", fields["cnic_number"]):
            con.close(); flash("CNIC format 12345-1234567-1 hona chahiye."); return redirect("/provider/profile")
        saved = {}
        upload_specs = {"profile_photo": ("profile_photo", IMAGE_EXTENSIONS), "cnic_front": ("cnic_front", DOC_EXTENSIONS), "cnic_back": ("cnic_back", DOC_EXTENSIONS)}
        for key, (form_key, allowed) in upload_specs.items():
            f = request.files.get(form_key)
            old = existing[key] if existing else None
            saved[key] = old
            if f and f.filename:
                ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
                if ext not in allowed:
                    con.close(); flash(f"{key.replace('_',' ').title()} ki file type allowed nahi. Allowed: {', '.join(sorted(allowed))}."); return redirect("/provider/profile")
                f.stream.seek(0, os.SEEK_END); size = f.stream.tell(); f.stream.seek(0)
                if size > MAX_UPLOAD_BYTES:
                    con.close(); flash("Har upload maximum 5 MB ho sakta hai."); return redirect("/provider/profile")
                filename = secure_filename(f"provider_{u['id']}_{key}_{secrets.token_hex(8)}.{ext}")
                f.save(os.path.join(UPLOAD_DIR, filename))
                saved[key] = filename
        all_values = {**fields, **saved}
        complete = all(all_values[k] for k in ("father_name","cnic_number","date_of_birth","full_address","profile_photo","cnic_front","cnic_back"))
        status = "Pending Review" if complete else "Incomplete"
        con.execute("""INSERT INTO provider_profiles(user_id,father_name,cnic_number,date_of_birth,gender,full_address,province,postal_code,emergency_contact,experience_years,about,profile_photo,cnic_front,cnic_back,verification_status,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
          father_name=excluded.father_name,cnic_number=excluded.cnic_number,date_of_birth=excluded.date_of_birth,gender=excluded.gender,
          full_address=excluded.full_address,province=excluded.province,postal_code=excluded.postal_code,emergency_contact=excluded.emergency_contact,
          experience_years=excluded.experience_years,about=excluded.about,profile_photo=excluded.profile_photo,cnic_front=excluded.cnic_front,
          cnic_back=excluded.cnic_back,verification_status=excluded.verification_status,updated_at=excluded.updated_at""",
          (u["id"],fields["father_name"],fields["cnic_number"],fields["date_of_birth"],fields["gender"],fields["full_address"],fields["province"],fields["postal_code"],fields["emergency_contact"],fields["experience_years"],fields["about"],saved["profile_photo"],saved["cnic_front"],saved["cnic_back"],status,datetime.now().isoformat(timespec="minutes")))
        con.commit(); con.close()
        flash("Profile save ho gayi. Required documents complete hain to admin review ke liye submit kar di gayi hai.")
        return redirect("/provider/profile")
    con.close()
    return page("""
    <div class="card" style="max-width:850px;margin:auto"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><div><h2>👤 Complete Your Provider Profile</h2><p class="muted">Customer trust aur identity verification ke liye apni real details enter karein.</p></div><span class="pill">{{profile.verification_status if profile else 'Incomplete'}}</span></div>
    <div class="notice"><b>Privacy & security:</b> CNIC images private storage mein rakhi jayengi. Ye public website par show nahi hongi; sirf authorized admin review ke liye access hongi. Sirf official verification ke liye upload karein.</div>
    <form method="post" enctype="multipart/form-data">
    <h3>Personal information</h3><label>Full name (account)</label><input value="{{u.name}}" disabled><label>Father's full name *</label><input name="father_name" value="{{profile.father_name if profile else ''}}" required>
    <label>CNIC number *</label><input name="cnic_number" placeholder="12345-1234567-1" pattern="[0-9]{5}-[0-9]{7}-[0-9]" value="{{profile.cnic_number if profile else ''}}" required>
    <label>Date of birth *</label><input type="date" name="date_of_birth" value="{{profile.date_of_birth if profile else ''}}" required>
    <label>Gender (optional)</label><select name="gender"><option value="">Prefer not to say</option><option {% if profile and profile.gender=='Male' %}selected{% endif %}>Male</option><option {% if profile and profile.gender=='Female' %}selected{% endif %}>Female</option><option {% if profile and profile.gender=='Other' %}selected{% endif %}>Other</option></select>
    <label>Complete home address *</label><textarea name="full_address" required>{{profile.full_address if profile else ''}}</textarea><div class="grid"><div><label>Province</label><input name="province" value="{{profile.province if profile else ''}}" placeholder="Punjab"></div><div><label>Postal code</label><input name="postal_code" value="{{profile.postal_code if profile else ''}}"></div></div>
    <div class="grid"><div><label>Emergency contact</label><input name="emergency_contact" value="{{profile.emergency_contact if profile else ''}}" placeholder="03XXXXXXXXX"></div><div><label>Experience</label><input name="experience_years" value="{{profile.experience_years if profile else ''}}" placeholder="e.g. 3 years"></div></div>
    <label>About your work / skills</label><textarea name="about" maxlength="1000" placeholder="Apne experience, skills aur service quality ke bare mein likhein">{{profile.about if profile else ''}}</textarea>
    <h3>Required documents</h3><p class="muted">Profile photo: JPG/PNG/WEBP. CNIC front/back: JPG/PNG/WEBP/PDF. Maximum 5 MB each. Existing file ko replace karna zaroori nahi.</p>
    <label>Profile photo * {% if profile and profile.profile_photo %}<span class="pill">Already uploaded</span>{% endif %}</label><input type="file" name="profile_photo" accept="image/jpeg,image/png,image/webp" {% if not profile or not profile.profile_photo %}required{% endif %}>
    <label>CNIC front side * {% if profile and profile.cnic_front %}<span class="pill">Already uploaded</span>{% endif %}</label><input type="file" name="cnic_front" accept="image/jpeg,image/png,image/webp,application/pdf" {% if not profile or not profile.cnic_front %}required{% endif %}>
    <label>CNIC back side * {% if profile and profile.cnic_back %}<span class="pill">Already uploaded</span>{% endif %}</label><input type="file" name="cnic_back" accept="image/jpeg,image/png,image/webp,application/pdf" {% if not profile or not profile.cnic_back %}required{% endif %}>
    <button type="submit">Save Profile & Submit for Review →</button></form></div>
    """,u=u,profile=existing)

@app.route("/admin/provider-document/<int:uid>/<kind>")
@login_required
def admin_provider_document(uid, kind):
    admin_required()
    if kind not in ("profile_photo", "cnic_front", "cnic_back"):
        abort(404)
    con=db(); row=con.execute(f"SELECT {kind} AS filename FROM provider_profiles WHERE user_id=?",(uid,)).fetchone(); con.close()
    if not row or not row["filename"]: abort(404)
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, row["filename"], as_attachment=True, download_name=f"provider_{uid}_{kind}_{row['filename'].rsplit('.',1)[-1]}")

@app.route("/admin/provider-profile/<int:uid>/verify", methods=["POST"])
@login_required
def admin_verify_provider_profile(uid):
    admin_required()
    con=db(); profile=con.execute("SELECT * FROM provider_profiles WHERE user_id=?",(uid,)).fetchone()
    if not _profile_complete(profile):
        con.close(); flash("Provider ki profile/documents abhi incomplete hain."); return redirect("/admin/manage")
    con.execute("UPDATE provider_profiles SET verification_status='Verified' WHERE user_id=?",(uid,))
    con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)",(uid,"Provider profile verified","Aapki identity profile admin ne verify kar di hai.","/provider/profile",datetime.now().isoformat(timespec="minutes")))
    con.commit(); con.close(); flash("Provider profile verified."); return redirect("/admin/manage")

@app.route("/provider/services", methods=["GET", "POST"])
@login_required
def provider_services_manage():
    u=current_user()
    if not u or u["role"]!="provider" or not u["approved"]:
        abort(403)
    con=db()
    if request.method=="POST":
        selected=[c for c in approved_categories() if c in request.form.getlist("services")]
        con.execute("DELETE FROM provider_services WHERE provider_id=?",(u["id"],))
        for category in selected:
            con.execute("INSERT OR IGNORE INTO provider_services(provider_id,category) VALUES(?,?)",(u["id"],category))
        con.commit()
        con.close()
        flash("Aapki service fields update ho gayi hain. Ab matching category aur city ki jobs milengi.")
        return redirect("/dashboard")
    current=[r["category"] for r in con.execute("SELECT category FROM provider_services WHERE provider_id=?",(u["id"],)).fetchall()]
    pending=con.execute("SELECT name,created_at FROM service_categories WHERE submitted_by=? AND status='Pending' ORDER BY id DESC",(u["id"],)).fetchall()
    con.close()
    return page("""<div class="card"><h2>🧰 Select your service fields</h2><p class="muted">Jo services aap provide karte hain unhein select karein. Agar aapki service list mein nahi hai to neeche Add Your Own Service se suggest karein. Admin approval ke baad woh website par sab customers aur providers ke liye available ho jayegi.</p>
    <form method="post"><div class="grid">{% for c in categories %}<label style="display:flex;align-items:center;gap:10px;padding:12px;border:1px solid #e5ecf5;border-radius:12px;margin:4px 0;background:#fbfdff"><input style="width:auto;margin:0" type="checkbox" name="services" value="{{c}}" {% if c in current %}checked{% endif %}> <span>{{c}}</span></label>{% endfor %}</div><button style="margin-top:15px">Save My Services</button></form>
    <div style="border-top:1px solid #e5ecf5;margin-top:24px;padding-top:20px"><h2>➕ Add Your Own Service</h2><p class="muted">Misal: Solar Panel Installer, CCTV Technician, Mobile Repair, Makeup Artist. Duplicate service add nahi hogi.</p><form method="post" action="/suggest-category"><label>New service name</label><input name="name" maxlength="50" minlength="3" placeholder="e.g. Solar Panel Installer" required><button type="submit" class="btn dark">Submit for Approval</button></form></div>
    {% if pending %}<div class="notice"><b>⏳ Your pending service suggestions</b>{% for item in pending %}<p style="margin:7px 0">{{item.name}} — Waiting for admin approval</p>{% endfor %}</div>{% endif %}
    </div>""",categories=approved_categories(),current=current,pending=pending)

@app.route("/notifications/read-all", methods=["POST"])
@login_required
def notifications_read_all():
    con=db()
    con.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["uid"],))
    con.commit(); con.close()
    return redirect("/dashboard")

@app.route("/suggest-category", methods=["POST"])
@login_required
def suggest_category():
    u=current_user()
    if not u or u["role"]!="provider" or not u["approved"]:
        abort(403)
    name=request.form.get("name","").strip()
    if len(name)<3 or len(name)>50:
        flash("Category name 3 se 50 characters ke darmiyan honi chahiye.")
        return redirect("/dashboard")
    con=db()
    exists=con.execute("SELECT id FROM service_categories WHERE name=? COLLATE NOCASE",(name,)).fetchone()
    if exists:
        flash("Ye category pehle se maujood hai ya approval ke liye bheji ja chuki hai.")
    else:
        con.execute("INSERT INTO service_categories(name,status,submitted_by,created_at) VALUES(?,?,?,?)",
                    (name,"Pending",u["id"],datetime.now().isoformat(timespec="minutes")))
        con.commit()
        flash("Category suggestion admin ko approval ke liye bhej di gayi.")
    con.close()
    return redirect("/dashboard")


@app.route("/admin/manage")
@login_required
def admin_manage():
    admin_required()
    con=db()
    categories=con.execute("""SELECT sc.*,u.name provider_name FROM service_categories sc
        LEFT JOIN users u ON u.id=sc.submitted_by
        ORDER BY CASE sc.status WHEN 'Pending' THEN 0 WHEN 'Approved' THEN 1 ELSE 2 END,sc.name""").fetchall()
    providers=con.execute("""SELECT u.*,pp.father_name,pp.cnic_number,pp.date_of_birth,pp.full_address,pp.about,pp.profile_photo,pp.cnic_front,pp.cnic_back,pp.verification_status
        FROM users u LEFT JOIN provider_profiles pp ON pp.user_id=u.id WHERE u.role='provider' ORDER BY u.id DESC""").fetchall()
    customers=con.execute("SELECT * FROM users WHERE role='customer' ORDER BY id DESC").fetchall()
    requests=con.execute("""SELECT r.*,u.name customer_name FROM job_requests r JOIN users u ON u.id=r.customer_id ORDER BY r.id DESC LIMIT 100""").fetchall()
    offers=con.execute("""SELECT o.*,r.title,p.name provider_name FROM offers o JOIN job_requests r ON r.id=o.request_id JOIN users p ON p.id=o.provider_id ORDER BY o.id DESC LIMIT 100""").fetchall()
    totals={
      "users":con.execute("SELECT COUNT(*) FROM users WHERE role IN ('customer','provider')").fetchone()[0],
      "providers":con.execute("SELECT COUNT(*) FROM users WHERE role='provider' AND approved=1").fetchone()[0],
      "customers":con.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0],
      "pending_categories":con.execute("SELECT COUNT(*) FROM service_categories WHERE status='Pending'").fetchone()[0],
      "completed":con.execute("SELECT COUNT(*) FROM marketplace_bookings WHERE status='Completed'").fetchone()[0],
      "commission":con.execute("SELECT COALESCE(SUM(commission),0) FROM marketplace_bookings WHERE status='Completed' AND customer_confirmed=1").fetchone()[0]
    }
    con.close()
    return page("""
    <div class="dashboard-shell"><aside class="dash-side"><div class="dash-logo">✦ SabKaam</div><p class="muted">Admin Management Center</p>
      <div class="dash-nav"><a href="/dashboard">▦ Overview</a><a href="/admin/manage">⚙ Management</a><a href="/admin/orders">▤ Orders</a><a href="/admin/finance">💰 Finance</a><a href="/admin/funds">💵 Fund Management</a><a href="/admin/payment-ledger">💳 Payment Ledger (Sandbox)</a><a href="/admin/disputes">⚖ Disputes</a><a href="/admin/notifications">🔔 Notifications</a><a href="/admin/analytics">📊 Analytics</a><a href="/admin/website">🌐 Website</a><a href="/admin/security">🛡 Security</a><a href="/requests">▤ Open Requests</a><a href="/">⌂ Website</a><a href="/logout">↪ Logout</a></div></aside>
    <section class="dash-main"><div class="dash-top"><div><h1>Premium Admin Center</h1><p>SabKaam marketplace ka complete business control.</p></div><span class="pill">● Super Admin</span></div>
    <div class="dash-section"><h2>✦ Admin Control Center</h2><div class="grid"><a class="btn" href="/admin/orders">Orders Management</a><a class="btn" href="/admin/finance">Commission & Payments</a><a class="btn" href="/admin/disputes">Complaints & Disputes</a><a class="btn" href="/admin/notifications">Notifications Center</a><a class="btn" href="/admin/analytics">Analytics & Reports</a><a class="btn" href="/admin/website">Website Management</a><a class="btn" href="/admin/security">Security & Backups</a></div></div>
    <div class="dash-stats">
      <div class="dash-stat"><div class="symbol">👥</div><strong>{{totals.users}}</strong><span>Total users</span></div>
      <div class="dash-stat"><div class="symbol">🧰</div><strong>{{totals.providers}}</strong><span>Approved providers</span></div>
      <div class="dash-stat"><div class="symbol">🧍</div><strong>{{totals.customers}}</strong><span>Customers</span></div>
      <div class="dash-stat"><div class="symbol">🧩</div><strong>{{totals.pending_categories}}</strong><span>Pending categories</span></div>
      <div class="dash-stat"><div class="symbol">✅</div><strong>{{totals.completed}}</strong><span>Completed jobs</span></div>
      <div class="dash-stat"><div class="symbol">💰</div><strong>Rs. {{'%.0f'|format(totals.commission)}}</strong><span>Confirmed commission</span></div>
    </div>
    <div class="dash-section"><h2>🧩 Service category management</h2><p class="muted">Admin category add kar sakta hai. Provider ki suggested category approval ke baad customer form mein available hogi.</p>
      <form method="post" action="/admin/category/add"><label>Add a new category</label><input name="name" maxlength="50" placeholder="e.g. Solar Panel Technician" required><button>Add Category</button></form>
      <div class="grid" style="margin-top:18px">
      {% for c in categories %}<div class="offer" style="margin:0"><b>{{c.name}}</b> <span class="pill">{{c.status}}</span><p class="muted">{% if c.provider_name %}Suggested by {{c.provider_name}}{% else %}Built-in / admin category{% endif %}</p>
        {% if c.status=='Pending' %}<form class="inline" method="post" action="/admin/category/{{c.id}}/approve"><button class="small">Approve & publish</button></form>{% endif %}
        {% if c.status=='Approved' %}<form class="inline" method="post" action="/admin/category/{{c.id}}/remove" onsubmit="return confirm('Is category ko website se remove karna hai? Purane orders safe rahenge.');"><button class="small" style="background:#a66a18">Remove from website</button></form>{% endif %}
        {% if c.status=='Disabled' %}<form class="inline" method="post" action="/admin/category/{{c.id}}/enable"><button class="small">Publish again</button></form>{% endif %}
        <form class="inline" method="post" action="/admin/category/{{c.id}}/delete" onsubmit="return confirm('Permanent delete? Agar category purane orders ya providers mein use hui hai to sirf disable hogi.');"><button class="small" style="background:#b83232">Delete</button></form>
      </div>{% else %}<p>No categories.</p>{% endfor %}</div>
    </div>
    <div class="dash-section"><h2>🧰 Provider management & identity verification</h2>{% for p in providers %}<div class="offer"><b>{{p.name}}</b> <span class="pill">{% if p.approved %}Approved{% else %}Pending account{% endif %}</span> <span class="pill">{{p.verification_status or 'Profile incomplete'}}</span><p class="muted">{{p.email}} · {{p.phone or 'No phone'}} · {{p.city or 'No city'}}</p>
      {% if p.father_name %}<p>Father: {{p.father_name}} · CNIC: {{p.cnic_number}} · DOB: {{p.date_of_birth}}</p><p>{{p.full_address}}</p><p>{{p.about or ''}}</p>{% endif %}
      {% if p.profile_photo %}<a class="btn small" href="/admin/provider-document/{{p.id}}/profile_photo">View profile photo</a>{% endif %} {% if p.cnic_front %}<a class="btn small" href="/admin/provider-document/{{p.id}}/cnic_front">View CNIC front</a>{% endif %} {% if p.cnic_back %}<a class="btn small" href="/admin/provider-document/{{p.id}}/cnic_back">View CNIC back</a>{% endif %}
      {% if p.verification_status=='Pending Review' %}<form class="inline" method="post" action="/admin/provider-profile/{{p.id}}/verify"><button class="small">Verify identity</button></form>{% endif %}
      {% if not p.approved %}<form class="inline" method="post" action="/approve/{{p.id}}"><button class="small">Approve</button></form><form class="inline" method="post" action="/reject/{{p.id}}"><button class="small" style="background:#b83232">Reject</button></form>{% endif %}</div>{% else %}<p>No providers.</p>{% endfor %}</div>
    <div class="dash-section"><h2>🧍 Customer accounts</h2>{% for c in customers %}<div class="offer"><b>{{c.name}}</b><p class="muted">{{c.email}} · {{c.phone or 'No phone'}} · {{c.city or 'No city'}}</p></div>{% else %}<p>No customers.</p>{% endfor %}</div>
    <div class="dash-section"><h2>📝 All customer requests</h2>{% for r in requests %}<div class="offer"><b>#{{r.id}} · {{r.title}}</b> <span class="pill">{{r.status}}</span><p class="muted">{{r.category}} · {{r.city}} · {{r.customer_name}}</p></div>{% else %}<p>No requests.</p>{% endfor %}</div>
    <div class="dash-section"><h2>💬 Recent provider offers</h2>{% for o in offers %}<div class="offer"><b>{{o.provider_name}}</b> offered <b>Rs. {{o.amount}}</b> for {{o.title}} <span class="pill">{{o.status}}</span></div>{% else %}<p>No offers.</p>{% endfor %}</div>
    </section></div>
    """,categories=categories,providers=providers,customers=customers,requests=requests,offers=offers,totals=totals)


@app.route("/admin/category/add",methods=["POST"])
@login_required
def admin_add_category():
    admin_required()
    name=request.form.get("name","").strip()
    if len(name)<3 or len(name)>50:
        flash("Category name 3 se 50 characters ke darmiyan honi chahiye.")
        return redirect("/admin/manage")
    con=db()
    try:
        con.execute("INSERT INTO service_categories(name,status,created_at) VALUES(?,?,?)",
                    (name,"Approved",datetime.now().isoformat(timespec="minutes")))
        con.commit()
        flash("New category add ho gayi aur ab available hai.")
    except sqlite3.IntegrityError:
        flash("Ye category pehle se maujood hai.")
    con.close()
    return redirect("/admin/manage")


@app.route("/admin/category/<int:cid>/approve",methods=["POST"])
@login_required
def admin_approve_category(cid):
    admin_required()
    con=db()
    category=con.execute("SELECT name,submitted_by FROM service_categories WHERE id=?",(cid,)).fetchone()
    con.execute("UPDATE service_categories SET status='Approved' WHERE id=?",(cid,))
    if category and category["submitted_by"]:
        con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)",
                    (category["submitted_by"],"Your service is approved",f"{category['name']} is now live on the SabKaam website. You can select it in My Service Fields.","/provider/services",datetime.now().isoformat(timespec="minutes")))
    con.commit()
    con.close()
    flash("Category approved and now available on the website.")
    return redirect("/admin/manage")


@app.route("/admin/category/<int:cid>/disable",methods=["POST"])
@app.route("/admin/category/<int:cid>/remove",methods=["POST"])
@login_required
def admin_disable_category(cid):
    admin_required()
    con=db()
    cur=con.execute("UPDATE service_categories SET status='Disabled' WHERE id=?",(cid,))
    con.commit()
    con.close()
    flash("Category website se remove ho gayi. Purane orders aur records safe hain." if cur.rowcount else "Category nahi mili.")
    return redirect("/admin/manage")


@app.route("/admin/category/<int:cid>/enable",methods=["POST"])
@login_required
def admin_enable_category(cid):
    admin_required()
    con=db()
    cur=con.execute("UPDATE service_categories SET status='Approved' WHERE id=?",(cid,))
    con.commit()
    con.close()
    flash("Category dobara website par publish ho gayi." if cur.rowcount else "Category nahi mili.")
    return redirect("/admin/manage")


@app.route("/admin/category/<int:cid>/delete",methods=["POST"])
@login_required
def admin_delete_category(cid):
    admin_required()
    con=db()
    category=con.execute("SELECT name FROM service_categories WHERE id=?",(cid,)).fetchone()
    if not category:
        con.close()
        flash("Category nahi mili.")
        return redirect("/admin/manage")
    name=category["name"]
    # Keep category rows when referenced so historical orders and provider skill
    # records remain consistent. In that case, remove it from public selection.
    used_requests=con.execute("SELECT 1 FROM job_requests WHERE category=? COLLATE NOCASE LIMIT 1",(name,)).fetchone()
    used_providers=con.execute("SELECT 1 FROM provider_services WHERE category=? COLLATE NOCASE LIMIT 1",(name,)).fetchone()
    if used_requests or used_providers:
        con.execute("UPDATE service_categories SET status='Disabled' WHERE id=?",(cid,))
        message="Category purane orders ya provider profiles mein use ho rahi hai, is liye permanently delete ke bajaye website se remove kar di. Existing records safe hain."
    else:
        con.execute("DELETE FROM service_categories WHERE id=?",(cid,))
        message="Category permanently delete ho gayi."
    con.commit()
    con.close()
    flash(message)
    return redirect("/admin/manage")


@app.route("/approve/<int:uid>", methods=["POST"])
@login_required
def approve(uid):
    con=db()
    admin=con.execute("SELECT role FROM users WHERE id=?",(session["uid"],)).fetchone()
    if not admin or admin["role"]!="admin":
        con.close()
        abort(403)
    con.execute("UPDATE users SET approved=1 WHERE id=? AND role='provider'",(uid,))
    con.commit()
    con.close()
    flash("Provider approved.")
    return redirect("/dashboard")

@app.route("/reject/<int:uid>", methods=["POST"])
@login_required
def reject(uid):
    con=db()
    admin=con.execute("SELECT role FROM users WHERE id=?",(session["uid"],)).fetchone()
    if not admin or admin["role"]!="admin":
        con.close()
        abort(403)
    con.execute("DELETE FROM users WHERE id=? AND role='provider' AND approved=0",(uid,))
    con.commit()
    con.close()
    flash("Pending provider request rejected.")
    return redirect("/dashboard")


# Premium Super Admin modules

def admin_log(action, details=''):
    con=db(); con.execute("INSERT INTO admin_audit_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)",(session.get('uid'),action,details,datetime.now().isoformat(timespec='seconds'))); con.commit(); con.close()

def admin_page(title, content, **context):
    shell='''<div class="dashboard-shell"><aside class="dash-side"><div class="dash-logo">✦ SabKaam</div><p class="muted">Premium Super Admin</p><div class="dash-nav"><a href="/dashboard">▦ Overview</a><a href="/admin/manage">⚙ Management</a><a href="/admin/orders">▤ Orders</a><a href="/admin/finance">💰 Finance</a><a href="/admin/funds">💵 Fund Management</a><a href="/admin/payment-ledger">💳 Payment Ledger (Sandbox)</a><a href="/admin/disputes">⚖ Disputes</a><a href="/admin/notifications">🔔 Notifications</a><a href="/admin/analytics">📊 Analytics</a><a href="/admin/website">🌐 Website</a><a href="/admin/security">🛡 Security</a><a href="/logout">↪ Logout</a></div></aside><section class="dash-main"><div class="dash-top"><div><h1>'''+title+'''</h1><p>SabKaam Super Admin Control Center</p></div><span class="pill">● Secure Admin</span></div>'''+content+'''</section></div>'''
    return page(shell, **context)

@app.route('/admin/orders')
@login_required
def admin_orders():
    admin_required(); con=db(); rows=con.execute('''SELECT b.*,r.title,r.category,r.city,c.name customer_name,p.name provider_name FROM marketplace_bookings b JOIN job_requests r ON r.id=b.request_id JOIN users c ON c.id=b.customer_id JOIN users p ON p.id=b.provider_id ORDER BY b.id DESC''').fetchall(); con.close()
    html='''<div class="dash-section"><h2>All bookings & orders</h2>{% for b in rows %}<div class="offer"><b>#{{b.id}} · {{b.title}}</b> <span class="pill">{{b.status}}</span><p>{{b.category}} · {{b.city}} · Customer: {{b.customer_name}} · Provider: {{b.provider_name}}</p><p><b>Price Rs. {{'%.0f'|format(b.amount)}}</b> · Commission Rs. {{'%.0f'|format(b.commission)}} · Customer confirmed: {{'Yes' if b.customer_confirmed else 'No'}}</p><form method="post" action="/admin/order/{{b.id}}/status" class="inline"><select name="status"><option>Accepted</option><option>In Progress</option><option>Completed</option><option>Cancelled</option><option>Disputed</option></select><button class="small">Update status</button></form> <a class="btn small" href="/inbox/{{b.id}}">Open chat</a></div>{% else %}<p class="empty-state">No bookings yet.</p>{% endfor %}</div>'''
    return admin_page('Orders Management',html,rows=rows)

@app.route('/admin/order/<int:bid>/status',methods=['POST'])
@login_required
def admin_order_status(bid):
    admin_required(); status=request.form.get('status','')
    if status not in ('Accepted','In Progress','Completed','Cancelled','Disputed'): abort(400)
    con=db(); b=con.execute('SELECT customer_id,provider_id FROM marketplace_bookings WHERE id=?',(bid,)).fetchone()
    if not b: con.close(); abort(404)
    con.execute('UPDATE marketplace_bookings SET status=? WHERE id=?',(status,bid))
    now=datetime.now().isoformat(timespec='minutes')
    for uid in (b['customer_id'],b['provider_id']): con.execute('INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)',(uid,'Booking status updated',f'Admin ne booking #{bid} ka status {status} kar diya hai.',f'/inbox/{bid}',now))
    con.commit(); con.close(); admin_log('Booking status changed',f'Booking #{bid}: {status}'); flash('Booking status updated.'); return redirect('/admin/orders')

@app.route('/admin/finance',methods=['GET','POST'])
@login_required
def admin_finance():
    admin_required(); con=db()
    if request.method=='POST':
        try: rate=float(request.form.get('commission_rate','5'))
        except ValueError: rate=-1
        if not 0<=rate<=50: flash('Commission 0 se 50 percent ke darmiyan rakhein.')
        else: con.execute("INSERT INTO admin_settings(setting_key,setting_value) VALUES('commission_rate',?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",(str(rate),)); con.commit(); admin_log('Commission rate updated',f'{rate}%'); flash('Commission rate update ho gaya.')
    rate=float(con.execute("SELECT setting_value FROM admin_settings WHERE setting_key='commission_rate'").fetchone()['setting_value'])
    due=con.execute("SELECT COALESCE(SUM(commission),0) FROM marketplace_bookings WHERE status='Completed' AND customer_confirmed=1").fetchone()[0]
    paid=con.execute("SELECT COALESCE(SUM(amount),0) FROM commission_payments WHERE status='Paid'").fetchone()[0]
    payments=con.execute('SELECT cp.*,u.name provider_name FROM commission_payments cp JOIN users u ON u.id=cp.provider_id ORDER BY cp.id DESC').fetchall()
    eligible=con.execute("SELECT b.id,b.commission,p.name provider_name FROM marketplace_bookings b JOIN users p ON p.id=b.provider_id WHERE b.status='Completed' AND b.customer_confirmed=1 ORDER BY b.id DESC").fetchall(); con.close()
    html='''<div class="dash-stats"><div class="dash-stat"><strong>Rs. {{'%.0f'|format(due)}}</strong><span>Confirmed commission</span></div><div class="dash-stat"><strong>Rs. {{'%.0f'|format(paid)}}</strong><span>Payments marked paid</span></div><div class="dash-stat"><strong>Rs. {{'%.0f'|format(due-paid if due>paid else 0)}}</strong><span>Estimated outstanding</span></div></div><div class="dash-section"><h2>Commission settings</h2><p><b>Current payment setup:</b> JazzCash preference · 5% commission · Platform bears gateway fee · Payout after customer confirmation. Live mode disabled.</p><a class="btn small" href="/admin/payment-ledger">Open Sandbox Payment Ledger →</a><form method="post"><label>Commission percentage (0–50%)</label><input type="number" name="commission_rate" min="0" max="50" step="0.5" value="{{rate}}" required><button>Save commission rate</button></form><p class="muted">New bookings par naya rate apply hoga; purani bookings ka rate change nahi hoga.</p></div><div class="dash-section"><h2>Record provider payment</h2><form method="post" action="/admin/payment/add"><label>Eligible completed booking</label><select name="booking_id" required>{% for b in eligible %}<option value="{{b.id}}">#{{b.id}} · {{b.provider_name}} · Rs. {{b.commission}}</option>{% endfor %}</select><label>Amount (PKR)</label><input name="amount" type="number" min="1" step="0.01" required><label>Method</label><select name="method"><option>JazzCash</option><option>EasyPaisa</option><option>Bank Transfer</option><option>Cash</option><option>Other</option></select><label>Transaction reference</label><input name="reference" maxlength="100"><button>Record as pending</button></form></div><div class="dash-section"><h2>Payment history</h2>{% for x in payments %}<div class="offer"><b>{{x.provider_name}}</b> · Rs. {{x.amount}} · {{x.method or '—'}} · {{x.status}} · {{x.reference or '—'}} {% if x.status!='Paid' %}<form class="inline" method="post" action="/admin/payment/{{x.id}}/paid"><button class="small">Mark paid</button></form>{% endif %}</div>{% else %}<p>No payments yet.</p>{% endfor %}</div>'''
    return admin_page('Commission & Payments',html,rate=rate,due=due,paid=paid,payments=payments,eligible=eligible)

@app.route('/admin/payment/add',methods=['POST'])
@login_required
def admin_payment_add():
    admin_required(); bid=request.form.get('booking_id',type=int); amount=request.form.get('amount',type=float)
    if not bid or not amount or amount<=0: flash('Valid booking aur amount enter karein.'); return redirect('/admin/finance')
    con=db(); b=con.execute("SELECT provider_id,commission,status,customer_confirmed FROM marketplace_bookings WHERE id=?",(bid,)).fetchone()
    already_paid=con.execute("SELECT COALESCE(SUM(amount),0) FROM commission_payments WHERE booking_id=?",(bid,)).fetchone()[0]
    if not b or b['status']!='Completed' or not b['customer_confirmed'] or amount+already_paid>b['commission']: con.close(); flash('Payment sirf confirmed completed booking ki commission se record karein.'); return redirect('/admin/finance')
    con.execute('INSERT INTO commission_payments(booking_id,provider_id,amount,method,reference,status,created_at) VALUES(?,?,?,?,?,?,?)',(bid,b['provider_id'],amount,request.form.get('method'),request.form.get('reference','').strip(),'Pending',datetime.now().isoformat(timespec='minutes'))); con.commit(); con.close(); admin_log('Commission payment recorded',f'Booking #{bid}, Rs {amount}'); flash('Payment pending record ho gayi.'); return redirect('/admin/finance')

@app.route('/admin/payment/<int:pid>/paid',methods=['POST'])
@login_required
def admin_payment_paid(pid):
    admin_required(); con=db(); con.execute("UPDATE commission_payments SET status='Paid',paid_at=? WHERE id=?",(datetime.now().isoformat(timespec='minutes'),pid)); con.commit(); con.close(); admin_log('Commission payment marked paid',f'Payment #{pid}'); flash('Payment paid mark ho gayi.'); return redirect('/admin/finance')

@app.route('/checkout/<int:bid>')
@login_required
def payment_checkout(bid):
    u=current_user(); con=db()
    b=con.execute("SELECT b.*,r.title,p.name provider_name FROM marketplace_bookings b JOIN job_requests r ON r.id=b.request_id JOIN users p ON p.id=b.provider_id WHERE b.id=?",(bid,)).fetchone()
    led=con.execute("SELECT * FROM payment_ledger WHERE booking_id=?",(bid,)).fetchone(); con.close()
    if not b or b['customer_id']!=u['id'] or not led: abort(403)
    html='''<div class="dash-section"><span class="pill">SANDBOX · REAL PAYMENTS DISABLED</span><h2>JazzCash test checkout</h2><p>Booking #{{b.id}} · {{b.title}} · Provider {{b.provider_name}}</p><div class="dash-stats"><div class="dash-stat"><strong>Rs. {{'%.2f'|format(led.gross_amount)}}</strong><span>Customer total</span></div><div class="dash-stat"><strong>Rs. {{'%.2f'|format(led.commission_amount)}}</strong><span>SabKaam 5% commission</span></div><div class="dash-stat"><strong>Rs. {{'%.2f'|format(led.provider_net)}}</strong><span>Provider share</span></div></div><p>Gateway: JazzCash · Mode: Sandbox · Gateway fee: platform bears it (test estimate Rs. 0)</p><p><b>Status:</b> {{led.payment_status}}</p>{% if led.payment_status=='Pending' %}<form method="post" action="/sandbox/checkout/{{b.id}}"><button name="result" value="success">Simulate successful test payment</button> <button class="alt" name="result" value="fail">Simulate failed test payment</button></form>{% else %}<p>Test payment already processed. No real money was charged.</p>{% endif %}<p class="muted">This is a local simulation only. It does not connect to JazzCash or collect money.</p></div>'''
    return page(html,b=b,led=led)

@app.route('/sandbox/checkout/<int:bid>',methods=['POST'])
@login_required
def sandbox_checkout(bid):
    u=current_user(); result=request.form.get('result'); con=db()
    led=con.execute("SELECT * FROM payment_ledger WHERE booking_id=? AND customer_id=?",(bid,u['id'])).fetchone()
    if not led: con.close(); abort(403)
    now=datetime.now().isoformat(timespec='minutes')
    if led['payment_status']!='Pending': con.close(); flash('Payment pehle hi process ho chuki hai.'); return redirect(f'/checkout/{bid}')
    if result=='success':
        ref='JAZZ-SANDBOX-'+secrets.token_hex(5).upper()
        con.execute("UPDATE payment_ledger SET payment_status='Paid (Sandbox)',gateway_reference=?,paid_at=? WHERE booking_id=?",(ref,now,bid))
        con.execute("UPDATE marketplace_bookings SET payment_status='Paid (Sandbox)' WHERE id=?",(bid,))
        con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(bid,'SANDBOX_PAYMENT_SUCCESS',f'Test-only reference {ref}; no real charge.',now))
        con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) SELECT provider_id,'Customer payment test successful',?, ?, ? FROM marketplace_bookings WHERE id=?",(f'Booking #{bid} payment sandbox mein successful simulate hui. Real funds collect nahi huay.',f'/inbox/{bid}',now,bid))
        flash('Sandbox payment successful simulate ho gayi. Real JazzCash payment nahi hui.')
    else:
        con.execute("INSERT INTO payment_events(booking_id,event_type,details,created_at) VALUES(?,?,?,?)",(bid,'SANDBOX_PAYMENT_FAILED','Simulated failed test; no charge.',now))
        flash('Sandbox payment failure simulate hui. Koi paisa charge nahi hua.')
    con.commit(); con.close(); return redirect(f'/checkout/{bid}')

@app.route('/admin/payment-ledger')
@login_required
def admin_payment_ledger():
    admin_required(); con=db()
    rows=con.execute("SELECT l.*,c.name customer_name,p.name provider_name FROM payment_ledger l JOIN users c ON c.id=l.customer_id JOIN users p ON p.id=l.provider_id ORDER BY l.id DESC").fetchall()
    payouts=con.execute("SELECT pp.*,u.name provider_name FROM provider_payouts pp JOIN users u ON u.id=pp.provider_id ORDER BY pp.id DESC").fetchall()
    events=con.execute("SELECT * FROM payment_events ORDER BY id DESC LIMIT 40").fetchall()
    con.close()
    html='''<div class="dash-section"><span class="pill">SANDBOX ONLY · LIVE DISABLED</span><h2>Payment ledger</h2><p>Manual wallet instructions configured. Deposits require admin verification; payouts are manual. Live gateway integration remains disabled.</p>{% for x in rows %}<div class="offer"><b>Booking #{{x.booking_id}}</b> · {{x.customer_name}} → {{x.provider_name}}<p>Gross Rs. {{'%.2f'|format(x.gross_amount)}} · Commission {{x.commission_rate}}% = Rs. {{'%.2f'|format(x.commission_amount)}} · Provider net Rs. {{'%.2f'|format(x.provider_net)}}</p><p>{{x.gateway}} / {{x.mode}} · {{x.payment_status}} · Ref: {{x.gateway_reference or '—'}}</p></div>{% else %}<p>No ledger entries.</p>{% endfor %}</div><div class="dash-section"><h2>Provider payouts</h2>{% for p in payouts %}<div class="offer">Booking #{{p.booking_id}} · {{p.provider_name}} · Rs. {{'%.2f'|format(p.amount)}} · {{p.status}}<p class="muted">Sandbox ledger only; no real transfer.</p></div>{% else %}<p>No payouts ready yet.</p>{% endfor %}</div><div class="dash-section"><h2>Recent payment events</h2>{% for e in events %}<div class="offer"><b>{{e.event_type}}</b> · Booking #{{e.booking_id}}<p>{{e.details}}</p><span class="muted">{{e.created_at}}</span></div>{% else %}<p>No events.</p>{% endfor %}</div>'''
    return admin_page('Payment Ledger (Sandbox)',html,rows=rows,payouts=payouts,events=events)

@app.route('/admin/payment-live',methods=['POST'])
@login_required
def admin_payment_live():
    admin_required()
    # Intentionally impossible to enable from this app until provider's written marketplace approval and secure webhook integration.
    con=db(); con.execute("INSERT INTO admin_settings(setting_key,setting_value) VALUES('jazzcash_live_enabled','0') ON CONFLICT(setting_key) DO UPDATE SET setting_value='0'"); con.commit(); con.close()
    admin_log('Live payments remain disabled','JazzCash marketplace payout approval and verified server webhook are not configured.')
    flash('Live payments disabled rahengi jab tak JazzCash ki written marketplace/payout approval aur verified server integration complete na ho.')
    return redirect('/admin/payment-ledger')

@app.route('/report-dispute/<int:bid>',methods=['GET','POST'])
@login_required
def report_dispute(bid):
    u=current_user(); con=db(); b=con.execute('SELECT * FROM marketplace_bookings WHERE id=?',(bid,)).fetchone()
    if not b or u['id'] not in (b['customer_id'],b['provider_id']): con.close(); abort(403)
    if request.method=='POST':
        subject=request.form.get('subject','').strip(); details=request.form.get('details','').strip()
        if len(subject)<4 or len(details)<10: flash('Subject aur details mukammal likhein.')
        else:
            now=datetime.now().isoformat(timespec='minutes'); con.execute('INSERT INTO disputes(booking_id,reporter_id,subject,details,created_at,updated_at) VALUES(?,?,?,?,?,?)',(bid,u['id'],subject,details,now,now))
            con.execute("INSERT INTO notifications(user_id,title,body,link,created_at) SELECT id,?,?,?,? FROM users WHERE role='admin'",('New complaint / dispute',f'Booking #{bid}: {subject}','/admin/disputes',now)); con.commit(); con.close(); flash('Complaint admin ko send kar di gayi.'); return redirect('/dashboard')
    con.close(); return page('''<div class="card"><h2>Report a problem</h2><p>Booking #{{bid}}</p><form method="post"><label>Complaint subject</label><input name="subject" required maxlength="120"><label>Details</label><textarea name="details" required minlength="8"></textarea><button>Submit complaint</button></form></div>''',bid=bid)

@app.route('/admin/disputes')
@login_required
def admin_disputes():
    admin_required(); con=db(); rows=con.execute('''SELECT d.*,u.name reporter_name,b.amount FROM disputes d JOIN users u ON u.id=d.reporter_id JOIN marketplace_bookings b ON b.id=d.booking_id ORDER BY CASE d.status WHEN 'Open' THEN 0 ELSE 1 END,d.id DESC''').fetchall(); con.close()
    html='''<div class="dash-section"><h2>Complaints & disputes</h2>{% for d in rows %}<div class="offer"><b>#{{d.id}} · {{d.subject}}</b> <span class="pill">{{d.status}}</span><p>Booking #{{d.booking_id}} · {{d.reporter_name}} · Rs. {{d.amount}}</p><p>{{d.details}}</p><p class="muted">Admin note: {{d.admin_note or 'No note'}}</p><form method="post" action="/admin/dispute/{{d.id}}/update"><select name="status"><option>Open</option><option>Under Review</option><option>Resolved</option><option>Rejected</option></select><input name="admin_note" placeholder="Admin resolution note"><button class="small">Save resolution</button></form></div>{% else %}<p>No complaints received.</p>{% endfor %}</div>'''
    return admin_page('Complaints & Disputes',html,rows=rows)

@app.route('/admin/dispute/<int:did>/update',methods=['POST'])
@login_required
def admin_dispute_update(did):
    admin_required(); status=request.form.get('status')
    if status not in ('Open','Under Review','Resolved','Rejected'): abort(400)
    con=db(); d=con.execute('SELECT reporter_id FROM disputes WHERE id=?',(did,)).fetchone()
    if not d: con.close(); abort(404)
    note=request.form.get('admin_note','').strip(); con.execute('UPDATE disputes SET status=?,admin_note=?,updated_at=? WHERE id=?',(status,note,datetime.now().isoformat(timespec='minutes'),did)); con.execute('INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)',(d['reporter_id'],'Complaint update',f'Complaint #{did}: {status}. {note}','/dashboard',datetime.now().isoformat(timespec='minutes'))); con.commit(); con.close(); admin_log('Dispute updated',f'#{did}: {status}'); flash('Dispute updated.'); return redirect('/admin/disputes')

@app.route('/admin/notifications',methods=['GET','POST'])
@login_required
def admin_notifications():
    admin_required(); con=db()
    if request.method=='POST':
        title=request.form.get('title','').strip(); body=request.form.get('body','').strip(); audience=request.form.get('audience','all')
        if not title or not body: flash('Title aur message required hain.')
        else:
            roles=['customer','provider'] if audience=='all' else [audience]
            if audience not in ('all','customer','provider'): con.close(); abort(400)
            marks=','.join('?' for _ in roles); users=con.execute(f'SELECT id FROM users WHERE role IN ({marks})',roles).fetchall(); now=datetime.now().isoformat(timespec='minutes')
            con.executemany('INSERT INTO notifications(user_id,title,body,link,created_at) VALUES(?,?,?,?,?)',[(x['id'],title,body,'/dashboard',now) for x in users]); con.commit(); admin_log('Broadcast notification',f'{audience}: {title} ({len(users)} recipients)'); flash(f'Notification {len(users)} users ko send kar di.')
    recent=con.execute('SELECT title,body,created_at,COUNT(*) recipients FROM notifications GROUP BY title,body,created_at ORDER BY created_at DESC LIMIT 30').fetchall(); con.close()
    html='''<div class="dash-section"><h2>Send announcement</h2><form method="post"><label>Audience</label><select name="audience"><option value="all">All customers & providers</option><option value="customer">Customers only</option><option value="provider">Providers only</option></select><label>Title</label><input name="title" maxlength="100" required><label>Message</label><textarea name="body" maxlength="1000" required></textarea><button>Send notification</button></form></div><div class="dash-section"><h2>Recent notifications</h2>{% for n in recent %}<div class="offer"><b>{{n.title}}</b><p>{{n.body}}</p><span class="muted">{{n.created_at}} · {{n.recipients}} recipient records</span></div>{% else %}<p>No notifications yet.</p>{% endfor %}</div>'''
    return admin_page('Notifications Center',html,recent=recent)

@app.route('/admin/analytics')
@login_required
def admin_analytics():
    admin_required(); con=db(); stats={'customers':con.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0],'providers':con.execute("SELECT COUNT(*) FROM users WHERE role='provider'").fetchone()[0],'verified':con.execute("SELECT COUNT(*) FROM provider_profiles WHERE verification_status='Verified'").fetchone()[0],'requests':con.execute('SELECT COUNT(*) FROM job_requests').fetchone()[0],'offers':con.execute('SELECT COUNT(*) FROM offers').fetchone()[0],'bookings':con.execute('SELECT COUNT(*) FROM marketplace_bookings').fetchone()[0],'completed':con.execute("SELECT COUNT(*) FROM marketplace_bookings WHERE status='Completed' AND customer_confirmed=1").fetchone()[0],'commission':con.execute("SELECT COALESCE(SUM(commission),0) FROM marketplace_bookings WHERE status='Completed' AND customer_confirmed=1").fetchone()[0]}; popular=con.execute('SELECT category,COUNT(*) total FROM job_requests GROUP BY category ORDER BY total DESC LIMIT 10').fetchall(); cities=con.execute('SELECT city,COUNT(*) total FROM job_requests GROUP BY city ORDER BY total DESC LIMIT 10').fetchall(); con.close()
    html='''<div class="dash-stats">{% for label,value in stats.items() %}<div class="dash-stat"><strong>{{value if label!='commission' else 'Rs. %.0f'|format(value)}}</strong><span>{{label|title}}</span></div>{% endfor %}</div><div class="grid"><div class="dash-section"><h2>Popular services</h2>{% for x in popular %}<div class="offer">{{x.category}} <span class="pill">{{x.total}} requests</span></div>{% else %}<p>No data yet.</p>{% endfor %}</div><div class="dash-section"><h2>Top cities</h2>{% for x in cities %}<div class="offer">{{x.city}} <span class="pill">{{x.total}} requests</span></div>{% else %}<p>No data yet.</p>{% endfor %}</div></div><div class="dash-section"><a class="btn" href="/admin/analytics.csv">Download CSV report</a></div>'''
    return admin_page('Analytics & Reports',html,stats=stats,popular=popular,cities=cities)

@app.route('/admin/analytics.csv')
@login_required
def admin_analytics_csv():
    admin_required(); con=db(); rows=con.execute('SELECT id,customer_id,title,category,city,status,created_at FROM job_requests ORDER BY id DESC').fetchall(); con.close(); import csv,io
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(['Request ID','Customer ID','Title','Category','City','Status','Created']); writer.writerows([tuple(r) for r in rows]); admin_log('Analytics CSV exported','Job requests CSV')
    return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=sabkaam_requests_report.csv'})

@app.route('/admin/website',methods=['GET','POST'])
@login_required
def admin_website():
    admin_required(); con=db()
    if request.method=='POST':
        for key in ('site_name','tagline','support_email','support_phone'):
            val=request.form.get(key,'').strip()[:180]; con.execute('INSERT INTO website_content(content_key,content_value) VALUES(?,?) ON CONFLICT(content_key) DO UPDATE SET content_value=excluded.content_value',(key,val))
        con.commit(); admin_log('Website content updated','Website identity and support settings'); flash('Website settings save ho gayi.')
    values={r['content_key']:r['content_value'] for r in con.execute('SELECT content_key,content_value FROM website_content').fetchall()}; con.close()
    html='''<div class="dash-section"><h2>Website identity & contact</h2><form method="post"><label>Website name</label><input name="site_name" value="{{v.get('site_name','SabKaam')}}"><label>Tagline</label><input name="tagline" value="{{v.get('tagline','')}}"><label>Support email</label><input type="email" name="support_email" value="{{v.get('support_email','')}}"><label>Support phone</label><input name="support_phone" value="{{v.get('support_phone','')}}"><button>Save website settings</button></form><p class="muted">Settings database mein save hoti hain. Homepage par apply karne ke liye template integration alag se karni hogi.</p></div>'''
    return admin_page('Website Management',html,v=values)

@app.route('/admin/security',methods=['GET','POST'])
@login_required
def admin_security():
    admin_required(); con=db()
    if request.method=='POST':
        old=request.form.get('old_password',''); new=request.form.get('new_password',''); confirm=request.form.get('confirm_password',''); u=con.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone()
        if not check_password_hash(u['password'],old): flash('Current password sahi nahi hai.')
        elif len(new)<8 or new!=confirm: flash('New password kam az kam 8 characters aur confirmation match honi chahiye.')
        else: con.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(new),session['uid'])); con.commit(); admin_log('Admin password changed','Password updated through security center'); flash('Admin password change ho gaya.')
    logs=con.execute('SELECT * FROM admin_audit_logs ORDER BY id DESC LIMIT 50').fetchall(); con.close()
    html='''<div class="dash-section"><h2>Change admin password</h2><form method="post"><label>Current password</label><input type="password" name="old_password" required><label>New password (minimum 8 characters)</label><input type="password" name="new_password" minlength="8" required><label>Confirm new password</label><input type="password" name="confirm_password" minlength="8" required><button>Update password</button></form></div><div class="dash-section"><h2>Database backup</h2><a class="btn" href="/admin/backup">Download database backup</a><p class="muted">Backup private rakhein; ismein user data ho sakta hai.</p></div><div class="dash-section"><h2>Admin activity log</h2>{% for x in logs %}<div class="offer"><b>{{x.action}}</b><p>{{x.details or ''}}</p><span class="muted">{{x.created_at}}</span></div>{% else %}<p>No admin actions logged yet.</p>{% endfor %}</div>'''
    return admin_page('Security & Backups',html,logs=logs)

@app.route('/admin/backup')
@login_required
def admin_backup():
    admin_required(); source=os.path.abspath(DB)
    if not os.path.exists(source): abort(404)
    import tempfile
    fd,path=tempfile.mkstemp(prefix='sabkaam_backup_',suffix='.db'); os.close(fd); src=sqlite3.connect(source); dest=sqlite3.connect(path); src.backup(dest); dest.close(); src.close(); admin_log('Database backup downloaded','SQLite backup')
    return send_file(path,as_attachment=True,download_name='sabkaam_backup_'+datetime.now().strftime('%Y%m%d_%H%M')+'.db')

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
