import streamlit as st
import pandas as pd
import time
from datetime import datetime, timedelta
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- 設定你的日曆 ID (通常是你的 Gmail) ---
# ⚠️ 請一定要修改這裡！
YOUR_CALENDAR_ID = 'cargoada@gmail.com'
# ---------------------------------------

# --- 頁面設定 ---
st.set_page_config(page_title="老師排課小幫手 (雲端+日曆版)", page_icon="☁️", layout="centered")

# --- CSS 美化 ---
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 12px; height: 3em; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

st.title("☁️ 天才超級家教系統")

# --- 🟢 資料庫連線 (GSheets) ---
conn = st.connection("gsheets", type=GSheetsConnection)


# --- 🟢 Google 日曆連線設定 ---
def get_calendar_service():
    """建立 Google Calendar API 連線"""
    try:
        # 從 Streamlit secrets 讀取憑證
        creds_info = st.secrets["connections"]["gsheets"]
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        st.error(f"日曆連線失敗: {e}")
        return None


# --- 日曆操作函式 ---
def create_google_event(title, start_dt, end_dt, description=""):
    """在 Google 日曆建立活動"""
    service = get_calendar_service()
    if not service: return None

    event = {
        'summary': title,
        'description': description,
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': 'Asia/Taipei',
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': 'Asia/Taipei',
        },
    }
    try:
        event = service.events().insert(calendarId=YOUR_CALENDAR_ID, body=event).execute()
        return event.get('id')
    except Exception as e:
        st.error(f"無法寫入 Google 日曆: {e}")
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    """更新 Google 日曆活動"""
    service = get_calendar_service()
    if not service or not event_id: return

    try:
        event = service.events().get(calendarId=YOUR_CALENDAR_ID, eventId=event_id).execute()
        event['summary'] = title
        event['start']['dateTime'] = start_dt.isoformat()
        event['end']['dateTime'] = end_dt.isoformat()
        service.events().update(calendarId=YOUR_CALENDAR_ID, eventId=event_id, body=event).execute()
    except Exception as e:
        st.warning(f"更新日曆失敗 (可能已被刪除): {e}")


def delete_google_event(event_id):
    """刪除 Google 日曆活動"""
    service = get_calendar_service()
    if not service or not event_id: return
    try:
        service.events().delete(calendarId=YOUR_CALENDAR_ID, eventId=event_id).execute()
    except Exception as e:
        st.warning(f"刪除日曆失敗: {e}")


# --- 資料庫操作函式 ---
def get_data(worksheet_name):
    try:
        df = conn.read(worksheet=worksheet_name, ttl=0)
        if worksheet_name == 'students':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        elif worksheet_name == 'sessions':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['invoice_id'] = pd.to_numeric(df['invoice_id'], errors='coerce').astype('Int64')
            # 確保有 google_event_id 欄位
            if 'google_event_id' not in df.columns:
                df['google_event_id'] = ""
        elif worksheet_name == 'invoices':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
        return df
    except Exception as e:
        # st.error(f"讀取 {worksheet_name} 失敗: {e}")
        return pd.DataFrame()


def update_data(worksheet_name, df):
    conn.update(worksheet=worksheet_name, data=df)


def get_next_id(df):
    if df.empty: return 1
    return int(df['id'].max()) + 1


# --- 初始化 Session State ---
if 'edit_session_id' not in st.session_state:
    st.session_state.edit_session_id = None

# --- 導航分頁 ---
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

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
        st.write("正在連接雲端...")
    st.divider()
    st.info(
        f"📅 你的 Google 日曆同步狀態：{'已設定' if YOUR_CALENDAR_ID != '你的Gmail信箱@gmail.com' else '⚠️ 請修改程式碼中的 YOUR_CALENDAR_ID'}")

