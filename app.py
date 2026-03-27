from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont
import os, uuid, math
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'change-this-in-production-please'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///adoptables.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
PRESET_STATUSES = ['open', 'pending', 'sold', 'nfs', 'ota', 'tent']
CURRENCIES = ['USD', 'EUR', 'GBP', 'AUD', 'CAD', 'JPY', 'SGD', 'PHP', 'Points', 'Other']

db = SQLAlchemy(app)

# ── Models ────────────────────────────────────────────────────────────────────

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    adoptables = db.relationship('Adoptable', backref='owner', lazy=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    cover_filename = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    adoptables = db.relationship('Adoptable', backref='category', lazy=True)

class Adoptable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.String(80))
    currency = db.Column(db.String(20), default='USD')
    status = db.Column(db.String(40), default='open')
    species = db.Column(db.String(80))
    gender = db.Column(db.String(40))
    tags = db.Column(db.String(255))
    watermark_text = db.Column(db.String(120))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship('AdoptableImage', backref='adoptable', lazy=True,
                             cascade='all, delete-orphan', order_by='AdoptableImage.order')

class AdoptableImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    adoptable_id = db.Column(db.Integer, db.ForeignKey('adoptable.id'), nullable=False)
    orig_filename = db.Column(db.String(255), nullable=False)
    wm_filename = db.Column(db.String(255), nullable=False)
    order = db.Column(db.Integer, default=0)
    is_cover = db.Column(db.Boolean, default=False)

# ── Helpers ───────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def apply_watermark(input_path, output_path, text):
    img = Image.open(input_path).convert('RGBA')
    w, h = img.size
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font_size = max(32, min(w, h) // 7)
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_size)
    except:
        try:
            font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', font_size)
        except:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    diagonal = math.sqrt(w**2 + h**2)
    sx, sy = tw + 80, th + 60
    cols = int(diagonal / sx) + 3
    rows = int(diagonal / sy) + 3
    cx, cy = w / 2, h / 2
    ar = math.radians(-35)
    for row in range(-rows, rows + 1):
        for col in range(-cols, cols + 1):
            ox, oy = col * sx, row * sy
            rx = cx + ox * math.cos(ar) - oy * math.sin(ar)
            ry = cy + ox * math.sin(ar) + oy * math.cos(ar)
            draw.text((rx - tw/2, ry - th/2), text, font=font, fill=(255, 255, 255, 85))
    Image.alpha_composite(img, overlay).convert('RGB').save(output_path, quality=92)

def save_image_file(file, wm_text):
    ext = file.filename.rsplit('.', 1)[1].lower()
    orig_name = f"{uuid.uuid4().hex}.{ext}"
    wm_name   = f"wm_{uuid.uuid4().hex}.jpg"
    orig_path = os.path.join(app.config['UPLOAD_FOLDER'], orig_name)
    wm_path   = os.path.join(app.config['UPLOAD_FOLDER'], wm_name)
    file.save(orig_path)
    apply_watermark(orig_path, wm_path, wm_text)
    return orig_name, wm_name

def save_cover_file(file):
    ext = file.filename.rsplit('.', 1)[1].lower()
    name = f"cover_{uuid.uuid4().hex}.{ext}"
    img = Image.open(file)
    img.save(os.path.join(app.config['UPLOAD_FOLDER'], name))
    return name

def cover_image(adoptable):
    for img in adoptable.images:
        if img.is_cover:
            return img
    return adoptable.images[0] if adoptable.images else None

