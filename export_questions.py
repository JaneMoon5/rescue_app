import sqlite3, csv, os

basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'questionnaire.db')
csv_path = os.path.join(basedir, 'questions.csv')

conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute('SELECT section, type, question_text, options, required, sort_order FROM questions ORDER BY sort_order')
rows = c.fetchall()
conn.close()

with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['section', 'type', 'question_text', 'options', 'required', 'sort_order'])
    for row in rows:
        writer.writerow(list(row))

print(f'已将数据库题目导出至 {csv_path}')