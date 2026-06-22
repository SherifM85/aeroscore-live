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
    """Background thread: pure OpenCV motion analysis — no GPU libraries required."""
    import sqlite3 as _sq
    db = _sq.connect(str(DB_PATH), detect_types=_sq.PARSE_DECLTYPES)
    db.row_factory = _sq.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    def upd(progress, step, log=""):
        db.execute("UPDATE analysis_jobs SET progress=?, current_step=? WHERE id=?",
                   (progress, step, job_id))
        db.commit()
        print(f"  [{progress}%] {step}" + (f": {log}" if log else ""), flush=True)

    try:
        db.execute("UPDATE analysis_jobs SET status='PROCESSING', started_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        db.commit()
        upd(5, "Loading video", "Opening with OpenCV")

        import cv2
        import numpy as np

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
        upd(15, "Analysing motion", f"{duration:.1f}s @ {fps:.0f}fps")

        # ── Pure OpenCV motion analysis ─────────────────────────────────────
        # Sample every 80ms
        frame_step     = max(2, int(fps * 0.08))
        pose_frames    = []
        prev_gray      = None
        prev_com_y     = None
        frame_idx      = 0
        total_expected = max(1, int(n_frames / frame_step))

        cap = cv2.VideoCapture(video_path)

        # Background subtractor to detect person region
        bg_sub = cv2.createBackgroundSubtractorMOG2(history=50, varThreshold=40, detectShadows=False)

        # Warm up background subtractor with first 30 frames
        for _ in range(min(30, n_frames)):
            ret, frame = cap.read()
            if ret:
                bg_sub.apply(frame)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_step == 0:
                timestamp  = frame_idx / fps
                gray       = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                h, w       = gray.shape
                features   = {}
                confidence = 0.0

                # ── Detect person region via background subtraction ──────────
                fg_mask = bg_sub.apply(frame)
                # Clean up mask
                kernel  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
                fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)

                # Find largest contour = person
                contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                person_box  = None
                if contours:
                    largest = max(contours, key=cv2.contourArea)
                    area    = cv2.contourArea(largest)
                    if area > (h * w * 0.005):  # at least 0.5% of frame
                        x, y, bw, bh = cv2.boundingRect(largest)
                        person_box   = (x, y, bw, bh)
                        confidence   = min(0.9, float(area) / (h * w * 0.15))

                        # COM = centre of bounding box, normalised 0-1
                        com_x = float(x + bw / 2) / w
                        com_y = float(y + bh / 2) / h
                        features["com_x"]       = com_x
                        features["com_y"]       = com_y
                        features["is_elevated"] = bool(com_y < 0.40)
                        features["near_floor"]  = bool(com_y > 0.68)
                        features["foot_height"] = float(1.0 - (y + bh) / h)
                        # Aspect ratio: tall = standing, wide = floor
                        features["aspect_ratio"] = float(bh / max(bw, 1))
                        # Normalised bounding box height = person extension
                        features["body_height_norm"] = float(bh / h)
                        # Width spread (useful for split detection)
                        features["body_width_norm"]  = float(bw / w)

                # ── Optical flow for movement speed ──────────────────────────
                if prev_gray is not None:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray, gray, None,
                        pyr_scale=0.5, levels=3, winsize=15,
                        iterations=3, poly_n=5, poly_sigma=1.2, flags=0)
                    mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    # Only measure flow in person region if available
                    if person_box is not None:
                        x, y, bw, bh = person_box
                        region_mag   = mag[y:y+bh, x:x+bw]
                        mean_flow    = float(np.mean(region_mag)) if region_mag.size > 0 else float(np.mean(mag))
                    else:
                        mean_flow = float(np.mean(mag))

                    features["movement_speed"] = mean_flow
                    if prev_com_y is not None and "com_y" in features:
                        features["com_velocity_y"] = float((features["com_y"] - prev_com_y) / 0.08)

                prev_gray   = gray
                prev_com_y  = features.get("com_y")

                features_json = json.dumps({
                    k: float(v) if isinstance(v, (float, int, np.floating, np.integer)) else bool(v)
                    for k, v in features.items()
                })

                db.execute(
                    "INSERT INTO pose_frames (id,routine_id,timestamp,frame_index,confidence,features) "
                    "VALUES (?,?,?,?,?,?)",
                    (_uid(), routine_id, round(timestamp, 3), frame_idx,
                     round(confidence, 3), features_json))
                pose_frames.append({"t": timestamp, "conf": confidence, "features": features})

                processed = len(pose_frames)
                if processed % 15 == 0:
                    db.commit()
                    progress = 15 + int((processed / total_expected) * 55)
                    upd(min(70, progress), "Analysing motion", f"{processed} frames")

            frame_idx += 1

        cap.release()
        db.commit()
        upd(72, "Detecting events", f"{len(pose_frames)} frames analysed")

        events = detect_events_cv(pose_frames, duration)
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
        upd(85, "Calculating score")

        confs        = [pf["conf"] for pf in pose_frames if pf["conf"] > 0]
        avg_conf     = float(np.mean(confs)) if confs else 0.0
        low_conf     = sum(1 for c in confs if c < 0.3)
        total_frames = max(len(pose_frames), 1)
        warnings     = []
        if low_conf / total_frames > 0.4: warnings.append("high_low_confidence_ratio")
        overall_conf = max(0.0, min(1.0, avg_conf * (1 - low_conf / total_frames * 0.5)))

        db.execute("UPDATE routines SET ai_confidence=?, confidence_warnings=? WHERE id=?",
                   (round(overall_conf, 3), json.dumps(warnings), routine_id))
        db.commit()

        _calculate_score_direct(routine_id, db)
        upd(100, "Complete", f"{len(events)} events, {overall_conf:.0%} confidence")

        db.execute("UPDATE analysis_jobs SET status='COMPLETED', completed_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        db.execute("UPDATE routines SET status='ANALYZED' WHERE id=?", (routine_id,))
        db.commit()

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("ANALYSIS FAILED:\n", tb, flush=True)
        try:
            db.execute("UPDATE analysis_jobs SET status='FAILED', error_message=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                       (tb, job_id))
            db.execute("UPDATE routines SET status='FAILED' WHERE id=?", (routine_id,))
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def detect_events_cv(frames, duration):
    """Event detection from pure OpenCV motion features."""
    import numpy as np
    events = []
    if not frames:
        return events

    n = len(frames)

    # Calibrate baselines
    all_ys = [f["features"].get("com_y") for f in frames if f["features"].get("com_y") is not None]
    if not all_ys:
        return events
    baseline_y = float(np.percentile(all_ys, 60))

    all_speeds = [f["features"].get("movement_speed", 0) for f in frames]
    speed_p75  = float(np.percentile([s for s in all_speeds if s > 0], 75)) if any(s > 0 for s in all_speeds) else 0.1

    def add(etype, t0, t1, conf, sev, affects, explanation, min_gap=1.0):
        for ev in events:
            if ev["event_type"] == etype and abs(ev["start_time"] - t0) < min_gap:
                return
        events.append({
            "event_type":    etype,
            "start_time":    round(t0, 3),
            "end_time":      round(t1, 3),
            "confidence":    round(min(0.92, max(0.35, conf)), 3),
            "severity":      sev,
            "affects_score": affects,
            "evidence":      {"explanation": explanation}
        })

    # ── 1. JUMP: COM rises above baseline ────────────────────────────────────
    in_jump = False; j_start = 0; j_max_elev = 0; j_frames = 0
    for fr in frames:
        cy = fr["features"].get("com_y")
        if cy is None: continue
        elev = baseline_y - cy
        if elev > 0.06:
            if not in_jump: in_jump = True; j_start = fr["t"]; j_max_elev = 0
            j_max_elev = max(j_max_elev, elev); j_frames += 1
        else:
            if in_jump and j_frames >= 2:
                conf = min(0.90, 0.50 + j_max_elev * 3.0)
                add("jump", j_start, fr["t"], conf, None, False,
                    f"Centre of mass rose {j_max_elev:.1%} above baseline over {j_frames} frames.")
            in_jump = False; j_max_elev = 0; j_frames = 0

    # ── 2. TURN: high speed + stable COM height ───────────────────────────────
    for i in range(3, n - 3):
        speeds = [frames[j]["features"].get("movement_speed", 0) for j in range(i-2, i+3)]
        cys    = [frames[j]["features"].get("com_y") for j in range(i-2, i+3) if frames[j]["features"].get("com_y")]
        if not cys: continue
        avg_speed = float(np.mean(speeds))
        cy_var    = float(np.std(cys))
        # Fast movement but stable vertical position = turn (not jump/fall)
        if avg_speed > speed_p75 * 1.5 and cy_var < 0.04:
            conf = min(0.82, 0.45 + avg_speed / speed_p75 * 0.15)
            add("turn", frames[i-2]["t"], frames[i+2]["t"], conf, None, False,
                f"High rotational motion detected (speed {avg_speed:.3f}, vertical stability {cy_var:.3f}).",
                min_gap=1.2)

    # ── 3. FLOOR SUPPORT: low COM + wide body + slow movement ─────────────────
    in_supp = False; s_start = 0; s_frames = 0
    for fr in frames:
        cy     = fr["features"].get("com_y", 0)
        aspect = fr["features"].get("aspect_ratio", 1.5)
        speed  = fr["features"].get("movement_speed", 0)
        # Low COM + body more horizontal (low aspect ratio) + slow = floor support
        if cy > 0.62 and aspect < 1.2 and speed < speed_p75 * 0.5:
            if not in_supp: in_supp = True; s_start = fr["t"]; s_frames = 0
            s_frames += 1
        else:
            if in_supp and s_frames >= 4:
                dur  = fr["t"] - s_start
                conf = min(0.85, 0.50 + s_frames * 0.04)
                add("floor_support", s_start, fr["t"], conf, None, False,
                    f"Athlete in floor position for {dur:.1f}s (low COM, horizontal body).")
            in_supp = False; s_frames = 0

    # ── 4. LEAP / HIGH JUMP: elevated + wide body ────────────────────────────
    for fr in frames:
        cy    = fr["features"].get("com_y")
        bw    = fr["features"].get("body_width_norm", 0)
        speed = fr["features"].get("movement_speed", 0)
        if cy is None: continue
        elev = baseline_y - cy
        if elev > 0.05 and bw > 0.25 and speed > speed_p75:
            conf = min(0.85, 0.48 + elev * 2.0 + bw * 0.5)
            add("leap", fr["t"] - 0.08, fr["t"] + 0.25, conf, None, False,
                f"Elevated airborne position with wide body spread ({bw:.2f} width).", min_gap=1.5)

    # ── 5. FALL: rapid COM drop ───────────────────────────────────────────────
    for i in range(3, n):
        cy_now = frames[i]["features"].get("com_y")
        cy_ago = frames[i-3]["features"].get("com_y")
        if cy_now is None or cy_ago is None: continue
        drop = cy_now - cy_ago
        if drop > 0.20:
            conf = min(0.90, 0.55 + drop * 1.5)
            add("fall", frames[i-3]["t"], frames[i]["t"], conf, "major", True,
                f"Rapid body drop of {drop:.1%} detected — possible fall.", min_gap=2.0)

    # ── 6. LANDING INSTABILITY: speed spike followed by oscillation near floor ─
    for i in range(5, n):
        near  = frames[i]["features"].get("near_floor", False)
        if not near: continue
        recent = [frames[j]["features"].get("movement_speed", 0) for j in range(i-4, i+1)]
        if max(recent[:2]) > speed_p75 * 1.2 and float(np.std(recent[2:])) > 0.015:
            conf = min(0.75, 0.40 + float(np.std(recent)) * 2.0)
            add("landing_instability", frames[i-3]["t"], frames[i]["t"], conf, "minor", True,
                "Speed oscillation after ground contact — possible unstable landing.", min_gap=1.5)

    # ── 7. HIGH ACTIVITY BURST (aerobics-specific) ────────────────────────────
    window = 5
    for i in range(window, n - window):
        window_speeds = [frames[j]["features"].get("movement_speed", 0) for j in range(i-window, i+window)]
        avg = float(np.mean(window_speeds))
        if avg > speed_p75 * 2.0:
            add("possible_difficulty_element", frames[i-window]["t"], frames[i+window]["t"],
                min(0.70, 0.40 + avg / speed_p75 * 0.10), None, False,
                f"High intensity movement burst detected (avg speed {avg:.3f}).", min_gap=2.0)

    events.sort(key=lambda e: e["start_time"])
    return events


def _calculate_score_direct(routine_id: str, db) -> dict:
    """Calculate A/E/D scores using a provided db connection (safe for background threads)."""
    return _score_impl(routine_id, db)


def _score_impl(routine_id: str, db) -> dict:
    """Core scoring logic."""
    import numpy as np
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
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
