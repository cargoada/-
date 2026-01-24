import streamlit as st
import pandas as pd
import time
from datetime import datetime, timedelta
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- 頁面設定 ---
st.set_page_config(page_title="超級家教系統 (多人版)", page_icon="🏫", layout="centered")

# --- CSS 美化 ---
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 12px; height: 3em; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 🟢 初始化 Session State (記錄誰登入了) ---
if 'current_user' not in st.session_state:
    st.session_state.current_user = None

# ==========================================
# 🚪 登入畫面 (如果還沒登入，就只顯示這裡)
# ==========================================
if not st.session_state.current_user:
    st.title("🏫 歡迎使用家教系統")
    st.info("請選擇你的身份登入")

    # 從 secrets 讀取使用者名單
    users_config = st.secrets["users"]
    user_keys = [k for k in users_config.keys() if k != "admin_password"]
    user_names = [users_config[k]["name"] for k in user_keys]

    with st.form("login_form"):
        selected_name = st.selectbox("你是誰？", user_names)
        password = st.text_input("輸入密碼", type="password")
        submitted = st.form_submit_button("登入")

        if submitted:
            if password == users_config["admin_password"]:
                # 找出對應的 key (例如 'jiong' 或 'friend')
                selected_key = user_keys[user_names.index(selected_name)]
                st.session_state.current_user = users_config[selected_key]
                st.toast(f"歡迎回來，{selected_name}！", icon="👋")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("密碼錯誤！")

    st.stop()  # ⛔ 停止執行下面的程式碼，直到登入成功

# ==========================================
# 👇 以下是登入後才會執行的主程式
# ==========================================

# 取得當前使用者的專屬設定
USER_CONFIG = st.session_state.current_user
CURRENT_SHEET_URL = USER_CONFIG["sheet_url"]
CURRENT_CALENDAR_ID = USER_CONFIG["calendar_id"]

st.title(f"☁️ {USER_CONFIG['name']}的家教系統")

if st.button("登出", type="secondary"):
    st.session_state.current_user = None
    st.rerun()

# --- 🟢 資料庫連線 (GSheets) ---
conn = st.connection("gsheets", type=GSheetsConnection)


# --- 🟢 Google 日曆連線 ---
def get_calendar_service():
    try:
        creds_info = st.secrets["connections"]["gsheets"]
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        return None


def create_google_event(title, start_dt, end_dt):
    service = get_calendar_service()
    if not service: return None
    event = {
        'summary': title,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Taipei'},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Taipei'},
    }
    try:
        # 使用當前登入者的 Calendar ID
        event = service.events().insert(calendarId=CURRENT_CALENDAR_ID, body=event).execute()
        return event.get('id')
    except:
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    service = get_calendar_service()
    if not service or not event_id: return
    try:
        event = service.events().get(calendarId=CURRENT_CALENDAR_ID, eventId=event_id).execute()
        event['summary'] = title
        event['start']['dateTime'] = start_dt.isoformat()
        event['end']['dateTime'] = end_dt.isoformat()
        service.events().update(calendarId=CURRENT_CALENDAR_ID, eventId=event_id, body=event).execute()
    except:
        pass


def delete_google_event(event_id):
    service = get_calendar_service()
    if not service or not event_id: return
    try:
        service.events().delete(calendarId=CURRENT_CALENDAR_ID, eventId=event_id).execute()
    except:
        pass


# --- 資料庫操作 (關鍵：要傳入 spreadsheet 參數) ---
def get_data(worksheet_name):
    # 移除 try...except，這樣如果有錯，螢幕會直接顯示紅字告訴我們原因
    # 或是保留但加入 st.error
    try:
        # 👇 這裡改成了 ttl=5
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=5)

        # 欄位型別轉換 (保持不變)
        if worksheet_name == 'students':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        elif worksheet_name == 'sessions':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['invoice_id'] = pd.to_numeric(df['invoice_id'], errors='coerce').astype('Int64')
            if 'google_event_id' not in df.columns: df['google_event_id'] = ""
        elif worksheet_name == 'invoices':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
        return df
    except Exception as e:
        # 👇 讓錯誤顯示出來，這樣我們才知道發生什麼事 (如果是 Quota exceeded 就是請求太多次)
        st.warning(f"讀取 {worksheet_name} 時遇到連線問題 (若是頻率限制請稍等)：{e}")
        return pd.DataFrame()


def update_data(worksheet_name, df):
    # ⚠️ 關鍵修正：寫入時也要指定 URL
    conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df)


