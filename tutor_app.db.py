import sqlite3

def create_database():
    # 連結到資料庫 (如果檔案不存在，會自動建立)
    conn = sqlite3.connect('tutor_app.db')
    cursor = conn.cursor()

    # 1. 建立學生表 (Students)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_contact TEXT,
        default_rate INTEGER DEFAULT 500,
        color TEXT DEFAULT '#3498db'
    )
    ''')

    # 2. 建立課程記錄表 (Sessions)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        start_time DATETIME NOT NULL,
        end_time DATETIME NOT NULL,
        status TEXT DEFAULT '已預約', -- 已預約 / 已完成 / 已取消
        actual_rate INTEGER,
        invoice_id INTEGER,
        FOREIGN KEY (student_id) REFERENCES students (id),
        FOREIGN KEY (invoice_id) REFERENCES invoices (id)
    )
    ''')

    # 3. 建立帳單表 (Invoices)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        period_start DATE,
        period_end DATE,
        total_amount INTEGER DEFAULT 0,
        is_paid BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students (id)
    )
    ''')

    conn.commit()
    conn.close()
    print("✅ 資料庫與所有表格建立成功！(檔名: tutor_app.db)")

# 執行建立
if __name__ == "__main__":
    create_database()