import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 系統核心設定
# ==========================================
TARGET_CALENDAR_ID = 'cargoada@gmail.com' 
st.set_page_config(page_title="家教排課系統 v2.9", page_icon="📅", layout="centered")
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']

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
    st.title("👋 歡迎使用排課系統 v2.9")
    if "users" in st.secrets:
        selected_login = st.selectbox("請選擇您的身分", list(st.secrets["users"].keys()))
        if st.button("🚀 進入系統", type="primary", use_container_width=True):
            st.session_state.current_user = selected_login
            st.rerun()
    st.stop()

CURRENT_SHEET_URL = st.secrets["users"][st.session_state.current_user]
conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 3. 雲端讀寫與日曆模組 
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
        if df is None or df.empty: return pd.DataFrame(columns=req_cols)
        for col in req_cols:
            if col not in df.columns: df[col] = ""
        for num_col in ['id', 'invoice_id', 'actual_rate', 'is_paid', 'cumulative_offset']:
            if num_col in df.columns: df[num_col] = pd.to_numeric(df[num_col], errors='coerce').fillna(0)
        return df.copy()
    except: return pd.DataFrame(columns=req_cols)

def save_to_cloud(ws_name, df):
    try:
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=ws_name, data=df.astype(object).fillna(""))
        st.cache_data.clear() 
    except Exception as e: st.error(f"雲端寫入失敗: {e}")