# ==========================================
# Tab 2: 📅 排課 (同步日曆版)
# ==========================================
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # --- 編輯模式 ---
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
            s_index = list(student_map.keys()).index(s_name) if s_name in student_map else 0

            with st.container(border=True):
                c1, c2 = st.columns(2)
                edit_stu = c1.selectbox("學生", list(student_map.keys()), index=s_index)
                edit_date = c2.date_input("日期", s_dt.date())
                c3, c4 = st.columns(2)
                edit_time = c3.time_input("時間", s_dt.time())
                old_dur = (e_dt - s_dt).total_seconds() / 3600
                edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)

                new_start = datetime.combine(edit_date, edit_time)
                new_end = new_start + timedelta(hours=edit_dur)
                st.caption(f"變更後：{new_start.strftime('%Y/%m/%d %H:%M')} ~ {new_end.strftime('%H:%M')}")

                col_save, col_cancel = st.columns([1, 1])
                with col_save:
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

                        # 同步更新 Google 日曆
                        g_event_id = row['google_event_id'] if 'google_event_id' in row and pd.notna(
                            row['google_event_id']) else None
                        if g_event_id:
                            update_google_event(g_event_id, f"家教: {edit_stu}", new_start, new_end)

                        update_data("sessions", df_sess)
                        st.session_state.edit_session_id = None
                        st.toast("修改成功！", icon="✅")
                        st.rerun()
                with col_cancel:
                    if st.button("❌ 取消"):
                        st.session_state.edit_session_id = None
                        st.rerun()
    else:
        # --- 新增模式 ---
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())
                c3, c4 = st.columns(2)
                now_rounded = datetime.now().replace(minute=0, second=0, microsecond=0)
                t_input = c3.time_input("開始", now_rounded)
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                start_p = datetime.combine(d_input, t_input)
                end_p = start_p + timedelta(hours=dur)
                st.info(f"🕒 {start_p.strftime('%H:%M')} ~ {end_p.strftime('%H:%M')}")

                if st.button("✅ 確認新增", type="primary"):
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                    status = '已完成' if start_p < datetime.now() else '已預約'

                    # 1. 寫入 Google 日曆，並取得 event ID
                    g_event_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    # 2. 寫入資料庫
                    new_id = get_next_id(df_sess)
                    new_row = pd.DataFrame([{
                        'id': new_id, 'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': status, 'actual_rate': rate, 'invoice_id': None,
                        'google_event_id': g_event_id  # 存下來！
                    }])

                    df_sess = pd.concat([df_sess, new_row], ignore_index=True)
                    update_data("sessions", df_sess)
                    st.toast("已同步至 Google 日曆！", icon="📅")
                    st.rerun()
            else:
                st.warning("請先新增學生！")

    st.divider()
    # --- 日曆顯示 ---
    st.subheader("🗓️ 課程行事曆")
    if not df_sess.empty and not df_stu.empty:
        merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', suffixes=('_sess', '_stu'))
        events = []
        for _, row in merged.iterrows():
            events.append({
                "title": row['name'], "start": row['start_time'], "end": row['end_time'],
                "backgroundColor": row['color'], "borderColor": row['color']
            })
        calendar_options = {
            "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
            "initialView": "dayGridMonth", "timeZone": "local", "locale": "zh-tw",
        }
        calendar(events=events, options=calendar_options)

    # --- 列表刪除 ---
    with st.expander("📋 詳細列表 / 編輯 / 刪除"):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
            df_display['start_dt'] = pd.to_datetime(df_display['start_time'])
            df_display = df_display.sort_values('start_dt', ascending=False).head(10)

            for _, row in df_display.iterrows():
                sess_id = row['id_x']
                name = row['name']
                t_str = row['start_dt'].strftime('%m/%d %H:%M')
                status = row['status']

                with st.container(border=True):
                    c1, c2, c3 = st.columns([5, 1.5, 1.5])
                    c1.markdown(f"**{name}** - {t_str} ({status})")
                    if c2.button("✏️", key=f"ed_{sess_id}"):
                        st.session_state.edit_session_id = sess_id
                        st.rerun()
                    if c3.button("🗑️", key=f"del_{sess_id}"):
                        # 刪除時同步刪除 Google 日曆活動
                        g_event_id = row['google_event_id'] if 'google_event_id' in row and pd.notna(
                            row['google_event_id']) else None
                        if g_event_id:
                            delete_google_event(g_event_id)

                        df_sess = df_sess[df_sess['id'] != sess_id]
                        update_data("sessions", df_sess)
                        st.toast("已刪除", icon="🗑️")
                        st.rerun()

