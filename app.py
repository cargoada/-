import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time

# 外部套件
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 系統核心設定
# ==========================================
TARGET_CALENDAR_ID = 'cargoada@gmail.com' 

st.set_page_config(page_title="家教排課系統 v2.7", page_icon="📅", layout="centered")

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
except:
    pass

# ==========================================
# 2. 身分驗證與登入
# ==========================================
if 'current_user' not in st.session_state: st.session_state.current_user = None

if st.session_state.current_user is None:
    st.title("👋 歡迎使用排課系統 2.7")
    if "users" in st.secrets:
        user_dict = st.secrets["users"]
        selected_login = st.selectbox("請選擇您的身分", list(user_dict.keys()))
        if st.button("🚀 進入系統", type="primary", use_container_width=True):
            st.session_state.current_user = selected_login
            st.rerun()
    else:
        st.error("❌ Secrets 設定檔找不到 [users] 區塊")
    st.stop()

# 載入當前使用者雲端網址
CURRENT_USER = st.session_state.current_user
CURRENT_SHEET_URL = st.secrets["users"][CURRENT_USER]

conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 3. 點對點無快取讀寫模組 
# ==========================================
def get_cloud_data(worksheet_name):
    schemas = {
        "sessions": ['id', 'student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'google_event_id', 'progress', 'invoice_id'],
        "invoices": ['id', 'student_id', 'total_amount', 'created_at', 'is_paid', 'note'],
        "students": ['id', 'name', 'default_rate', 'color'],
        "stats": ['cumulative_offset']
    }
    req_cols = schemas.get(worksheet_name, [])
    
    try:
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=0)
        if df is None: df = pd.DataFrame()
        
        if df.empty:
            return pd.DataFrame(columns=req_cols)

        for col in req_cols:
            if col not in df.columns:
                df[col] = ""
                
        if 'id' in df.columns: df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        if 'invoice_id' in df.columns: df['invoice_id'] = pd.to_numeric(df['invoice_id'], errors='coerce').fillna(0).astype(int)
        if 'actual_rate' in df.columns: df['actual_rate'] = pd.to_numeric(df['actual_rate'], errors='coerce').fillna(0).astype(int)
        if 'is_paid' in df.columns: df['is_paid'] = pd.to_numeric(df['is_paid'], errors='coerce').fillna(0).astype(int)
        if 'cumulative_offset' in df.columns: df['cumulative_offset'] = pd.to_numeric(df['cumulative_offset'], errors='coerce').fillna(0)

        return df.copy()
    except Exception:
        return pd.DataFrame(columns=req_cols)

def save_to_cloud(worksheet_name, df):
    df_clean = df.astype(object).fillna("")
    try:
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df_clean)
        st.cache_data.clear() 
    except Exception as e:
        st.error(f"雲端寫入失敗，請檢查網路: {e}")

# --- Google 日曆串接 ---
def create_google_event(title, start_dt, end_dt):
    if service is None: return ""
    try:
        event = service.events().insert(calendarId=TARGET_CALENDAR_ID, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return event.get('id', "")
    except Exception as e:
        st.toast(f"⚠️ Google 日曆新增失敗: {e}", icon="❌")
        return ""

def update_google_event(event_id, title, start_dt, end_dt):
    if service is None or not event_id: return False
    try:
        service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=event_id, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return True
    except Exception as e:
        st.toast(f"⚠️ Google 日曆更新失敗: {e}", icon="❌")
        return False

def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=event_id).execute()
        return True
    except Exception as e:
        st.toast(f"⚠️ Google 日曆刪除失敗: {e}", icon="❌")
        return False


# ==========================================
# 4. 主程式資料狀態初始化
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況中心", "📅 課表排程", "💰 帳單中心", "🧑‍🎓 學生戰情"])

if 'initialized' not in st.session_state:
    st.session_state.initialized = False
    st.session_state.df_stu = pd.DataFrame()
    st.session_state.df_sess = pd.DataFrame()
    st.session_state.df_inv = pd.DataFrame()
    st.session_state.df_stats = pd.DataFrame()

if not st.session_state.initialized:
    with st.spinner("⚡ 正在從雲端安全載入所有資料庫..."):
        st.session_state.df_stu = get_cloud_data("students")
        st.session_state.df_sess = get_cloud_data("sessions")
        st.session_state.df_inv = get_cloud_data("invoices")
        st.session_state.df_stats = get_cloud_data("stats")
        st.session_state.initialized = True