def get_next_id(df):
    if df.empty: return 1
    return int(df['id'].max()) + 1


# --- 初始化 ---
if 'edit_session_id' not in st.session_state:
    st.session_state.edit_session_id = None

# --- 分頁 ---
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# (以下內容與之前相同，但所有 get_data/update_data 都會自動使用上面的 URL 設定)
# 為了篇幅，我保留核心邏輯，直接貼上整合好的部分：

# ==========================================
# Tab 1: 🏠 概況
# ==========================================
with tab1:
    st.subheader("📊 本月速覽")
    try:
        df_sess = get_data("sessions")
        pending = df_sess[
            (df_sess['status'] == '已完成') & ((df_sess['invoice_id'].isna()) | (df_sess['invoice_id'] == 0))]
        count = len(pending)
        total_income = 0
        for _, row in pending.iterrows():
            try:
                s = pd.to_datetime(row['start_time'])
                e = pd.to_datetime(row['end_time'])
                h = (e - s).total_seconds() / 3600
                total_income += h * row['actual_rate']
            except:
                pass
        c1, c2 = st.columns(2)
        c1.metric("待結算堂數", f"{count}", delta="堂", delta_color="off")
        c2.metric("待收學費", f"${int(total_income):,}")
    except:
        st.write("連線中...")

# ==========================================
# Tab 2: 📅 排課
# ==========================================
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯課程")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]
        if not row.empty:
            row = row.iloc[0]
            s_dt = pd.to_datetime(row['start_time'])
            e_dt = pd.to_datetime(row['end_time'])
            current_sid = int(row['student_id'])
            s_name = df_stu[df_stu['id'] == current_sid]['name'].values[0] if current_sid in df_stu['id'].values else ""

            with st.container(border=True):
                c1, c2 = st.columns(2)
                edit_stu = c1.selectbox("學生", list(student_map.keys()),
                                        index=list(student_map.keys()).index(s_name) if s_name in student_map else 0)
                edit_date = c2.date_input("日期", s_dt.date())
                c3, c4 = st.columns(2)
                edit_time = c3.time_input("時間", s_dt.time())
                old_dur = (e_dt - s_dt).total_seconds() / 3600
                edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)

                new_start = datetime.combine(edit_date, edit_time)
                new_end = new_start + timedelta(hours=edit_dur)

                if st.button("💾 儲存修改", type="primary"):
                    new_sid = student_map[edit_stu]
                    rate = int(df_stu[df_stu['id'] == new_sid]['default_rate'].values[0])
                    status = '已完成' if new_start < datetime.now() else '已預約'

                    idx = df_sess[df_sess['id'] == edit_id].index
                    df_sess.loc[idx, 'student_id'] = new_sid
                    df_sess.loc[idx, 'start_time'] = new_start.strftime('%Y-%m-%dT%H:%M:%S')
                    df_sess.loc[idx, 'end_time'] = new_end.strftime('%Y-%m-%dT%H:%M:%S')
                    df_sess.loc[idx, 'status'] = status
                    df_sess.loc[idx, 'actual_rate'] = rate

                    g_event_id = row['google_event_id'] if 'google_event_id' in row and pd.notna(
                        row['google_event_id']) else None
                    if g_event_id: update_google_event(g_event_id, f"家教: {edit_stu}", new_start, new_end)

                    update_data("sessions", df_sess)
                    st.session_state.edit_session_id = None
                    st.rerun()
                if st.button("❌ 取消"):
                    st.session_state.edit_session_id = None
                    st.rerun()
    else:
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())
                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                if st.button("✅ 確認新增", type="primary"):
                    start_p = datetime.combine(d_input, t_input)
                    end_p = start_p + timedelta(hours=dur)
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                    status = '已完成' if start_p < datetime.now() else '已預約'

                    g_event_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    new_id = get_next_id(df_sess)
                    new_row = pd.DataFrame([{
                        'id': new_id, 'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': status, 'actual_rate': rate, 'invoice_id': None,
                        'google_event_id': g_event_id
                    }])

                    df_sess = pd.concat([df_sess, new_row], ignore_index=True)
                    update_data("sessions", df_sess)
                    st.rerun()
            else:
                st.warning("請先新增學生")

    st.divider()
    st.subheader("🗓️ 課程行事曆")
    if not df_sess.empty and not df_stu.empty:
        merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
        events = [{"title": r['name'], "start": r['start_time'], "end": r['end_time'], "backgroundColor": r['color']}
                  for _, r in merged.iterrows()]
        calendar(events=events, options={
            "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
            "initialView": "dayGridMonth", "timeZone": "local", "locale": "zh-tw"})

    with st.expander("📋 詳細列表 / 編輯 / 刪除"):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                10)
            for _, row in df_display.iterrows():
                sess_id = row['id_x']
                with st.container(border=True):
                    c1, c2, c3 = st.columns([5, 1.5, 1.5])
                    c1.markdown(f"**{row['name']}** - {pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')}")
                    if c2.button("✏️", key=f"ed_{sess_id}"):
                        st.session_state.edit_session_id = sess_id
                        st.rerun()
                    if c3.button("🗑️", key=f"del_{sess_id}"):
                        if 'google_event_id' in row and pd.notna(row['google_event_id']): delete_google_event(
                            row['google_event_id'])
                        df_sess = df_sess[df_sess['id'] != sess_id]
                        update_data("sessions", df_sess)
                        st.rerun()