# ==========================================
# Tab 3: 💰 帳單
# ==========================================
with tab3:
    st.subheader("💰 帳單中心")
    df_sess = get_data("sessions")
    df_inv = get_data("invoices")
    df_stu = get_data("students")

    # 檢查有無漏掉的課
    now_str = datetime.now().isoformat()
    missed_mask = (df_sess['end_time'] < now_str) & (df_sess['status'] == '已預約')
    if missed_mask.any():
        st.warning(f"偵測到 {missed_mask.sum()} 堂過期未完成課程")
        if st.button("✅ 一鍵改為已完成"):
            df_sess.loc[missed_mask, 'status'] = '已完成'
            update_data("sessions", df_sess)
            st.rerun()

    with st.expander("⚡ 生成帳單 (智慧合併)", expanded=True):
        if st.button("⚡ 一鍵結算"):
            pending_mask = (df_sess['status'] == '已完成') & (
                        (df_sess['invoice_id'].isna()) | (df_sess['invoice_id'] == 0))
            pending_sids = df_sess[pending_mask]['student_id'].unique()

            if len(pending_sids) == 0:
                st.warning("沒有需要結算的課程")
            else:
                bar = st.progress(0)
                for idx, sid in enumerate(pending_sids):
                    bar.progress((idx + 1) / len(pending_sids))
                    s_mask = (df_sess['student_id'] == sid) & pending_mask
                    my_sessions = df_sess[s_mask]
                    total = 0
                    for _, r in my_sessions.iterrows():
                        s = pd.to_datetime(r['start_time'])
                        e = pd.to_datetime(r['end_time'])
                        total += ((e - s).total_seconds() / 3600) * r['actual_rate']

                    if not df_inv.empty:
                        unpaid_mask = (df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)
                        if unpaid_mask.any():
                            target_inv = df_inv[unpaid_mask].sort_values('created_at', ascending=False).iloc[0]
                            inv_id = target_inv['id']
                            new_amt = target_inv['total_amount'] + int(total)
                            df_inv.loc[df_inv['id'] == inv_id, 'total_amount'] = new_amt
                            df_inv.loc[df_inv['id'] == inv_id, 'created_at'] = datetime.now().isoformat()
                        else:
                            inv_id = get_next_id(df_inv)
                            new_inv = pd.DataFrame([{
                                'id': inv_id, 'student_id': sid, 'total_amount': int(total),
                                'created_at': datetime.now().isoformat(), 'is_paid': 0
                            }])
                            df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                    else:
                        inv_id = 1
                        new_inv = pd.DataFrame([{
                            'id': 1, 'student_id': sid, 'total_amount': int(total),
                            'created_at': datetime.now().isoformat(), 'is_paid': 0
                        }])
                        df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                    df_sess.loc[s_mask, 'invoice_id'] = inv_id

                update_data("invoices", df_inv)
                update_data("sessions", df_sess)
                st.success("結算完成！")
                st.rerun()

    st.divider()
    st.subheader("💵 待收款")
    if not df_inv.empty:
        df_unpaid = df_inv[df_inv['is_paid'] == 0].copy()
        if not df_unpaid.empty:
            df_unpaid = pd.merge(df_unpaid, df_stu, left_on='student_id', right_on='id')
            for _, row in df_unpaid.iterrows():
                inv_id = row['id_x']
                name = row['name']
                amt = row['total_amount']
                date_str = pd.to_datetime(row['created_at']).strftime('%Y/%m/%d')
                with st.container(border=True):
                    c1, c2, c3 = st.columns([2, 2, 1.5])
                    c1.markdown(f"**{name}** ({date_str})")
                    c2.markdown(f"### ${amt:,}")
                    if c3.button("✅ 收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        update_data("invoices", df_inv)
                        st.toast("已收款！")
                        st.rerun()

# ==========================================
# Tab 4: 🧑‍🎓 學生
# ==========================================
with tab4:
    st.subheader("🧑‍🎓 學生名冊")
    df_stu = get_data("students")

    with st.expander("➕ 新增學生"):
        c1, c2 = st.columns(2)
        n_name = c1.text_input("姓名")
        n_rate = c2.number_input("時薪", 500, step=50)
        n_contact = st.text_input("聯絡方式")
        colors = {"🔴": "#FF5733", "🔵": "#3498DB", "🟢": "#2ECC71", "🟠": "#FFC300"}
        c_name = st.selectbox("顏色", list(colors.keys()))
        if st.button("新增"):
            new_id = get_next_id(df_stu)
            new_row = pd.DataFrame([{
                'id': new_id, 'name': n_name, 'parent_contact': n_contact,
                'default_rate': int(n_rate), 'color': colors[c_name]
            }])
            df_stu = pd.concat([df_stu, new_row], ignore_index=True)
            update_data("students", df_stu)
            st.rerun()

    st.divider()
    if not df_stu.empty:
        for _, row in df_stu.iterrows():
            with st.container(border=True):
                c1, c2, c3 = st.columns([1, 4, 1])
                c1.color_picker("", row['color'], disabled=True, label_visibility="collapsed")
                c2.markdown(f"**{row['name']}** (${row['default_rate']}/hr)")
                if c3.button("🗑️", key=f"del_s_{row['id']}"):
                    update_data("students", df_stu[df_stu['id'] != row['id']])
                    st.rerun()