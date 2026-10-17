import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 系統核心與台灣時區設定
# ==========================================
TARGET_CALENDAR_ID = 'cargoada@gmail.com' 
st.set_page_config(page_title="家教排課系統 v3.5", page_icon="📅", layout="centered")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']
TW_TZ = ZoneInfo("Asia/Taipei")

def now_tw():
    """取得台灣標準時間 (無時區標籤)"""
    return datetime.now(TW_TZ).replace(tzinfo=None)

def clean_gid(val):
    """過濾無效的 Google Event ID"""
    if pd.isna(val): return ""
    s = str(val).strip()
    return "" if s.lower() in ["", "nan", "none", "0", "0.0"] else s

def parse_dt(series):
    """萬用時間解析器"""
    s = series.astype(str).str.strip().str.replace('T', ' ', regex=False)
    return pd.to_datetime(s, format='mixed', errors='coerce')

def sanitize_edt(s_series, e_series):
    """自動修復異常的結束時間"""
    edt = e_series.fillna(s_series + timedelta(hours=1.5))
    dur_h = (edt - s_series).dt.total_seconds() / 3600
    bad_mask = (dur_h <= 0) | (dur_h > 8) | edt.isna()
    edt = edt.where(~bad_mask, s_series + timedelta(hours=1.5))
    return edt

service = None
try:
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = dict(st.secrets["connections"]["gsheets"])
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
except: pass

# ==========================================
# 2. 身分驗證與登入
# ==========================================
if 'current_user' not in st.session_state: st.session_state.current_user = None
if st.session_state.current_user is None:
    st.title("👋 歡迎使用排課系統 v3.5")
    if "users" in st.secrets:
        selected_login = st.selectbox("請選擇您的身分", list(st.secrets["users"].keys()))
        if st.button("🚀 進入系統", type="primary", use_container_width=True):
            st.session_state.current_user = selected_login
            st.rerun()
    st.stop()

CURRENT_SHEET_URL = st.secrets["users"][st.session_state.current_user]
conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 3. 雲端讀寫與自動壓實模組 
# ==========================================
SCHEMAS = {
    "sessions": ['id', 'student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'google_event_id', 'progress', 'invoice_id'],
    "invoices": ['id', 'student_id', 'total_amount', 'created_at', 'is_paid', 'note'],
    "students": ['id', 'name', 'default_rate', 'color'],
    "stats": ['cumulative_offset']
}

def get_cloud_data(worksheet_name):
    req_cols = SCHEMAS.get(worksheet_name, [])
    try:
        st.cache_data.clear()
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=0)
        if df is None or df.empty: return pd.DataFrame(columns=req_cols)
        
        df = df.dropna(how='all').copy()
        for col in req_cols:
            if col not in df.columns: df[col] = ""
        df = df[req_cols].copy()
        
        if worksheet_name == "sessions":
            valid_mask = (df['start_time'].astype(str).str.strip().replace('nan', '') != '') & \
                         (df['student_id'].astype(str).str.strip().replace('nan', '') != '')
            df = df[valid_mask].copy()
            df['status'] = df['status'].astype(str).str.strip()
            
        for num_col in ['id', 'invoice_id', 'actual_rate', 'is_paid', 'total_amount', 'default_rate']:
            if num_col in df.columns: df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0).astype(int)
        if 'cumulative_offset' in df.columns:
            df['cumulative_offset'] = pd.to_numeric(df['cumulative_offset'], errors='coerce').fillna(0.0)
            
        if 'id' in df.columns and not df.empty:
            if (df['id'] == 0).any() or df['id'].duplicated().any():
                df['id'] = range(1, len(df) + 1)
                
        return df.reset_index(drop=True)
    except: return pd.DataFrame(columns=req_cols)

def save_to_cloud(ws_name, df):
    req_cols = SCHEMAS.get(ws_name, list(df.columns))
    try:
        df_save = df.dropna(how='all').copy()
        for col in req_cols:
            if col not in df_save.columns: df_save[col] = ""
            
        if ws_name == "sessions" and not df_save.empty:
            valid_mask = df_save['start_time'].astype(str).str.strip().replace('nan', '') != ''
            df_save = df_save[valid_mask].copy()
            df_save['status'] = df_save['status'].astype(str).str.strip()
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

def do_gcal(action, title="", s_dt=None, e_dt=None, eid=""):
    eid = clean_gid(eid)
    if service is None: return "" if action=="insert" else False
    try:
        if action == "insert":
            ev = service.events().insert(calendarId=TARGET_CALENDAR_ID, body={'summary': title, 'start': {'dateTime': s_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}, 'end': {'dateTime': e_dt.strftime('%Y-%m-%dT%H:%M:%S', 'timeZone': 'Asia/Taipei')}}).execute()
            return ev.get('id', "")
        elif action == "update" and eid:
            service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=eid, body={'summary': title, 'start': {'dateTime': s_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}, 'end': {'dateTime': e_dt.strftime('%Y-%m-%dT%H:%M:%S', 'timeZone': 'Asia/Taipei')}}).execute()
            return True
        elif action == "delete" and eid:
            service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=eid).execute()
            return True
    except Exception:
        st.toast(f"⚠️ 日曆{action}未同步 (可能已手動刪除)", icon="⚠️")
    return "" if action=="insert" else False

