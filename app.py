import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 全域常數與系統設定
# ==========================================
TARGET_CALENDAR_ID = 'cargoada@gmail.com'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']
TW_TZ = ZoneInfo("Asia/Taipei")
WEEKDAYS_TW = ["一", "二", "三", "四", "五", "六", "日"]
STATUS_OPTS = ["已預約", "請假", "已取消", "已完成"]

COLOR_OPTS = [
    "#EF4444 🔴 珊瑚紅",
    "#3B82F6 🔵 寶石藍",
    "#10B981 🟢 翡翠綠"
]

SCHEMAS = {
    "sessions": ['id', 'student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'google_event_id', 'progress', 'invoice_id'],
    "invoices": ['id', 'student_id', 'total_amount', 'created_at', 'is_paid', 'note'],
    "students": ['id', 'name', 'default_rate', 'color'],
    "stats": ['cumulative_offset']
}

st.set_page_config(page_title="家教排課系統 v3.5.1", page_icon="📅", layout="centered")

# ==========================================
# 🎨 注入自訂 CSS 美化樣式 (現代極簡卡片風)
# ==========================================
st.markdown("""
<style>
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 3rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background-color: rgba(128, 128, 128, 0.08);
        padding: 6px;
        border-radius: 14px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px;
        padding: 8px 12px;
        font-weight: 600;
        border: none !important;
        transition: all 0.2s ease;
    }
    .stTabs [aria-selected="true"] {
        background-color: #3B82F6 !important;
        color: white !important;
        box-shadow: 0 4px 10px rgba(59, 130, 246, 0.3);
    }
    div[data-testid="stMetric"] {
        background: rgba(128, 128, 128, 0.04);
        border: 1px solid rgba(128, 128, 128, 0.12);
        padding: 14px 16px;
        border-radius: 16px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
    }
    div[data-testid="stMetricValue"] {
        font-weight: 800 !important;
        color: #3B82F6;
    }
    div[data-testid="stExpander"] {
        border-radius: 14px !important;
        border: 1px solid rgba(128, 128, 128, 0.15) !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
        overflow: hidden;
    }
    .stButton > button {
        border-radius: 10px;
        font-weight: 600;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .stButton > button:active {
        transform: scale(0.97);
    }
    .stu-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
        margin-left: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 核心工具與日曆智慧引擎
# ==========================================
def now_tw() -> datetime:
    """取得台灣標準時間 (無時區標籤)"""
    return datetime.now(TW_TZ).replace(tzinfo=None)

def clean_gid(val) -> str:
    """過濾無效的 Google Event ID"""
    if pd.isna(val): return ""
    s = str(val).strip()
    return "" if s.lower() in ["", "nan", "none", "0", "0.0"] else s

def parse_dt(series: pd.Series) -> pd.Series:
    """萬用時間解析器"""
    s = series.astype(str).str.strip().str.replace('T', ' ', regex=False)
    return pd.to_datetime(s, format='mixed', errors='coerce')

def sanitize_edt(s_series: pd.Series, e_series: pd.Series) -> pd.Series:
    """自動修復異常的結束時間"""
    edt = e_series.fillna(s_series + timedelta(hours=1.5))
    dur_h = (edt - s_series).dt.total_seconds() / 3600.0
    bad_mask = (dur_h <= 0) | (dur_h > 8) | edt.isna()
    return edt.where(~bad_mask, s_series + timedelta(hours=1.5))

def get_gcal_service():
    """🔥 每次操作建立新鮮的 Google Calendar 連線，防止 SSL 憑證閒置斷線"""
    try:
        if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
            creds_dict = dict(st.secrets["connections"]["gsheets"])
            creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            return build('calendar', 'v3', credentials=creds, cache_discovery=False)
    except Exception as e:
        st.toast(f"⚠️ 日曆認證失敗: {e}", icon="⚠️")
    return None

def find_gcal_event(service, title: str, old_s_dt: datetime) -> str:
    """🔍 智慧反查：當試算表遺失 event_id 時，自動用『原時段 + 學生姓名』去日曆找出該筆行程 ID"""
    if service is None or old_s_dt is None or pd.isna(old_s_dt):
        return ""
    try:
        t_min = (old_s_dt - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        t_max = (old_s_dt + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        events_result = service.events().list(
            calendarId=TARGET_CALENDAR_ID,
            timeMin=t_min,
            timeMax=t_max,
            singleEvents=True
        ).execute()
        sname_pure = title.replace("家教:", "").strip()
        for ev in events_result.get('items', []):
            summary = ev.get('summary', '')
            if title in summary or (sname_pure and sname_pure in summary):
                return ev.get('id', "")
    except Exception:
        pass
    return ""

def do_gcal(action: str, title: str = "", s_dt: datetime = None, e_dt: datetime = None, eid: str = "", old_s_dt: datetime = None) -> str:
    """
    🔥 終極日曆同步核心：
    - insert: 新增日曆並回傳新 ID
    - update: 更新日曆（若無 ID 則自動反查原時段；若反查也無，則自動補建新日曆！回傳最終有效 ID）
    - delete: 刪除日曆（若無 ID 則自動反查原時段並刪除）
    """
    service = get_gcal_service()
    eid = clean_gid(eid)
    if service is None:
        return eid

    # 若沒有 eid，啟動智慧反查去日曆裡抓出來
    if not eid and old_s_dt is not None:
        eid = find_gcal_event(service, title, old_s_dt)

    body = {}
    if s_dt is not None and e_dt is not None:
        body = {
            'summary': title,
            'start': {'dateTime': s_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': e_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}
        }

    try:
        if action == "insert":
            ev = service.events().insert(calendarId=TARGET_CALENDAR_ID, body=body).execute()
            return ev.get('id', "")
            
        elif action == "update":
            if eid:
                try:
                    service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=eid, body=body).execute()
                    return eid
                except Exception:
                    # 若原 ID 在日曆上已被手動刪掉或失效，直接在新時段補建一筆！
                    ev = service.events().insert(calendarId=TARGET_CALENDAR_ID, body=body).execute()
                    return ev.get('id', "")
            else:
                # 原本排課沒勾同步，調課時自動幫老師補建進 Google 日曆！
                ev = service.events().insert(calendarId=TARGET_CALENDAR_ID, body=body).execute()
                return ev.get('id', "")
                
        elif action == "delete":
            if eid:
                try:
                    service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=eid).execute()
                except Exception:
                    pass
            return ""
    except Exception as e:
        st.toast(f"⚠️ 日曆同步異常: {e}", icon="⚠️")
    return eid

# ==========================================
# 3. 身分驗證與登入
# ==========================================
if 'current_user' not in st.session_state:
    st.session_state.current_user = None

if st.session_state.current_user is None:
    st.markdown("<h1 style='text-align: center; margin-bottom: 0.2rem;'>📅 家教排課系統</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: gray; margin-bottom: 2rem;'>v3.5.1 日曆雙向追蹤版</p>", unsafe_allow_html=True)
    with st.container(border=True):
        if "users" in st.secrets:
            selected_login = st.selectbox("👤 請選擇您的老師身分", list(st.secrets["users"].keys()))
            st.write("")
            if st.button("🚀 進入管理系統", type="primary", use_container_width=True):
                st.session_state.current_user = selected_login
                st.rerun()
    st.stop()

CURRENT_SHEET_URL = st.secrets["users"][st.session_state.current_user]
conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 4. 雲端讀寫與自動壓實模組
# ==========================================
def get_cloud_data(ws_name: str) -> pd.DataFrame:
    req_cols = SCHEMAS.get(ws_name, [])
    try:
        st.cache_data.clear()
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=ws_name, ttl=0)
        if df is None or df.empty:
            return pd.DataFrame(columns=req_cols)
        
        df = df.dropna(how='all').copy()
        for col in req_cols:
            if col not in df.columns: df[col] = ""
        df = df[req_cols].copy()
        
        if ws_name == "sessions":
            valid_mask = (df['start_time'].astype(str).str.strip().replace('nan', '') != '') & \
                         (df['student_id'].astype(str).str.strip().replace('nan', '') != '')
            df = df[valid_mask].copy()
            df['status'] = df['status'].astype(str).str.strip()
            df['google_event_id'] = df['google_event_id'].apply(clean_gid)
            
        for num_col in ['id', 'invoice_id', 'actual_rate', 'is_paid', 'total_amount', 'default_rate']:
            if num_col in df.columns:
                df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0).astype(int)
        if 'cumulative_offset' in df.columns:
            df['cumulative_offset'] = pd.to_numeric(df['cumulative_offset'], errors='coerce').fillna(0.0)
            
        if 'id' in df.columns and not df.empty:
            if (df['id'] == 0).any() or df['id'].duplicated().any():
                df['id'] = range(1, len(df) + 1)
                
        return df.reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=req_cols)

def save_to_cloud(ws_name: str, df: pd.DataFrame) -> bool:
    req_cols = SCHEMAS.get(ws_name, list(df.columns))
    try:
        df_save = df.dropna(how='all').copy()
        for col in req_cols:
            if col not in df_save.columns: df_save[col] = ""
            
        if ws_name == "sessions" and not df_save.empty:
            valid_mask = df_save['start_time'].astype(str).str.strip().replace('nan', '') != ''
            df_save = df_save[valid_mask].copy()
            df_save['status'] = df_save['status'].astype(str).str.strip()
            df_save['google_event_id'] = df_save['google_event_id'].apply(clean_gid)
            s_dt = parse_dt(df_save['start_time'])
            e_dt = sanitize_edt(s_dt, parse_dt(df_save['end_time']))
            valid_dt = s_dt.notna()
            df_save.loc[valid_dt, 'start_time'] = s_dt[valid_dt].dt.strftime('%Y-%m-%dT%H:%M:%S')
            df_save.loc[valid_dt, 'end_time'] = e_dt[valid_dt].dt.strftime('%Y-%m-%dT%H:%M:%S')
            
        df_save = df_save[req_cols].astype(object).fillna("")
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=ws_name, data=df_save)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"❌ 雲端寫入失敗: {e}")
        return False

# ==========================================
# 5. 狀態初始化與集中式資料預處理流水線
# ==========================================
if 'initialized' not in st.session_state:
    st.session_state.initialized = False
    st.session_state.draft_list = []
    for k in ['df_stu', 'df_sess', 'df_inv', 'df_stats']:
        st.session_state[k] = pd.DataFrame()

if not st.session_state.initialized:
    with st.spinner("⚡ 載入雲端最新資料中..."):
        st.session_state.df_stu = get_cloud_data("students")
        st.session_state.df_sess = get_cloud_data("sessions")
        st.session_state.df_inv = get_cloud_data("invoices")
        st.session_state.df_stats = get_cloud_data("stats")
        st.session_state.initialized = True

name_map, rate_map, color_map, name_to_id = {}, {}, {}, {}
if not st.session_state.df_stu.empty:
    for _, r in st.session_state.df_stu.iterrows():
        sid = str(int(r['id']))
        sn = str(r.get('name', '')).strip()
        name_map[sid] = sn
        color_map[sid] = r.get('color', '#3B82F6')
        name_to_id[sn] = sid
        rate_map[sid] = int(pd.to_numeric(r.get('default_rate', 500), errors='coerce') or 500)

def resolve_sid(raw_val) -> str:
    s = str(raw_val).strip().split('.')[0]
    return name_to_id.get(s, s)

def prepare_sessions_df() -> pd.DataFrame:
    if st.session_state.df_sess.empty:
        return pd.DataFrame()
    df = st.session_state.df_sess.copy()
    df['sdt'] = parse_dt(df['start_time'])
    df['edt'] = sanitize_edt(df['sdt'], parse_dt(df['end_time']))
    df['st_clean'] = df['status'].astype(str).str.strip()
    df['rsid'] = df['student_id'].apply(resolve_sid)
    df['sname'] = df['rsid'].map(lambda x: name_map.get(x, f"未知({x})"))
    df['scolor'] = df['rsid'].map(lambda x: color_map.get(x, "#3B82F6"))
    df['dur_h'] = (df['edt'] - df['sdt']).dt.total_seconds() / 3600.0
    df['rate_num'] = pd.to_numeric(df['actual_rate'], errors='coerce').fillna(0).astype(int)
    df['amt'] = df['dur_h'] * df['rate_num']
    df['inv_num'] = pd.to_numeric(df.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
    return df

df_all_sess = prepare_sessions_df()
df_s = df_all_sess.dropna(subset=['sdt']).copy() if not df_all_sess.empty else pd.DataFrame()

def get_conflicts(new_s: datetime, new_e: datetime, exclude_id: int = None, exclude_draft_idx: int = None) -> list:
    c_list = []
    if not df_s.empty:
        active = df_s[~df_s['st_clean'].isin(['請假', '已取消'])]
        if exclude_id is not None:
            active = active[active['id'] != exclude_id]
        mask = (active['sdt'] < new_e) & (active['edt'] > new_s)
        for _, r in active[mask].iterrows():
            c_list.append(f"{r['sname']}({r['sdt'].strftime('%m/%d %H:%M')})")
            
    for idx, d in enumerate(st.session_state.draft_list):
        if exclude_draft_idx is not None and idx == exclude_draft_idx:
            continue
        if d['sdt'] < new_e and d['edt'] > new_s:
            c_list.append(f"購物車的 {d['sname']}({d['sdt'].strftime('%m/%d %H:%M')})")
    return c_list

# ==========================================
# 側邊欄與課表診斷器
# ==========================================
with st.sidebar:
    st.markdown(f"### 👤 {st.session_state.current_user}")
    st.caption(f"✨ 版本：v3.5.1 │ 🕒 {now_tw().strftime('%m/%d %H:%M')}")
    if st.button("🔄 同步 / 刷新雲端", use_container_width=True, key="side_refresh"):
        st.session_state.initialized = False
        st.rerun()
        
    with st.expander("🕵️ 課表診斷與修復"):
        total_rows = len(df_all_sess)
        st.caption(f"雲端已載入總筆數：`{total_rows}` 筆")
        if total_rows > 0:
            bad_rows = df_all_sess[df_all_sess['sdt'].isna()]
            if not bad_rows.empty:
                st.error(f"⚠️ 發現 {len(bad_rows)} 筆時間格式損壞的舊資料！")
                if st.button("🧹 一鍵清除損壞資料", type="primary", use_container_width=True):
                    clean_df = st.session_state.df_sess.loc[df_all_sess['sdt'].notna()].copy()
                    if save_to_cloud("sessions", clean_df):
                        st.session_state.df_sess = clean_df
                        st.toast("✅ 損壞資料已清除！")
                        time.sleep(0.4)
                        st.rerun()
            else:
                st.success("✅ 所有課程時間格式皆正常！")
                if st.button("🔧 強制重新壓實並校正時數", use_container_width=True):
                    if save_to_cloud("sessions", st.session_state.df_sess):
                        st.session_state.initialized = False
                        st.toast("✅ 雲端表格已重新壓實並校正時數！")
                        time.sleep(0.4)
                        st.rerun()
                        
    st.divider()
    if st.button("🚪 登出系統", use_container_width=True, key="side_logout"):
        st.session_state.current_user = None
        st.session_state.initialized = False
        st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況中心", "📅 課表排程", "💰 帳單中心", "🧑‍🎓 學生戰情"])

# ==========================================
# TAB 1: 概況中心
# ==========================================
with tab1:
    st.markdown("#### 📊 當日行程與打卡")
    with st.container(border=True):
        cal_date = st.date_input("查看當天課表：", now_tw().date(), label_visibility="collapsed", key="t1_cal")
        if not df_s.empty:
            df_td = df_s[(df_s['sdt'].dt.date == cal_date) & (df_s['st_clean'] != '已取消')].sort_values('sdt')
            st.caption(f"📅 **{cal_date.strftime('%Y / %m / %d')} (星期{WEEKDAYS_TW[cal_date.weekday()]}) 課表明細**")
            if not df_td.empty:
                for _, r in df_td.iterrows():
                    c_stt = r['st_clean']
                    scol = r['scolor']
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns([3.6, 1.8, 1.8, 1.8])
                        status_tag = " <span style='color:#EF4444; font-size:0.85rem;'>(請假)</span>" if c_stt == '請假' else ""
                        c1.markdown(
                            f"<div style='display:flex; align-items:center;'>"
                            f"<span style='display:inline-block; width:12px; height:12px; border-radius:50%; background-color:{scol}; margin-right:8px; box-shadow:0 0 6px {scol}88;'></span>"
                            f"<span style='font-size:1.05rem; font-weight:700;'>{r['sdt'].strftime('%H:%M')}</span>"
                            f"<span style='margin:0 6px; color:#ccc;'>│</span>"
                            f"<span style='font-size:1.02rem; font-weight:600;'>{r['sname']}</span>{status_tag}"
                            f"</div>",
                            unsafe_allow_html=True
                        )
                        if r.get('progress'):
                            c1.caption(f"🏷 {r['progress']}")
                        
                        is_done = (c_stt == '已完成')
                        if c2.checkbox("✅ 完課", value=is_done, key=f"t1_ck_{r['id']}") != is_done:
                            st.session_state.df_sess.loc[st.session_state.df_sess['id'] == r['id'], 'status'] = '已完成' if not is_done else '已預約'
                            if save_to_cloud("sessions", st.session_state.df_sess): st.rerun()
                            
                        if c3.button("❌ 停課", key=f"t1_cl_{r['id']}"):
                            # 🔥 傳入 old_s_dt，即使遺失 event_id 也能去日曆反查並刪除
                            do_gcal("delete", title=f"家教: {r['sname']}", eid=r.get('google_event_id', ""), old_s_dt=r['sdt'])
                            st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id'] != r['id']]
                            if save_to_cloud("sessions", st.session_state.df_sess):
                                st.toast("🗑️ 課程與日曆已同步刪除！"); time.sleep(0.3); st.rerun()
                            
                        with c4.popover("📅 調課"):
                            with st.form(f"t1_fm_{r['id']}"):
                                nd = st.date_input("日期", r['sdt'].date())
                                nt = st.time_input("時間", r['sdt'].time())
                                if st.form_submit_button("💾 確定", type="primary"):
                                    ns = datetime.combine(nd, nt)
                                    ne = ns + timedelta(hours=float(r['dur_h']))
                                    cf = get_conflicts(ns, ne, exclude_id=r['id'])
                                    if cf: st.toast(f"⚠️ 調課提醒：與 {', '.join(cf)} 衝突！", icon="⚠️")
                                    
                                    # 🔥 自動更新或補建日曆，並寫回最新 event_id
                                    new_gid = do_gcal("update", title=f"家教: {r['sname']}", s_dt=ns, e_dt=ne, eid=r.get('google_event_id', ""), old_s_dt=r['sdt'])
                                    
                                    idx = st.session_state.df_sess['id'] == r['id']
                                    st.session_state.df_sess.loc[idx, 'start_time'] = ns.strftime('%Y-%m-%dT%H:%M:%S')
                                    st.session_state.df_sess.loc[idx, 'end_time'] = ne.strftime('%Y-%m-%dT%H:%M:%S')
                                    st.session_state.df_sess.loc[idx, 'google_event_id'] = new_gid
                                    
                                    if save_to_cloud("sessions", st.session_state.df_sess):
                                        st.toast("📅 調課與日曆已同步更新！"); time.sleep(0.3); st.rerun()
            else:
                st.info("🏝️ 這天沒有排課，好好休息充電吧！")
        else:
            st.info("🏝️ 尚無排課資料。")

    if not df_s.empty:
        st.write("")
        st.markdown("#### 📈 財務營收總覽")
        dc = df_s[~df_s['st_clean'].isin(['請假', '已取消'])].copy()
        now = now_tw()
        tm = dc[(dc['sdt'].dt.year == now.year) & (dc['sdt'].dt.month == now.month)]['amt'].sum()
        pdn = dc[(dc['edt'] < now) & (dc['inv_num'] == 0)]['amt'].sum()
        offset = float(st.session_state.df_stats['cumulative_offset'].iloc[0]) if not st.session_state.df_stats.empty else 0.0
        hst = dc['amt'].sum() + offset
        
        c1, c2, c3 = st.columns(3)
        c1.metric("本月預估", f"${int(tm):,}")
        c2.metric("未結算學費", f"${int(pdn):,}")
        c3.metric("歷史總收入", f"${int(hst):,}")
        
        st.write("")
        dc['m'] = dc['sdt'].dt.strftime('%Y-%m')
        st.bar_chart(dc.groupby('m')['amt'].sum(), color="#3B82F6")

# ==========================================
# TAB 2: 課表排程
# ==========================================
with tab2:
    if st.session_state.df_stu.empty:
        st.warning("請先至「🧑‍🎓 學生戰情」建立學生檔案！")
    else:
        st.markdown("#### ➕ 新增排課")
        with st.expander("📌 單堂課程 (加入預排)"):
            with st.form("t2_sgl_f", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                sid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x], key="t2_sid")
                dt = c2.date_input("日期", now_tw().date())
                tm = c3.time_input("時間", now_tw().replace(minute=0, second=0).time())
                dur = c4.slider("時數", 0.5, 4.0, 1.5, 0.5)
                # 🔥 預設勾選同步日曆，避免忘記打勾導致後續日曆不連動
                sy, prog = st.checkbox("同步 Google 日曆", value=True, key="t2_sy"), st.text_input("備註 / 預定進度")
                if st.form_submit_button("➕ 加入預排清單", type="primary", use_container_width=True):
                    ns = datetime.combine(dt, tm)
                    ne = ns + timedelta(hours=dur)
                    cf = get_conflicts(ns, ne)
                    if cf: st.toast(f"⚠️ 注意！已加入預排，但與 {', '.join(cf)} 衝突！", icon="⚠️")
                    st.session_state.draft_list.append({'sid': sid, 'sname': name_map[sid], 'sdt': ns, 'edt': ne, 'sy': sy, 'prog': prog, 'rate': rate_map.get(sid, 500)})
                    st.rerun()

        with st.expander("📅 大範圍區間排課 (加入預排)"):
            with st.form("t2_rng_f"):
                c1, c2, c3 = st.columns([2, 1, 1])
                rsid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x], key="t2_rsid")
                sdt = c2.date_input("開始", now_tw().date())
                edt = c3.date_input("結束", now_tw().date() + timedelta(days=60))
                days = st.multiselect("上課星期", WEEKDAYS_TW, default=["一"], key="t2_rdys")
                c4, c5 = st.columns(2)
                rtm = c4.time_input("時間", now_tw().replace(hour=14, minute=0, second=0).time())
                rdur = c5.slider("時數", 0.5, 4.0, 2.0, 0.5, key="t2_rdur")
                rsy, rnote = st.checkbox("同步 Google 日曆", value=True, key="t2_rsy"), st.text_input("區間備註")
                if st.form_submit_button("➕ 批次加入預排清單", type="primary", use_container_width=True):
                    if sdt > edt: st.error("❌ 開始日期不能晚於結束日期！")
                    elif not days: st.error("❌ 請至少選擇一個上課星期！")
                    else:
                        nums = {WEEKDAYS_TW.index(d) for d in days}
                        cur, count, conflict_count = sdt, 0, 0
                        while cur <= edt:
                            if cur.weekday() in nums:
                                ns = datetime.combine(cur, rtm)
                                ne = ns + timedelta(hours=rdur)
                                if get_conflicts(ns, ne): conflict_count += 1
                                st.session_state.draft_list.append({'sid': rsid, 'sname': name_map[rsid], 'sdt': ns, 'edt': ne, 'sy': rsy, 'prog': rnote, 'rate': rate_map.get(rsid, 500)})
                                count += 1
                            cur += timedelta(days=1)
                        if count > 0:
                            msg = f"✅ 已加入 {count} 堂。" + (f" ⚠️ 發現 {conflict_count} 堂衝突！" if conflict_count else "")
                            st.toast(msg, icon="⚠️" if conflict_count else "✅"); st.rerun()
                        else:
                            st.error("❌ 區間無符合天數！")

        if st.session_state.draft_list:
            st.markdown(f"#### 🛒 待送出的排課購物車 (`{len(st.session_state.draft_list)}` 堂)")
            with st.container(border=True):
                for i, d in enumerate(st.session_state.draft_list):
                    c_inf, c_b = st.columns([5.2, 0.8])
                    ss = "🔄" if d['sy'] else ""
                    dur_h = (d['edt'] - d['sdt']).total_seconds() / 3600.0
                    live_cf = get_conflicts(d['sdt'], d['edt'], exclude_draft_idx=i)
                    base_txt = f"**{d['sdt'].strftime('%m/%d (%a) %H:%M')}** ({dur_h:.1f}h) │ 🧑‍🎓 **{d['sname']}** {ss} {d['prog']}"
                    if live_cf: c_inf.warning(f"{base_txt} ⚠️ 與 {', '.join(live_cf)} 衝突！")
                    else: c_inf.info(base_txt)
                    if c_b.button("✖️", key=f"t2_rm_df_{i}", help="移除此堂"):
                        st.session_state.draft_list.pop(i); st.rerun()
                
                cb_send, cb_clear = st.columns([3, 1])
                if cb_clear.button("🗑️ 清空", use_container_width=True):
                    st.session_state.draft_list = []; st.rerun()
                if cb_send.button("🚀 確認送出所有排課", type="primary", use_container_width=True):
                    with st.spinner("安全寫入雲端中..."):
                        mid = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty and 'id' in st.session_state.df_sess.columns else 0
                        n_ls = []
                        for d in st.session_state.draft_list:
                            gid = do_gcal("insert", f"家教: {d['sname']}", d['sdt'], d['edt']) if d['sy'] else ""
                            mid += 1
                            n_ls.append({
                                'id': mid, 'student_id': str(d['sid']),
                                'start_time': d['sdt'].strftime('%Y-%m-%dT%H:%M:%S'),
                                'end_time': d['edt'].strftime('%Y-%m-%dT%H:%M:%S'),
                                'status': '已預約', 'actual_rate': int(d['rate']),
                                'google_event_id': gid, 'progress': d['prog'], 'invoice_id': 0
                            })
                            if d['sy']: time.sleep(0.15)
                        
                        if n_ls:
                            updated_df = pd.concat([st.session_state.df_sess, pd.DataFrame(n_ls)], ignore_index=True)
                            if save_to_cloud("sessions", updated_df):
                                st.session_state.df_sess = updated_df
                                st.session_state.draft_list = []
                                st.toast("🎉 排課完成！"); time.sleep(0.4); st.rerun()

        current_t = now_tw()
        df_future = df_s[(df_s['edt'] > current_t) & (~df_s['st_clean'].isin(['已完成', '已取消']))].sort_values('sdt') if not df_s.empty else pd.DataFrame()

        with st.expander("🧹 批量刪課 (清理未來預約排課)"):
            if not df_future.empty:
                ops = {f"{r['sdt'].strftime('%Y-%m-%d %H:%M')} | {r['sname']} (ID:{r['id']})": r['id'] for _, r in df_future.iterrows()}
                sl = st.multiselect("選取要刪除的未來課程", list(ops.keys()), key="t2_mdel")
                if st.button("🚨 確認批量刪除", type="primary", key="t2_bmdel") and sl:
                    ids = [ops[k] for k in sl]
                    with st.spinner("同步清除雲端與日曆中..."):
                        for i in ids:
                            row_match = df_s[df_s['id'] == i]
                            if not row_match.empty:
                                r_del = row_match.iloc[0]
                                do_gcal("delete", title=f"家教: {r_del['sname']}", eid=r_del.get('google_event_id', ""), old_s_dt=r_del['sdt'])
                        updated_df_del = st.session_state.df_sess[~st.session_state.df_sess['id'].isin(ids)]
                        if save_to_cloud("sessions", updated_df_del):
                            st.session_state.df_sess = updated_df_del
                            st.toast("🗑️ 批量刪課與日曆同步完成！"); time.sleep(0.3); st.rerun()
            else:
                st.info("🎯 目前沒有未來的預約課程需要清理。")

    st.divider()
    st.markdown("#### 📋 未來待上課表調課中心")
    st.caption("僅顯示尚未結束之未來排課，已上完的課程會自動隱藏")
    if not df_future.empty:
        for _, r in df_future.iterrows():
            ico = " ⚠️(請假)" if r['st_clean'] == "請假" else ""
            w_day = WEEKDAYS_TW[r['sdt'].weekday()]
            with st.expander(f"📅 {r['sdt'].strftime('%m/%d')} (週{w_day}) {r['sdt'].strftime('%H:%M')}  │  🧑‍🎓 {r['sname']}{ico}"):
                with st.form(f"t2_ef_{r['id']}"):
                    c_e1, c_e2 = st.columns(2)
                    n_dt = c_e1.date_input("日期", r['sdt'].date())
                    n_st = c_e2.time_input("時間", r['sdt'].time())
                    safe_dur = float(r['dur_h']) if 0.5 <= r['dur_h'] <= 4.0 else 1.5
                    n_dur = st.slider("時數", 0.5, 4.0, safe_dur, 0.5, key=f"t2_dur_{r['id']}")
                    stt_idx = STATUS_OPTS.index(r['st_clean']) if r['st_clean'] in STATUS_OPTS else 0
                    stt = st.selectbox("狀態", STATUS_OPTS, index=stt_idx, key=f"t2_stt_{r['id']}")
                    n_prog = st.text_input("進度 / 備註", value=str(r.get('progress', '')), key=f"t2_prg_{r['id']}")
                    cb1, cb2 = st.columns([3, 1])
                    
                    if cb1.form_submit_button("💾 儲存更新", type="primary", use_container_width=True):
                        ns = datetime.combine(n_dt, n_st)
                        ne = ns + timedelta(hours=n_dur)
                        cf = get_conflicts(ns, ne, exclude_id=r['id'])
                        if cf: st.toast(f"⚠️ 調課注意：與 {', '.join(cf)} 衝突！", icon="⚠️")
                        
                        idx = st.session_state.df_sess['id'] == r['id']
                        
                        # 🔥 無論當初有無 event_id，皆強制執行日曆同步更新或刪除
                        if stt in ["請假", "已取消"]:
                            do_gcal("delete", title=f"家教: {r['sname']}", eid=r.get('google_event_id', ""), old_s_dt=r['sdt'])
                            st.session_state.df_sess.loc[idx, 'google_event_id'] = ""
                        else:
                            new_gid = do_gcal("update", title=f"家教: {r['sname']}", s_dt=ns, e_dt=ne, eid=r.get('google_event_id', ""), old_s_dt=r['sdt'])
                            st.session_state.df_sess.loc[idx, 'google_event_id'] = new_gid
                            
                        st.session_state.df_sess.loc[idx, 'start_time'] = ns.strftime('%Y-%m-%dT%H:%M:%S')
                        st.session_state.df_sess.loc[idx, 'end_time'] = ne.strftime('%Y-%m-%dT%H:%M:%S')
                        st.session_state.df_sess.loc[idx, 'status'] = stt
                        st.session_state.df_sess.loc[idx, 'progress'] = n_prog
                        
                        if save_to_cloud("sessions", st.session_state.df_sess):
                            st.toast("✅ 課表與 Google 日曆已同步更新！"); time.sleep(0.3); st.rerun()
                            
                    if cb2.form_submit_button("🗑️ 刪除", use_container_width=True):
                        do_gcal("delete", title=f"家教: {r['sname']}", eid=r.get('google_event_id', ""), old_s_dt=r['sdt'])
                        st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id'] != r['id']]
                        if save_to_cloud("sessions", st.session_state.df_sess):
                            st.toast("🗑️ 課程與日曆已同步刪除！"); time.sleep(0.3); st.rerun()
    else:
        st.info("🎯 目前沒有待上的未來排課。")

# ==========================================
# TAB 3: 帳單中心 (依學生合併開單與檢視)
# ==========================================
with tab3:
    st.markdown("#### 💰 未結算課程與開單")
    if st.button("⚡ 一鍵依學生合併開單", type="primary", key="t3_btn_inv", use_container_width=True):
        if not df_s.empty:
            pm = (~df_s['st_clean'].isin(['請假', '已取消'])) & (df_s['edt'] < now_tw()) & (df_s['inv_num'] == 0)
            df_p = df_s[pm].copy()
            
            if not df_p.empty:
                mid = int(st.session_state.df_inv['id'].max()) if not st.session_state.df_inv.empty else 0
                new_invs = []
                for rsid, grp in df_p.groupby('rsid'):
                    mid += 1
                    amt = int(grp['amt'].sum())
                    months = sorted(grp['sdt'].dt.strftime('%Y-%m').unique())
                    note_str = months[0] if len(months) == 1 else f"{months[0]}~{months[-1]}"
                    new_invs.append({'id': mid, 'student_id': rsid, 'total_amount': amt, 'created_at': now_tw().isoformat(), 'is_paid': 0, 'note': note_str})
                    st.session_state.df_sess.loc[st.session_state.df_sess['id'].isin(grp['id']), 'invoice_id'] = mid
                st.session_state.df_inv = pd.concat([st.session_state.df_inv, pd.DataFrame(new_invs)], ignore_index=True)
                if save_to_cloud("invoices", st.session_state.df_inv) and save_to_cloud("sessions", st.session_state.df_sess):
                    st.toast(f"🧾 已依學生合併產出 {len(new_invs)} 份帳單！"); st.rerun()
            else:
                st.info("目前沒有已結束且未結算的課程。")
        else:
            st.info("目前沒有任何排課紀錄。")
                
    st.divider()
    st.markdown("#### ⏳ 待收學費帳單 (依學生歸戶)")
    if not st.session_state.df_inv.empty:
        upd = st.session_state.df_inv[pd.to_numeric(st.session_state.df_inv['is_paid'], errors='coerce').fillna(0) == 0].copy()
        if not upd.empty:
            upd['rsid'] = upd['student_id'].apply(resolve_sid)
            for rsid, grp in upd.groupby('rsid'):
                sn = name_map.get(rsid, f"未知({rsid})")
                scol = color_map.get(rsid, "#3B82F6")
                total_amt = int(grp['total_amount'].sum())
                inv_ids = grp['id'].astype(int).tolist()
                notes = "、".join([str(n) for n in grp['note'].unique() if str(n).strip()])
                
                with st.container(border=True):
                    st.markdown(
                        f"<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;'>"
                        f"<div><span style='display:inline-block; width:12px; height:12px; border-radius:50%; background-color:{scol}; margin-right:8px;'></span>"
                        f"<span style='font-size:1.2rem; font-weight:700;'>{sn}</span> <span style='color:gray; font-size:0.9rem;'>({notes})</span></div>"
                        f"<div style='font-size:1.3rem; font-weight:800; color:#EF4444;'>${total_amt:,}</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    c1, c2 = st.columns(2)
                    if c2.button("💵 確認已收款", key=f"pay_stu_{rsid}", type="primary", use_container_width=True):
                        st.session_state.df_inv.loc[st.session_state.df_inv['id'].isin(inv_ids), 'is_paid'] = 1
                        if save_to_cloud("invoices", st.session_state.df_inv):
                            st.toast(f"✅ 已結清 {sn} 的帳單！"); st.rerun()
                    with c1.expander("💬 複製 Line 請款文案"):
                        if not df_s.empty:
                            mls = df_s[df_s['inv_num'].isin(inv_ids)].sort_values('sdt')
                            if not mls.empty:
                                msg = [f"【{sn} 課程費用明細】\n家長您好，以下是課程費用明細：\n"]
                                for _, ls in mls.iterrows():
                                    msg.append(f"📌 {ls['sdt'].strftime('%m/%d')} ({ls['dur_h']:.1f}h) : ${int(ls['amt']):,}")
                                msg.append(f"\n總計金額：${total_amt:,}元\n再麻煩您空閒時留意，謝謝！")
                                st.code("\n".join(msg), language=None)
        else:
            st.success("🎉 太棒了！所有學生的帳單皆已全數結清！")
    else:
        st.success("🎉 目前沒有任何未收帳單！")

# ==========================================
# TAB 4: 學生戰情
# ==========================================
with tab4:
    with st.expander("➕ 新增學生檔案"):
        with st.form("t4_asf", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 1.5, 2])
            n = c1.text_input("學生姓名")
            rt = c2.number_input("預設時薪", value=700, step=50)
            col = c3.selectbox("代表顏色 (紅/藍/綠)", COLOR_OPTS)
            if st.form_submit_button("💾 儲存建立", type="primary", use_container_width=True):
                if not n.strip():
                    st.error("❌ 請輸入學生姓名！")
                else:
                    mid = int(st.session_state.df_stu['id'].max()) if not st.session_state.df_stu.empty and 'id' in st.session_state.df_stu.columns else 0
                    nr = pd.DataFrame([{'id': mid + 1, 'name': n.strip(), 'default_rate': rt, 'color': col.split(" ")[0]}])
                    ud = pd.concat([st.session_state.df_stu, nr], ignore_index=True)
                    if save_to_cloud("students", ud):
                        st.session_state.df_stu = ud; st.toast(f"🎓 已建立 {n.strip()}"); time.sleep(0.3); st.rerun()

    st.divider()
    if not st.session_state.df_stu.empty:
        for _, r in st.session_state.df_stu.iterrows():
            sid, sn = str(int(r['id'])), r['name']
            scol = r.get('color', '#3B82F6')
            srate = int(pd.to_numeric(r.get('default_rate', 500), errors='coerce') or 500)
            df_ms = df_s[df_s['rsid'] == sid].copy() if not df_s.empty else pd.DataFrame()

            with st.container(border=True):
                c_head, c_del = st.columns([4.5, 1.2])
                c_head.markdown(
                    f"<div style='display:flex; align-items:center; margin-bottom:6px;'>"
                    f"<div style='width:18px; height:18px; background-color:{scol}; border-radius:50%; margin-right:10px; box-shadow:0 0 8px {scol}88;'></div>"
                    f"<span style='font-size:1.25rem; font-weight:800;'>{sn}</span>"
                    f"<span class='stu-badge' style='background-color:{scol}22; color:{scol}; border:1px solid {scol}55;'>${srate}/hr</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )
                if c_del.button("🗑️ 移除", key=f"t4_ds_{sid}", use_container_width=True):
                    ud = st.session_state.df_stu[st.session_state.df_stu['id'] != r['id']]
                    if save_to_cloud("students", ud):
                        st.session_state.df_stu = ud; st.rerun()

                with st.expander("🪄 智慧續排 (自動送入預排購物車)"):
                    if not df_ms.empty:
                        df_valid = df_ms[~df_ms['st_clean'].isin(['請假', '已取消'])]
                        if not df_valid.empty:
                            wp = df_valid[df_valid['sdt'] >= df_valid['sdt'].max() - timedelta(days=6)].sort_values('sdt')
                            for _, w in wp.iterrows():
                                st.write(f"📅 星期{WEEKDAYS_TW[w['sdt'].weekday()]} {w['sdt'].strftime('%H:%M')} ({w['dur_h']:.1f}h)")
                            wk = st.number_input("展延週數", 1, 8, 4, key=f"t4_w_{sid}")
                            sy = st.checkbox("同步日曆", value=True, key=f"t4_sg_{sid}")
                            if st.button("🛒 加入預排購物車", key=f"t4_rn_{sid}", type="primary", use_container_width=True):
                                cnt = 0
                                for i in range(1, int(wk) + 1):
                                    for _, p in wp.iterrows():
                                        ns = p['sdt'] + timedelta(weeks=i)
                                        ne = ns + timedelta(hours=float(p['dur_h']))
                                        st.session_state.draft_list.append({'sid': sid, 'sname': sn, 'sdt': ns, 'edt': ne, 'sy': sy, 'prog': '', 'rate': srate})
                                        cnt += 1
                                st.toast(f"✅ 已將 {cnt} 堂續排課加入「📅 課表排程」的購物車！"); time.sleep(0.5); st.rerun()
                        else:
                            st.info("尚無有效常規課可供參考。")
                    else:
                        st.info("目前無排課紀錄可供參考。")

                with st.expander("📝 歷史上課進度查看"):
                    if not df_ms.empty:
                        p_ls = df_ms[(df_ms['sdt'] < now_tw()) & (df_ms['st_clean'] != '已取消')].sort_values('sdt', ascending=False)
                        if not p_ls.empty:
                            for _, p_c in p_ls.iterrows():
                                st.markdown(f"**📅 {p_c['sdt'].strftime('%Y-%m-%d %H:%M')}** {'⚠️(請假)' if p_c['st_clean'] == '請假' else ''}")
                                st.write(p_c['progress'] if p_c['progress'] else "（無紀錄）"); st.divider()
                        else:
                            st.caption("尚無歷史上課紀錄。")
                    else:
                        st.caption("尚無排課紀錄。")

                with st.expander("💬 產生 Line 課表通知文案"):
                    if not df_ms.empty:
                        f_ls = df_ms[(df_ms['sdt'] >= now_tw()) & (df_ms['st_clean'] == '已預約')].sort_values('sdt')
                        if not f_ls.empty:
                            msg = [f"【{sn} 近期課程預告】\n家長您好，以下是接下來的排課時間：\n"]
                            for _, f_c in f_ls.iterrows():
                                msg.append(f"📌 {f_c['sdt'].strftime('%m/%d')} ({WEEKDAYS_TW[f_c['sdt'].weekday()]}) {f_c['sdt'].strftime('%H:%M')}")
                            msg.append("\n再請您核對時間，謝謝！")
                            st.code("\n".join(msg), language=None)
                        else:
                            st.caption("目前沒有未來的預約課程。")
                    else:
                        st.caption("尚無排課紀錄。")
# ===== 程式碼結束 =====