# ==========================================
# Tab 3: 💰 帳單
# ==========================================
with tab3:
    st.subheader("💰 帳單中心")
    df_sess = get_data("sessions")
    df_inv = get_data("invoices")

    with st.expander("⚡ 生成帳單"):
        if st.button("⚡ 一鍵結算"):
            pending_mask = (df_sess['status'] == '已完成') & (
                        (df_sess['invoice_id'].isna()) | (df_sess['invoice_id'] == 0))
            pending_sids = df_sess[pending_mask]['student_id'].unique()

            if len(pending_sids) > 0:
                for sid in pending_sids:
                    s_mask = (df_sess['student_id'] == sid) & pending_mask
                    my_sessions = df_sess[s_mask]
                    total = 0
                    for _, r in my_sessions.iterrows():
                        s = pd.to_datetime(r['start_time'])
                        e = pd.to_datetime(r['end_time'])
                        total += ((e - s).total_seconds() / 3600) * r['actual_rate']

                    inv_id = get_next_id(df_inv)
                    if not df_inv.empty:
                        unpaid_mask = (df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)
                        if unpaid_mask.any():
                            target = df_inv[unpaid_mask].iloc[0]
                            inv_id = target['id']
                            df_inv.loc[df_inv['id'] == inv_id, 'total_amount'] += int(total)
                        else:
                            new_inv = pd.DataFrame([{'id': inv_id, 'student_id': sid, 'total_amount': int(total),
                                                     'created_at': datetime.now().isoformat(), 'is_paid': 0}])
                            df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                    else:
                        new_inv = pd.DataFrame([{'id': inv_id, 'student_id': sid, 'total_amount': int(total),
                                                 'created_at': datetime.now().isoformat(), 'is_paid': 0}])
                        df_inv = pd.concat([df_inv, new_inv], ignore_index=True)

                    df_sess.loc[s_mask, 'invoice_id'] = inv_id

                update_data("invoices", df_inv)
                update_data("sessions", df_sess)
                st.success("完成！")
                st.rerun()
            else:
                st.warning("無資料")

    st.divider()
    if not df_inv.empty:
        df_unpaid = df_inv[df_inv['is_paid'] == 0]
        if not df_unpaid.empty:
            df_unpaid = pd.merge(df_unpaid, df_stu, left_on='student_id', right_on='id')
            for _, row in df_unpaid.iterrows():
                inv_id = row['id_x']
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1.5])
                    c1.markdown(f"**{row['name']}** - ${row['total_amount']:,}")
                    if c2.button("收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        update_data("invoices", df_inv)
                        st.rerun()

# ==========================================
# Tab 4: 🧑‍🎓 學生
# ==========================================
with tab4:
    df_stu = get_data("students")
    with st.expander("➕ 新增學生"):
        c1, c2 = st.columns(2)
        n_name = c1.text_input("姓名")
        n_rate = c2.number_input("時薪", 500, step=50)
        c_name = st.selectbox("顏色", ["🔴", "🔵", "🟢", "🟠"])
        if st.button("新增"):
            new_id = get_next_id(df_stu)
            colors = {"🔴": "#FF5733", "🔵": "#3498DB", "🟢": "#2ECC71", "🟠": "#FFC300"}
            new_row = pd.DataFrame([{'id': new_id, 'name': n_name, 'parent_contact': "", 'default_rate': int(n_rate),
                                     'color': colors[c_name]}])
            df_stu = pd.concat([df_stu, new_row], ignore_index=True)
            update_data("students", df_stu)
            st.rerun()

    if not df_stu.empty:
        for _, row in df_stu.iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['name']}** (${row['default_rate']}/hr)")