
from flask import Flask, request, redirect, session, render_template_string, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "CHANGE-THIS-TO-A-LONG-RANDOM-SECRET"

DB = "sabkaam.db"
ADMIN_EMAIL = "admin@sabkaam.pk"
ADMIN_PASSWORD = "ChangeMe123!"

CATEGORIES = [
    "Plumber", "Electrician", "Carpenter", "AC Technician",
    "Delivery Boy", "Ride Service", "Cleaning",
    "Graphic Design", "Website Development", "Software Development",
    "Data Entry", "Excel Services", "Home Tutor",
    "Accountant", "Painter", "Construction", "Other"
]

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
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

    CREATE TABLE IF NOT EXISTS services(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        city TEXT NOT NULL,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS bookings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        provider_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        commission REAL NOT NULL,
        status TEXT DEFAULT 'Pending',
        customer_confirmed INTEGER DEFAULT 0
    );
    """)
    admin = con.execute(
        "SELECT id FROM users WHERE email=?", (ADMIN_EMAIL,)
    ).fetchone()
    if not admin:
        con.execute(
            """INSERT INTO users(name,email,password,role,approved)
               VALUES(?,?,?,?,1)""",
            ("SabKaam Admin", ADMIN_EMAIL,
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

def page(body, **data):
    user = None
    if session.get("uid"):
        con = db()
        user = con.execute(
            "SELECT * FROM users WHERE id=?", (session["uid"],)
        ).fetchone()
        con.close()
    return render_template_string("""
    <!doctype html>
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SabKaam - All Services</title>
    <style>
    body{font-family:Arial;background:#f3f5f9;margin:0;color:#202a3a}
    header{background:#102c50;color:white;padding:18px}
    header a{color:white;margin-right:15px;text-decoration:none}
    main{max-width:950px;margin:25px auto;padding:15px}
    .card{background:white;padding:20px;margin:12px 0;border-radius:12px;
    box-shadow:0 2px 10px #0001}
    input,select,textarea{width:100%;padding:11px;margin:7px 0;
    box-sizing:border-box;border:1px solid #ccc;border-radius:6px}
    button,.btn{background:#1266d5;color:white;border:0;padding:11px 17px;
    border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}
    .muted{color:#667085}.green{color:green}.red{color:#c33}
    </style></head>
    <body><header><h2>🚀 SabKaam</h2>
    <a href="/">Home</a><a href="/services">Services</a>
    {% if user %}
    <a href="/dashboard">Dashboard</a><a href="/logout">Logout</a>
    {% else %}
    <a href="/login">Login</a><a href="/register">Register</a>
    {% endif %}
    </header><main>
    {% with messages=get_flashed_messages() %}
    {% for m in messages %}<div class="card">{{m}}</div>{% endfor %}
    {% endwith %}
    """ + body + "</main></body></html>", user=user, **data)

@app.route("/")
def home():
    return page("""
    <div class="card">
    <h1>Har Service, Ek Hi Jagah!</h1>
    <p>Pakistan mein apne shehar ki services dhoondein.</p>
    <a class="btn" href="/services">Find Services</a>
    <a class="btn" href="/register">Join as Provider</a>
    </div>
    <div class="card"><h3>Available Categories</h3>
    """ + "".join(f"<p>• {c}</p>" for c in CATEGORIES) + "</div>")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        role=request.form.get("role","customer")
        phone=request.form.get("phone","").strip()
        city=request.form.get("city","").strip()

        if not name or not email or len(password)<8 or not city:
            flash("Name, email, city aur 8-character password zaroori hain.")
        elif role not in ("customer","provider"):
            flash("Invalid account type.")
        else:
            con=db()
            try:
                con.execute("""INSERT INTO users
                (name,email,password,phone,city,role,approved)
                VALUES(?,?,?,?,?,?,?)""",
                (name,email,generate_password_hash(password),
                 phone,city,role,0 if role=="provider" else 1))
                con.commit()
                flash("Account created. Provider account admin approval ke baad active hoga.")
                return redirect("/login")
            except sqlite3.IntegrityError:
                flash("Ye email pehle se registered hai.")
            finally:
                con.close()
    return page("""
    <div class="card"><h2>Create Account</h2>
    <form method="post">
    <input name="name" placeholder="Full name" required>
    <input name="email" type="email" placeholder="Email" required>
    <input name="phone" placeholder="Phone number">
    <input name="city" placeholder="City e.g. Lahore" required>
    <input name="password" type="password" minlength="8" placeholder="Password" required>
    <select name="role">
    <option value="customer">Customer</option>
    <option value="provider">Service Provider</option>
    </select><button>Create Account</button>
    </form></div>
    """)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        con=db()
        u=con.execute("SELECT * FROM users WHERE email=?",
        (request.form.get("email","").lower(),)).fetchone()
        con.close()
        if u and check_password_hash(u["password"],request.form.get("password","")):
            if u["role"]=="provider" and not u["approved"]:
                flash("Your provider account is awaiting admin approval.")
                return redirect("/login")
            session["uid"]=u["id"]
            return redirect("/dashboard")
        flash("Incorrect email or password.")
    return page("""
    <div class="card"><h2>Login</h2>
    <form method="post">
    <input name="email" type="email" placeholder="Email" required>
    <input name="password" type="password" placeholder="Password" required>
    <button>Login</button></form></div>
    """)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/dashboard")
@login_required
def dashboard():
    con=db()
    u=con.execute("SELECT * FROM users WHERE id=?",
    (session["uid"],)).fetchone()
    if u["role"]=="admin":
        pending=con.execute("SELECT * FROM users WHERE role='provider' AND approved=0").fetchall()
        bookings=con.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
        earnings=con.execute("""SELECT COALESCE(SUM(commission),0)
        FROM bookings WHERE status='Completed' AND customer_confirmed=1""").fetchone()[0]
        con.close()
        return page("""
        <div class="card"><h2>Super Admin Dashboard</h2>
        <h3>Recorded commission: Rs. {{earnings}}</h3>
        <h3>Provider approval requests</h3>
        {% for p in pending %}
        <p>{{p.name}} | {{p.city}} | {{p.phone}} | {{p.email}}
        <a class="btn" href="/approve/{{p.id}}">Approve</a>
        <a class="btn" href="/reject/{{p.id}}">Reject</a></p>
        {% else %}<p>No pending providers.</p>{% endfor %}
        </div><div class="card"><h3>All bookings</h3>
        {% for b in bookings %}
        <p>Booking #{{b.id}} | Rs. {{b.amount}} |
        Commission Rs. {{b.commission}} | {{b.status}} |
        Customer confirmed: {{b.customer_confirmed}}</p>
        {% endfor %}</div>
        """,pending=pending,bookings=bookings,earnings=earnings)

    if u["role"]=="provider":
        services=con.execute("SELECT * FROM services WHERE provider_id=?",
        (u["id"],)).fetchall()
        orders=con.execute("SELECT * FROM bookings WHERE provider_id=? ORDER BY id DESC",
        (u["id"],)).fetchall()
        con.close()
        return page("""
        <div class="card"><h2>Provider Dashboard</h2>
        <p>Welcome {{u.name}}</p><a class="btn" href="/add-service">Add Service</a>
        <h3>Your services</h3>
        {% for s in services %}<p>{{s.title}} — {{s.city}} — Rs. {{s.price}}</p>{% endfor %}
        <h3>Your bookings</h3>
        {% for b in orders %}
        <p>Booking #{{b.id}} | Rs. {{b.amount}} | {{b.status}}
        {% if b.status=='Pending' %}
        <a class="btn" href="/status/{{b.id}}/Accepted">Accept</a>{% endif %}
        {% if b.status=='Accepted' %}
        <a class="btn" href="/status/{{b.id}}/Completed">Mark completed</a>{% endif %}
        </p>{% endfor %}
        </div>
        """,u=u,services=services,orders=orders)

    orders=con.execute("SELECT * FROM bookings WHERE customer_id=?",
    (u["id"],)).fetchall()
    con.close()
    return page("""
    <div class="card"><h2>Customer Dashboard</h2>
    <p>Welcome {{u.name}}</p><a class="btn" href="/services">Find a service</a>
    <h3>Your bookings</h3>
    {% for b in orders %}
    <p>Booking #{{b.id}} — Rs. {{b.amount}} — {{b.status}}
    {% if b.status=='Completed' and not b.customer_confirmed %}
    <a class="btn" href="/confirm/{{b.id}}">Confirm completion</a>
    {% endif %}</p>{% endfor %}</div>
    """,u=u,orders=orders)

@app.route("/approve/<int:uid>")
@login_required
def approve(uid):
    con=db()
    admin=con.execute("SELECT role FROM users WHERE id=?",(session["uid"],)).fetchone()
    if admin and admin["role"]=="admin":
        con.execute("UPDATE users SET approved=1 WHERE id=? AND role='provider'",(uid,))
        con.commit()
    con.close()
    return redirect("/dashboard")

@app.route("/reject/<int:uid>")
@login_required
def reject(uid):
    con=db()
    admin=con.execute("SELECT role FROM users WHERE id=?",(session["uid"],)).fetchone()
    if admin and admin["role"]=="admin":
        con.execute("DELETE FROM users WHERE id=? AND role='provider' AND approved=0",(uid,))
        con.commit()
    con.close()
    return redirect("/dashboard")

@app.route("/add-service",methods=["GET","POST"])
@login_required
def add_service():
    con=db()
    u=con.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
    if not u or u["role"]!="provider" or not u["approved"]:
        con.close()
        return "Not authorized",403
    if request.method=="POST":
        try:
            price=float(request.form.get("price","0"))
            if price<=0: raise ValueError()
            con.execute("""INSERT INTO services
            (provider_id,title,category,description,price,city)
            VALUES(?,?,?,?,?,?)""",
            (u["id"],request.form["title"],request.form["category"],
             request.form["description"],price,u["city"]))
            con.commit()
            con.close()
            flash("Service published successfully.")
            return redirect("/dashboard")
        except (ValueError,KeyError):
            flash("Please enter valid service details and price.")
    con.close()
    return page("""
    <div class="card"><h2>Add a Service</h2>
    <form method="post">
    <input name="title" placeholder="Service title" required>
    <select name="category">
    {% for c in categories %}<option>{{c}}</option>{% endfor %}
    </select>
    <textarea name="description" placeholder="Service details"></textarea>
    <input name="price" type="number" min="1" step="0.01" placeholder="Price in PKR" required>
    <button>Publish Service</button></form></div>
    """,categories=CATEGORIES)

@app.route("/services")
def services():
    city=request.args.get("city","").strip()
    category=request.args.get("category","").strip()
    con=db()
    sql="""SELECT services.*,users.name AS provider_name
    FROM services JOIN users ON users.id=services.provider_id
    WHERE services.active=1 AND users.approved=1"""
    args=[]
    if city:
        sql+=" AND LOWER(services.city)=LOWER(?)"
        args.append(city)
    if category:
        sql+=" AND services.category=?"
        args.append(category)
    results=con.execute(sql+" ORDER BY services.id DESC",args).fetchall()
    con.close()
    return page("""
    <div class="card"><h2>Find Services</h2>
    <form method="get">
    <input name="city" placeholder="City e.g. Lahore" value="{{city}}">
    <select name="category"><option value="">All categories</option>
    {% for c in categories %}<option value="{{c}}">{{c}}</option>{% endfor %}
    </select><button>Search</button></form></div>
    {% for s in results %}
    <div class="card"><h3>{{s.title}}</h3>
    <p>{{s.category}} | {{s.city}}</p><p>{{s.description}}</p>
    <p>Provider: {{s.provider_name}}</p><b>Rs. {{s.price}}</b>
    {% if user and user.role=='customer' %}
    <form method="post" action="/book/{{s.id}}">
    <button>Book Service</button></form>{% endif %}
    </div>
    {% else %}<p>No services found.</p>{% endfor %}
    """,results=results,city=city,categories=CATEGORIES)

@app.route("/book/<int:sid>",methods=["POST"])
@login_required
def book(sid):
    con=db()
    u=con.execute("SELECT * FROM users WHERE id=?",(session["uid"],)).fetchone()
    s=con.execute("""SELECT services.* FROM services
    JOIN users ON users.id=services.provider_id
    WHERE services.id=? AND services.active=1 AND users.approved=1""",(sid,)).fetchone()
    if not s or u["role"]!="customer" or u["city"].lower()!=s["city"].lower():
        con.close()
        flash("Invalid service or city mismatch.")
        return redirect("/services")
    commission=round(s["price"]*0.10,2)
    con.execute("""INSERT INTO bookings
    (service_id,customer_id,provider_id,amount,commission)
    VALUES(?,?,?,?,?)""",(sid,u["id"],s["provider_id"],s["price"],commission))
    con.commit()
    con.close()
    flash("Booking request submitted.")
    return redirect("/dashboard")

@app.route("/status/<int:bid>/<status>")
@login_required
def status(bid,status):
    if status not in ("Accepted","Completed"):
        return "Invalid status",400
    con=db()
    b=con.execute("SELECT * FROM bookings WHERE id=? AND provider_id=?",
    (bid,session["uid"])).fetchone()
    if b and ((status=="Accepted" and b["status"]=="Pending") or
              (status=="Completed" and b["status"]=="Accepted")):
        con.execute("UPDATE bookings SET status=? WHERE id=?",(status,bid))
        con.commit()
    con.close()
    return redirect("/dashboard")

@app.route("/confirm/<int:bid>")
@login_required
def confirm(bid):
    con=db()
    con.execute("""UPDATE bookings SET customer_confirmed=1
    WHERE id=? AND customer_id=? AND status='Completed'""",
    (bid,session["uid"]))
    con.commit()
    con.close()
    return redirect("/dashboard")

init_db()

if __name__=="__main__":
    app.run(host="127.0.0.1",port=5000,debug=False)
