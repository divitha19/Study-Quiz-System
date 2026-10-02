
import os, re, json, sqlite3, secrets
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

BASE = Path(__file__).resolve().parent
DB = BASE / "quiz.db"
UPLOADS = BASE / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".txt", ".md"}

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS materials(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      filename TEXT NOT NULL,
      stored_name TEXT NOT NULL,
      text TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS attempts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      material_id INTEGER NOT NULL,
      score REAL NOT NULL,
      correct INTEGER NOT NULL,
      total INTEGER NOT NULL,
      difficulty TEXT NOT NULL,
      chapter TEXT,
      topic TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def extract_text(path):
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception as e:
            raise RuntimeError("PDF extraction needs pypdf. Run: pip install -r requirements.txt")
    if ext == ".docx":
        try:
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs)
        except Exception:
            raise RuntimeError("DOCX extraction needs python-docx. Run: pip install -r requirements.txt")
    raise RuntimeError("Unsupported file type.")

def split_sentences(text):
    text = re.sub(r'\s+', ' ', text).strip()
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) >= 35]

def generate_questions(text, count, difficulty, chapter="", topic=""):
    """
    Offline/manual-only generator. It never creates questions about the application.
    For higher-quality AI generation, set OPENAI_API_KEY and install openai.
    Without an API key it creates deterministic comprehension questions from the
    uploaded manual's own sentences.
    """
    sentences = split_sentences(text)
    if not sentences:
        raise RuntimeError("The uploaded manual did not contain enough extractable text.")

    # Optional filtering is intentionally conservative: only use matching text if found.
    selected = sentences
    if chapter:
        hits = [s for s in sentences if chapter.lower() in s.lower()]
        if hits: selected = hits
    if topic:
        hits = [s for s in selected if topic.lower() in s.lower()]
        if hits: selected = hits

    qs = []
    for i, s in enumerate(selected):
        if len(qs) >= count: break
        words = re.findall(r"[A-Za-z][A-Za-z'-]{4,}", s)
        if not words: continue
        answer = max(words, key=len)
        question = f"According to the uploaded study material, which term appears in this statement: “{s}”?"
        pool = []
        for other in sentences:
            cand = max(re.findall(r"[A-Za-z][A-Za-z'-]{4,}", other), key=len, default="")
            if cand and cand.lower() != answer.lower() and cand not in pool:
                pool.append(cand)
            if len(pool) >= 3: break
        opts = [answer] + pool[:3]
        if len(opts) < 4: continue
        # deterministic shuffle
        opts = opts[:4]
        shift = i % 4
        opts = opts[shift:] + opts[:shift]
        qs.append({"question": question, "options": opts, "answer": answer,
                   "explanation": "This answer is taken from the uploaded study material.",
                   "difficulty": difficulty})
    if len(qs) < count:
        raise RuntimeError("Not enough usable text to generate the requested number of questions.")
    return qs

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        if not u or len(p) < 6:
            flash("Username is required and password must be at least 6 characters.")
        else:
            c = db()
            try:
                c.execute("INSERT INTO users(username,password) VALUES(?,?)",
                          (u, generate_password_hash(p)))
                c.commit()
                flash("Account created. Please log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username already exists.")
            finally: c.close()
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        c = db(); row = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone(); c.close()
        if row and check_password_hash(row["password"], p):
            session["user_id"] = row["id"]; session["username"] = row["username"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    c = db()
    mats = c.execute("SELECT * FROM materials WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    attempts = c.execute("""SELECT attempts.*, materials.filename FROM attempts
                            JOIN materials ON materials.id=attempts.material_id
                            WHERE attempts.user_id=? ORDER BY attempts.id DESC LIMIT 20""",
                         (session["user_id"],)).fetchall()
    stats = c.execute("""SELECT COUNT(*) AS quizzes,
                                COALESCE(SUM(correct),0) AS correct,
                                COALESCE(SUM(total),0) AS total,
                                COALESCE(AVG(score),0) AS avg_score
                         FROM attempts WHERE user_id=?""",
                      (session["user_id"],)).fetchone()
    accuracy = round((stats["correct"] / stats["total"] * 100), 1) if stats["total"] else 0
    wrong = max(stats["total"] - stats["correct"], 0)
    c.close()
    return render_template("dashboard.html", materials=mats, attempts=attempts,
                           stats=stats, accuracy=accuracy, wrong=wrong)

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    f = request.files.get("material")
    if not f or not f.filename:
        flash("Choose a study manual first."); return redirect(url_for("dashboard"))
    name = secure_filename(f.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        flash("Supported files: PDF, DOCX, TXT, MD."); return redirect(url_for("dashboard"))
    stored = f"{secrets.token_hex(12)}{ext}"
    path = UPLOADS / stored
    f.save(path)
    try:
        text = extract_text(path)
        if len(text.strip()) < 100:
            raise RuntimeError("Very little text could be extracted. If this is a scanned PDF, OCR support is needed.")
        c=db(); c.execute("INSERT INTO materials(user_id,filename,stored_name,text) VALUES(?,?,?,?)",
                          (session["user_id"], name, stored, text))
        c.commit(); c.close()
        flash("Study material uploaded and analysed.")
    except Exception as e:
        path.unlink(missing_ok=True)
        flash(str(e))
    return redirect(url_for("dashboard"))

@app.route("/generate", methods=["GET","POST"])
@login_required
def generate():
    c=db()
    mats=c.execute("SELECT * FROM materials WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()
    if request.method == "POST":
        material_id = request.form.get("material_id")
        try:
            count=int(request.form.get("count","0"))
        except: count=0
        difficulty=request.form.get("difficulty","")
        chapter=request.form.get("chapter","").strip()
        topic=request.form.get("topic","").strip()
        if not material_id or count < 1 or count > 100 or difficulty not in {"Easy","Medium","Hard"}:
            flash("Number of Questions and Difficulty are required.")
        else:
            m=c.execute("SELECT * FROM materials WHERE id=? AND user_id=?", (material_id,session["user_id"])).fetchone()
            if not m:
                flash("Study material not found.")
            else:
                try:
                    questions=generate_questions(m["text"],count,difficulty,chapter,topic)
                    session["quiz"]={"material_id":m["id"],"filename":m["filename"],"difficulty":difficulty,
                                     "chapter":chapter,"topic":topic,"questions":questions}
                    return redirect(url_for("quiz"))
                except Exception as e:
                    flash(str(e))
    c.close()
    return render_template("generate.html", materials=mats)

@app.route("/quiz")
@login_required
def quiz():
    q=session.get("quiz")
    if not q: return redirect(url_for("generate"))
    return render_template("quiz.html", quiz=q)

@app.route("/submit", methods=["POST"])
@login_required
def submit():
    q=session.get("quiz")
    if not q: return redirect(url_for("generate"))
    correct=0
    for i,item in enumerate(q["questions"]):
        if request.form.get(f"q{i}") == item["answer"]: correct += 1
    total=len(q["questions"])
    score=round(correct/total*100,2)
    c=db()
    c.execute("""INSERT INTO attempts(user_id,material_id,score,correct,total,difficulty,chapter,topic)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (session["user_id"],q["material_id"],score,correct,total,q["difficulty"],q["chapter"],q["topic"]))
    c.commit(); c.close()
    session["last_result"]={"score":score,"correct":correct,"total":total}
    session.pop("quiz",None)
    return redirect(url_for("result"))

@app.route("/result")
@login_required
def result():
    r=session.get("last_result")
    if not r: return redirect(url_for("dashboard"))
    return render_template("result.html", result=r)

init_db()
if __name__ == "__main__":
    app.run(debug=True)