# ==========================================
# 4. 初始化記憶體與姓名/ID 雙向映射
# ==========================================
if 'initialized' not in st.session_state:
    st.session_state.initialized = False
    st.session_state.draft_list = []
    for k in ['df_stu', 'df_sess', 'df_inv', 'df_stats']: st.session_state[k] = pd.DataFrame()

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
        name_map[sid], color_map[sid], name_to_id[sn] = sn, r.get('color', '#3498DB'), sid
        try: rate_map[sid] = int(r.get('default_rate', 500))
        except: rate_map[sid] = 500

def resolve_sid(raw_val):
    s = str(raw_val).strip().split('.')[0]
    return name_to_id.get(s, s)

# 🛡️ 智慧衝突偵測雷達
def get_conflicts(new_s, new_e, exclude_id=None, exclude_draft_idx=None):
    c_list = []
    if not st.session_state.df_sess.empty:
        df = st.session_state.df_sess.copy()
        df['s'] = parse_dt(df['start_time'])
        df['e'] = sanitize_edt(df['s'], parse_dt(df['end_time']))
        df['st_clean'] = df['status'].astype(str).str.strip()
        df = df[~df['st_clean'].isin(['請假', '已取消'])].dropna(subset=['s'])
        if exclude_id is not None: df = df[df['id'] != exclude_id]
        
        mask = (df['s'] < new_e) & (df['e'] > new_s)
        for _, r in df[mask].iterrows():
            rsn = name_map.get(resolve_sid(r['student_id']), '未知')
            c_list.append(f"{rsn}({r['s'].strftime('%m/%d %H:%M')})")
            
    for idx, d in enumerate(st.session_state.draft_list):
        if exclude_draft_idx is not None and idx == exclude_draft_idx: continue
        if d['sdt'] < new_e and d['edt'] > new_s:
            c_list.append(f"購物車的 {d['sname']}({d['sdt'].strftime('%m/%d %H:%M')})")
    return c_list

# ==========================================
# 側邊欄與課表診斷器
# ==========================================
with st.sidebar:
    st.header(f"👤 {st.session_state.current_user} (v3.5)")
    st.caption(f"🕒 台灣時間：{now_tw().strftime('%Y-%m-%d %H:%M')}")
    if st.button("🔄 同步/刷新雲端", use_container_width=True, key="side_refresh"):
        st.session_state.initialized = False
        st.rerun()
        
    with st.expander("🕵️ 課表診斷與修復"):
        total_rows = len(st.session_state.df_sess)
        st.caption(f"雲端已載入總筆數：`{total_rows}` 筆")
        if total_rows > 0:
            s_parsed = parse_dt(st.session_state.df_sess['start_time'])
            bad_rows = st.session_state.df_sess[s_parsed.isna()]
            if not bad_rows.empty:
                st.error(f"⚠️ 發現 {len(bad_rows)} 筆時間格式損壞的舊資料！")
                if st.button("🧹 一鍵清除損壞資料並壓實雲端", type="primary", use_container_width=True):
                    clean_df = st.session_state.df_sess[s_parsed.notna()].copy()
                    if save_to_cloud("sessions", clean_df):
                        st.session_state.df_sess = clean_df
                        st.toast("✅ 損壞資料已清除！")
                        time.sleep(0.5)
                        st.rerun()
            else:
                st.success("✅ 所有課程時間格式皆正常！")
                if st.button("🔧 強制重新壓實並修復異常時數", use_container_width=True):
                    if save_to_cloud("sessions", st.session_state.df_sess):
                        st.session_state.initialized = False
                        st.toast("✅ 雲端表格已重新壓實並校正時數！")
                        time.sleep(0.5)
                        st.rerun()
                        
    if st.button("🚪 登出系統", use_container_width=True, key="side_logout"):
        st.session_state.current_user = None
        st.session_state.initialized = False
        st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況中心", "📅 課表排程", "💰 帳單中心", "🧑‍🎓 學生戰情"])

