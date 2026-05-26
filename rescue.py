import shutil
import os
import csv
import sqlite3, json, io, uuid
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, Response, make_response


app = Flask(__name__)
# 从环境变量读取密钥，若不存在则使用一个随机生成的字符串（仅用于临时）
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# 管理员凭据也建议从环境变量读取
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin123')  # 强烈建议覆盖此值

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ===== 强制 HTTPS 跳转 =====
@app.before_request
def force_https():
    if request.url.startswith('http://') and not request.host.startswith('127.0.0.1'):
        return redirect(request.url.replace('http://', 'https://', 1), code=301)
# =====================================

# ===== 使用绝对路径确保数据库位于 app.py 同目录 =====
basedir = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(basedir, 'questionnaire.db')
# ===================================================

# ====================== 数据库初始化 ======================
def get_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        # 创建题目表和答案表
        db.execute('''CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT,
            type TEXT,
            question_text TEXT,
            options TEXT,
            required INTEGER DEFAULT 1,
            sort_order INTEGER
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_code TEXT,
            question_id INTEGER,
            answer_value TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            participant_code TEXT,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            ip_address TEXT
        )''')
        # 创建元数据表，用于记录 CSV 导入的时间
        db.execute('''CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')

        # 检查 CSV 文件是否存在，并比较修改时间
        csv_path = os.path.join(basedir, 'questions.csv')
        if not os.path.exists(csv_path):
            print(f"警告：CSV 文件 {csv_path} 不存在，跳过题目更新。")
            db.commit()
            return

        csv_mtime = os.path.getmtime(csv_path)

        # 获取数据库中记录的 CSV 上次导入时间
        cur = db.execute("SELECT value FROM metadata WHERE key = 'csv_last_import'")
        last_import = cur.fetchone()
        last_import_mtime = float(last_import['value']) if last_import else 0

        # 判断是否需要重新导入
        need_import = False
        # 题目表为空，总是需要导入
        question_count = db.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
        if question_count == 0:
            need_import = True
            print("数据库题目表为空，准备从 CSV 导入。")
        elif csv_mtime > last_import_mtime:
            need_import = True
            print(f"检测到 CSV 文件已更新（修改时间：{datetime.fromtimestamp(csv_mtime)}），将重新导入题目。")
            print("⚠️ 注意：这将会清空所有已有的题目和提交的答案！")

        if need_import:
            # ===== 自动备份：如果数据库已有数据，先复制整个文件 =====
            if question_count > 0:
                backup_dir = os.path.join(basedir, 'backups')
                if not os.path.exists(backup_dir):
                    os.makedirs(backup_dir)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_file = os.path.join(backup_dir, f'questionnaire_backup_{timestamp}.db')
                shutil.copy2(DATABASE, backup_file)
                print(f"✅ 已备份数据库至：{backup_file}")
            # ===== 清空现有数据 =====
            db.execute('DELETE FROM responses')
            db.execute('DELETE FROM questions')
            # 重新导入
            insert_default_questions(db)
            # 更新元数据
            db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES ('csv_last_import', ?)",
                (str(csv_mtime),)
            )
            db.commit()
            print("CSV 导入完成。")
        else:
            db.commit()
            print("数据库题目已是最新，无需更新。")

def insert_default_questions(db):
    csv_file = os.path.join(basedir, 'questions.csv')
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            section = row['section']
            qtype = row['type']
            question_text = row['question_text']
            options = row.get('options', '')
            # 容错处理 required 字段，空值默认为 1（必填）
            required_str = row.get('required', '1').strip()
            required = int(required_str) if required_str else 1
            sort_order = int(row.get('sort_order', 0))
            db.execute(
                'INSERT INTO questions (section, type, question_text, options, required, sort_order) VALUES (?,?,?,?,?,?)',
                (section, qtype, question_text, options, required, sort_order)
            )

# ====================== 管理员认证 ======================
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ====================== 前端页面 ======================
@app.route('/', methods=['GET', 'POST'])
def index():
    db = get_db()
    if request.method == 'POST':
        # 获取或生成被试编号
        participant_code = request.cookies.get('participant_code', str(uuid.uuid4()))
        # 保存答案
        questions = db.execute('SELECT id, type FROM questions ORDER BY sort_order').fetchall()
        for q in questions:
            qid = q['id']
            qtype = q['type']
            if qtype in ('radio', 'select', 'slider_0_100', 'slider_1_5', 'slider_0_10', 'slider_1_10', 'text', 'textarea'):
                val = request.form.get(f'q{qid}', '')
                db.execute('INSERT INTO responses (participant_code, question_id, answer_value) VALUES (?,?,?)',
                           (participant_code, qid, val))
        db.commit()

        # 更新会话结束时间
        beijing_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        db.execute('UPDATE sessions SET end_time = ? WHERE participant_code=? AND end_time IS NULL',
                   (beijing_time, participant_code))
        db.commit()

        resp = make_response(redirect(url_for('thankyou')))
        resp.set_cookie('participant_code', participant_code)
        return resp
    else:
        # 获取或创建受试者ID
        participant_code = request.cookies.get('participant_code', str(uuid.uuid4()))
        # 记录新会话（开始时间和IP）
        existing = db.execute('SELECT id FROM sessions WHERE participant_code=?', (participant_code,)).fetchone()
        if not existing:
            ip = request.remote_addr
            beijing_time = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            db.execute('INSERT INTO sessions (participant_code, start_time, ip_address) VALUES (?, ?, ?)',
                       (participant_code, beijing_time, ip))
            db.commit()

        questions_raw = db.execute('SELECT * FROM questions ORDER BY sort_order').fetchall()
        questions = []
        for q in questions_raw:
            q_dict = dict(q)
            # 如果 options 字段存在且为JSON字符串，解析成列表
            if q_dict['options'] and q_dict['type'] in ('radio', 'select'):
                try:
                    q_dict['options_list'] = json.loads(q_dict['options'])
                except:
                    q_dict['options_list'] = []
            else:
                q_dict['options_list'] = []
            questions.append(q_dict)
        # 按 section 分组
        sections = {}
        for q in questions:
            sec = q['section']
            if sec not in sections:
                sections[sec] = []
            sections[sec].append(q)

        # 设置cookie
        resp = make_response(render_template('index.html', sections=sections))
        resp.set_cookie('participant_code', participant_code)
        return resp

@app.route('/thankyou')
def thankyou():
    return render_template('thankyou.html')

# ====================== 管理员 ======================
@app.route('/admin/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and request.form['password'] == ADMIN_PASS:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        else:
            return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')

@app.route('/admin/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin():
    db = get_db()
    question_count = db.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
    response_count = db.execute('SELECT COUNT(DISTINCT participant_code) FROM responses').fetchone()[0]
    session_count = db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
    return render_template('admin.html', question_count=question_count, response_count=response_count, session_count=session_count)

@app.route('/admin/questions')
@login_required
def manage_questions():
    db = get_db()
    questions = db.execute('SELECT * FROM questions ORDER BY sort_order').fetchall()
    return render_template('questions.html', questions=questions)

@app.route('/admin/question/new', methods=['GET', 'POST'])
@login_required
def new_question():
    if request.method == 'POST':
        section = request.form['section']
        qtype = request.form['type']
        text = request.form['question_text']
        options = request.form.get('options', '')
        required = 1 if 'required' in request.form else 0
        sort = int(request.form['sort_order'])
        db = get_db()
        db.execute('INSERT INTO questions (section, type, question_text, options, required, sort_order) VALUES (?,?,?,?,?,?)',
                   (section, qtype, text, options, required, sort))
        db.commit()
        return redirect(url_for('manage_questions'))
    return render_template('edit.html', question=None)

@app.route('/admin/question/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_question(id):
    db = get_db()
    question = db.execute('SELECT * FROM questions WHERE id=?', (id,)).fetchone()
    if request.method == 'POST':
        section = request.form['section']
        qtype = request.form['type']
        text = request.form['question_text']
        options = request.form.get('options', '')
        required = 1 if 'required' in request.form else 0
        sort = int(request.form['sort_order'])
        db.execute('UPDATE questions SET section=?, type=?, question_text=?, options=?, required=?, sort_order=? WHERE id=?',
                   (section, qtype, text, options, required, sort, id))
        db.commit()
        return redirect(url_for('manage_questions'))
    return render_template('edit.html', question=question)

@app.route('/admin/question/<int:id>/delete')
@login_required
def delete_question(id):
    db = get_db()
    db.execute('DELETE FROM questions WHERE id=?', (id,))
    db.commit()
    return redirect(url_for('manage_questions'))

@app.route('/admin/results')
@login_required
def view_results():
    db = get_db()
    # 从 sessions 表获取所有被试的会话信息
    participants = db.execute('''
        SELECT s.participant_code, s.start_time, s.end_time, s.ip_address
        FROM sessions s
        ORDER BY s.start_time DESC
    ''').fetchall()
    return render_template('results.html', participants=participants)

@app.route('/admin/export')
@login_required
def export_csv():
    db = get_db()
    questions = db.execute('SELECT id, question_text FROM questions ORDER BY sort_order').fetchall()
    # 查询所有会话
    sessions = db.execute('SELECT participant_code, start_time, end_time, ip_address FROM sessions ORDER BY start_time').fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    # 标题行：先放会话信息，再放题目
    header = ['participant_code', 'start_time', 'end_time', 'ip_address'] + [q['question_text'] for q in questions]
    writer.writerow(header)

    for sess in sessions:
        row = [sess['participant_code'], sess['start_time'], sess['end_time'] or '', sess['ip_address']]
        for q in questions:
            ans = db.execute('SELECT answer_value FROM responses WHERE participant_code=? AND question_id=?',
                             (sess['participant_code'], q['id'])).fetchone()
            row.append(ans['answer_value'] if ans else '')
        writer.writerow(row)

    output.seek(0)
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=responses_{datetime.now().strftime("%Y%m%d")}.csv'}
    )

# ====================== 启动 ======================
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)