app.jinja_env.globals['cover_image'] = cover_image

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not email or not username or not password:
            flash('All fields are required.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Username already taken.', 'error')
        else:
            user = User(email=email, username=username,
                        password_hash=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            session['user_id'] = user.id
            session['username'] = user.username
            flash('Welcome! Your account has been created.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Public gallery ────────────────────────────────────────────────────────────

@app.route('/gallery/<username>')
def public_gallery(username):
    user = User.query.filter_by(username=username).first_or_404()
    status_filter   = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')
    query = Adoptable.query.filter_by(user_id=user.id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    if category_filter != 'all':
        query = query.filter_by(category_id=int(category_filter))
    adoptables = query.order_by(Adoptable.created_at.desc()).all()
    categories = Category.query.filter_by(user_id=user.id).all()
    return render_template('public_gallery.html', owner=user, adoptables=adoptables,
                           categories=categories, status_filter=status_filter,
                           category_filter=category_filter)

@app.route('/gallery/<username>/adoptable/<int:aid>')
def public_adoptable(username, aid):
    user = User.query.filter_by(username=username).first_or_404()
    adoptable = Adoptable.query.filter_by(id=aid, user_id=user.id).first_or_404()
    return render_template('public_adoptable.html', owner=user, adoptable=adoptable)

# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    status_filter   = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')
    search          = request.args.get('q', '')
    query = Adoptable.query.filter_by(user_id=user.id)
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    if category_filter != 'all':
        query = query.filter_by(category_id=int(category_filter))
    if search:
        query = query.filter(Adoptable.name.ilike(f'%{search}%'))
    adoptables = query.order_by(Adoptable.created_at.desc()).all()
    categories = Category.query.filter_by(user_id=user.id).all()
    counts = {s: Adoptable.query.filter_by(user_id=user.id, status=s).count()
              for s in PRESET_STATUSES}
    counts['total'] = Adoptable.query.filter_by(user_id=user.id).count()
    gallery_url = url_for('public_gallery', username=user.username, _external=True)
    return render_template('dashboard.html', user=user, adoptables=adoptables,
                           categories=categories, counts=counts,
                           status_filter=status_filter, category_filter=category_filter,
                           search=search, gallery_url=gallery_url)

# ── Adoptable CRUD ────────────────────────────────────────────────────────────

@app.route('/adoptable/new', methods=['GET', 'POST'])
@login_required
def new_adoptable():
    user = User.query.get(session['user_id'])
    categories = Category.query.filter_by(user_id=user.id).all()
    if request.method == 'POST':
        files = request.files.getlist('images')
        valid = [f for f in files if f and f.filename and allowed_file(f.filename)]
        if not valid:
            flash('Please upload at least one valid image.', 'error')
            return render_template('adoptable_form.html', categories=categories,
                                   adoptable=None, preset_statuses=PRESET_STATUSES,
                                   currencies=CURRENCIES)
        wm_text    = request.form.get('watermark_text') or user.username
        status_val = (request.form.get('status_custom') or request.form.get('status', 'open')).strip().lower()
        cat_id     = request.form.get('category_id') or None
        if cat_id: cat_id = int(cat_id)
        adoptable = Adoptable(
            name=request.form.get('name', 'Unnamed'),
            description=request.form.get('description', ''),
            price=request.form.get('price', ''),
            currency=request.form.get('currency', 'USD'),
            status=status_val,
            species=request.form.get('species', ''),
            gender=request.form.get('gender', ''),
            tags=request.form.get('tags', ''),
            watermark_text=wm_text,
            user_id=user.id,
            category_id=cat_id,
        )
        db.session.add(adoptable)
        db.session.flush()
        for i, file in enumerate(valid):
            o, w = save_image_file(file, wm_text)
            db.session.add(AdoptableImage(adoptable_id=adoptable.id, orig_filename=o,
                                          wm_filename=w, order=i, is_cover=(i == 0)))
        db.session.commit()
        flash('Adoptable added!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('adoptable_form.html', categories=categories, adoptable=None,
                           preset_statuses=PRESET_STATUSES, currencies=CURRENCIES)

@app.route('/adoptable/<int:aid>')
@login_required
def view_adoptable(aid):
    adoptable = Adoptable.query.filter_by(id=aid, user_id=session['user_id']).first_or_404()
    gallery_url = url_for('public_adoptable', username=adoptable.owner.username,
                          aid=aid, _external=True)
    return render_template('adoptable_detail.html', adoptable=adoptable, gallery_url=gallery_url)

@app.route('/adoptable/<int:aid>/edit', methods=['GET', 'POST'])
@login_required
def edit_adoptable(aid):
    adoptable = Adoptable.query.filter_by(id=aid, user_id=session['user_id']).first_or_404()
    user = User.query.get(session['user_id'])
    categories = Category.query.filter_by(user_id=user.id).all()
    if request.method == 'POST':
        adoptable.name        = request.form.get('name', adoptable.name)
        adoptable.description = request.form.get('description', '')
        adoptable.price       = request.form.get('price', '')
        adoptable.currency    = request.form.get('currency', 'USD')
        status_val = (request.form.get('status_custom') or request.form.get('status', 'open')).strip().lower()
        adoptable.status      = status_val
        adoptable.species     = request.form.get('species', '')
        adoptable.gender      = request.form.get('gender', '')
        adoptable.tags        = request.form.get('tags', '')
        cat_id = request.form.get('category_id') or None
        adoptable.category_id = int(cat_id) if cat_id else None
        wm_text = request.form.get('watermark_text') or user.username
        adoptable.watermark_text = wm_text

        # New images
        files = request.files.getlist('images')
        valid = [f for f in files if f and f.filename and allowed_file(f.filename)]
        next_order = max((img.order for img in adoptable.images), default=-1) + 1
        for i, file in enumerate(valid):
            o, w = save_image_file(file, wm_text)
            db.session.add(AdoptableImage(adoptable_id=adoptable.id, orig_filename=o,
                                          wm_filename=w, order=next_order + i,
                                          is_cover=(not adoptable.images and i == 0)))

        # Delete selected images
        for did in request.form.getlist('delete_image'):
            img = AdoptableImage.query.filter_by(id=int(did), adoptable_id=adoptable.id).first()
            if img: db.session.delete(img)

        # Set cover
        cover_id = request.form.get('cover_image')
        if cover_id:
            for img in adoptable.images:
                img.is_cover = (str(img.id) == cover_id)

        db.session.commit()
        flash('Adoptable updated!', 'success')
        return redirect(url_for('view_adoptable', aid=adoptable.id))
    return render_template('adoptable_form.html', categories=categories, adoptable=adoptable,
                           preset_statuses=PRESET_STATUSES, currencies=CURRENCIES)

@app.route('/adoptable/<int:aid>/delete', methods=['POST'])
@login_required
def delete_adoptable(aid):
    adoptable = Adoptable.query.filter_by(id=aid, user_id=session['user_id']).first_or_404()
    db.session.delete(adoptable)
    db.session.commit()
    flash('Adoptable deleted.', 'success')
    return redirect(url_for('dashboard'))

# ── Categories ────────────────────────────────────────────────────────────────

@app.route('/categories')
@login_required
def categories():
    cats = Category.query.filter_by(user_id=session['user_id']).all()
    return render_template('categories.html', categories=cats)

@app.route('/categories/new', methods=['POST'])
@login_required
def new_category():
    name = request.form.get('name', '').strip()
    if name:
        cover_file = request.files.get('cover')
        cover_name = None
        if cover_file and cover_file.filename and allowed_file(cover_file.filename):
            cover_name = save_cover_file(cover_file)
        db.session.add(Category(name=name, user_id=session['user_id'], cover_filename=cover_name))
        db.session.commit()
        flash(f'Category "{name}" created.', 'success')
    return redirect(url_for('categories'))

@app.route('/categories/<int:cid>/edit', methods=['POST'])
@login_required
def edit_category(cid):
    cat = Category.query.filter_by(id=cid, user_id=session['user_id']).first_or_404()
    name = request.form.get('name', '').strip()
    if name: cat.name = name
    cover_file = request.files.get('cover')
    if cover_file and cover_file.filename and allowed_file(cover_file.filename):
        cat.cover_filename = save_cover_file(cover_file)
    db.session.commit()
    flash('Category updated.', 'success')
    return redirect(url_for('categories'))

@app.route('/categories/<int:cid>/delete', methods=['POST'])
@login_required
def delete_category(cid):
    cat = Category.query.filter_by(id=cid, user_id=session['user_id']).first_or_404()
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('categories'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    app.run(debug=True, host='0.0.0.0', port=10000)