# ==========================================
# TAB 1: 概況中心
# ==========================================
with tab1:
    st.subheader("📊 營收動態與行程追蹤")
    with st.container(border=True):
        cal_date = st.date_input("查看當天課表：", now_tw().date(), label_visibility="collapsed", key="t1_cal")
        if not st.session_state.df_sess.empty:
            df_c = st.session_state.df_sess.copy()
            df_c['sdt'] = parse_dt(df_c['start_time'])
            df_c['edt'] = sanitize_edt(df_c['sdt'], parse_dt(df_c['end_time']))
            df_c['st_clean'] = df_c['status'].astype(str).str.strip()
            df_c = df_c.dropna(subset=['sdt'])
            df_td = df_c[(df_c['sdt'].dt.date == cal_date) & (df_c['st_clean'] != '已取消')].sort_values('sdt')
            st.markdown(f"**🔍 {cal_date.strftime('%m/%d')} 課表明細：**")
            if not df_td.empty:
                for _, r in df_td.iterrows():
                    sid = resolve_sid(r['student_id'])
                    sname, scolor = name_map.get(sid, f"未知({sid})"), color_map.get(sid, "#3498DB")
                    c_stt = r['st_clean']
                    
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns([3.5, 1.8, 1.8, 1.8])
                        c1.markdown(f"▶️ <span style='color:{scolor};'>●</span> **{r['sdt'].strftime('%H:%M')}** │ 🧑‍🎓 **{sname}** {'(請假)' if c_stt=='請假' else ''}", unsafe_allow_html=True)
                        if r.get('progress'): c1.caption(f"🏷 {r['progress']}")
                        
                        if c2.checkbox("✅ 完課", value=(c_stt == '已完成'), key=f"t1_ck_{r['id']}") != (c_stt == '已完成'):
                            st.session_state.df_sess.loc[st.session_state.df_sess['id']==r['id'], 'status'] = '已完成' if c_stt != '已完成' else '已預約'
                            if save_to_cloud("sessions", st.session_state.df_sess): st.rerun()
                            
                        if c3.button("❌ 停課", key=f"t1_cl_{r['id']}"):
                            do_gcal("delete", eid=r.get('google_event_id', ""))
                            st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id'] != r['id']]
                            if save_to_cloud("sessions", st.session_state.df_sess): st.rerun()
                            
                        with c4.popover("📅 調課"):
                            with st.form(f"t1_fm_{r['id']}"):
                                nd, nt = st.date_input("日期", r['sdt'].date()), st.time_input("時間", r['sdt'].time())
                                old_h = (r['edt'] - r['sdt']).total_seconds()/3600
                                if st.form_submit_button("💾 確定"):
                                    ns, ne = datetime.combine(nd, nt), datetime.combine(nd, nt) + timedelta(hours=old_h)
                                    cf = get_conflicts(ns, ne, exclude_id=r['id'])
                                    if cf: st.toast(f"⚠️ 調課提醒：與 {', '.join(cf)} 衝突！", icon="⚠️")
                                    
                                    idx = st.session_state.df_sess['id'] == r['id']
                                    st.session_state.df_sess.loc[idx, 'start_time'] = ns.strftime('%Y-%m-%dT%H:%M:%S')
                                    st.session_state.df_sess.loc[idx, 'end_time'] = ne.strftime('%Y-%m-%dT%H:%M:%S')
                                    
                                    do_gcal("update", f"家教: {sname}", ns, ne, r.get('google_event_id', ""))
                                    if save_to_cloud("sessions", st.session_state.df_sess):
                                        st.toast("📅 調課成功！"); time.sleep(0.3); st.rerun()
            else: st.write("🏝️ 沒排課，好好休息！")
        else:
            st.write("🏝️ 尚無排課資料。")

    if not st.session_state.df_sess.empty:
        dc = st.session_state.df_sess.copy()
        dc['sdt'] = parse_dt(dc['start_time'])
        dc['edt'] = sanitize_edt(dc['sdt'], parse_dt(dc['end_time']))
        dc['st_clean'] = dc['status'].astype(str).str.strip()
        dc = dc[~dc['st_clean'].isin(['請假', '已取消'])].dropna(subset=['sdt'])
        dc['amt'] = ((dc['edt'] - dc['sdt']).dt.total_seconds() / 3600) * pd.to_numeric(dc['actual_rate'], errors='coerce').fillna(0)
        now = now_tw()
        tm = dc[(dc['sdt'].dt.year == now.year) & (dc['sdt'].dt.month == now.month)]['amt'].sum()
        pdn = dc[(dc['edt'] < now) & (pd.to_numeric(dc['invoice_id'], errors='coerce').fillna(0) == 0)]['amt'].sum()
        hst = dc['amt'].sum() + (float(st.session_state.df_stats['cumulative_offset'].iloc[0]) if not st.session_state.df_stats.empty else 0)
        
        c1, c2, c3 = st.columns(3)
        c1.metric("本月預估", f"${int(tm):,}"); c2.metric("未結算", f"${int(pdn):,}"); c3.metric("總收入", f"${int(hst):,}")
        dc['m'] = dc['sdt'].dt.strftime('%Y-%m')
        st.bar_chart(dc.groupby('m')['amt'].sum(), color="#3498DB")

