import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time

# 外部套件 (🔥 已徹底拔除容易當機的 streamlit_calendar)
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 系統設定
# ==========================================
TARGET_CALENDAR_ID = 'cargoada@gmail.com' 

st.set_page_config(page_title="家教排課系統", page_icon="📅", layout="centered")

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]

# --- 啟動 Google 日曆機器人 ---
service = None
try:
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = dict(st.secrets["connections"]["gsheets"])
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
except Exception as e:
    print(f"Google 日曆連線失敗: {e}")

# ==========================================
# 2. 登入系統與「本地記憶體」初始化
# ==========================================
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'edit_session_id' not in st.session_state: st.session_state.edit_session_id = None
if 'data_loaded' not in st.session_state: st.session_state.data_loaded = False
if 'db_stu' not in st.session_state: st.session_state.db_stu = pd.DataFrame()
if 'db_sess' not in st.session_state: st.session_state.db_sess = pd.DataFrame()
if 'db_inv' not in st.session_state: st.session_state.db_inv = pd.DataFrame()
if 'db_stats' not in st.session_state: st.session_state.db_stats = pd.DataFrame()

# 顯示登入畫面
if st.session_state.current_user is None:
    st.title("👋 歡迎使用排課系統")
    st.markdown("請先選擇您的身分以載入資料：")
    try:
        if "users" in st.secrets:
            user_dict = st.secrets["users"]
            col1, col2 = st.columns([3, 1])
            with col1: selected_login = st.selectbox("請選擇身分", list(user_dict.keys()), label_visibility="collapsed")
            with col2:
                if st.button("🚀 進入系統", type="primary"):
                    st.session_state.current_user = selected_login
                    st.rerun()
        else: st.error("❌ Secrets 設定檔找不到 [users] 區塊")
    except Exception as e: st.error(f"讀取使用者失敗: {e}")
    st.stop()

# 載入使用者設定
try:
    CURRENT_USER = st.session_state.current_user
    CURRENT_SHEET_URL = st.secrets["users"][CURRENT_USER]
except:
    st.session_state.current_user = None
    st.rerun()

conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 3. 超高速資料同步模組 
# ==========================================
def fetch_sheet_safe(worksheet_name):
    try:
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=0)
        
        if df is None or df.empty:
            if worksheet_name == "sessions":
                return pd.DataFrame(columns=['id', 'student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'google_event_id', 'progress', 'invoice_id'])
            elif worksheet_name == "invoices":
                return pd.DataFrame(columns=['id', 'student_id', 'total_amount', 'created_at', 'is_paid', 'note'])
            elif worksheet_name == "students":
                return pd.DataFrame(columns=['id', 'name', 'default_rate', 'color'])
            elif worksheet_name == "stats":
                return pd.DataFrame([{'cumulative_offset': 0}])
            return pd.DataFrame()

        if 'id' in df.columns: df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        
        if worksheet_name == "sessions":
            for col in ['google_event_id', 'progress', 'invoice_id']:
                if col not in df.columns: df[col] = "" if col != 'invoice_id' else 0
        elif worksheet_name == "invoices":
            for col in ['note', 'created_at', 'is_paid']:
                if col not in df.columns: df[col] = "" if col != 'is_paid' else 0
        elif worksheet_name == "stats":
            if 'cumulative_offset' not in df.columns: df['cumulative_offset'] = 0
            
        return df
    except: return pd.DataFrame()

def sync_from_cloud():
    with st.spinner("⚡ 正在同步雲端資料..."):
        st.session_state.db_stu = fetch_sheet_safe("students")
        st.session_state.db_sess = fetch_sheet_safe("sessions")
        st.session_state.db_inv = fetch_sheet_safe("invoices")
        st.session_state.db_stats = fetch_sheet_safe("stats")
        st.session_state.data_loaded = True
        st.toast("✅ 資料同步完成！", icon="🚀")

def push_to_cloud(worksheet_name, df):
    df_clean = df.astype(object).fillna("")
    try:
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df_clean)
        st.cache_data.clear()
    except Exception as e:
        st.toast(f"⚠️ 雲端同步延遲: {e}")

if CURRENT_USER and not st.session_state.data_loaded:
    sync_from_cloud()

# --- 側邊欄與維護 ---
with st.sidebar:
    st.header(f"👤 您好，{CURRENT_USER}")
    st.caption(f"日曆同步中：{TARGET_CALENDAR_ID}")
    
    if st.button("🔄 強制重新載入資料", use_container_width=True):
        sync_from_cloud()
        st.rerun()
        
    if st.button("🚪 登出系統", use_container_width=True):
        st.session_state.current_user = None
        st.session_state.data_loaded = False
        st.rerun()

    st.divider()
    st.subheader("🧹 系統維護")
    with st.expander("🗑️ 清理歷史資料"):
        months_to_keep = st.number_input("保留最近幾個月的資料？", min_value=1, max_value=24, value=6)
        st.warning("刪除後的課表將無法在列表中查看，但「總收入」會被保留。")
        if st.button("⚠️ 確認刪除並結轉收入", type="primary"):
            cutoff = datetime.now() - timedelta(days=months_to_keep * 30)
            
            df_sess = st.session_state.db_sess.copy()
            if not df_sess.empty:
                df_sess['temp_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
                to_delete_mask = (df_sess['temp_dt'] < cutoff) & (pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0) != 0)
                
                df_to_del = df_sess[to_delete_mask].copy()
                if not df_to_del.empty:
                    df_to_del['start_dt'] = pd.to_datetime(df_to_del['start_time'], errors='coerce')
                    df_to_del['end_dt'] = pd.to_datetime(df_to_del['end_time'], errors='coerce')
                    df_to_del = df_to_del.dropna(subset=['start_dt', 'end_dt'])
                    
                    df_to_del['amt'] = ((df_to_del['end_dt'] - df_to_del['start_dt']).dt.total_seconds() / 3600) * pd.to_numeric(df_to_del.get('actual_rate', 0))
                    deleted_revenue = df_to_del['amt'].sum()
                    
                    current_offset = pd.to_numeric(st.session_state.db_stats.get('cumulative_offset', pd.Series([0]))).iloc[0]
                    new_stats = pd.DataFrame([{'cumulative_offset': current_offset + deleted_revenue}])
                    st.session_state.db_stats = new_stats
                    push_to_cloud("stats", new_stats)
                
                st.session_state.db_sess = df_sess[~to_delete_mask].drop(columns=['temp_dt'])
                push_to_cloud("sessions", st.session_state.db_sess)
                
            st.toast("🧹 清理完成，歷史收入已安全結轉！", icon="🛡️")
            st.rerun()

# --- 日曆操作 ---
def create_google_event(title, start_dt, end_dt):
    if service is None: return None
    try:
        event = service.events().insert(calendarId=TARGET_CALENDAR_ID, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return event.get('id')
    except: return None

def update_google_event(event_id, title, start_dt, end_dt):
    if service is None or not event_id: return False
    try:
        service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=event_id, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return True
    except: return False

def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=event_id).execute()
        return True
    except: return False

# ==========================================
# 4. 主程式分頁
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# ================= Tab 1: 概況 =================
with tab1:
    st.subheader("📊 營收儀表板")
    df_sess = st.session_state.db_sess.copy()
    df_stu = st.session_state.db_stu.copy()
    hist_offset = pd.to_numeric(st.session_state.db_stats.get('cumulative_offset', pd.Series([0]))).iloc[0]
    
    if not df_sess.empty:
        df_sess['start_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')
        df_sess = df_sess.dropna(subset=['start_dt', 'end_dt']) 
        
        df_sess['actual_rate'] = pd.to_numeric(df_sess.get('actual_rate', 0), errors='coerce').fillna(0)
        df_sess['amount'] = ((df_sess['end_dt'] - df_sess['start_dt']).dt.total_seconds() / 3600) * df_sess['actual_rate']
        
        df_sess['safe_invoice_id'] = pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
        current_time = datetime.now()
        
        this_month_income = df_sess[df_sess['start_dt'].dt.month == current_time.month]['amount'].sum()
        pending_income = df_sess[(df_sess['end_dt'] < current_time) & (df_sess['safe_invoice_id'] == 0)]['amount'].sum()
        total_income = df_sess['amount'].sum() + hist_offset
        
        col1, col2, col3 = st.columns(3)
        col1.metric("本月預估", f"${int(this_month_income):,}")
        col2.metric("待結算", f"${int(pending_income):,}")
        col3.metric("歷史總收入", f"${int(total_income):,}", help="包含已清理的歷史紀錄收入")

        st.divider()

        if not df_stu.empty:
            chart_df = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', how='left')
            chart_df['name'] = chart_df.get('name', pd.Series("未知", index=chart_df.index)).fillna("未知")
        else:
            chart_df = df_sess.copy()
            chart_df['name'] = "未知"

        st.subheader("📈 月營收趨勢 (現有資料)")
        chart_df['month_str'] = chart_df['start_dt'].dt.strftime('%Y-%m')
        st.bar_chart(chart_df.groupby('month_str')['amount'].sum(), color="#3498DB")

        st.subheader("🏆 學生營收貢獻 (現有資料)")
        st.bar_chart(chart_df.groupby('name')['amount'].sum().sort_values(ascending=False), horizontal=True, color="#FF5733")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("本月預估", "$0")
        col2.metric("待結算", "$0")
        col3.metric("歷史總收入", f"${int(hist_offset):,}")
        st.info("目前沒有現有課程資料，圖表將在排課後顯示。")

# ================= Tab 2: 排課 (🔥 大改版：原生看板取代日曆) =================
with tab2:
    df_stu = st.session_state.db_stu.copy()
    df_sess = st.session_state.db_sess.copy()
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯或刪除課程")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]
        
        if not row.empty:
            row = row.iloc[0]
            s_dt = pd.to_datetime(row.get('start_time'), errors='coerce')
            e_dt = pd.to_datetime(row.get('end_time'), errors='coerce')
            
            if pd.notna(s_dt) and pd.notna(e_dt):
                cur_sid = int(row['student_id'])
                s_name = df_stu[df_stu['id'] == cur_sid]['name'].values[0] if cur_sid in df_stu['id'].values else "未知"
                old_prog = row.get('progress', "")
                gid = row.get('google_event_id', "")
                
                with st.container(border=True):
                    st.info(f"正在編輯：**{s_name}** - {s_dt.strftime('%m/%d %H:%M')}")
                    with st.form(key=f"edit_form_{edit_id}"):
                        c1, c2 = st.columns(2)
                        s_idx = list(student_map.keys()).index(s_name) if s_name in student_map else 0
                        edit_stu = c1.selectbox("學生", list(student_map.keys()), index=s_idx)
                        edit_date = c2.date_input("日期", s_dt.date())
                        c3, c4 = st.columns(2)
                        edit_time = c3.time_input("時間", s_dt.time())
                        old_dur = (e_dt - s_dt).total_seconds() / 3600
                        edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur) if float(old_dur) >= 0.5 else 1.5, 0.5)
                        edit_prog = st.text_area("當日進度", value=old_prog)
                        submit_save = st.form_submit_button("💾 儲存變更", type="primary")

                    col_del, col_cancel = st.columns([1, 1])
                    
                    if col_del.button("🗑️ 刪除此課程", key="btn_del_direct"):
                        if pd.notna(gid) and str(gid) != "" and service: delete_google_event(gid)
                        df_sess = df_sess[df_sess['id'] != edit_id]
                        st.session_state.db_sess = df_sess
                        push_to_cloud("sessions", df_sess)
                        st.session_state.edit_session_id = None
                        st.toast("🗑️ 課程已刪除", icon="✅")
                        st.rerun()

                    if col_cancel.button("❌ 取消返回"):
                        st.session_state.edit_session_id = None
                        st.rerun()

                    if submit_save:
                        new_start = datetime.combine(edit_date, edit_time)
                        new_end = new_start + timedelta(hours=edit_dur)
                        new_sid = student_map[edit_stu]
                        rate = int(df_stu[df_stu['id'] == new_sid]['default_rate'].values[0])
                        
                        idx = df_sess[df_sess['id'] == edit_id].index
                        df_sess.loc[idx, ['student_id', 'start_time', 'end_time', 'actual_rate', 'progress']] = \
                            [new_sid, new_start.strftime('%Y-%m-%dT%H:%M:%S'), new_end.strftime('%Y-%m-%dT%H:%M:%S'), rate, edit_prog]
                        
                        if gid and service: update_google_event(gid, f"家教: {edit_stu}", new_start, new_end)
                        
                        st.session_state.db_sess = df_sess
                        push_to_cloud("sessions", df_sess)
                        st.session_state.edit_session_id = None
                        st.toast("💾 變更已儲存", icon="✅")
                        st.rerun()
            else:
                st.error("此課程日期資料異常，無法編輯。請刪除後重新建立。")
                if st.button("❌ 取消返回"):
                    st.session_state.edit_session_id = None
                    st.rerun()
        else:
            st.session_state.edit_session_id = None
            st.rerun()

    else:
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            is_recurring = st.toggle("🔁 啟用週期性排課", value=False)
            
            with st.form(key="add_form"):
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist() if not df_stu.empty else ["無"])
                d_input = c2.date_input("首堂日期", datetime.now())
                
                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始時間", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)
                
                repeat_type = "單次"
                repeat_count = 1
                
                if is_recurring:
                    st.markdown("---")
                    c_rep1, c_rep2 = st.columns(2)
                    repeat_type = c_rep1.selectbox("重複頻率", ["每週固定", "隔週固定 (雙週)"])
                    repeat_count = c_rep2.number_input("建立幾堂？", min_value=2, max_value=12, value=4)
                
                st.markdown("---")
                do_sync = st.checkbox("🔄 同步至 Google 日曆", value=False)
                n_prog = st.text_area("預定進度")
                
                btn_text = f"✅ 建立 {repeat_count} 堂課程" if is_recurring else "✅ 建立課程"
                add_submit = st.form_submit_button(btn_text, type="primary")

            if add_submit and not df_stu.empty:
                sid = student_map[sel_stu]
                rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                start_base = datetime.combine(d_input, t_input)
                new_rows = []
                loop_count = repeat_count if is_recurring else 1
                
                for i in range(loop_count):
                    offset = timedelta(0)
                    if is_recurring:
                        if repeat_type == "每週固定": offset = timedelta(weeks=i)
                        elif repeat_type == "隔週固定 (雙週)": offset = timedelta(weeks=i*2)
                    
                    current_start = start_base + offset
                    current_end = current_start + timedelta(hours=dur)
                    
                    g_id = ""
                    if do_sync and service:
                        g_id = create_google_event(f"家教: {sel_stu}", current_start, current_end)
                    
                    new_rows.append({
                        'id': int(st.session_state.db_sess['id'].max() + len(new_rows) + 1) if not st.session_state.db_sess.empty else len(new_rows) + 1,
                        'student_id': sid,
                        'start_time': current_start.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': current_end.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': '已預約',
                        'actual_rate': rate,
                        'google_event_id': g_id,
                        'progress': n_prog,
                        'invoice_id': 0
                    })
                
                if new_rows:
                    st.session_state.db_sess = pd.concat([df_sess, pd.DataFrame(new_rows)], ignore_index=True)
                    push_to_cloud("sessions", st.session_state.db_sess)
                
                st.toast(f"✅ 成功排定 {loop_count} 堂課！", icon="🎉")
                st.rerun()

    st.divider()
    c_head, c_ref = st.columns([4, 1])
    c_head.subheader("🗓️ 近期課表與總覽 (原生加速版)")
    if c_ref.button("🔄 重整畫面"): 
        st.cache_data.clear()
        st.rerun()
        
    if not df_sess.empty and not df_stu.empty:
        merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
        merged['s_dt_safe'] = pd.to_datetime(merged['start_time'], errors='coerce')
        merged['e_dt_safe'] = pd.to_datetime(merged['end_time'], errors='coerce')
        merged = merged.dropna(subset=['s_dt_safe', 'e_dt_safe'])
        
        tab_up, tab_all = st.tabs(["🚀 未來兩週動態", "📚 完整課表資料庫"])
        
        with tab_up:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            two_weeks = today + timedelta(days=14)
            upcoming = merged[(merged['s_dt_safe'] >= today) & (merged['s_dt_safe'] <= two_weeks)].sort_values('s_dt_safe')
            
            if not upcoming.empty:
                upcoming['date_str'] = upcoming['s_dt_safe'].dt.strftime('%Y-%m-%d')
                grouped = upcoming.groupby('date_str')
                weekdays_tw = ["一", "二", "三", "四", "五", "六", "日"]
                
                for d_str, group in grouped:
                    wd = weekdays_tw[pd.to_datetime(d_str).weekday()]
                    with st.container(border=True):
                        st.markdown(f"**📅 {d_str} (星期{wd})**")
                        for _, r in group.iterrows():
                            sid = int(r['id_x'])
                            s_time = r['s_dt_safe'].strftime('%H:%M')
                            e_time = r['e_dt_safe'].strftime('%H:%M')
                            s_name = r.get('name', '未知')
                            gid = r.get('google_event_id', "")
                            connected = pd.notna(gid) and str(gid) != ""
                            
                            c1, c2, c3, c4 = st.columns([2, 3, 1, 1])
                            c1.markdown(f"`{s_time} - {e_time}`")
                            c2.markdown(f"🧑‍🎓 **{s_name}** {'🔗' if connected else ''}")
                            
                            if c3.button("✏️", key=f"ed_up_{sid}"):
                                st.session_state.edit_session_id = sid
                                st.rerun()
                            if c4.button("🗑️", key=f"del_up_{sid}"):
                                if connected: delete_google_event(gid)
                                st.session_state.db_sess = df_sess[df_sess['id'] != sid]
                                push_to_cloud("sessions", st.session_state.db_sess)
                                st.toast("已刪除！", icon="🗑️")
                                st.rerun()
            else:
                st.info("未來兩週目前沒有排課喔！去放個假吧 🏝️")
                
        with tab_all:
            df_table = merged.sort_values('s_dt_safe', ascending=False).copy()
            df_table['日期'] = df_table['s_dt_safe'].dt.strftime('%Y-%m-%d')
            df_table['時間'] = df_table['s_dt_safe'].dt.strftime('%H:%M') + " - " + df_table['e_dt_safe'].dt.strftime('%H:%M')
            df_table['學生'] = df_table.get('name', '未知')
            df_table['狀態'] = df_table['status']
            df_table['同步日曆'] = df_table['google_event_id'].apply(lambda x: "✅" if pd.notna(x) and x != "" else "❌")
            
            st.dataframe(
                df_table[['日期', '時間', '學生', '狀態', '同步日曆', 'progress']],
                use_container_width=True,
                hide_index=True
            )
    else:
        st.info("目前尚無課程資料")

