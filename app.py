"""
AeroScore AI - Live MVP
Flask + SQLite + MediaPipe + OpenCV
All-in-one runnable server
"""
import os, sys, json, hashlib, hmac, secrets, time, math, threading
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file, g

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = BASE_DIR / "static" / "uploads"
DB_PATH     = BASE_DIR / "db" / "aeroscore.db"
SECRET_KEY  = os.environ.get("SECRET_KEY", "aeroscore-live-secret-2024")
MAX_UPLOAD  = 500 * 1024 * 1024  # 500 MB

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "db").mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp

@app.route("/api/<path:p>", methods=["OPTIONS"])
def cors_preflight(p):
    return "", 204

# ── Database ──────────────────────────────────────────────────────────────────
import sqlite3

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'COACH',
            club TEXT,
            country TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            gender TEXT,
            birth_date TEXT,
            club TEXT,
            country TEXT,
            notes TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS routines (
            id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL,
            category TEXT NOT NULL,
            age_group TEXT,
            routine_date TEXT,
            competition_name TEXT,
            video_path TEXT,
            video_url TEXT,
            video_metadata TEXT,
            declared_elements TEXT DEFAULT '[]',
            status TEXT DEFAULT 'PENDING',
            ai_confidence REAL,
            confidence_warnings TEXT DEFAULT '[]',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(player_id) REFERENCES players(id)
        );
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            routine_id TEXT NOT NULL,
            status TEXT DEFAULT 'QUEUED',
            progress INTEGER DEFAULT 0,
            current_step TEXT,
            logs TEXT DEFAULT '[]',
            error_message TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(routine_id) REFERENCES routines(id)
        );
        CREATE TABLE IF NOT EXISTS pose_frames (
            id TEXT PRIMARY KEY,
            routine_id TEXT NOT NULL,
            timestamp REAL,
            frame_index INTEGER,
            confidence REAL,
            features TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS detected_events (
            id TEXT PRIMARY KEY,
            routine_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            start_time REAL,
            end_time REAL,
            confidence REAL,
            severity TEXT,
            evidence TEXT,
            affects_score INTEGER DEFAULT 1,
            source TEXT DEFAULT 'AI_DETECTED',
            accepted INTEGER,
            rejected INTEGER,
            judge_notes TEXT,
            edited_event_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(routine_id) REFERENCES routines(id)
        );
        CREATE TABLE IF NOT EXISTS scores (
            id TEXT PRIMARY KEY,
            routine_id TEXT UNIQUE NOT NULL,
            artistry_score REAL DEFAULT 0,
            execution_score REAL DEFAULT 10,
            difficulty_score REAL DEFAULT 0,
            total_deductions REAL DEFAULT 0,
            final_score REAL DEFAULT 0,
            ai_confidence REAL,
            calculation_details TEXT,
            approved_by TEXT,
            approved_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deductions (
            id TEXT PRIMARY KEY,
            routine_id TEXT NOT NULL,
            deduction_type TEXT,
            amount REAL,
            reason TEXT,
            source TEXT DEFAULT 'JUDGE',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(routine_id) REFERENCES routines(id)
        );
        CREATE TABLE IF NOT EXISTS difficulty_elements (
            id TEXT PRIMARY KEY,
            code TEXT UNIQUE,
            name TEXT,
            grp TEXT,
            family TEXT,
            value REAL,
            description TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            entity_type TEXT,
            entity_id TEXT,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        db.commit()
        _seed_db(db)

def _seed_db(db):
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return
    def pw(p): return hashlib.pbkdf2_hmac("sha256", p.encode(), SECRET_KEY.encode(), 260000).hex()
    users = [
        (_uid(), "Admin User",     "admin@aeroscore.ai",  pw("admin123"),  "ADMIN", "AeroScore HQ",       "USA"),
        (_uid(), "Carlos Mendes",  "coach@aeroscore.ai",  pw("coach123"),  "COACH", "Elite Aerobics",     "Brazil"),
        (_uid(), "Sarah Mitchell", "judge@aeroscore.ai",  pw("judge123"),  "JUDGE", "FIG Committee",      "USA"),
    ]
    for u in users:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", u)

    coach_id = db.execute("SELECT id FROM users WHERE email='coach@aeroscore.ai'").fetchone()[0]
    p1 = _uid(); p2 = _uid()
    db.execute("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
               (p1, "Ana Silva",    "female", "2002-03-15", "Elite Aerobics", "Brazil",   None, coach_id))
    db.execute("INSERT INTO players VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
               (p2, "Marco Torres", "male",   "2000-07-22", "Elite Aerobics", "Brazil",   None, coach_id))

    elements = [
        (_uid(),"J1","Tuck Jump","Jump","jump",0.2,"Jump with tucked knees"),
        (_uid(),"J2","Pike Jump","Jump","jump",0.3,"Jump with horizontal legs"),
        (_uid(),"J3","Straddle Jump","Jump","jump",0.3,"Jump with straddled legs"),
        (_uid(),"J4","Split Leap","Jump","leap",0.5,"Leap with 180° split"),
        (_uid(),"J5","Stag Leap","Jump","leap",0.4,"Leap with bent front leg"),
        (_uid(),"T1","Single Turn","Turn","turn",0.2,"360° turn on one foot"),
        (_uid(),"T2","Double Turn","Turn","turn",0.4,"720° turn on one foot"),
        (_uid(),"T3","Triple Turn","Turn","turn",0.6,"1080° turn on one foot"),
        (_uid(),"B1","Balance","Balance","balance",0.1,"Static balance"),
        (_uid(),"B2","Arabesque","Balance","balance",0.2,"Balance, back leg horizontal"),
        (_uid(),"F1","Split on Floor","Flexibility","split",0.3,"Full split"),
        (_uid(),"F2","Standing Split","Flexibility","split",0.4,"Standing split"),
        (_uid(),"S1","Push-up (1)","Strength","floor_support",0.1,"Single push-up"),
        (_uid(),"S2","Push-up (4)","Strength","floor_support",0.3,"Four push-ups"),
        (_uid(),"S3","Plank Hold","Strength","floor_support",0.2,"Plank hold"),
        (_uid(),"S4","V-Hold","Strength","floor_support",0.4,"V-sit hold"),
    ]
    for el in elements:
        db.execute("INSERT INTO difficulty_elements VALUES (?,?,?,?,?,?,?,1)", el)
    db.commit()
    print("✅ Database seeded with demo data")

def _uid():
    return secrets.token_hex(12)

# ── Auth helpers ──────────────────────────────────────────────────────────────
import jwt as pyjwt

def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), SECRET_KEY.encode(), 260000).hex()

def verify_password(password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), stored_hash)