def do_gcal(action, title="", s_dt=None, e_dt=None, eid=""):
    if service is None: return "" if action=="insert" else False
    try:
        if action == "insert":
            ev = service.events().insert(calendarId=TARGET_CALENDAR_ID, body={'summary': title, 'start': {'dateTime': s_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}, 'end': {'dateTime': e_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}}).execute()
            return ev.get('id', "")
        elif action == "update":
            service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=eid, body={'summary': title, 'start': {'dateTime': s_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}, 'end': {'dateTime': e_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'}}).execute()
            return True
        elif action == "delete" and eid:
            service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=eid).execute()
            return True
    except Exception as e:
        st.toast(f"日曆{action}失敗", icon="❌")
        return "" if action=="insert" else False

# ==========================================
# 4. 初始化記憶體
# ==========================================
if 'initialized' not in st.session_state:
    st.session_state.initialized = False
    for k in ['df_stu', 'df_sess', 'df_inv', 'df_stats']: st.session_state[k] = pd.DataFrame()

if not st.session_state.initialized:
    with st.spinner("⚡ 載入雲端資料中..."):
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

with st.sidebar:
    st.header(f"👤 {st.session_state.current_user}")
    if st.button("🔄 同步/刷新", use_container_width=True): st.session_state.initialized = False; st.rerun()
    if st.button("🚪 登出系統", use_container_width=True): st.session_state.current_user = None; st.session_state.initialized = False; st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況中心", "📅 課表排程", "💰 帳單中心", "🧑‍🎓 學生戰情"])

# ==========================================
# TAB 1: 概況中心
# ==========================================
with tab1:
    st.subheader("📊 營收動態與行程追蹤")
    with st.container(border=True):
        cal_date = st.date_input("查看當天課表：", datetime.now(), label_visibility="collapsed")
        if not st.session_state.df_sess.empty:
            df_c = st.session_state.df_sess.copy()
            df_c['start_dt'] = pd.to_datetime(df_c['start_time'], errors='coerce')
            df_c = df_c.dropna(subset=['start_dt'])
            df_td = df_c[(df_c['start_dt'].dt.strftime('%Y-%m-%d') == cal_date.strftime('%Y-%m-%d')) & (df_c['status'] != '已取消')].sort_values('start_dt')
            st.markdown(f"**🔍 {cal_date.strftime('%m/%d')} 課表明細：**")
            if not df_td.empty:
                for _, r in df_td.iterrows():
                    rsid = str(r['student_id']).strip().split('.')[0]
                    sid = name_to_id.get(rsid, rsid)
                    sname, scolor = name_map.get(sid, "未知"), color_map.get(sid, "#3498DB")
                    c_stt = r.get('status', '已預約')
                    
                    with st.container(border=True):
                        c1, c2, c3, c4 = st.columns([3.5, 1.8, 1.8, 1.8])
                        c1.markdown(f"▶️ <span style='color:{scolor};'>●</span> **{r['start_dt'].strftime('%H:%M')}** │ 🧑‍🎓 **{sname}**", unsafe_allow_html=True)
                        if r.get('progress'): c1.caption(f"🏷 {r['progress']}")
                        
                        if c2.checkbox("✅ 完課", value=(c_stt == '已完成'), key=f"d_{r['id']}") != (c_stt == '已完成'):
                            st.session_state.df_sess.loc[st.session_state.df_sess['id']==r['id'], 'status'] = '已完成' if c_stt != '已完成' else '已預約'
                            save_to_cloud("sessions", st.session_state.df_sess); st.rerun()
                        if c3.button("❌ 停課", key=f"c_{r['id']}"):
                            do_gcal("delete", eid=r.get('google_event_id', ""))
                            st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id'] != r['id']]
                            save_to_cloud("sessions", st.session_state.df_sess); st.rerun()
                        with c4.popover("📅 調課"):
                            with st.form(f"p_{r['id']}"):
                                nd, nt = st.date_input("日期", r['start_dt'].date()), st.time_input("時間", r['start_dt'].time())
                                old_h = 1.5 if pd.isna(r.get('end_time')) else (pd.to_datetime(r['end_time']) - r['start_dt']).total_seconds()/3600
                                if st.form_submit_button("💾 確定"):
                                    ns_dt = datetime.combine(nd, nt)
                                    ne_dt = ns_dt + timedelta(hours=old_h)
                                    st.session_state.df_sess.loc[st.session_state.df_sess['id']==r['id'], ['start_time','end_time']] = [ns_dt.strftime('%Y-%m-%dT%H:%M:%S'), ne_dt.strftime('%Y-%m-%dT%H:%M:%S')]
                                    if r.get('google_event_id'): do_gcal("update", f"家教: {sname}", ns_dt, ne_dt, r['google_event_id'])
                                    save_to_cloud("sessions", st.session_state.df_sess); st.rerun()
            else: st.write("🏝️ 沒排課，好好休息！")

    # 營收計算
    if not st.session_state.df_sess.empty:
        df_calc = st.session_state.df_sess.copy()
        df_calc['sdt'] = pd.to_datetime(df_calc['start_time'], errors='coerce')
        df_calc['edt'] = pd.to_datetime(df_calc['end_time'], errors='coerce').fillna(df_calc['sdt'] + timedelta(hours=1.5))
        df_calc = df_calc[~df_calc['status'].isin(['請假', '已取消'])].dropna(subset=['sdt'])
        df_calc['amt'] = ((df_calc['edt'] - df_calc['sdt']).dt.total_seconds() / 3600) * pd.to_numeric(df_calc['actual_rate'], errors='coerce').fillna(0)
        now = datetime.now()
        this_m = df_calc[df_calc['sdt'].dt.month == now.month]['amt'].sum()
        pend = df_calc[(df_calc['edt'] < now) & (pd.to_numeric(df_calc['invoice_id'], errors='coerce').fillna(0) == 0)]['amt'].sum()
        hist = df_calc['amt'].sum() + (float(st.session_state.df_stats['cumulative_offset'].iloc[0]) if not st.session_state.df_stats.empty else 0)
        
        st.columns(3)[0].metric("本月預估", f"${int(this_m):,}"); st.columns(3)[1].metric("未結算", f"${int(pend):,}"); st.columns(3)[2].metric("總收入", f"${int(hist):,}")
        df_calc['m'] = df_calc['sdt'].dt.strftime('%Y-%m')
        st.bar_chart(df_calc.groupby('m')['amt'].sum(), color="#3498DB")

# ==========================================
# TAB 2: 課表排程
# ==========================================
with tab2:
    if st.session_state.df_stu.empty: st.warning("請先建立學生檔案！")
    else:
        with st.expander("➕ 單堂課程"):
            with st.form("sf"):
                c1, c2, c3, c4 = st.columns(4)
                sid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x])
                dt, tm, dur = c2.date_input("日期"), c3.time_input("時間", datetime.now().replace(minute=0,second=0)), c4.slider("時數", 0.5, 4.0, 1.5, 0.5)
                sy, prog = st.checkbox("同步 Google 日曆"), st.text_input("備註")
                if st.form_submit_button("確認建立", type="primary"):
                    sdt, edt = datetime.combine(dt, tm), datetime.combine(dt, tm) + timedelta(hours=dur)
                    gid = do_gcal("insert", f"家教: {name_map[sid]}", sdt, edt) if sy else ""
                    mid = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty else 0
                    st.session_state.df_sess.loc[len(st.session_state.df_sess)] = [mid+1, sid, sdt.strftime('%Y-%m-%dT%H:%M:%S'), edt.strftime('%Y-%m-%dT%H:%M:%S'), '已預約', rate_map.get(sid,500), gid, prog, 0]
                    save_to_cloud("sessions", st.session_state.df_sess); st.rerun()

        with st.expander("📅 大範圍區間排課"):
            with st.form("rf"):
                c1, c2, c3 = st.columns([2, 1, 1])
                rsid = c1.selectbox("學生", list(name_map.keys()), format_func=lambda x: name_map[x])
                sdt, edt = c2.date_input("開始", datetime.now()), c3.date_input("結束", datetime.now()+timedelta(days=60))
                days = st.multiselect("星期", list({"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6}.keys()), default=["一"])
                c4, c5 = st.columns(2)
                rtm, rdur = c4.time_input("時間", datetime.now().replace(hour=14,minute=0,second=0)), c5.slider("時數", 0.5, 4.0, 2.0, 0.5)
                rsy, rnote = st.checkbox("同步日曆"), st.text_input("區間備註")
                if st.form_submit_button("大量建立", type="primary"):
                    nums = [{"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6}[d] for d in days]
                    cur, l = sdt, []
                    mid = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty else 0
                    with st.spinner("排課中..."):
                        while cur <= edt:
                            if cur.weekday() in nums:
                                ns, ne = datetime.combine(cur, rtm), datetime.combine(cur, rtm) + timedelta(hours=rdur)
                                gid = do_gcal("insert", f"家教: {name_map[rsid]}", ns, ne) if rsy else ""
                                mid += 1
                                l.append({'id': mid, 'student_id': rsid, 'start_time': ns.strftime('%Y-%m-%dT%H:%M:%S'), 'end_time': ne.strftime('%Y-%m-%dT%H:%M:%S'), 'status': '已預約', 'actual_rate': rate_map.get(rsid,500), 'google_event_id': gid, 'progress': rnote, 'invoice_id': 0})
                                if rsy: time.sleep(0.15)
                            cur += timedelta(days=1)
                        if l:
                            st.session_state.df_sess = pd.concat([st.session_state.df_sess, pd.DataFrame(l)], ignore_index=True)
                            save_to_cloud("sessions", st.session_state.df_sess); st.rerun()

        with st.expander("🧹 批量刪課 (清理錯誤排課)"):
            if not st.session_state.df_sess.empty:
                df_del = st.session_state.df_sess.copy()
                df_del['dt'] = pd.to_datetime(df_del['start_time'], errors='coerce')
                df_del = df_del.dropna(subset=['dt']).sort_values('dt', ascending=False)
                opts = {f"{r['dt'].strftime('%Y-%m-%d %H:%M')} | {name_map.get(name_to_id.get(str(r['student_id']).split('.')[0], str(r['student_id']).split('.')[0]), '未知')} (ID:{r['id']})": r['id'] for _, r in df_del.iterrows()}
                sel = st.multiselect("選取刪除", list(opts.keys()))
                if st.button("🚨 批量刪除", type="primary") and sel:
                    ids = [opts[k] for k in sel]
                    with st.spinner("清除中..."):
                        for i in ids:
                            gid = str(st.session_state.df_sess[st.session_state.df_sess['id']==i]['google_event_id'].iloc[0])
                            if gid and gid.lower() not in ["nan", "none", ""]: do_gcal("delete", eid=gid)
                        st.session_state.df_sess = st.session_state.df_sess[~st.session_state.df_sess['id'].isin(ids)]
                        save_to_cloud("sessions", st.session_state.df_sess); st.rerun()

    st.divider(); st.subheader("📋 未來課表調課中心")
    if not st.session_state.df_sess.empty:
        df_l = st.session_state.df_sess.copy()
        df_l['dt'] = pd.to_datetime(df_l['start_time'], errors='coerce')
        df_l['edt'] = pd.to_datetime(df_l['end_time'], errors='coerce').fillna(df_l['dt'] + timedelta(hours=1.5))
        df_l = df_l.dropna(subset=['dt'])
        df_f = df_l[df_l['edt'] >= datetime.now()].sort_values('dt')
        for _, r in df_f.iterrows():
            sn = name_map.get(name_to_id.get(str(r['student_id']).split('.')[0], str(r['student_id']).split('.')[0]), '未知')
            ico = "⚠️" if r.get('status')=="請假" else "❌" if r.get('status')=="已取消" else ""
            with st.expander(f"{r['dt'].strftime('%m/%d %H:%M')} │ 🧑‍🎓 {sn} {ico}"):
                with st.form(f"ef_{r['id']}"):
                    n_dt, n_st = st.date_input("日期", r['dt'].date()), st.time_input("時間", r['dt'].time())
                    n_dur = st.slider("時數", 0.5, 4.0, (r['edt']-r['dt']).total_seconds()/3600, 0.5)
                    stt = st.selectbox("狀態", ["已預約","請假","已取消","已完成"], index=["已預約","請假","已取消","已完成"].index(r.get('status','已預約') if r.get('status') in ["已預約","請假","已取消","已完成"] else "已預約"))
                    c1, c2 = st.columns([3, 1])
                    if c1.form_submit_button("💾 更新", type="primary"):
                        ns, ne = datetime.combine(n_dt, n_st), datetime.combine(n_dt, n_st) + timedelta(hours=n_dur)
                        st.session_state.df_sess.loc[st.session_state.df_sess['id']==r['id'], ['start_time','end_time','status']] = [ns.strftime('%Y-%m-%dT%H:%M:%S'), ne.strftime('%Y-%m-%dT%H:%M:%S'), stt]
                        gid = r.get('google_event_id', "")
                        if gid and gid.lower() not in ["nan", "none", ""]:
                            if stt in ["請假", "已取消"]: do_gcal("delete", eid=gid); st.session_state.df_sess.loc[st.session_state.df_sess['id']==r['id'], 'google_event_id']=""
                            else: do_gcal("update", f"家教: {sn}", ns, ne, gid)
                        save_to_cloud("sessions", st.session_state.df_sess); st.rerun()
                    if st.button("🗑️ 刪除", key=f"d_{r['id']}"):
                        gid = r.get('google_event_id', "")
                        if gid and gid.lower() not in ["nan", "none", ""]: do_gcal("delete", eid=gid)
                        st.session_state.df_sess = st.session_state.df_sess[st.session_state.df_sess['id']!=r['id']]
                        save_to_cloud("sessions", st.session_state.df_sess); st.rerun()

# ==========================================
# TAB 3: 帳單中心
# ==========================================
with tab3:
    st.subheader("💰 未結算課程與開單")
    if st.button("⚡ 分月開單", type="primary"):
        if not st.session_state.df_sess.empty:
            df = st.session_state.df_sess.copy()
            df['dt'] = pd.to_datetime(df['start_time'], errors='coerce')
            pm = (~df['status'].isin(['請假','已取消'])) & (df['dt'] < datetime.now()) & (pd.to_numeric(df.get('invoice_id',0), errors='coerce').fillna(0) == 0)
            df_p = df[pm].copy()
            if not df_p.empty:
                df_p['mk'] = df_p['dt'].dt.strftime('%Y-%m')
                mid = int(st.session_state.df_inv['id'].max()) if not st.session_state.df_inv.empty else 0
                for (sid, mk), grp in df_p.groupby(['student_id', 'mk']):
                    mid += 1
                    amt = sum([((pd.to_datetime(r['end_time'] if pd.notna(r.get('end_time')) else r['dt']+timedelta(hours=1.5))-r['dt']).total_seconds()/3600)*int(r.get('actual_rate',500)) for _, r in grp.iterrows() if pd.notna(r['dt'])])
                    st.session_state.df_inv.loc[len(st.session_state.df_inv)] = [mid, str(sid).split('.')[0], int(amt), datetime.now().isoformat(), 0, mk]
                    st.session_state.df_sess.loc[grp.index, 'invoice_id'] = mid
                save_to_cloud("invoices", st.session_state.df_inv); save_to_cloud("sessions", st.session_state.df_sess); st.rerun()
                
    st.divider(); st.subheader("⏳ 未收賬單")
    if not st.session_state.df_inv.empty:
        upd = st.session_state.df_inv[pd.to_numeric(st.session_state.df_inv['is_paid'], errors='coerce').fillna(0) == 0]
        for _, r in upd.iterrows():
            sn = name_map.get(name_to_id.get(str(r['student_id']).split('.')[0], str(r['student_id']).split('.')[0]), '未知')
            with st.container(border=True):
                st.markdown(f"### {sn} ({r.get('note', '')}) - 💰 ${int(r['total_amount']):,}")
                c1, c2 = st.columns(2)
                if c2.button("💵 已收款", key=f"pay_{r['id']}", type="primary"):
                    st.session_state.df_inv.loc[st.session_state.df_inv['id']==r['id'], 'is_paid'] = 1
                    save_to_cloud("invoices", st.session_state.df_inv); st.rerun()
                with c1.expander("複製 Line 文案"):
                    mls = st.session_state.df_sess[pd.to_numeric(st.session_state.df_sess.get('invoice_id',0), errors='coerce').fillna(0) == int(r['id'])].copy()
                    if not mls.empty:
                        mls['dt'] = pd.to_datetime(mls['start_time'])
                        msg = [f"【{sn} {r.get('note','')} 費用明細】"]
                        for _, ls in mls.sort_values('dt').iterrows():
                            h = (pd.to_datetime(ls.get('end_time') if pd.notna(ls.get('end_time')) else ls['dt']+timedelta(hours=1.5)) - ls['dt']).total_seconds()/3600
                            msg.append(f"📌 {ls['dt'].strftime('%m/%d')} ({h:.1f}h) : ${int(h*int(ls.get('actual_rate',500))):,}")
                        msg.append(f"\n總計：${int(r['total_amount'])}元，謝謝老師！")
                        st.code("\n".join(msg), language=None)

# ==========================================
# TAB 4: 學生戰情
# ==========================================
with tab4:
    with st.expander("➕ 新增學生"):
        with st.form("asf"):
            c1, c2 = st.columns(2)
            n, rt = c1.text_input("姓名"), c2.number_input("時薪", value=700)
            col = st.selectbox("顏色", ["#FF5733", "#3498DB", "#2ECC71", "#F1C40F", "#9B59B6"])
            if st.form_submit_button("儲存"):
                mid = int(st.session_state.df_stu['id'].max()) if not st.session_state.df_stu.empty else 0
                st.session_state.df_stu.loc[len(st.session_state.df_stu)] = [mid+1, n, rt, col.split(" ")[0]]
                save_to_cloud("students", st.session_state.df_stu); st.rerun()
                
    st.divider()
    if not st.session_state.df_stu.empty:
        for _, r in st.session_state.df_stu.iterrows():
            sid, sn = str(int(r['id'])), r['name']
            df_ms = st.session_state.df_sess[st.session_state.df_sess['student_id'].astype(str).str.split('.').str[0].map(lambda x: name_to_id.get(x,x)) == sid].copy() if not st.session_state.df_sess.empty else pd.DataFrame()
            
            with st.container(border=True):
                c1, c2, c3 = st.columns([0.5, 4, 1.5])
                c1.markdown(f"<div style='width:25px;height:25px;background-color:{r.get('color','#3498DB')};border-radius:50%;'></div>", unsafe_allow_html=True)
                c2.markdown(f"### {sn} (${r.get('default_rate',500)}/hr)")
                if c3.button("🗑️ 移除", key=f"ds_{sid}"):
                    st.session_state.df_stu = st.session_state.df_stu[st.session_state.df_stu['id']!=r['id']]
                    save_to_cloud("students", st.session_state.df_stu); st.rerun()
                
                with st.expander("🪄 智慧續排"):
                    if not df_ms.empty:
                        df_ms['dt'] = pd.to_datetime(df_ms['start_time'], errors='coerce')
                        last_class = df_ms['dt'].max()
                        wp = df_ms[df_ms['dt'] >= last_class - timedelta(days=6)]
                        for _, w in wp.iterrows(): st.write(f"📅 {w['dt'].strftime('%a %H:%M')}")
                        wk = st.number_input("展延週數", 1, 8, 4, key=f"w_{sid}")
                        sy = st.checkbox("同步日曆", key=f"sg_{sid}")
                        if st.button("🚀 產生", key=f"rn_{sid}"):
                            mid = int(st.session_state.df_sess['id'].max()) if not st.session_state.df_sess.empty else 0
                            l = []
                            for i in range(1, int(wk)+1):
                                for _, p in wp.iterrows():
                                    ns = pd.to_datetime(p['start_time']) + timedelta(weeks=i)
                                    ne = (pd.to_datetime(p['end_time']) if pd.notna(p['end_time']) else ns+timedelta(hours=1.5)) + timedelta(weeks=i)
                                    gid = do_gcal("insert", f"家教: {sn}", ns, ne) if sy else ""
                                    mid += 1; l.append({'id':mid, 'student_id':sid, 'start_time':ns.strftime('%Y-%m-%dT%H:%M:%S'), 'end_time':ne.strftime('%Y-%m-%dT%H:%M:%S'), 'status':'已預約', 'actual_rate':r.get('default_rate',500), 'google_event_id':gid, 'progress':'', 'invoice_id':0})
                            if l:
                                st.session_state.df_sess = pd.concat([st.session_state.df_sess, pd.DataFrame(l)], ignore_index=True)
                                save_to_cloud("sessions", st.session_state.df_sess); st.rerun()

# ===== 程式碼結束 =====