# ================= Tab 3: 帳單 =================
with tab3:
    st.subheader("💰 帳單中心")
    df_inv = st.session_state.db_inv.copy()
    df_sess = st.session_state.db_sess.copy()
    
    if st.button("⚡ 一鍵結算 (自動分月開單)", type="primary"):
        df_sess['end_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce') 
        df_sess['safe_inv'] = pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
        
        df_sess = df_sess.dropna(subset=['end_dt'])
        
        mask = ((df_sess.get('status', '已完成') == '已完成') | (df_sess['end_dt'] < datetime.now())) & (df_sess['safe_inv'] == 0)
        pending_df = df_sess[mask].copy()
        
        if not pending_df.empty:
            pending_df['month_str'] = pending_df['end_dt'].dt.strftime('%Y-%m')
            groups = pending_df.groupby(['student_id', 'month_str'])
            new_inv_count = 0
            
            for (sid, m_str), group in groups:
                total_amt = sum(((pd.to_datetime(r['end_time'], errors='coerce') - pd.to_datetime(r['start_time'], errors='coerce')).total_seconds() / 3600) * int(r.get('actual_rate', 0)) for _, r in group.iterrows())
                inv_id = int(df_inv['id'].max()) + 1 if not df_inv.empty else 1
                new_inv = pd.DataFrame([{'id': inv_id, 'student_id': sid, 'total_amount': int(total_amt), 'created_at': datetime.now().isoformat(), 'is_paid': 0, 'note': m_str}])
                df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                df_sess.loc[group.index, 'invoice_id'] = inv_id
                new_inv_count += 1
            
            st.session_state.db_inv = df_inv
            st.session_state.db_sess = df_sess
            push_to_cloud("invoices", df_inv)
            push_to_cloud("sessions", df_sess)
            st.toast(f"結算完成！產出 {new_inv_count} 張帳單。", icon="🧾")
            st.rerun()
        else:
            st.info("目前沒有未結算的課程")

    st.divider()
    if not df_inv.empty:
        unpaid = df_inv[df_inv.get('is_paid', 0) == 0]
        if not unpaid.empty:
            df_disp = pd.merge(unpaid, st.session_state.db_stu, left_on='student_id', right_on='id', how='left')
            if 'created_at' in df_disp.columns:
                df_disp['sort_dt'] = pd.to_datetime(df_disp['created_at'], errors='coerce')
                df_disp = df_disp.sort_values('sort_dt', ascending=False)
                
            for _, row in df_disp.iterrows():
                inv_id = row.get('id_x', 0)
                s_name = row.get('name', "未知")
                note_val = row.get('note', "")
                bill_month = str(note_val) if pd.notna(note_val) and str(note_val).strip() != "" else "未知月份"
                total_amt = row.get('total_amount', 0)
                
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{s_name} ({bill_month})**") 
                    c1.caption(f"💰 **${total_amt:,}**")
                    if c2.button("收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        st.session_state.db_inv = df_inv
                        push_to_cloud("invoices", df_inv)
                        st.toast("入帳成功！", icon="💵")
                        st.rerun()
                    
                    with st.expander("💬 產生收費通知 (一鍵複製)"):
                        my_ds = df_sess[pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce') == inv_id].copy()
                        if not my_ds.empty:
                            my_ds['start_dt_safe'] = pd.to_datetime(my_ds['start_time'], errors='coerce')
                            my_ds['end_dt_safe'] = pd.to_datetime(my_ds['end_time'], errors='coerce')
                            my_ds = my_ds.dropna(subset=['start_dt_safe', 'end_dt_safe']).sort_values('start_dt_safe')
                            
                            msg_lines = [f"【{s_name} {bill_month} 課程費用明細】"]
                            msg_lines.append("家長您好，以下是本期課程的費用明細：\n")
                            
                            for _, r in my_ds.iterrows():
                                dt_str = r['start_dt_safe'].strftime('%m/%d')
                                dur_h = (r['end_dt_safe'] - r['start_dt_safe']).total_seconds() / 3600
                                amt = int(dur_h * r.get('actual_rate', 0))
                                msg_lines.append(f"📌 {dt_str} ({dur_h:.1f} 小時) : ${amt:,}")
                                
                            msg_lines.append(f"\n💰 總計金額：${total_amt:,}")
                            msg_lines.append("再麻煩您留意，謝謝！")
                            
                            final_msg = "\n".join(msg_lines)
                            st.caption("👇 點擊區塊右上角的📄圖示，即可一鍵複製傳給家長")
                            st.code(final_msg, language=None)
                        else:
                            st.info("找不到此帳單的課程明細")
        else:
            st.info("🎉 帳單全數結清！")
    else:
        st.info("尚無帳單")

# ================= Tab 4: 學生戰情室 =================
with tab4:
    st.subheader("🧑‍🎓 學生戰情室")
    df_stu = st.session_state.db_stu.copy()
    df_sess = st.session_state.db_sess.copy()
    
    with st.expander("➕ 新增學生"):
        with st.form("add_stu_form"):
            c1, c2 = st.columns(2)
            n = c1.text_input("姓名"); r = c2.number_input("時薪", 500)
            color_opt = st.selectbox("顏色", ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)", "#9B59B6 (紫)"])
            if st.form_submit_button("新增"):
                final_color = color_opt.split(" ")[0]
                new_stu = pd.DataFrame([{'id': int(df_stu['id'].max()+1) if not df_stu.empty else 1, 'name': n, 'default_rate': r, 'color': final_color}])
                st.session_state.db_stu = pd.concat([df_stu, new_stu], ignore_index=True)
                push_to_cloud("students", st.session_state.db_stu)
                st.toast("學生新增成功！", icon="🎓")
                st.rerun()
    
    st.divider()

    if not df_stu.empty and not df_sess.empty:
        full_data = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', how='left')
        
        full_data['start_dt'] = pd.to_datetime(full_data['start_time'], errors='coerce')
        full_data = full_data.dropna(subset=['start_dt']) 
        
        weekdays_tw = ["一", "二", "三", "四", "五", "六", "日"]
        
        for _, row in df_stu.iterrows():
            sid = row['id']
            s_name = row.get('name', '未知')
            my_classes = full_data[full_data['student_id'] == sid].sort_values('start_dt', ascending=False)
            total_count = len(my_classes)
            next_class = my_classes[my_classes['start_dt'] >= datetime.now()].sort_values('start_dt').head(1)
            next_class_str = next_class.iloc[0]['start_dt'].strftime('%m/%d %H:%M') if not next_class.empty else "無待辦課程"
            
            with st.container(border=True):
                c_icon, c_info, c_action = st.columns([0.5, 4, 1.5])
                c_icon.markdown(f'<div style="width:30px;height:30px;background-color:{row.get("color", "#3498DB")};border-radius:50%;margin-top:5px;"></div>', unsafe_allow_html=True)
                with c_info:
                    st.markdown(f"**{s_name}**")
                    st.caption(f"📅 下次上課：{next_class_str} (累計 {total_count} 堂)")
                
                with st.expander("🪄 智慧續排 (一鍵展延下個月)"):
                    if not my_classes.empty:
                        last_dt = my_classes['start_dt'].max()
                        start_of_last_week = last_dt - timedelta(days=6)
                        pattern_classes = my_classes[(my_classes['start_dt'] >= start_of_last_week) & (my_classes['start_dt'] <= last_dt)].sort_values('start_dt')

                        st.write("🔍 **系統偵測到最近的上課模式：**")
                        for _, pc in pattern_classes.iterrows():
                            pc_end = pd.to_datetime(pc['end_time'], errors='coerce')
                            if pd.notna(pc_end):
                                dur_h = (pc_end - pc['start_dt']).total_seconds() / 3600
                                st.info(f"星期{weekdays_tw[pc['start_dt'].weekday()]} {pc['start_dt'].strftime('%H:%M')} ({dur_h:.1f} 小時)")
                        
                        c_ex1, c_ex2 = st.columns(2)
                        extend_weeks = c_ex1.number_input("要往後展延幾週？", min_value=1, max_value=12, value=4, key=f"wk_{sid}")
                        do_sync = c_ex2.checkbox("🔄 同步至 Google 日曆", value=True, key=f"sync_{sid}")

                        if st.button("🚀 確定一鍵展延", key=f"btn_renew_{sid}", type="primary"):
                            with st.spinner("自動排課中..."):
                                new_rows = []
                                rate = row.get('default_rate', 500)
                                
                                for w in range(1, extend_weeks + 1):
                                    for _, pc in pattern_classes.iterrows():
                                        pc_end = pd.to_datetime(pc['end_time'], errors='coerce')
                                        if pd.isna(pc_end): continue
                                        
                                        new_start = pc['start_dt'] + timedelta(weeks=w)
                                        dur = (pc_end - pc['start_dt']).total_seconds() / 3600
                                        new_end = new_start + timedelta(hours=dur)

                                        g_id = ""
                                        if do_sync and service:
                                            g_id = create_google_event(f"家教: {s_name}", new_start, new_end)
                                            time.sleep(0.3)

                                        new_rows.append({
                                            'id': int(st.session_state.db_sess['id'].max() + len(new_rows) + 1) if not st.session_state.db_sess.empty else len(new_rows) + 1,
                                            'student_id': sid,
                                            'start_time': new_start.strftime('%Y-%m-%dT%H:%M:%S'),
                                            'end_time': new_end.strftime('%Y-%m-%dT%H:%M:%S'),
                                            'status': '已預約',
                                            'actual_rate': rate,
                                            'google_event_id': g_id,
                                            'progress': "",
                                            'invoice_id': 0
                                        })

                                if new_rows:
                                    st.session_state.db_sess = pd.concat([st.session_state.db_sess, pd.DataFrame(new_rows)], ignore_index=True)
                                    push_to_cloud("sessions", st.session_state.db_sess)

                                st.toast("✅ 課表展延成功！", icon="🎉")
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.info("該學生尚無上課紀錄，請先在「排課」手動新增一次，系統才能學習模式喔！")

                with st.expander("📝 查看學習歷程 (過去進度)"):
                    if not my_classes.empty:
                        past_classes = my_classes[my_classes['start_dt'] < datetime.now()]
                        if not past_classes.empty:
                            for _, cls in past_classes.iterrows():
                                st.markdown(f"**{cls['start_dt'].strftime('%Y/%m/%d')}**")
                                st.text(cls.get('progress', '（無紀錄）'))
                                st.divider()
                        else: st.info("尚無過去的上課紀錄")
                    else: st.info("尚無課程資料")

                with st.expander("💬 生成 Line 課表通知"):
                    future_classes = my_classes[my_classes['start_dt'] >= datetime.now()].sort_values('start_dt')
                    if not future_classes.empty:
                        msg_lines = [f"【{s_name} 課程預告】\n家長您好，以下是接下來的課程安排：\n"]
                        for _, cls in future_classes.iterrows():
                            msg_lines.append(f"📌 {cls['start_dt'].strftime('%m/%d (%a) %H:%M')}")
                        msg_lines.append(f"\n再請您確認時間，謝謝！")
                        st.caption("👇 點擊區塊右上角的📄圖示，即可一鍵複製")
                        st.code("\n".join(msg_lines), language=None)
                    else: st.warning("沒有未來的課程，無法生成預告。")

                if c_action.button("🗑️", key=f"ds_{sid}", help="刪除此學生"):
                    st.session_state.db_stu = df_stu[df_stu['id']!=sid]
                    push_to_cloud("students", st.session_state.db_stu)
                    st.toast("已移除學生", icon="🗑️")
                    st.rerun()

    elif df_stu.empty:
        st.info("目前沒有學生，請先新增。")