def make_token(user_id: str, email: str, role: str) -> str:
    payload = {"sub": user_id, "email": email, "role": role,
               "exp": datetime.utcnow() + timedelta(days=7)}
    return pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401
        try:
            payload = pyjwt.decode(auth[7:], SECRET_KEY, algorithms=["HS256"])
            g.user = {"id": payload["sub"], "email": payload["email"], "role": payload["role"]}
        except Exception:
            return jsonify({"error": "Invalid or expired token"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Routes: Auth ──────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register():
    d = request.json or {}
    name, email, password = d.get("name"), d.get("email"), d.get("password")
    if not all([name, email, password]):
        return jsonify({"error": "name, email, password required"}), 400
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        return jsonify({"error": "Email already registered"}), 409
    uid = _uid()
    role = d.get("role", "COACH")
    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
               (uid, name, email, hash_password(password), role, d.get("club"), d.get("country")))
    db.commit()
    token = make_token(uid, email, role)
    return jsonify({"user": {"id": uid, "name": name, "email": email, "role": role}, "token": token}), 201

@app.post("/api/auth/login")
def login():
    d = request.json or {}
    email, password = d.get("email", "").lower(), d.get("password", "")
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401
    token = make_token(row["id"], row["email"], row["role"])
    return jsonify({"user": dict(row), "token": token})

@app.get("/api/auth/me")
@require_auth
def me():
    row = get_db().execute("SELECT id,name,email,role,club,country,created_at FROM users WHERE id=?",
                           (g.user["id"],)).fetchone()
    return jsonify(dict(row)) if row else (jsonify({"error": "Not found"}), 404)

# ── Routes: Players ───────────────────────────────────────────────────────────
@app.get("/api/players")
@require_auth
def get_players():
    rows = get_db().execute(
        "SELECT p.*, COUNT(r.id) as routine_count FROM players p "
        "LEFT JOIN routines r ON r.player_id=p.id "
        "WHERE p.created_by=? GROUP BY p.id ORDER BY p.created_at DESC",
        (g.user["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/players")
@require_auth
def create_player():
    d = request.json or {}
    if not d.get("fullName"):
        return jsonify({"error": "fullName required"}), 400
    uid = _uid()
    get_db().execute(
        "INSERT INTO players VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (uid, d["fullName"], d.get("gender"), d.get("birthDate"),
         d.get("club"), d.get("country"), d.get("notes"), g.user["id"]))
    get_db().commit()
    return jsonify({"id": uid, **d}), 201

@app.get("/api/players/<pid>")
@require_auth
def get_player(pid):
    db = get_db()
    player = db.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
    if not player: return jsonify({"error": "Not found"}), 404
    routines = db.execute(
        "SELECT r.*, s.final_score FROM routines r LEFT JOIN scores s ON s.routine_id=r.id "
        "WHERE r.player_id=? ORDER BY r.created_at DESC", (pid,)).fetchall()
    result = dict(player)
    result["routines"] = [dict(r) for r in routines]
    return jsonify(result)

@app.put("/api/players/<pid>")
@require_auth
def update_player(pid):
    d = request.json or {}
    get_db().execute(
        "UPDATE players SET full_name=COALESCE(?,full_name), gender=COALESCE(?,gender), "
        "club=COALESCE(?,club), country=COALESCE(?,country), notes=COALESCE(?,notes) WHERE id=?",
        (d.get("fullName"), d.get("gender"), d.get("club"), d.get("country"), d.get("notes"), pid))
    get_db().commit()
    return jsonify({"success": True})

# ── Routes: Routines ──────────────────────────────────────────────────────────
@app.get("/api/routines")
@require_auth
def get_routines():
    db = get_db()
    where = "" if g.user["role"] == "ADMIN" else "WHERE r.created_by=?"
    params = () if g.user["role"] == "ADMIN" else (g.user["id"],)
    rows = db.execute(
        f"SELECT r.*, p.full_name as player_name, p.club as player_club, "
        f"s.final_score, s.artistry_score, s.execution_score, s.difficulty_score "
        f"FROM routines r LEFT JOIN players p ON p.id=r.player_id "
        f"LEFT JOIN scores s ON s.routine_id=r.id {where} ORDER BY r.created_at DESC",
        params).fetchall()
    return jsonify({"routines": [dict(r) for r in rows], "total": len(rows)})

@app.post("/api/routines")
@require_auth
def create_routine():
    d = request.json or {}
    if not d.get("playerId") or not d.get("category"):
        return jsonify({"error": "playerId and category required"}), 400
    uid = _uid()
    declared = json.dumps(d.get("declaredElements", []))
    get_db().execute(
        "INSERT INTO routines (id,player_id,category,age_group,routine_date,competition_name,"
        "declared_elements,created_by,status) VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, d["playerId"], d["category"], d.get("ageGroup"), d.get("routineDate"),
         d.get("competitionName"), declared, g.user["id"], "PENDING"))
    get_db().commit()
    return jsonify({"id": uid, **d}), 201

@app.get("/api/routines/<rid>")
@require_auth
def get_routine(rid):
    db = get_db()
    r = db.execute(
        "SELECT r.*, p.full_name as player_name FROM routines r "
        "LEFT JOIN players p ON p.id=r.player_id WHERE r.id=?", (rid,)).fetchone()
    if not r: return jsonify({"error": "Not found"}), 404
    result = dict(r)
    result["score"] = dict(db.execute("SELECT * FROM scores WHERE routine_id=?", (rid,)).fetchone() or {})
    result["events"] = [dict(e) for e in db.execute(
        "SELECT * FROM detected_events WHERE routine_id=? ORDER BY start_time", (rid,)).fetchall()]
    result["deductions"] = [dict(d) for d in db.execute(
        "SELECT * FROM deductions WHERE routine_id=?", (rid,)).fetchall()]
    result["analysisJob"] = dict(db.execute(
        "SELECT * FROM analysis_jobs WHERE routine_id=? ORDER BY created_at DESC LIMIT 1", (rid,)
    ).fetchone() or {})
    return jsonify(result)

@app.post("/api/routines/<rid>/upload")
@require_auth
def upload_video(rid):
    if "video" not in request.files:
        return jsonify({"error": "No video file. Use field name 'video'"}), 400
    f = request.files["video"]
    allowed = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}
    if f.content_type not in allowed and not f.filename.lower().endswith((".mp4",".mov",".webm",".avi")):
        return jsonify({"error": f"Unsupported type: {f.content_type}"}), 400
    db = get_db()
    r = db.execute("SELECT * FROM routines WHERE id=?", (rid,)).fetchone()
    if not r: return jsonify({"error": "Routine not found"}), 404
    ext = Path(f.filename).suffix.lower() or ".mp4"
    filename = f"routine_{rid}_{int(time.time())}{ext}"
    path = UPLOAD_DIR / filename
    f.save(str(path))
    video_url = f"/static/uploads/{filename}"
    meta = json.dumps({"filename": f.filename, "size": path.stat().st_size,
                        "mimetype": f.content_type, "uploadedAt": datetime.utcnow().isoformat()})
    db.execute("UPDATE routines SET video_path=?, video_url=?, video_metadata=?, status='UPLOADING' WHERE id=?",
               (str(path), video_url, meta, rid))
    db.commit()
    return jsonify({"success": True, "videoUrl": video_url})