# 建立雙向名字/ID 識別字典
student_name_map = {}
student_rate_map = {}
student_color_map = {}
student_name_to_id = {}

if not st.session_state.df_stu.empty and 'id' in st.session_state.df_stu.columns:
    for _, r in st.session_state.df_stu.iterrows():
        s_id = str(r['id']).split('.')[0]
        s_name = str(r.get('name', '')).strip()
        student_name_map[s_id] = s_name
        student_color_map[s_id] = r.get('color', '#3498DB')
        if s_name:
            student_name_to_id[s_name] = s_id
        try: student_rate_map[s_id] = int(r.get('default_rate', 500))
        except: student_rate_map[s_id] = 500

with st.sidebar:
    st.header(f"👤 老師：{CURRENT_USER}")
    
    if st.button("🔄 同步/刷新雲端資料", use_container_width=True):
        st.session_state.initialized = False
        st.rerun()
        
    if st.button("🚪 登出系統", use_container_width=True):
        st.session_state.current_user = None
        st.session_state.initialized = False
        st.rerun()

# ==========================================
# TAB 1: 概況中心 (🔥 大改版：快速完課、秒殺停課、彈出調課)
# ==========================================
with tab1:
    st.subheader("📊 營收動態與行程追蹤")
    
    with st.container(border=True):
        st.markdown("### 📅 老師小日曆")
        cal_date = st.date_input("點擊下方日曆切換日期，可查看當天課表：", datetime.now(), label_visibility="collapsed")
        
        if not st.session_state.df_sess.empty:
            df_cal_check = st.session_state.df_sess.copy()
            df_cal_check['start_dt_safe'] = pd.to_datetime(df_cal_check['start_time'], errors='coerce')
            df_cal_check = df_cal_check.dropna(subset=['start_dt_safe'])
            
            target_date_str = cal_date.strftime('%Y-%m-%d')
            # 這裡不預先排除狀態，讓當天所有的預約狀態一併秀出
            df_today_lessons = df_cal_check[df_cal_check['start_dt_safe'].dt.strftime('%Y-%m-%d') == target_date_str]
            df_today_lessons = df_today_lessons.sort_values('start_dt_safe')
            
            st.markdown(f"**🔍 {cal_date.strftime('%m/%d')} 當日課表明細：**")
            if not df_today_lessons.empty:
                for _, l_row in df_today_lessons.iterrows():
                    raw_sid = str(l_row['student_id']).strip().split('.')[0]
                    s_id_str = student_name_to_id.get(raw_sid, raw_sid)
                    l_name = student_name_map.get(s_id_str, "未知學生")
                    l_color = student_color_map.get(s_id_str, "#3498DB")
                    
                    s_time = l_row['start_dt_safe'].strftime('%H:%M')
                    curr_status = l_row.get('status', '已預約')
                    
                    # 互動式快捷打卡與調課面板
                    with st.container(border=True):
                        c_info, c_check, c_cancel, c_resched = st.columns([3.5, 1.8, 1.8, 1.8])
                        with c_info:
                            st.markdown(f"▶️ <span style='color:{l_color};'>●</span> **{s_time}** │ 🧑‍🎓 **{l_name}**", unsafe_allow_html=True)
                            if l_row.get('progress', ""):
                                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;🏷 *備註：{l_row['progress']}")
                        
                        # 1. ✅ 一鍵完課勾選
                        with c_check:
                            is_done = st.checkbox("✅ 完課", value=(curr_status == '已完成'), key=f"done_{l_row['id']}")
                            if is_done and curr_status != '已完成':
                                st.session_state.df_sess.loc[st.session_state.df_sess['id']==l_row['id'], 'status'] = '已完成'
                                save_to_cloud("sessions", st.session_state.df_sess)
                                st.rerun()
                            elif not is_done and curr_status == '宿舍' or curr_status == '已完成':
                                st.session_state.df_sess.loc[st.session_state.df_sess['id']==l_row['id'], 'status'] = '已預約'
                                save_to_cloud("sessions", st.session_state.df_sess)
                                st.rerun()
                                
                        # 2. ❌ 直接停課就直接刪除！
                        with c_cancel:
                            if st.button("❌ 停課", key=f"cancel_{l_row['id']}", help="直接刪除此堂課，並連動刪除 Google 日曆"):
                                with st.spinner("正在刪除課程..."):
                                    gid = l_row.get('google_event_id', "")
                                    if gid:
                                        delete_google_event(gid)
                                    # 直接從狀態中抹除該行，不留任何痕跡
                                    st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id'] != l_row['id']]
                                    save_to_cloud("sessions", st.session_state.df_sess)
                                    time.sleep(0.3)
                                st.rerun()
                                
                        # 3. 📅 旁邊出現快速調課小視窗
                        with c_resched:
                            with st.popover("📅 調課"):
                                with st.form(key=f"pop_re_{l_row['id']}"):
                                    quick_date = st.date_input("選擇新日期", l_row['start_dt_safe'].date())
                                    quick_time = st.time_input("選擇新時間", l_row['start_dt_safe'].time())
                                    
                                    # 計算原本時數
                                    old_dur_hours = 1.5
                                    try:
                                        if 'end_time' in l_row and pd.notna(l_row['end_time']):
                                            old_dur_hours = (pd.to_datetime(l_row['end_time']) - l_row['start_dt_safe']).total_seconds() / 3600
                                    except:
                                        pass
                                        
                                    if st.form_submit_button("💾 確定改期"):
                                        with st.spinner("正在同步更改日曆與雲端..."):
                                            new_start_dt = datetime.combine(quick_date, quick_time)
                                            new_end_dt = new_start_dt + timedelta(hours=old_dur_hours)
                                            
                                            st.session_state.df_sess.loc[st.session_state.df_sess['id'] == l_row['id'], 'start_time'] = new_start_dt.strftime('%Y-%m-%dT%H:%M:%S')
                                            st.session_state.df_sess.loc[st.session_state.df_sess['id'] == l_row['id'], 'end_time'] = new_end_dt.strftime('%Y-%m-%dT%H:%M:%S')
                                            
                                            # 連動修改 Google 日曆
                                            gid = l_row.get('google_event_id', "")
                                            if gid:
                                                update_google_event(gid, f"家教: {l_name}", new_start_dt, new_end_dt)
                                                
                                            save_to_cloud("sessions", st.session_state.df_sess)
                                        st.toast("📅 快速調課完成！")
                                        time.sleep(0.4)
                                        st.rerun()
            else:
                st.write("🏝️ 這天目前沒有排課，好好休息一下吧！")
        else:
            st.write("尚無排課資料。")
            
    st.divider()
    
    hist_offset = 0
    if not st.session_state.df_stats.empty and 'cumulative_offset' in st.session_state.df_stats.columns:
        try: hist_offset = float(st.session_state.df_stats['cumulative_offset'].iloc[0])
        except: pass
        
    if not st.session_state.df_sess.empty and 'start_time' in st.session_state.df_sess.columns:
        df_calc = st.session_state.df_sess.copy()
        df_calc['start_dt'] = pd.to_datetime(df_calc['start_time'], errors='coerce')
        df_calc['end_dt'] = pd.to_datetime(df_calc['end_time'], errors='coerce')
        df_calc = df_calc.dropna(subset=['start_dt'])
        df_calc['end_dt'] = df_calc['end_dt'].fillna(df_calc['start_dt'] + timedelta(hours=1.5))
        df_calc = df_calc[~df_calc['status'].isin(['請假', '已取消'])]
        
        if not df_calc.empty:
            df_calc['rate_safe'] = pd.to_numeric(df_calc.get('actual_rate', 0), errors='coerce').fillna(0)
            df_calc['invoice_id_safe'] = pd.to_numeric(df_calc.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
            df_calc['hours'] = (df_calc['end_dt'] - df_calc['start_dt']).dt.total_seconds() / 3600
            df_calc['amount'] = df_calc['hours'] * df_calc['rate_safe']
            
            now = datetime.now()
            this_month_amt = df_calc[df_calc['start_dt'].dt.month == now.month]['amount'].sum()
            pending_amt = df_calc[(df_calc['end_dt'] < now) & (df_calc['invoice_id_safe'] == 0)]['amount'].sum()
            total_amt = df_calc['amount'].sum() + hist_offset
            
            c1, col2, col3 = st.columns(3)
            c1.metric("本月預估收入", f"${int(this_month_amt):,}")
            col2.metric("未結算金額", f"${int(pending_amt):,}")
            col3.metric("歷史總收入", f"${int(total_amt):,}")
            
            st.divider()
            st.subheader("📈 營收圖表分析")
            df_calc['month_str'] = df_calc['start_dt'].dt.strftime('%Y-%m')
            st.bar_chart(df_calc.groupby('month_str')['amount'].sum(), color="#3498DB")
        else: st.info("目前尚無有效的計費課程紀錄")
    else:
        st.columns(3)[2].metric("歷史總收入", f"${int(hist_offset):,}")
        st.info("尚無現有課程資料")

# ==========================================
# TAB 2: 課表排程
# ==========================================
with tab2:
    if st.session_state.df_stu.empty:
        st.warning("⚠️ 請先前往「🧑‍🎓 學生戰情」建立學生資料，才能開始排課喔！")
    else:
        # --- 單堂建立 ---
        with st.expander("➕ 快速建立【單堂】課程"):
            with st.form("new_lesson_form"):
                c1, c2 = st.columns(2)
                choose_stu_id = c1.selectbox("選擇上課學生", list(student_name_map.keys()), format_func=lambda x: student_name_map[x], key="single_stu")
                choose_date = c2.date_input("上課日期", datetime.now(), key="single_date")
                
                c3, c4 = st.columns(2)
                choose_time = c3.time_input("開始時間", datetime.now().replace(minute=0, second=0), key="single_time")
                choose_dur = c4.slider("課程時數 (小時)", 0.5, 4.0, 1.5, 0.5, key="single_dur")
                
                do_sync_gcal = st.checkbox("🔄 同步到 Google 日曆", value=False, key="single_sync")
                lesson_prog = st.text_area("預定授課進度 / 備註", key="single_note")
                
                if st.form_submit_button("✅ 確認建立單堂排課", type="primary"):
                    target_rate = student_rate_map.get(choose_stu_id, 500)
                    start_datetime = datetime.combine(choose_date, choose_time)
                    end_datetime = start_datetime + timedelta(hours=choose_dur)
                    
                    g_id = ""
                    if do_sync_gcal:
                        g_id = create_google_event(f"家教: {student_name_map[choose_stu_id]}", start_datetime, end_datetime)
                    
                    max_id = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty and 'id' in st.session_state.df_sess.columns else 0
                    new_lesson = pd.DataFrame([{
                        'id': max_id + 1, 'student_id': choose_stu_id,
                        'start_time': start_datetime.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_datetime.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': '已預約', 'actual_rate': target_rate,
                        'google_event_id': g_id, 'progress': lesson_prog, 'invoice_id': 0
                    }])
                    
                    st.session_state.df_sess = pd.concat([st.session_state.df_sess, new_lesson], ignore_index=True)
                    save_to_cloud("sessions", st.session_state.df_sess)
                    st.toast("🎉 單堂課程建立成功！")
                    st.rerun()

        # --- 大範圍批量建立 ---
        with st.expander("📅 批量建立【大範圍區間】課程 (例如：暑假 7/1 - 8/31)"):
            with st.form("range_lesson_form"):
                st.markdown("##### ⚙️ 區間與學生設定")
                r_stu_id = st.selectbox("選擇上課學生", list(student_name_map.keys()), format_func=lambda x: student_name_map[x], key="range_stu")
                
                col_start, col_end = st.columns(2)
                r_start_date = col_start.date_input("📆 區間開始日期", datetime.now())
                r_end_date = col_end.date_input("📆 區間結束日期", datetime.now() + timedelta(days=60))
                
                st.divider()
                st.markdown("##### ⏰ 每週固定上課時間 (可複選多個時段)")
                
                weekdays_map = {"星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6}
                selected_days = st.multiselect("選擇每週上課天", list(weekdays_map.keys()), default=["星期一"])
                
                col_t1, col_t2 = st.columns(2)
                r_time = col_t1.time_input("⏰ 上課開始時間", datetime.now().replace(hour=14, minute=0, second=0))
                r_dur = col_t2.slider("課程時數 (小時)", 0.5, 4.0, 2.0, 0.5, key="range_dur")
                
                r_sync_gcal = st.checkbox("🔄 同時將所有產生的課程同步到 Google 日曆", value=False)
                r_note = st.text_input("備註 (例如：暑期常規課)")
                
                if st.form_submit_button("🚀 啟動大範圍自動排課", type="primary"):
                    if not selected_days:
                        st.error("❌ 請至少選擇一個每週上課天")
                    elif r_start_date > r_end_date:
                        st.error("❌ 開始日期不能大於結束日期喔！")
                    else:
                        target_rate = student_rate_map.get(r_stu_id, 500)
                        target_wday_nums = [weekdays_map[d] for d in selected_days]
                        
                        current_date = r_start_date
                        new_lessons_bulk = []
                        max_id_bulk = int(st.session_state.df_sess['id'].max()) if not st.session_sta
