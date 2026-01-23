import streamlit as st
import sqlite3
import pandas as pd
import time
from datetime import datetime, timedelta, date
from streamlit_calendar import calendar


# --- 🛠️ 針對 Python 3.12+ 的日期修正 ---
def adapt_date_iso(val):
    return val.isoformat()


def adapt_datetime_iso(val):
    return val.isoformat()


sqlite3.register_adapter(date, adapt_date_iso)
sqlite3.register_adapter(datetime, adapt_datetime_iso)
# ------------------------------------------------

# --- 資料庫連線設定 ---
DB_FILE = 'tutor_app.db'


def get_connection():
    return sqlite3.connect(DB_FILE)


# --- 頁面設定 ---
st.set_page_config(page_title="老師排課小幫手", page_icon="🎓", layout="centered")

# --- CSS 美化 ---
st.markdown("""
    <style>
    .stButton>button {
        width: 100%;
        border-radius: 12px;
        height: 3em;
        font-weight: bold;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 初始化 Session State (用來記憶現在是不是在編輯模式) ---
if 'edit_session_id' not in st.session_state:
    st.session_state.edit_session_id = None

st.title("🚀 炯翃的超級家教系統")

# --- 導航分頁 ---
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# ==========================================
# Tab 1: 🏠 首頁概況
# ==========================================
with tab1:
    conn = get_connection()
    st.subheader("📊 本月速覽")

    col1, col2 = st.columns(2)
    try:
        pending_sessions = \
        conn.execute("SELECT COUNT(*) FROM sessions WHERE status='已完成' AND invoice_id IS NULL").fetchone()[0]
        col1.metric("待結算堂數", f"{pending_sessions}", delta="堂", delta_color="off")

        # 統計待收金額
        est_income = conn.execute("""
                                  SELECT SUM((strftime('%s', end_time) - strftime('%s', start_time)) / 3600.0 *
                                             actual_rate)
                                  FROM sessions
                                  WHERE status = '已完成'
                                    AND invoice_id IS NULL
                                  """).fetchone()[0]
        est_income = int(est_income) if est_income else 0
        col2.metric("待收學費", f"${est_income:,}")
    except Exception as e:
        st.error(f"讀取數據錯誤: {e}")

    st.divider()
    st.info("💡 小撇步：在「排課」分頁現在可以直接修改舊課程囉！")
    conn.close()

# ==========================================
# Tab 2: 📅 排課與記錄 (新增編輯功能)
# ==========================================
with tab2:
    conn = get_connection()

    # 先撈取學生名單供選單使用
    students = pd.read_sql("SELECT id, name FROM students", conn)
    student_map = dict(zip(students['name'], students['id'])) if not students.empty else {}

    # --- 判斷是「新增模式」還是「編輯模式」 ---
    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯課程模式")
        st.info("正在修改一堂現有的課程...")

        # 1. 撈取該堂課的舊資料
        edit_id = st.session_state.edit_session_id
        old_data = conn.execute("SELECT student_id, start_time, end_time FROM sessions WHERE id=?",
                                (edit_id,)).fetchone()

        if old_data:
            old_sid, old_start_str, old_end_str = old_data

            # 轉換時間格式
            try:
                if isinstance(old_start_str, str):
                    s_dt = datetime.fromisoformat(old_start_str)
                    e_dt = datetime.fromisoformat(old_end_str)
                else:
                    s_dt, e_dt = old_start_str, old_end_str
            except:
                s_dt = datetime.now()
                e_dt = s_dt + timedelta(hours=1.5)

            # 找出學生名字 (為了預設選中)
            current_student_name = next((k for k, v in student_map.items() if v == old_sid), None)
            student_index = list(student_map.keys()).index(current_student_name) if current_student_name else 0

            # --- 顯示編輯表單 ---
            with st.container(border=True):
                c1, c2 = st.columns(2)
                # 預設選中舊的學生
                edit_student = c1.selectbox("學生", list(student_map.keys()), index=student_index, key="edit_stu")
                # 預設填入舊日期
                edit_date = c2.date_input("日期", s_dt.date(), key="edit_date")

                c3, c4 = st.columns(2)
                # 預設填入舊時間
                edit_time = c3.time_input("時間", s_dt.time(), key="edit_time")
                # 計算舊時數
                old_duration = (e_dt - s_dt).total_seconds() / 3600
                edit_duration = c4.slider("時數", 0.5, 3.0, float(old_duration), 0.5, key="edit_dur")

                # 計算新時間
                new_start = datetime.combine(edit_date, edit_time)
                new_end = new_start + timedelta(hours=edit_duration)

                st.caption(f"變更後：{new_start.strftime('%Y/%m/%d %H:%M')} ~ {new_end.strftime('%H:%M')}")

                col_save, col_cancel = st.columns([1, 1])

                with col_save:
                    if st.button("💾 儲存修改", type="primary"):
                        new_sid = student_map[edit_student]
                        # 重新抓取費率 (假設費率隨學生走)
                        rate = conn.execute("SELECT default_rate FROM students WHERE id=?", (new_sid,)).fetchone()[0]
                        new_status = '已完成' if new_start < datetime.now() else '已預約'

                        conn.execute('''
                                     UPDATE sessions
                                     SET student_id=?,
                                         start_time=?,
                                         end_time=?,
                                         status=?,
                                         actual_rate=?
                                     WHERE id = ?
                                     ''', (new_sid, new_start, new_end, new_status, rate, edit_id))
                        conn.commit()

                        # 清除編輯狀態
                        st.session_state.edit_session_id = None
                        st.toast("修改成功！", icon="✅")
                        time.sleep(0.5)
                        st.rerun()

                with col_cancel:
                    if st.button("❌ 取消"):
                        st.session_state.edit_session_id = None
                        st.rerun()
        else:
            st.error("找不到這堂課的資料！")
            st.session_state.edit_session_id = None
            st.rerun()

    else:
        # --- 標準新增模式 (原本的程式碼) ---
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not students.empty:
                c1, c2 = st.columns(2)
                selected_student = c1.selectbox("選擇學生", students['name'])
                date_input = c2.date_input("日期", datetime.now())

                c3, c4 = st.columns(2)
                now_rounded = datetime.now().replace(minute=0, second=0, microsecond=0)
                time_input = c3.time_input("開始時間", value=now_rounded)
                duration = c4.slider("時數 (小時)", 0.5, 3.0, 1.5, 0.5)

                start_preview = datetime.combine(date_input, time_input)
                end_preview = start_preview + timedelta(hours=duration)

                st.info(
                    f"🕒 確認時間： **{start_preview.strftime('%Y/%m/%d %H:%M')}** ~ **{end_preview.strftime('%H:%M')}**")

                if st.button("✅ 確認新增課程", type="primary"):
                    student_id = student_map[selected_student]
                    rate = conn.execute("SELECT default_rate FROM students WHERE id=?", (student_id,)).fetchone()[0]
                    status = '已完成' if start_preview < datetime.now() else '已預約'

                    conn.execute(
                        'INSERT INTO sessions (student_id, start_time, end_time, status, actual_rate) VALUES (?, ?, ?, ?, ?)',
                        (student_id, start_preview, end_preview, status, rate))
                    conn.commit()
                    st.toast(f"已記錄：{selected_student}", icon="🎉")
                    time.sleep(1)
                    st.rerun()
            else:
                st.warning("⚠️ 請先到「學生」分頁新增學生資料！")

    st.divider()

    # --- 視覺化日曆 ---
    st.subheader("🗓️ 課程行事曆")

    cal_query = '''
                SELECT students.name, sessions.start_time, sessions.end_time, students.color
                FROM sessions
                         JOIN students ON sessions.student_id = students.id \
                '''
    rows = conn.execute(cal_query).fetchall()

    events = []
    for row in rows:
        name, start, end, color = row
        try:
            if isinstance(start, str):
                s_dt = datetime.fromisoformat(start)
                e_dt = datetime.fromisoformat(end)
            else:
                s_dt, e_dt = start, end
            s_str = s_dt.strftime('%Y-%m-%dT%H:%M:%S')
            e_str = e_dt.strftime('%Y-%m-%dT%H:%M:%S')
        except:
            s_str, e_str = str(start), str(end)

        events.append({
            "title": name, "start": s_str, "end": e_str,
            "backgroundColor": color, "borderColor": color, "textColor": "#FFFFFF"
        })

    calendar_options = {
        "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
        "initialView": "dayGridMonth",
        "navLinks": True, "selectable": True, "nowIndicator": True,
        "timeZone": "local", "locale": "zh-tw",
    }
    calendar(events=events, options=calendar_options, custom_css=".fc-event-title { font-weight: bold; }")

    # --- 列表模式 (加入編輯按鈕) ---
    with st.expander("📋 詳細列表 / 編輯 / 刪除", expanded=True):
        query = '''
                SELECT sessions.id, students.name, sessions.start_time, sessions.status
                FROM sessions
                         JOIN students ON sessions.student_id = students.id
                ORDER BY sessions.start_time DESC LIMIT 10 \
                '''
        try:
            sessions_list = conn.execute(query).fetchall()
            for sess in sessions_list:
                sess_id, name, start_time, status = sess
                try:
                    dt = datetime.fromisoformat(start_time)
                except:
                    dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')

                fmt_time = dt.strftime('%m/%d %H:%M')

                with st.container(border=True):
                    # 分割成：文字資訊 | 編輯鈕 | 刪除鈕
                    c1, c2, c3 = st.columns([5, 1.5, 1.5])
                    with c1:
                        st.markdown(f"**{name}**")
                        st.caption(f"{fmt_time} ({status})")

                    with c2:
                        # ✏️ 編輯按鈕
                        if st.button("✏️", key=f"edit_{sess_id}", help="編輯這堂課"):
                            st.session_state.edit_session_id = sess_id
                            st.rerun()  # 重新整理，上面的表單就會變成編輯模式

                    with c3:
                        # 🗑️ 刪除按鈕
                        if st.button("🗑️", key=f"del_{sess_id}", help="刪除這堂課"):
                            conn.execute("DELETE FROM sessions WHERE id=?", (sess_id,))
                            conn.commit()
                            # 如果刪除的剛好是正在編輯的，要清空編輯狀態
                            if st.session_state.edit_session_id == sess_id:
                                st.session_state.edit_session_id = None
                            st.toast("已刪除", icon="🗑️")
                            time.sleep(0.5)
                            st.rerun()
        except Exception as e:
            st.write("尚無資料")

    conn.close()

# ==========================================
# Tab 3: 💰 帳單中心 (智慧合併版)
# ==========================================
with tab3:
    conn = get_connection()
    st.subheader("💰 月底結算與收款")

    # --- 1. 結算按鈕 (加入合併邏輯) ---
    with st.expander("⚡ 生成帳單 (系統會自動合併未付帳單)", expanded=True):
        st.info("💡 說明：如果該學生已有 **未付款** 的帳單，新課程會自動合併進去，不會產生兩張單子喔！")

        if st.button("⚡ 一鍵結算本月學費", type="primary"):
            cursor = conn.cursor()
            # 找出有「已完成」且「未結帳」課程的學生
            cursor.execute("SELECT DISTINCT student_id FROM sessions WHERE status = '已完成' AND invoice_id IS NULL")
            student_ids = [row[0] for row in cursor.fetchall()]

            if not student_ids:
                st.warning("⚠️ 目前沒有需要結算的課程！")
            else:
                progress_text = "正在啟動智慧結算..."
                my_bar = st.progress(0, text=progress_text)
                count_new = 0
                count_merge = 0
                total_students = len(student_ids)

                for index, s_id in enumerate(student_ids):
                    # 取得學生姓名
                    cursor.execute("SELECT name FROM students WHERE id=?", (s_id,))
                    s_name = cursor.fetchone()[0]
                    my_bar.progress((index + 1) / total_students, text=f"正在處理：{s_name}")

                    # 1. 算出新課程的總金額
                    cursor.execute(
                        "SELECT id, start_time, end_time, actual_rate FROM sessions WHERE student_id = ? AND status = '已完成' AND invoice_id IS NULL",
                        (s_id,))
                    sessions = cursor.fetchall()
                    if not sessions: continue

                    current_batch_amount = 0
                    s_ids_update = []

                    for sess in sessions:
                        sid, start, end, rate = sess
                        try:
                            s_dt = datetime.fromisoformat(start)
                            e_dt = datetime.fromisoformat(end)
                        except:
                            s_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                            e_dt = datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
                        h = (e_dt - s_dt).total_seconds() / 3600
                        current_batch_amount += h * rate
                        s_ids_update.append(sid)

                    # 2. 檢查該學生是否有「未付款」的舊帳單
                    cursor.execute(
                        "SELECT id, total_amount FROM invoices WHERE student_id=? AND is_paid=0 ORDER BY created_at DESC LIMIT 1",
                        (s_id,))
                    existing_inv = cursor.fetchone()

                    if existing_inv:
                        # --- A. 合併模式 ---
                        inv_id = existing_inv[0]
                        old_amount = existing_inv[1]
                        new_total = old_amount + int(current_batch_amount)

                        # 更新舊帳單金額 & 更新日期 (讓它浮到最上面)
                        cursor.execute("UPDATE invoices SET total_amount=?, created_at=? WHERE id=?",
                                       (new_total, datetime.now(), inv_id))
                        count_merge += 1
                    else:
                        # --- B. 新增模式 ---
                        cursor.execute("INSERT INTO invoices (student_id, total_amount, created_at) VALUES (?, ?, ?)",
                                       (s_id, int(current_batch_amount), datetime.now()))
                        inv_id = cursor.lastrowid
                        count_new += 1

                    # 3. 將這些課程標記歸屬於該帳單 ID
                    for sid in s_ids_update:
                        cursor.execute("UPDATE sessions SET invoice_id = ? WHERE id = ?", (inv_id, sid))

                    conn.commit()
                    time.sleep(0.1)

                my_bar.empty()
                st.balloons()
                st.success(f"✅ 處理完成！新增 {count_new} 張帳單，合併 {count_merge} 張舊帳單。")
                time.sleep(1.5)
                st.rerun()

    st.divider()

    # --- 2. 待收款帳單 (邏輯不變，但資料會變整齊) ---
    st.subheader("💵 待收款帳單")

    unpaid_invs = conn.execute('''
                               SELECT invoices.id, students.name, invoices.total_amount, invoices.created_at
                               FROM invoices
                                        JOIN students ON invoices.student_id = students.id
                               WHERE invoices.is_paid = 0
                               ORDER BY invoices.created_at DESC
                               ''').fetchall()

    if not unpaid_invs:
        st.success("目前沒有待收款帳單！")
    else:
        for inv in unpaid_invs:
            inv_id, name, amount, created_at = inv
            try:
                date_obj = datetime.fromisoformat(created_at)
                date_str = date_obj.strftime('%Y/%m/%d')
                # 檔名範例：王小明_20260124_學費明細.csv
                csv_filename = f"{name}_{date_obj.strftime('%Y%m%d')}_學費明細.csv"
            except:
                date_str = str(created_at)[:10]
                csv_filename = "billing.csv"

            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 2, 1.5])
                with c1:
                    st.markdown(f"**{name}**")
                    st.caption(f"📅 更新日期：{date_str}")
                with c2:
                    st.markdown(f"### ${amount:,}")
                with c3:
                    if st.button("✅ 收款", key=f"pay_{inv_id}", type="primary"):
                        conn.execute("UPDATE invoices SET is_paid = 1 WHERE id = ?", (inv_id,))
                        conn.commit()
                        st.toast(f"收到 {name} 的款項囉！", icon="💰")
                        time.sleep(0.5)
                        st.rerun()

                # 明細與匯出
                with st.expander("📄 查看明細 / 下載 CSV"):
                    # 撈取該帳單下的「所有」課程 (包含之前合併進來的)
                    details = conn.execute(
                        "SELECT start_time, end_time, actual_rate FROM sessions WHERE invoice_id = ? ORDER BY start_time",
                        (inv_id,)).fetchall()

                    csv_data = []
                    display_data = []

                    for d in details:
                        start, end, rate = d
                        try:
                            s_dt = datetime.fromisoformat(start)
                            e_dt = datetime.fromisoformat(end)
                        except:
                            s_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                            e_dt = datetime.strptime(end, '%Y-%m-%d %H:%M:%S')

                        hours = (e_dt - s_dt).total_seconds() / 3600
                        cost = hours * rate
                        time_range = f"{s_dt.strftime('%H:%M')}~{e_dt.strftime('%H:%M')}"

                        display_data.append(
                            [s_dt.strftime('%m/%d'), time_range, f"{hours}hr", f"${rate}", f"${int(cost)}"])
                        csv_data.append(
                            {"日期": s_dt.strftime('%Y/%m/%d'), "時間": time_range, "時數": hours, "時薪": rate,
                             "小計": int(cost)})

                    st.table(pd.DataFrame(display_data, columns=["日期", "時間", "時數", "時薪", "小計"]))

                    df_csv = pd.DataFrame(csv_data)
                    st.download_button(label="📥 下載 Excel (CSV) 明細",
                                       data=df_csv.to_csv(index=False).encode('utf-8-sig'), file_name=csv_filename,
                                       mime='text/csv', key=f"dl_{inv_id}")

    # --- 3. 歷史記錄 (保持不變) ---
    with st.expander("📂 查看已結案歷史記錄", expanded=False):
        paid_invs = conn.execute(
            "SELECT invoices.id, students.name, invoices.total_amount, invoices.created_at FROM invoices JOIN students ON invoices.student_id = students.id WHERE invoices.is_paid = 1 ORDER BY invoices.created_at DESC").fetchall()
        if paid_invs:
            for inv in paid_invs:
                inv_id, name, amount, created_at = inv
                try:
                    date_str = datetime.fromisoformat(created_at).strftime('%Y/%m/%d')
                except:
                    date_str = str(created_at)[:10]
                with st.expander(f"✅ {date_str} - {name} (${amount:,})"):
                    details = conn.execute(
                        "SELECT start_time, end_time, actual_rate FROM sessions WHERE invoice_id = ? ORDER BY start_time",
                        (inv_id,)).fetchall()
                    if details:
                        rows = []
                        for d in details:
                            start, end, rate = d
                            try:
                                s_dt = datetime.fromisoformat(start)
                                e_dt = datetime.fromisoformat(end)
                            except:
                                s_dt = datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                                e_dt = datetime.strptime(end, '%Y-%m-%d %H:%M:%S')
                            h = (e_dt - s_dt).total_seconds() / 3600
                            rows.append(
                                [s_dt.strftime('%m/%d'), f"{s_dt.strftime('%H:%M')}~{e_dt.strftime('%H:%M')}", f"{h}hr",
                                 f"${int(h * rate)}"])
                        st.table(pd.DataFrame(rows, columns=["日期", "時間", "時數", "小計"]))
        else:
            st.write("查無歷史資料")

    # --- 4. 除錯工具 (保持不變) ---
    with st.expander("🔧 資料除錯與重置", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 重置所有課程為「未結帳」"):
                conn.execute("UPDATE sessions SET invoice_id = NULL")
                conn.execute("DELETE FROM invoices")
                conn.commit()
                st.toast("已重置！", icon="🔄")
                time.sleep(1)
                st.rerun()
        with c2:
            if st.button("✅ 強制將過去課程設為已完成"):
                now_str = datetime.now().isoformat()
                conn.execute(
                    f"UPDATE sessions SET status = '已完成' WHERE start_time < '{now_str}' AND status = '已預約'")
                conn.commit()
                st.toast("狀態已更新！", icon="✅")
                time.sleep(1)
                st.rerun()
    conn.close()
# ==========================================
# Tab 4: 🧑‍🎓 學生管理
# ==========================================
with tab4:
    conn = get_connection()
    st.subheader("🧑‍🎓 學生名冊")

    COLOR_OPTIONS = {
        "🔴 熱情紅": "#FF5733", "🟠 活力橘": "#FFC300", "🟡 快樂黃": "#F1C40F", "🟢 清新綠": "#2ECC71",
        "🔵 穩重藍": "#3498DB", "🟣 優雅紫": "#9B59B6", "🟤 大地棕": "#A0522D", "⚫ 極簡灰": "#34495E"
    }

    with st.expander("➕ 新增一位學生", expanded=False):
        with st.form("add_student_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            new_name = col1.text_input("學生姓名", placeholder="例如：王小明")
            new_rate = col2.number_input("預設時薪", value=500, step=50)
            new_contact = st.text_input("家長聯絡方式")
            selected_color_name = st.selectbox("選擇代表色", list(COLOR_OPTIONS.keys()), index=4)
            new_color = COLOR_OPTIONS[selected_color_name]

            submitted = st.form_submit_button("確認新增")
            if submitted and new_name:
                conn.execute('INSERT INTO students (name, parent_contact, default_rate, color) VALUES (?, ?, ?, ?)',
                             (new_name, new_contact, int(new_rate), new_color))
                conn.commit()
                st.toast(f"🎉 歡迎 {new_name} 加入！", icon="✅")
                time.sleep(1)
                st.rerun()

    st.divider()
    st.caption("目前所有的學生資料：")

    students = conn.execute("SELECT id, name, default_rate, parent_contact, color FROM students").fetchall()

    if not students:
        st.info("目前還沒有學生，趕快新增一位吧！")

    for s in students:
        s_id, name, rate, contact, color = s
        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 4, 1.5])
            with c1:
                st.markdown(
                    f"<div style='width:40px;height:40px;background-color:{color};border-radius:50%;margin-top:10px;'></div>",
                    unsafe_allow_html=True)
            with c2:
                st.markdown(f"**{name}**")
                st.caption(f"💰 ${rate}/hr | 📞 {contact}")
            with c3:
                st.write("")
                if st.button("刪除", key=f"del_stu_{s_id}", type="primary"):
                    conn.execute("DELETE FROM sessions WHERE student_id=?", (s_id,))
                    conn.execute("DELETE FROM invoices WHERE student_id=?", (s_id,))
                    conn.execute("DELETE FROM students WHERE id=?", (s_id,))
                    conn.commit()
                    st.toast(f"已刪除 {name}", icon="👋")
                    time.sleep(1)
                    st.rerun()
    conn.close()