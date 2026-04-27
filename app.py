import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

# 外部套件
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar
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
if 'db_stats' not in st.session_state: st.session_state.db_stats = pd.DataFrame() # 🔥 新增：統計表

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
# 3. 超高速資料同步模組 (🔥加入統計表防禦)
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
        
        # 欄位補全
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

# --- 初始化載入 ---
if CURRENT_USER and not st.session_state.data_loaded:
    sync_from_cloud()

# --- 側邊欄與維護 (🔥修正清理邏輯：保存收入) ---
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
            
            # 1. 處理 Sessions (計算被刪除的收入)
            df_sess = st.session_state.db_sess.copy()
            if not df_sess.empty:
                df_sess['temp_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
                # 判定哪些要刪除
                to_delete_mask = (df_sess['temp_dt'] < cutoff) & (pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0) != 0)
                
                # 🔥 計算要刪除的課表總價值
                df_to_del = df_sess[to_delete_mask].copy()
                if not df_to_del.empty:
                    df_to_del['start_dt'] = pd.to_datetime(df_to_del['start_time'])
                    df_to_del['end_dt'] = pd.to_datetime(df_to_del['end_time'])
                    df_to_del['amt'] = ((df_to_del['end_dt'] - df_to_del['start_dt']).dt.total_seconds() / 3600) * pd.to_numeric(df_to_del.get('actual_rate', 0))
                    deleted_revenue = df_to_del['amt'].sum()
                    
                    # 更新 Offset 表
                    current_offset = pd.to_numeric(st.session_state.db_stats.get('cumulative_offset', pd.Series([0]))).iloc[0]
                    new_stats = pd.DataFrame([{'cumulative_offset': current_offset + deleted_revenue}])
                    st.session_state.db_stats = new_stats
                    push_to_cloud("stats", new_stats)
                
                # 執行刪除
                st.session_state.db_sess = df_sess[~to_delete_mask].drop(columns=['temp_dt'])
                push_to_cloud("sessions", st.session_state.db_sess)
                
            st.toast("🧹 清理完成，歷史收入已安全結轉！", icon="🛡️")
            st.rerun()

# --- 日曆操作 (省略，同前版本) ---
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

# --- Tab 1: 概況 (🔥修正總收入計算) ---
with tab1:
    st.subheader("📊 營收儀表板")
    df_sess = st.session_state.db_sess.copy()
    df_stu = st.session_state.db_stu.copy()
    
    # 讀取歷史結轉金額
    hist_offset = pd.to_numeric(st.session_state.db_stats.get('cumulative_offset', pd.Series([0]))).iloc[0]
    
    if not df_sess.empty:
        df_sess['start_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')
        df_sess['actual_rate'] = pd.to_numeric(df_sess.get('actual_rate', 0), errors='coerce').fillna(0)
        df_sess['amount'] = ((df_sess['end_dt'] - df_sess['start_dt']).dt.total_seconds() / 3600) * df_sess['actual_rate']
        
        df_sess['safe_invoice_id'] = pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
        current_time = datetime.now()
        
        # 計算指標
        this_month_income = df_sess[df_sess['start_dt'].dt.month == current_time.month]['amount'].sum()
        pending_income = df_sess[(df_sess['end_dt'] < current_time) & (df_sess['safe_invoice_id'] == 0)]['amount'].sum()
        
        # 🔥 總收入 = 現有的 + 歷史結轉的
        total_income = df_sess['amount'].sum() + hist_offset
        
        col1, col2, col3 = st.columns(3)
        col1.metric("本月預估", f"${int(this_month_income):,}")
        col2.metric("待結算", f"${int(pending_income):,}")
        col3.metric("歷史總收入", f"${int(total_income):,}", help="包含已清理的歷史紀錄收入")

        st.divider()

        # 圖表製作
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
        # 即使沒有現有資料，依然要顯示歷史總收入
        col1, col2, col3 = st.columns(3)
        col1.metric("本 month 預估", "$0")
        col2.metric("待結算", "$0")
        col3.metric("歷史總收入", f"${int(hist_offset):,}")
        st.info("目前沒有現有課程資料，圖表將在排課後顯示。")

# --- 其餘 Tab (Tab 2, 3, 4) 保持跟上一個版本完全一致即可 ---
# [此處省略 Tab 2, 3, 4 的代碼以節省空間，請沿用你目前檔案中的代碼]