@app.post("/api/routines/<rid>/analyze")
@require_auth
def start_analysis(rid):
    db = get_db()
    r = db.execute("SELECT * FROM routines WHERE id=?", (rid,)).fetchone()
    if not r: return jsonify({"error": "Not found"}), 404
    if not r["video_path"]: return jsonify({"error": "No video uploaded"}), 400
    job_id = _uid()
    db.execute("INSERT INTO analysis_jobs (id,routine_id,status,progress,current_step,logs,created_at) "
               "VALUES (?,?,'QUEUED',0,'Queued','[]',CURRENT_TIMESTAMP)", (job_id, rid))
    db.execute("UPDATE routines SET status='PROCESSING' WHERE id=?", (rid,))
    db.commit()
    # Run analysis in background thread
    threading.Thread(target=run_analysis_bg, args=(rid, job_id, r["video_path"]), daemon=True).start()
    return jsonify({"jobId": job_id, "status": "queued"})

@app.get("/api/routines/<rid>/analysis")
@require_auth
def get_analysis(rid):
    db = get_db()
    r = db.execute(
        "SELECT r.*, p.full_name as player_name FROM routines r "
        "LEFT JOIN players p ON p.id=r.player_id WHERE r.id=?", (rid,)).fetchone()
    if not r: return jsonify({"error": "Not found"}), 404
    result = dict(r)
    result["score"] = dict(db.execute("SELECT * FROM scores WHERE routine_id=?", (rid,)).fetchone() or {})
    result["events"] = [dict(e) for e in db.execute(
        "SELECT * FROM detected_events WHERE routine_id=? ORDER BY start_time", (rid,)).fetchall()]
    result["deductions"] = [dict(d) for d in db.execute(
        "SELECT * FROM deductions WHERE routine_id=?", (rid,)).fetchall()]
    job = db.execute("SELECT * FROM analysis_jobs WHERE routine_id=? ORDER BY created_at DESC LIMIT 1", (rid,)).fetchone()
    return jsonify({"routine": result, "job": dict(job) if job else None})

@app.post("/api/routines/<rid>/recalculate-score")
@require_auth
def recalculate(rid):
    score = calculate_score(rid)
    return jsonify(score)

@app.post("/api/routines/<rid>/approve")
@require_auth
def approve(rid):
    db = get_db()
    db.execute("UPDATE scores SET approved_by=?, approved_at=CURRENT_TIMESTAMP WHERE routine_id=?",
               (g.user["id"], rid))
    db.execute("UPDATE routines SET status='APPROVED' WHERE id=?", (rid,))
    db.execute("INSERT INTO audit_log VALUES (?,?,?,?,'APPROVE',NULL,?,CURRENT_TIMESTAMP)",
               (_uid(), g.user["id"], "Score", rid, json.dumps({"approvedBy": g.user["id"]})))
    db.commit()
    return jsonify({"success": True})