# ==========================================
# TAB 2: 課表排程
# ==========================================
with tab2:
    if st.session_state.df_stu.empty: st.warning("請先建立學生檔案！")
    else:
        with st.expander("➕ 單堂課程 (加入預排)"):
            with st.form("t2_sgl_f", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                sid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x], key="t2_sid")
                dt, tm, dur = c2.date_input("日期", now_tw().date()), c3.time_input("時間", now_tw().replace(minute=0,second=0).time()), c4.slider("時數", 0.5, 4.0, 1.5, 0.5)
                sy, prog = st.checkbox("同步 Google 日曆", key="t2_sy"), st.text_input("備註")
                if st.form_submit_button("➕ 加入預排", type="primary"):
                    ns, ne = datetime.combine(dt, tm), datetime.combine(dt, tm) + timedelta(hours=dur)
                    cf = get_conflicts(ns, ne) 
                    if cf: st.toast(f"⚠️ 注意！已加入預排，但與 {', '.join(cf)} 衝突！", icon="⚠️")
                    st.session_state.draft_list.append({'sid': sid, 'sname': name_map[sid], 'sdt': ns, 'edt': ne, 'sy': sy, 'prog': prog, 'rate': rate_map.get(sid, 500)})
                    st.rerun()

        with st.expander("📅 大範圍區間排課 (加入預排)"):
            with st.form("t2_rng_f"):
                c1, c2, c3 = st.columns([2, 1, 1])
                rsid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x], key="t2_rsid")
                sdt, edt = c2.date_input("開始", now_tw().date()), c3.date_input("結束", now_tw().date()+timedelta(days=60))
                days = st.multiselect("星期", list({"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6}.keys()), default=["一"], key="t2_rdys")
                c4, c5 = st.columns(2)
                rtm, rdur = c4.time_input("時間", now_tw().replace(hour=14,minute=0,second=0).time()), c5.slider("時數", 0.5, 4.0, 2.0, 0.5, key="t2_rdur")
                rsy, rnote = st.checkbox("同步日曆", key="t2_rsy"), st.text_input("區間備註")
                if st.form_submit_button("➕ 批次加入預排", type="primary"):
                    if sdt > edt: st.error("❌ 開始日期不能晚於結束日期！")
                    elif not days: st.error("❌ 請至少選擇一個上課星期！")
                    else:
                        nums = [{"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6}[d] for d in days]
                        cur, count, conflict_count = sdt, 0, 0
                        while cur <= edt:
                            if cur.weekday() in nums:
                                ns, ne = datetime.combine(cur, rtm), datetime.combine(cur, rtm) + timedelta(hours=rdur)
                                cf = get_conflicts(ns, ne)
                                if cf: conflict_count += 1
                                st.session_state.draft_list.append({'sid': rsid, 'sname': name_map[rsid], 'sdt': ns, 'edt': ne, 'sy': rsy, 'prog': rnote, 'rate': rate_map.get(rsid,500)})
                                count += 1
                            cur += timedelta(days=1)
                        if count > 0:
                            msg = f"✅ 已加入 {count} 堂。" + (f" ⚠️ 發現 {conflict_count} 堂衝突！" if conflict_count else "")
                            st.toast(msg, icon="⚠️" if conflict_count else "✅"); st.rerun()
                        else: st.error("❌ 區間無符合天數！")

        if st.session_state.draft_list:
            st.markdown(f"### 🛒 待送出的排課購物車 (共 {len(st.session_state.draft_list)} 堂)")
            with st.container(border=True):
                for i, d in enumerate(st.session_state.draft_list):
                    c_inf, c_b = st.columns([5, 1])
                    ss = "🔄" if d['sy'] else ""
                    dur_h = (d['edt'] - d['sdt']).total_seconds() / 3600
                    live_cf = get_conflicts(d['sdt'], d['edt'], exclude_draft_idx=i)
                    if live_cf: c_inf.warning(f"{d['sdt'].strftime('%m/%d %H:%M')} ({dur_h:.1f}h) │ 🧑‍🎓 **{d['sname']}** {ss} {d['prog']} ⚠️ 與 {', '.join(live_cf)} 衝突！")
                    else: c_inf.info(f"{d['sdt'].strftime('%m/%d %H:%M')} ({dur_h:.1f}h) │ 🧑‍🎓 **{d['sname']}** {ss} {d['prog']}")
                    if c_b.button("❌", key=f"t2_rm_df_{i}", help="移除"):
                        st.session_state.draft_list.pop(i); st.rerun()
                
                cb_send, cb_clear = st.columns([3, 1])
                if cb_clear.button("🗑️ 清空推車", use_container_width=True):
                    st.session_state.draft_list = []; st.rerun()
                if cb_send.button("🚀 確認送出以上所有排課", type="primary", use_container_width=True):
                    with st.spinner("安全寫入雲端中..."):
                        mid = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty and 'id' in st.session_state.df_sess.columns else 0
                        n_ls = []
                        for d in st.session_state.draft_list:
                            gid = do_gcal("insert", f"家教: {d['sname']}", d['sdt'], d['edt']) if d['sy'] else ""
                            mid += 1
                            n_ls.append({'id': mid, 'student_id': str(d['sid']), 'start_time': d['sdt'].strftime('%Y-%m-%dT%H:%M:%S'), 'end_time': d['edt'].strftime('%Y-%m-%dT%H:%M:%S'), 'status': '已預約', 'actual_rate': int(d['rate']), 'google_event_id': gid, 'progress': d['prog'], 'invoice_id': 0})
                            if d['sy']: time.sleep(0.15)
                        
                        if n_ls:
                            updated_df = pd.concat([st.session_state.df_sess, pd.DataFrame(n_ls)], ignore_index=True)
                            if save_to_cloud("sessions", updated_df):
                                st.session_state.df_sess = updated_df
                                st.session_state.draft_list = [] 
                                st.toast("🎉 排課完成！"); time.sleep(0.4); st.rerun()

        with st.expander("🧹 批量刪課 (清理未來預約排課)"):
            if not st.session_state.df_sess.empty:
                df_dl = st.session_state.df_sess.copy()
                df_dl['dt'] = parse_dt(df_dl['start_time'])
                df_dl['edt'] = sanitize_edt(df_dl['dt'], parse_dt(df_dl['end_time']))
                df_dl['st_clean'] = df_dl['status'].astype(str).str.strip()
                df_dl = df_dl.dropna(subset=['dt'])
                
                current_t = now_tw()
                df_dl = df_dl[(df_dl['edt'] > current_t) & (~df_dl['st_clean'].isin(['已完成', '已取消']))].sort_values('dt', ascending=True)
                
                if not df_dl.empty:
                    ops = {f"{r['dt'].strftime('%Y-%m-%d %H:%M')} | {name_map.get(resolve_sid(r['student_id']), '未知')} (ID:{r['id']})": r['id'] for _, r in df_dl.iterrows()}
                    sl = st.multiselect("選取要刪除的未來課程", list(ops.keys()), key="t2_mdel")
                    if st.button("🚨 批量刪除", type="primary", key="t2_bmdel") and sl:
                        ids = [ops[k] for k in sl]
                        with st.spinner("清除中..."):
                            for i in ids:
                                gid = clean_gid(st.session_state.df_sess[st.session_state.df_sess['id']==i]['google_event_id'].iloc[0])
                                if gid: do_gcal("delete", eid=gid)
                            updated_df_del = st.session_state.df_sess[~st.session_state.df_sess['id'].isin(ids)]
                            if save_to_cloud("sessions", updated_df_del):
                                st.session_state.df_sess = updated_df_del; st.rerun()
                else:
                    st.info("🎯 目前沒有未來的預約課程需要清理。")

    st.divider()
    st.subheader("📋 未來待上課表調課中心 (已上過自動隱藏)")
    if not st.session_state.df_sess.empty:
        df_l = st.session_state.df_sess.copy()
        df_l['dt'] = parse_dt(df_l['start_time'])
        df_l['edt'] = sanitize_edt(df_l['dt'], parse_dt(df_l['end_time']))
        df_l['st_clean'] = df_l['status'].astype(str).str.strip()
        df_l = df_l.dropna(subset=['dt'])
        
        current_t = now_tw()
        df_f = df_l[(df_l['edt'] > current_t) & (~df_l['st_clean'].isin(['已完成', '已取消']))].sort_values('dt')
        
        if not df_f.empty:
            for _, r in df_f.iterrows():
                sn = name_map.get(resolve_sid(r['student_id']), '未知')
                ico = "⚠️(請假)" if r['st_clean']=="請假" else ""
                with st.expander(f"{r['dt'].strftime('%m/%d %H:%M')} │ 🧑‍🎓 {sn} {ico}"):
                    with st.form(f"t2_ef_{r['id']}"):
                        c_e1, c_e2 = st.columns(2)
                        n_dt, n_st = c_e1.date_input("日期", r['dt'].date()), c_e2.time_input("時間", r['dt'].time())
                        calc_dur = (r['edt']-r['dt']).total_seconds()/3600
                        safe_dur = float(calc_dur) if 0.5 <= calc_dur <= 4.0 else 1.5
                        n_dur = st.slider("時數", 0.5, 4.0, safe_dur, 0.5, key=f"t2_dur_{r['id']}")
                        stt = st.selectbox("狀態", ["已預約","請假","已取消","已完成"], index=["已預約","請假","已取消","已完成"].index(r['st_clean'] if r['st_clean'] in ["已預約","請假","已取消","已完成"] else "已預約"), key=f"t2_stt_{r['id']}")
                        n_prog = st.text_input("進度 / 備註", value=str(r.get('progress', '')), key=f"t2_prg_{r['id']}")
                        cb1, cb2 = st.columns([3, 1])
                        
                        if cb1.form_submit_button("💾 更新", type="primary"):
                            ns, ne = datetime.combine(n_dt, n_st), datetime.combine(n_dt, n_st) + timedelta(hours=n_dur)
                            cf = get_conflicts(ns, ne, exclude_id=r['id'])
                            if cf: st.toast(f"⚠️ 調課注意：與 {', '.join(cf)} 衝突！", icon="⚠️")
                            
                            idx = st.session_state.df_sess['id'] == r['id']
                            st.session_state.df_sess.loc[idx, 'start_time'] = ns.strftime('%Y-%m-%dT%H:%M:%S')
                            st.session_state.df_sess.loc[idx, 'end_time'] = ne.strftime('%Y-%m-%dT%H:%M:%S')
                            st.session_state.df_sess.loc[idx, 'status'] = stt
                            st.session_state.df_sess.loc[idx, 'progress'] = n_prog
                            
                            gid = clean_gid(r.get('google_event_id', ""))
                            if gid:
                                if stt in ["請假", "已取消"]: do_gcal("delete", eid=gid); st.session_state.df_sess.loc[idx, 'google_event_id']=""
                                else: do_gcal("update", f"家教: {sn}", ns, ne, gid)
                            if save_to_cloud("sessions", st.session_state.df_sess): st.rerun()
                                
                        if cb2.form_submit_button("🗑️ 刪除"):
                            gid = clean_gid(r.get('google_event_id', ""))
                            if gid: do_gcal("delete", eid=gid)
                            st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id']!=r['id']]
                            if save_to_cloud("sessions", st.session_state.df_sess): st.rerun()
        else:
            st.info("🎯 目前沒有待上的未來排課。")
    else:
        st.info("🎯 目前沒有任何排課紀錄。")

# ==========================================
# TAB 3: 帳單中心 (🔥 全新升級：以學生為中心的聚合帳單)
# ==========================================
with tab3:
    st.subheader("💰 未結算課程與開單")
    if st.button("⚡ 一鍵自動開單 (將已結束課程轉為帳單)", type="primary", key="t3_btn_inv", use_container_width=True):
        if not st.session_state.df_sess.empty:
            df = st.session_state.df_sess.copy()
            df['dt'] = parse_dt(df['start_time'])
            df['edt'] = sanitize_edt(df['dt'], parse_dt(df['end_time']))
            df['st_clean'] = df['status'].astype(str).str.strip()
            # 抓出已結束、非請假/取消、且尚未開單 (invoice_id == 0) 的課程
            pm = (~df['st_clean'].isin(['請假','已取消'])) & (df['edt'] < now_tw()) & (pd.to_numeric(df.get('invoice_id',0), errors='coerce').fillna(0) == 0)
            df_p = df[pm].copy()
            if not df_p.empty:
                df_p['mk'] = df_p['dt'].dt.strftime('%Y-%m')
                mid = int(st.session_state.df_inv['id'].max()) if not st.session_state.df_inv.empty else 0
                new_invs = []
                for (sid, mk), grp in df_p.groupby(['student_id', 'mk']):
                    mid += 1
                    amt = sum([((r['edt'] - r['dt']).total_seconds()/3600)*int(r.get('actual_rate',500)) for _, r in grp.iterrows() if pd.notna(r['dt'])])
                    new_invs.append({'id': mid, 'student_id': resolve_sid(sid), 'total_amount': int(amt), 'created_at': now_tw().isoformat(), 'is_paid': 0, 'note': mk})
                    st.session_state.df_sess.loc[grp.index, 'invoice_id'] = mid
                st.session_state.df_inv = pd.concat([st.session_state.df_inv, pd.DataFrame(new_invs)], ignore_index=True)
                if save_to_cloud("invoices", st.session_state.df_inv) and save_to_cloud("sessions", st.session_state.df_sess):
                    st.toast(f"🧾 已產出 {len(new_invs)} 張帳單！"); st.rerun()
            else: st.info("目前沒有已結束且未結算的課程可供開單。")
                
    st.divider()
    st.subheader("⏳ 未收賬單 (依學生聚合檢視)")
    
    if not st.session_state.df_inv.empty:
        inv_df = st.session_state.df_inv.copy()
        inv_df['is_paid_clean'] = pd.to_numeric(inv_df['is_paid'], errors='coerce').fillna(0)
        upd = inv_df[inv_df['is_paid_clean'] == 0].copy()
        
        if not upd.empty:
            upd['resolved_sid'] = upd['student_id'].apply(resolve_sid)
            
            # 🔥 核心升級：將同一個學生的所有未收帳單聚合成群組
            for sid, group in upd.groupby('resolved_sid'):
                sn = name_map.get(sid, f"未知學生({sid})")
                total_student_amt = group['total_amount'].sum()
                
                with st.container(border=True):
                    st.markdown(f"### 🧑‍🎓 {sn} │ 總欠費：<span style='color:#E74C3C;'>**${int(total_student_amt):,}**</span>", unsafe_allow_html=True)
                    
                    # 條列展示該學生的各期帳單細節
                    for _, inv_row in group.iterrows():
                        invid = int(inv_row['id'])
                        note = inv_row.get('note', '')
                        amt = int(inv_row['total_amount'])
                        
                        col_info, col_btn = st.columns([3, 1])
                        col_info.write(f"📅 帳單月份：**{note}** (金額: ${amt:,})")
                        
                        if col_btn.button("💵 此筆已收款", key=f"pay_single_{invid}", type="secondary"):
                            st.session_state.df_inv.loc[st.session_state.df_inv['id'] == invid, 'is_paid'] = 1
                            if save_to_cloud("invoices", st.session_state.df_inv): st.rerun()
                    
                    st.markdown("---")
                    c_all_pay, c_line = st.columns(2)
                    
                    # 一鍵結清該學生所有未收帳單
                    if c_all_pay.button(f"✅ 結清 {sn} 所有帳單", key=f"pay_all_{sid}", type="primary", use_container_width=True):
                        all_inv_ids = group['id'].tolist()
                        st.session_state.df_inv.loc[st.session_state.df_inv['id'].isin(all_inv_ids), 'is_paid'] = 1
                        if save_to_cloud("invoices", st.session_state.df_inv): 
                            st.toast(f"🎉 已結清 {sn} 的所有未收帳單！")
                            time.sleep(0.5)
                            st.rerun()
                            
                    # 產生包含該學生所有未結算月份的綜合 Line 請款文案
                    with c_line.expander("💬 複製 Line 綜合請款文案"):
                        inv_ids_list = group['id'].tolist()
                        mls = st.session_state.df_sess[pd.to_numeric(st.session_state.df_sess.get('invoice_id',0), errors='coerce').isin(inv_ids_list)].copy()
                        if not mls.empty:
                            mls['dt'] = parse_dt(mls['start_time'])
                            mls['edt'] = sanitize_edt(mls['dt'], parse_dt(mls['end_time']))
                            msg = [f"【{sn} 課程費用催繳通知】\n家長您好，以下是累積的課程費用明細：\n"]
                            for _, ls in mls.dropna(subset=['dt']).sort_values('dt').iterrows():
                                h = (ls['edt'] - ls['dt']).total_seconds()/3600
                                msg.append(f"📌 {ls['dt'].strftime('%m/%d')} ({h:.1f}h) : ${int(h*int(ls.get('actual_rate',500))):,}")
                            msg.append(f"\n合計總金額：${int(total_student_amt):,}元\n再麻煩您空閒時協助轉帳，謝謝老師！")
                            st.code("\n".join(msg), language=None)
        else:
            st.success("🎉 目前所有學生的帳單都已經全數結清囉！")
    else:
        st.success("🎉 目前沒有任何未收帳單紀錄。")

# ==========================================
# TAB 4: 學生戰情
# ==========================================
with tab4:
    with st.expander("➕ 新增學生"):
        with st.form("t4_asf", clear_on_submit=True):
            c1, c2 = st.columns(2)
            n, rt = c1.text_input("姓名"), c2.number_input("時薪", value=700, step=50)
            col = st.selectbox("顏色", ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)", "#9B59B6 (紫)"])
            if st.form_submit_button("儲存", type="primary"):
                if n.strip() == "": st.error("❌ 請輸入學生姓名！")
                else:
                    mid = int(st.session_state.df_stu['id'].max()) if not st.session_state.df_stu.empty and 'id' in st.session_state.df_stu.columns else 0
                    nr = pd.DataFrame([{'id': mid+1, 'name': n.strip(), 'default_rate': rt, 'color': col.split(" ")[0]}])
                    ud = pd.concat([st.session_state.df_stu, nr], ignore_index=True)
                    if save_to_cloud("students", ud):
                        st.session_state.df_stu = ud; st.toast(f"🎓 已建立 {n.strip()}"); time.sleep(0.3); st.rerun()

    st.divider()
    if not st.session_state.df_stu.empty:
        weekdays_tw = ["一", "二", "三", "四", "五", "六", "日"]
        for _, r in st.session_state.df_stu.iterrows():
            sid, sn = str(int(r['id'])), r['name']
            df_ms = pd.DataFrame()
            if not st.session_state.df_sess.empty and 'student_id' in st.session_state.df_sess.columns:
                dtmp = st.session_state.df_sess.copy()
                dtmp['rsid'] = dtmp['student_id'].apply(resolve_sid)
                df_ms = dtmp[dtmp['rsid'] == sid].copy()

            with st.container(border=True):
                c1, c2, c3 = st.columns([0.5, 4, 1.5])
                c1.markdown(f"<div style='width:25px;height:25px;background-color:{r.get('color','#3498DB')};border-radius:50%;'></div>", unsafe_allow_html=True)
                c2.markdown(f"### {sn} (${r.get('default_rate',500)}/hr)")
                if c3.button("🗑️ 移除", key=f"t4_ds_{sid}"):
                    ud = st.session_state.df_stu[st.session_state.df_stu['id']!=r['id']]
                    if save_to_cloud("students", ud): st.session_state.df_stu = ud; st.rerun()

                with st.expander("🪄 智慧續排 (自動送入預排購物車)"):
                    if not df_ms.empty:
                        df_ms['dt'] = parse_dt(df_ms['start_time'])
                        df_ms['edt'] = sanitize_edt(df_ms['dt'], parse_dt(df_ms['end_time']))
                        df_ms['st_clean'] = df_ms['status'].astype(str).str.strip()
                        df_valid = df_ms[~df_ms['st_clean'].isin(['請假', '已取消'])].dropna(subset=['dt'])
                        if not df_valid.empty:
                            wp = df_valid[df_valid['dt'] >= df_valid['dt'].max() - timedelta(days=6)].sort_values('dt')
                            for _, w in wp.iterrows(): st.write(f"📅 星期{weekdays_tw[w['dt'].weekday()]} {w['dt'].strftime('%H:%M')}")
                            wk = st.number_input("展延週數", 1, 8, 4, key=f"t4_w_{sid}")
                            sy = st.checkbox("同步日曆", key=f"t4_sg_{sid}")
                            if st.button("🛒 加入預排購物車", key=f"t4_rn_{sid}", type="primary"):
                                cnt = 0
                                for i in range(1, int(wk)+1):
                                    for _, p in wp.iterrows():
                                        p_sdt = p['dt']
                                        dur_h = (p['edt'] - p_sdt).total_seconds()/3600
                                        ns = p_sdt + timedelta(weeks=i)
                                        ne = ns + timedelta(hours=dur_h)
                                        st.session_state.draft_list.append({'sid': sid, 'sname': sn, 'sdt': ns, 'edt': ne, 'sy': sy, 'prog': '', 'rate': int(r.get('default_rate', 500))})
                                        cnt += 1
                                st.toast(f"✅ 已將 {cnt} 堂續排課加入「📅 課表排程」的購物車！"); time.sleep(0.5); st.rerun()
                        else: st.info("尚無有效常規課可供參考。")
                    else: st.info("目前無排課紀錄可供參考。")

                with st.expander("📝 歷史上課進度查看"):
                    if not df_ms.empty:
                        df_ms['dt_p'] = parse_dt(df_ms['start_time'])
                        df_ms['st_clean'] = df_ms['status'].astype(str).str.strip()
                        p_ls = df_ms[(df_ms['dt_p'] < now_tw()) & (df_ms['st_clean'] != '已取消')].sort_values('dt_p', ascending=False)
                        if not p_ls.empty:
                            for _, p_c in p_ls.iterrows():
                                st.markdown(f"**📅 {p_c['dt_p'].strftime('%Y-%m-%d %H:%M')}** {'(請假)' if p_c['st_clean'] == '請假' else ''}")
                                st.write(p_c['progress'] if p_c['progress'] else "（無紀錄）"); st.divider()

                with st.expander("💬 產生 Line 課表通知文案"):
                    if not df_ms.empty:
                        df_ms['dt_f'] = parse_dt(df_ms['start_time'])
                        df_ms['st_clean'] = df_ms['status'].astype(str).str.strip()
                        f_ls = df_ms[(df_ms['dt_f'] >= now_tw()) & (df_ms['st_clean'] == '已預約')].sort_values('dt_f')
                        if not f_ls.empty:
                            msg = [f"【{sn} 近期課程預告】\n家長您好，以下是接下來的排課時間：\n"]
                            for _, f_c in f_ls.iterrows(): msg.append(f"📌 {f_c['dt_f'].strftime('%m/%d')} ({weekdays_tw[f_c['dt_f'].weekday()]}) {f_c['dt_f'].strftime('%H:%M')}")
                            msg.append("\n再請您核對時間，謝謝！")
                            st.code("\n".join(msg), language=None)
# ===== 程式碼結束 =====