@app.post("/api/routines/<rid>/deductions")
@require_auth
def add_deduction(rid):
    d = request.json or {}
    did = _uid()
    get_db().execute("INSERT INTO deductions VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                     (did, rid, d.get("deductionType"), d.get("amount"), d.get("reason"), "JUDGE", g.user["id"]))
    get_db().commit()
    calculate_score(rid)
    return jsonify({"id": did}), 201

# ── Routes: Events ────────────────────────────────────────────────────────────
@app.post("/api/events/<eid>/accept")
@require_auth
def accept_event(eid):
    d = request.json or {}
    get_db().execute("UPDATE detected_events SET accepted=1, rejected=0, judge_notes=? WHERE id=?",
                     (d.get("judgeNotes"), eid))
    get_db().commit()
    return jsonify({"success": True})

@app.post("/api/events/<eid>/reject")
@require_auth
def reject_event(eid):
    d = request.json or {}
    get_db().execute("UPDATE detected_events SET accepted=0, rejected=1, judge_notes=? WHERE id=?",
                     (d.get("judgeNotes"), eid))
    get_db().commit()
    return jsonify({"success": True})

@app.put("/api/events/<eid>")
@require_auth
def update_event(eid):
    d = request.json or {}
    get_db().execute("UPDATE detected_events SET edited_event_type=?, judge_notes=? WHERE id=?",
                     (d.get("eventType"), d.get("judgeNotes"), eid))
    get_db().commit()
    return jsonify({"success": True})

# ── Routes: Rules ─────────────────────────────────────────────────────────────
@app.get("/api/rulesets/difficulty-elements")
@require_auth
def get_elements():
    rows = get_db().execute("SELECT * FROM difficulty_elements WHERE active=1 ORDER BY grp, value").fetchall()
    result = []
    for r in rows:
        d = dict(r); d["group"] = d.pop("grp")
        result.append(d)
    return jsonify(result)

# ── Routes: Reports ───────────────────────────────────────────────────────────
@app.get("/api/reports/routines/<rid>/report")
@require_auth
def get_report(rid):
    db = get_db()
    r = db.execute("SELECT r.*, p.full_name,p.club,p.country FROM routines r "
                   "LEFT JOIN players p ON p.id=r.player_id WHERE r.id=?", (rid,)).fetchone()
    if not r: return jsonify({"error": "Not found"}), 404
    score = db.execute("SELECT * FROM scores WHERE routine_id=?", (rid,)).fetchone()
    events = db.execute("SELECT * FROM detected_events WHERE routine_id=? ORDER BY start_time", (rid,)).fetchall()
    deductions = db.execute("SELECT * FROM deductions WHERE routine_id=?", (rid,)).fetchall()
    return jsonify({
        "routine": dict(r), "score": dict(score) if score else {},
        "events": [dict(e) for e in events], "deductions": [dict(d) for d in deductions],
        "generatedAt": datetime.utcnow().isoformat(),
        "aiWarning": "AI analysis is a recommendation only. Final scoring must be reviewed by a qualified human judge."
    })

# ── AI Analysis Pipeline ──────────────────────────────────────────────────────
MODEL_URL  = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
MODEL_FILE = BASE_DIR / "pose_landmarker_lite.task"

def _get_pose_model():
    """Return path to pose model, downloading it if needed."""
    # Check for any existing model (prefer full > heavy > lite)
    for name in ["pose_landmarker_full.task", "pose_landmarker_heavy.task",
                 "pose_landmarker_lite.task", "pose_landmarker.task"]:
        p = BASE_DIR / name
        if p.exists():
            print(f"Pose model found: {name}")
            return str(p)

    # Not found — download lite model automatically
    print("Pose model not found. Downloading (~7 MB)...")
    import urllib.request, ssl
    # Create SSL context that works on macOS Python 3.12+ and Railway
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(MODEL_URL, context=ctx, timeout=60) as r:
            MODEL_FILE.write_bytes(r.read())
        print(f"Model downloaded to {MODEL_FILE}")
        return str(MODEL_FILE)
    except ssl.SSLCertVerificationError:
        # macOS local dev: SSL cert issue — try without verification as fallback
        print("SSL verification failed — retrying without verification (local dev only)...")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(MODEL_URL, context=ctx, timeout=60) as r:
            MODEL_FILE.write_bytes(r.read())
        print(f"Model downloaded to {MODEL_FILE}")
        return str(MODEL_FILE)


def _download_model_at_startup():
    """Pre-download the pose model in a background thread at startup."""
    def _dl():
        try:
            _get_pose_model()
            print("✅ Pose model ready")
        except Exception as e:
            print(f"⚠️  Could not download pose model at startup: {e}")
            print("   Analysis will attempt download again when first video is uploaded.")
    threading.Thread(target=_dl, daemon=True).start()


def run_analysis_bg(routine_id: str, job_id: str, video_path: str):
    """Background thread: MediaPipe Tasks pose analysis (v0.10+ API)."""
    with app.app_context():
        db = get_db()
        def upd(progress, step, log=""):
            db.execute("UPDATE analysis_jobs SET progress=?, current_step=? WHERE id=?",
                       (progress, step, job_id))
            db.commit()
            if log: print(f"  [{progress}%] {step}: {log}")

        try:
            db.execute("UPDATE analysis_jobs SET status='PROCESSING', started_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
            db.commit()
            upd(5, "Loading video", "Opening with OpenCV")

            import cv2, numpy as np

            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise ValueError(f"Cannot open video: {video_path}")

            fps      = cap.get(cv2.CAP_PROP_FPS) or 30.0
            n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = n_frames / fps if fps > 0 else 0
            cap.release()

            meta = json.dumps({"fps": round(fps, 2), "frame_count": n_frames,
                               "duration": round(duration, 2), "width": width,
                               "height": height, "resolution": f"{width}x{height}",
                               "file_size": Path(video_path).stat().st_size})
            db.execute("UPDATE routines SET video_metadata=? WHERE id=?", (meta, routine_id))
            db.commit()
            upd(12, "Downloading pose model", "First run: fetching ~7 MB model")

            # ── MediaPipe Tasks API (v0.10+) ────────────────────────────────
            import mediapipe as mp
            VisionRunningMode = mp.tasks.vision.RunningMode
            PoseLandmarker    = mp.tasks.vision.PoseLandmarker
            PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
            BaseOptions       = mp.tasks.BaseOptions

            model_path = _get_pose_model()
            upd(15, "Extracting frames", f"{duration:.1f}s @ {fps:.0f}fps")

            options = PoseLandmarkerOpts(
                base_options=BaseOptions(model_asset_path=model_path),
                running_mode=VisionRunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=0.4,
                min_pose_presence_confidence=0.4,
                min_tracking_confidence=0.4,
                output_segmentation_masks=False,
            )

            # Sample every 80ms for better temporal resolution
            # Cap at every 2 frames minimum to avoid redundant processing
            sample_interval = 0.08
            frame_step  = max(2, int(fps * sample_interval))
            pose_frames = []
            prev_lms_raw = None

            with PoseLandmarker.create_from_options(options) as landmarker:
                cap = cv2.VideoCapture(video_path)
                frame_idx   = 0
                total_expected = max(1, int(n_frames / frame_step))

                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    if frame_idx % frame_step == 0:
                        timestamp = frame_idx / fps
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                        result = landmarker.detect(mp_image)

                        if result.pose_landmarks and len(result.pose_landmarks) > 0:
                            lms = result.pose_landmarks[0]   # list of NormalizedLandmark

                            key_indices = [11, 12, 23, 24, 25, 26, 27, 28]
                            key_vis = [lms[i].visibility for i in key_indices if i < len(lms)]
                            confidence = float(np.mean(key_vis)) if key_vis else 0.0

                            def pt(idx):
                                if idx >= len(lms): return None
                                lm = lms[idx]
                                if lm.visibility > 0.3:
                                    return np.array([lm.x, lm.y])
                                return None

                            features = extract_features(lms, pt, prev_lms_raw, frame_idx)
                            features_json = json.dumps({
                                k: bool(v) if isinstance(v, (bool, np.bool_)) else
                                   float(v) if isinstance(v, (float, np.floating, int, np.integer)) else
                                   v
                                for k, v in features.items()
                                if not isinstance(v, (dict, list))
                            })

                            pf_id = _uid()
                            db.execute(
                                "INSERT INTO pose_frames (id,routine_id,timestamp,frame_index,confidence,features) "
                                "VALUES (?,?,?,?,?,?)",
                                (pf_id, routine_id, round(timestamp, 3), frame_idx,
                                 round(confidence, 3), features_json))
                            pose_frames.append({"t": timestamp, "conf": confidence,
                                                "features": features, "lms": lms})
                            prev_lms_raw = lms
                        else:
                            pose_frames.append({"t": frame_idx / fps, "conf": 0.0,
                                                "features": {}, "lms": None})

                        processed = len(pose_frames)
                        if processed % 10 == 0:
                            db.commit()
                            progress = 15 + int((processed / total_expected) * 55)
                            upd(min(70, progress), "Pose estimation", f"{processed} frames")

                    frame_idx += 1

                cap.release()

            db.commit()
            upd(72, "Detecting events", f"Analysing {len(pose_frames)} pose frames")

            # ── Event detection ─────────────────────────────────────────────
            events = detect_events(pose_frames, duration)
            db.execute("DELETE FROM detected_events WHERE routine_id=? AND source='AI_DETECTED'", (routine_id,))
            for ev in events:
                db.execute(
                    "INSERT INTO detected_events "
                    "(id,routine_id,event_type,start_time,end_time,confidence,severity,"
                    "evidence,affects_score,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_uid(), routine_id, ev["event_type"], ev["start_time"], ev["end_time"],
                     ev["confidence"], ev.get("severity"), json.dumps(ev.get("evidence", {})),
                     1 if ev.get("affects_score", False) else 0, "AI_DETECTED"))
            db.commit()
            upd(82, "Calculating confidence", "Scoring AI quality")

            # ── Confidence score ─────────────────────────────────────────────
            confs     = [pf["conf"] for pf in pose_frames if pf["conf"] > 0]
            avg_conf  = float(np.mean(confs)) if confs else 0.0
            low_conf  = sum(1 for c in confs if c < 0.5)
            no_det    = sum(1 for pf in pose_frames if pf["conf"] == 0)
            total     = max(len(pose_frames), 1)
            warnings  = []
            if low_conf / total > 0.3: warnings.append("high_low_confidence_ratio")
            if no_det  / total > 0.2:  warnings.append("out_of_frame")
            overall_conf = max(0.0, min(1.0,
                avg_conf * (1 - low_conf / total * 0.5) * (1 - no_det / total * 0.8)))

            db.execute("UPDATE routines SET ai_confidence=?, confidence_warnings=? WHERE id=?",
                       (round(overall_conf, 3), json.dumps(warnings), routine_id))
            db.commit()
            upd(90, "Calculating score")
            calculate_score(routine_id)
            upd(100, "Complete", f"{len(events)} events, {overall_conf:.0%} confidence")

            db.execute("UPDATE analysis_jobs SET status='COMPLETED', completed_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
            db.execute("UPDATE routines SET status='ANALYZED' WHERE id=?", (routine_id,))
            db.commit()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print("ANALYSIS FAILED:\n", tb)
            db.execute("UPDATE analysis_jobs SET status='FAILED', error_message=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                       (tb, job_id))
            db.execute("UPDATE routines SET status='FAILED' WHERE id=?", (routine_id,))
            db.commit()


def extract_features(lms, pt, prev_lms, frame_idx):
    """Extract rich movement features from MediaPipe landmarks."""
    import numpy as np
    f = {}
    try:
        # ── Centre of mass (hip midpoint) ────────────────────────────────
        lh = pt(23); rh = pt(24)
        if lh is not None and rh is not None:
            com = (lh + rh) / 2
            f["com_x"] = float(com[0])
            f["com_y"] = float(com[1])
            f["is_elevated"] = bool(float(com[1]) < 0.42)

        # ── Foot positions ───────────────────────────────────────────────
        la = pt(27); ra = pt(28)
        lhe = pt(29); rhe = pt(30)   # heels
        if la is not None and ra is not None:
            avg_foot_y = float((la[1] + ra[1]) / 2)
            f["near_floor"]  = bool(avg_foot_y > 0.72)
            f["foot_height"] = float(1.0 - avg_foot_y)
            f["foot_spread"] = float(abs(la[0] - ra[0]))  # lateral split indicator

        # ── Joint angles ─────────────────────────────────────────────────
        def angle(a, b, c):
            if a is None or b is None or c is None: return None
            ba = a - b; bc = c - b
            n1 = np.linalg.norm(ba); n2 = np.linalg.norm(bc)
            if n1 < 1e-6 or n2 < 1e-6: return None
            cos = np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)
            return float(np.degrees(np.arccos(cos)))

        # Knee angles
        lk = angle(pt(23), pt(25), pt(27))
        rk = angle(pt(24), pt(26), pt(28))
        if lk is not None and rk is not None:
            f["knee_angle_l"]  = float(lk)
            f["knee_angle_r"]  = float(rk)
            f["knee_avg"]      = float((lk + rk) / 2)
            f["leg_extension"] = float((lk + rk) / (2 * 180))
            f["body_symmetry"] = float(max(0.0, 1.0 - abs(lk - rk) / 90.0))

        # Hip angles (leg raise)
        lhip = angle(pt(11), pt(23), pt(25))
        rhip = angle(pt(12), pt(24), pt(26))
        if lhip is not None and rhip is not None:
            f["hip_angle_l"] = float(lhip)
            f["hip_angle_r"] = float(rhip)
            f["hip_avg"]     = float((lhip + rhip) / 2)

        # Elbow / arm extension
        le = angle(pt(11), pt(13), pt(15))
        re = angle(pt(12), pt(14), pt(16))
        if le is not None and re is not None:
            f["arm_extension"] = float((le + re) / (2 * 180))

        # Shoulder width (turn indicator — narrows when rotating)
        ls = pt(11); rs = pt(12)
        if ls is not None and rs is not None:
            f["shoulder_width"] = float(abs(ls[0] - rs[0]))

        # ── Torso lean ───────────────────────────────────────────────────
        if ls is not None and rs is not None and lh is not None and rh is not None:
            sm = (ls + rs) / 2
            hm = (lh + rh) / 2
            tv = sm - hm
            norm = np.linalg.norm(tv)
            if norm > 1e-6:
                cos = np.clip(np.dot(tv, np.array([0.0, -1.0])) / norm, -1.0, 1.0)
                f["torso_lean"] = float(np.degrees(np.arccos(cos)))

        # ── Spine length (compression indicator) ─────────────────────────
        nose = pt(0)
        if nose is not None and lh is not None and rh is not None:
            hm = (lh + rh) / 2
            f["spine_length"] = float(np.linalg.norm(nose - hm))

        # ── Movement speed & acceleration ────────────────────────────────
        if prev_lms is not None and lh is not None and rh is not None:
            try:
                plh = np.array([prev_lms[23].x, prev_lms[23].y])
                prh = np.array([prev_lms[24].x, prev_lms[24].y])
                pcom = (plh + prh) / 2
                disp = float(np.linalg.norm(com - pcom))
                f["movement_speed"] = disp / 0.08   # per second (80ms interval)
                f["com_velocity_y"] = float((com[1] - pcom[1]) / 0.08)  # + = downward
            except Exception:
                pass

    except Exception:
        pass
    return f


def detect_events(frames, duration):
    """Improved event detection with better thresholds and deduplication."""
    import numpy as np
    events = []
    if not frames:
        return events

    n = len(frames)

    # ── Calibrate baseline from calmest section ──────────────────────────────
    # Use median of all com_y values as baseline (more robust than first-10 mean)
    all_ys = [fr["features"].get("com_y") for fr in frames if fr["features"].get("com_y") is not None]
    if not all_ys:
        return events
    baseline_y = float(np.percentile(all_ys, 60))  # 60th pct = typical standing height

    # Calibrate shoulder width baseline for turn detection
    all_sw = [fr["features"].get("shoulder_width") for fr in frames if fr["features"].get("shoulder_width")]
    sw_baseline = float(np.percentile(all_sw, 75)) if all_sw else None

    def add_event(etype, t0, t1, conf, sev, affects, explanation, min_gap=1.0):
        """Add event only if not too close to an existing one of the same type."""
        for ev in events:
            if ev["event_type"] == etype and abs(ev["start_time"] - t0) < min_gap:
                return
        events.append({
            "event_type": etype,
            "start_time": round(t0, 3),
            "end_time":   round(t1, 3),
            "confidence": round(min(0.95, max(0.3, conf)), 3),
            "severity":   sev,
            "affects_score": affects,
            "evidence": {"explanation": explanation}
        })

    # ── 1. JUMP detection ────────────────────────────────────────────────────
    # Detect upward COM displacement above baseline
    JUMP_THRESH = 0.05   # 5% elevation above baseline
    in_jump = False; j_start = 0; j_max_elev = 0; j_frames = 0
    for i, fr in enumerate(frames):
        cy = fr["features"].get("com_y")
        vy = fr["features"].get("com_velocity_y", 0)
        if cy is None:
            continue
        elev = baseline_y - cy
        if elev > JUMP_THRESH:
            if not in_jump:
                in_jump = True; j_start = fr["t"]; j_max_elev = elev; j_frames = 0
            j_max_elev = max(j_max_elev, elev)
            j_frames += 1
        else:
            if in_jump and j_frames >= 2:
                conf = min(0.93, 0.50 + j_max_elev * 3.5)
                leg_ext = fr["features"].get("leg_extension", 0.7)
                add_event("jump", j_start, fr["t"], conf, None, False,
                    f"Centre of mass rose {j_max_elev:.1%} above baseline. "
                    f"Leg extension: {leg_ext:.0%}. Duration: {fr['t']-j_start:.2f}s.")
            in_jump = False; j_max_elev = 0; j_frames = 0

    # ── 2. TURN detection ────────────────────────────────────────────────────
    # Shoulder width narrows significantly when body rotates
    if sw_baseline and sw_baseline > 0.01:
        in_turn = False; t_start = 0; t_min_width = sw_baseline; t_frames = 0
        for fr in frames:
            sw = fr["features"].get("shoulder_width")
            if sw is None:
                continue
            ratio = sw / sw_baseline
            if ratio < 0.6:   # shoulder width < 60% of baseline = rotation
                if not in_turn:
                    in_turn = True; t_start = fr["t"]; t_min_width = sw; t_frames = 0
                t_min_width = min(t_min_width, sw)
                t_frames += 1
            else:
                if in_turn and t_frames >= 2:
                    narrowing = 1.0 - (t_min_width / sw_baseline)
                    conf = min(0.90, 0.45 + narrowing * 1.5)
                    # Estimate rotations from duration and narrowing
                    dur = fr["t"] - t_start
                    est_rots = max(1, round(dur / 0.4))
                    add_event("turn", t_start, fr["t"], conf, None, False,
                        f"Shoulder width narrowed {narrowing:.0%} — body rotation detected. "
                        f"~{est_rots} rotation(s), {dur:.2f}s duration.")
                in_turn = False; t_min_width = sw_baseline; t_frames = 0

    # ── 3. LEAP / HIGH JUMP (split leap) ─────────────────────────────────────
    # Jump + wide foot spread simultaneously
    for fr in frames:
        cy  = fr["features"].get("com_y")
        fs  = fr["features"].get("foot_spread", 0)
        hip = fr["features"].get("hip_avg", 180)
        lk  = fr["features"].get("knee_angle_l", 180)
        rk  = fr["features"].get("knee_angle_r", 180)
        if cy is None:
            continue
        elev = baseline_y - cy
        if elev > 0.04 and fs > 0.25 and hip is not None and hip < 120:
            conf = min(0.88, 0.50 + elev * 2.0 + fs * 0.5)
            add_event("leap", fr["t"] - 0.1, fr["t"] + 0.3, conf, None, False,
                f"Airborne with wide leg spread ({fs:.2f} normalised). "
                f"Hip angle: {hip:.0f}°. Possible split leap.", min_gap=1.5)

    # ── 4. FLOOR SUPPORT ─────────────────────────────────────────────────────
    in_supp = False; s_start = 0; s_frames = 0
    for fr in frames:
        cy   = fr["features"].get("com_y", 0)
        near = fr["features"].get("near_floor", False)
        arm  = fr["features"].get("arm_extension", 0)
        lean = abs(fr["features"].get("torso_lean", 90))
        # Low COM + feet near floor + arms extended + torso not upright
        if cy > 0.65 and near and arm > 0.55 and lean > 25:
            if not in_supp:
                in_supp = True; s_start = fr["t"]; s_frames = 0
            s_frames += 1
        else:
            if in_supp and s_frames >= 3:
                dur = fr["t"] - s_start
                conf = min(0.88, 0.50 + s_frames * 0.04)
                add_event("floor_support", s_start, fr["t"], conf, None, False,
                    f"Floor support position held for {dur:.1f}s. "
                    f"Arm extension: {arm:.0%}.")
            in_supp = False; s_frames = 0

    # ── 5. SPLIT POSITION (on floor) ─────────────────────────────────────────
    for fr in frames:
        cy  = fr["features"].get("com_y", 0)
        fs  = fr["features"].get("foot_spread", 0)
        lk  = fr["features"].get("knee_angle_l")
        rk  = fr["features"].get("knee_angle_r")
        if lk is None or rk is None:
            continue
        # Low COM + extended knees + wide foot spread = split
        if cy > 0.60 and fs > 0.30 and lk > 150 and rk > 150:
            conf = min(0.85, 0.45 + fs * 1.0 + (lk + rk - 300) / 300)
            add_event("split", fr["t"] - 0.05, fr["t"] + 0.4, conf, None, False,
                f"Split position detected. Foot spread: {fs:.2f}. "
                f"Knee angles: L={lk:.0f}° R={rk:.0f}°.", min_gap=2.0)

    # ── 6. FALL detection ────────────────────────────────────────────────────
    # Rapid downward COM displacement exceeding normal landing
    for i in range(4, n):
        t_now  = frames[i]["t"]
        cy_now = frames[i]["features"].get("com_y")
        cy_ago = frames[i-4]["features"].get("com_y")
        if cy_now is None or cy_ago is None:
            continue
        drop = cy_now - cy_ago   # positive = COM moved down
        if drop > 0.18:
            conf = min(0.92, 0.55 + drop * 1.5)
            add_event("fall", frames[i-4]["t"], t_now, conf, "major", True,
                f"Rapid body drop of {drop:.1%} in {t_now - frames[i-4]['t']:.2f}s — likely fall.",
                min_gap=2.0)

    # ── 7. LANDING INSTABILITY ───────────────────────────────────────────────
    # High speed followed by oscillation near floor
    for i in range(6, n):
        near  = frames[i]["features"].get("near_floor", False)
        speed = frames[i]["features"].get("movement_speed", 0)
        if not near:
            continue
        recent_speeds = [frames[j]["features"].get("movement_speed", 0) for j in range(i-5, i+1)]
        if max(recent_speeds[:3]) > 0.08 and float(np.std(recent_speeds[3:])) > 0.02:
            conf = min(0.78, 0.40 + float(np.std(recent_speeds)) * 3.0)
            add_event("landing_instability", frames[i-3]["t"], frames[i]["t"], conf, "minor", True,
                "Speed variance after ground contact suggests unstable landing.",
                min_gap=1.5)

    # ── 8. POOR ALIGNMENT ───────────────────────────────────────────────────
    pa_start = None; pa_frames = 0
    for fr in frames:
        lean = abs(fr["features"].get("torso_lean", 0))
        spine = fr["features"].get("spine_length")
        # Significant lean while standing (not in floor support)
        near = fr["features"].get("near_floor", False)
        if 22 < lean < 60 and not near:
            if pa_start is None:
                pa_start = fr["t"]; pa_frames = 0
            pa_frames += 1
        else:
            if pa_start is not None and pa_frames >= 5:
                dur = fr["t"] - pa_start
                conf = min(0.72, 0.35 + pa_frames * 0.05)
                add_event("poor_alignment", pa_start, fr["t"], conf, "minor", True,
                    f"Torso lean exceeded 22° for {pa_frames} frames ({dur:.1f}s).",
                    min_gap=2.0)
            pa_start = None; pa_frames = 0

    # ── 9. OUT OF FRAME ──────────────────────────────────────────────────────
    oof_start = None; oof_frames = 0
    for fr in frames:
        if fr["conf"] < 0.28:
            if oof_start is None:
                oof_start = fr["t"]; oof_frames = 0
            oof_frames += 1
        else:
            if oof_start is not None and oof_frames >= 3:
                add_event("out_of_frame", oof_start, fr["t"], 0.90, "warning", True,
                    f"Low pose confidence for {oof_frames} frames — athlete may be out of frame.",
                    min_gap=1.0)
            oof_start = None; oof_frames = 0

    events.sort(key=lambda e: e["start_time"])
    return events


def calculate_score(routine_id: str) -> dict:
    """Calculate A/E/D scores from events and pose frames."""
    import numpy as np
    db = get_db()
    events = db.execute("SELECT * FROM detected_events WHERE routine_id=?", (routine_id,)).fetchall()
    frames = db.execute("SELECT confidence, features FROM pose_frames WHERE routine_id=? ORDER BY timestamp",
                        (routine_id,)).fetchall()
    deductions_rows = db.execute("SELECT * FROM deductions WHERE routine_id=?", (routine_id,)).fetchall()
    routine = db.execute("SELECT declared_elements FROM routines WHERE id=?", (routine_id,)).fetchone()

    # ── Artistry ──────────────────────────────────────────────────────────
    feats = []
    for fr in frames:
        try: feats.append(json.loads(fr["features"] or "{}"))
        except: pass

    speeds = [f.get("movement_speed", 0) for f in feats if f.get("movement_speed")]
    if len(speeds) > 1:
        mean = np.mean(speeds)
        cv = np.std(speeds) / mean if mean > 0 else 1
        rhythm = float(max(0, min(1, 1 - cv * 0.5)))
    else:
        rhythm = 0.5

    event_types = set(dict(e)["event_type"] for e in events)
    variety = min(1.0, len(event_types) / 6)

    com_xs = [f.get("com_x", 0.5) for f in feats if f.get("com_x")]
    spatial = float(min(1, (max(com_xs) - min(com_xs)) / 0.5)) if len(com_xs) > 1 else 0.5

    syms = [f.get("body_symmetry", 0.5) for f in feats if f.get("body_symmetry")]
    symmetry = float(np.mean(syms)) if syms else 0.5

    artistry = round((rhythm*0.3 + variety*0.25 + spatial*0.25 + symmetry*0.2) * 10, 3)

    # ── Execution ─────────────────────────────────────────────────────────
    EXEC_DEDUCTIONS = {
        "fall":                 1.0,
        "landing_instability":  0.3,
        "poor_alignment":       0.1,
        "out_of_frame":         0.1,
        "small_form_error":     0.1,
        "medium_form_error":    0.3,
    }
    execution = 10.0
    exec_applied = []
    accepted_events = [dict(e) for e in events if dict(e).get("rejected") != 1]
    for ev in accepted_events:
        ev_type = ev["event_type"]
        ded = EXEC_DEDUCTIONS.get(ev_type, 0)
        if ded > 0 and ev.get("affects_score"):
            execution -= ded
            exec_applied.append({"type": ev_type, "deduction": ded})
    execution = round(max(0, execution), 3)

    # ── Difficulty ────────────────────────────────────────────────────────
    elements = db.execute("SELECT * FROM difficulty_elements WHERE active=1").fetchall()
    elem_map = {e["code"]: dict(e) for e in elements}
    declared = []
    try: declared = json.loads(routine["declared_elements"] or "[]")
    except: pass

    difficulty = 0.0
    diff_elements = []
    for code in declared:
        el = elem_map.get(code.strip().upper())
        if el:
            difficulty += el["value"]
            diff_elements.append({"code": el["code"], "name": el["name"], "value": el["value"], "source": "declared"})

    # AI-detected difficulty bonus
    AI_DIFF_TYPES = {
        "jump":          0.20,
        "leap":          0.45,   # split leap is high value
        "turn":          0.30,
        "floor_support": 0.20,
        "split":         0.30,
    }
    seen_types = set()
    for ev in accepted_events:
        etype = ev["event_type"]
        conf  = ev.get("confidence", 0)
        if etype in AI_DIFF_TYPES and conf >= 0.60 and etype not in seen_types:
            base  = AI_DIFF_TYPES[etype]
            bonus = round(base * conf, 3)
            difficulty += bonus
            seen_types.add(etype)
            diff_elements.append({
                "type": etype, "value": bonus,
                "source": "ai_detected", "confidence": conf
            })

    difficulty = round(difficulty, 3)

    # ── Manual deductions ─────────────────────────────────────────────────
    manual_ded = sum(d["amount"] for d in deductions_rows)

    total = round(artistry + execution + difficulty, 3)
    final = round(max(0, total - manual_ded), 3)

    avg_conf = float(np.mean([fr["confidence"] for fr in frames])) if frames else 0

    calc_details = json.dumps({
        "artistry": {"score": artistry, "rhythm": round(rhythm,3), "variety": round(variety,3),
                     "spatial": round(spatial,3), "symmetry": round(symmetry,3)},
        "execution": {"score": execution, "deductions_applied": exec_applied},
        "difficulty": {"score": difficulty, "elements": diff_elements},
        "manual_deductions": manual_ded, "confidence": round(avg_conf, 3)
    })

    # Upsert score
    existing = db.execute("SELECT id FROM scores WHERE routine_id=?", (routine_id,)).fetchone()
    if existing:
        db.execute("UPDATE scores SET artistry_score=?,execution_score=?,difficulty_score=?,"
                   "total_deductions=?,final_score=?,ai_confidence=?,calculation_details=? WHERE routine_id=?",
                   (artistry, execution, difficulty, round(manual_ded,3), final, round(avg_conf,3), calc_details, routine_id))
    else:
        db.execute("INSERT INTO scores (id,routine_id,artistry_score,execution_score,difficulty_score,"
                   "total_deductions,final_score,ai_confidence,calculation_details) VALUES (?,?,?,?,?,?,?,?,?)",
                   (_uid(), routine_id, artistry, execution, difficulty, round(manual_ded,3), final, round(avg_conf,3), calc_details))
    db.commit()
    return {"artistryScore": artistry, "executionScore": execution, "difficultyScore": difficulty,
            "totalDeductions": round(manual_ded,3), "finalScore": final}

# ── Frontend (single-page app served from Flask) ──────────────────────────────
@app.get("/")
def index():
    return send_file(str(BASE_DIR / "templates" / "index.html"))

@app.get("/static/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)

# Debug endpoint — returns last job error
@app.get("/api/debug/last-error")
def last_error():
    db = get_db()
    row = db.execute(
        "SELECT * FROM analysis_jobs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return jsonify({"message": "No jobs found"})
    return jsonify(dict(row))

# Health check
@app.get("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("🚀 Initialising AeroScore AI...")
    init_db()
    print("✅ Database ready")
    _download_model_at_startup()   # pre-fetch pose model in background
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
