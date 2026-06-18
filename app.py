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

st.set_page_config(page_title="家教排課系統 v2.5", page_icon="📅", layout="centered")

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
    st.title("👋 歡迎使用排課系統 2.5")
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
    try:
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=0)
        if df is None: df = pd.DataFrame()
        
        schemas = {
            "sessions": ['id', 'student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'google_event_id', 'progress', 'invoice_id'],
            "invoices": ['id', 'student_id', 'total_amount', 'created_at', 'is_paid', 'note'],
            "students": ['id', 'name', 'default_rate', 'color'],
            "stats": ['cumulative_offset']
        }
        
        req_cols = schemas.get(worksheet_name, [])
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
    except Exception as e:
        return pd.DataFrame()

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
# 4. 主程式資料預載
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況中心", "📅 課表排程", "💰 帳單中心", "🧑‍🎓 學生戰情"])

df_stu = get_cloud_data("students")
df_sess = get_cloud_data("sessions")
df_inv = get_cloud_data("invoices")
df_stats = get_cloud_data("stats")

# 建立雙向名字/ID 識別字典
student_name_map = {}
student_rate_map = {}
student_color_map = {}
student_name_to_id = {}

if not df_stu.empty and 'id' in df_stu.columns:
    for _, r in df_stu.iterrows():
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
    st.caption(f"📅 系統時間：{datetime.now().strftime('%Y-%m-%d')}")
    if st.button("🚪 登出系統", use_container_width=True):
        st.session_state.current_user = None
        st.rerun()

# ==========================================
# TAB 1: 概況中心
# ==========================================
with tab1:
    st.subheader("📊 營收動態與行程追蹤")
    
    with st.container(border=True):
        st.markdown("### 📅 老師小日曆")
        cal_date = st.date_input("點擊下方日曆切換日期，可查看當天課表：", datetime.now(), label_visibility="collapsed")
        
        if not df_sess.empty:
            df_cal_check = df_sess.copy()
            df_cal_check['start_dt_safe'] = pd.to_datetime(df_cal_check['start_time'], errors='coerce')
            df_cal_check = df_cal_check.dropna(subset=['start_dt_safe'])
            
            target_date_str = cal_date.strftime('%Y-%m-%d')
            df_today_lessons = df_cal_check[(df_cal_check['start_dt_safe'].dt.strftime('%Y-%m-%d') == target_date_str) & (df_cal_check['status'] != '已取消')]
            df_today_lessons = df_today_lessons.sort_values('start_dt_safe')
            
            st.markdown(f"**🔍 {cal_date.strftime('%m/%d')} 當日課表明細：**")
            if not df_today_lessons.empty:
                for _, l_row in df_today_lessons.iterrows():
                    raw_sid = str(l_row['student_id']).strip().split('.')[0]
                    s_id_str = student_name_to_id.get(raw_sid, raw_sid)
                    l_name = student_name_map.get(s_id_str, "未知學生")
                    l_color = student_color_map.get(s_id_str, "#3498DB")
                    
                    s_time = l_row['start_dt_safe'].strftime('%H:%M')
                    st.markdown(f"▶️ `<span style='color:{l_color};'>●</span>` **{s_time}** │ 🧑‍🎓 **{l_name}** {'(請假)' if l_row.get('status') == '請假' else ''}", unsafe_allow_html=True)
                    if l_row.get('progress', ""):
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;🏷 *備註：{l_row['progress']}")
            else:
                st.write("🏝️ 這天目前沒有排課，好好休息一下吧！")
        else:
            st.write("尚無排課資料。")
            
    st.divider()
    
    hist_offset = 0
    if not df_stats.empty and 'cumulative_offset' in df_stats.columns:
        try: hist_offset = float(df_stats['cumulative_offset'].iloc[0])
        except: pass
        
    if not df_sess.empty and 'start_time' in df_sess.columns:
        df_calc = df_sess.copy()
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
    if df_stu.empty:
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
                    
                    max_id = int(df_sess['id'].max()) if not df_sess.empty and 'id' in df_sess.columns else 0
                    new_lesson = pd.DataFrame([{
                        'id': max_id + 1, 'student_id': choose_stu_id,
                        'start_time': start_datetime.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_datetime.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': '已預約', 'actual_rate': target_rate,
                        'google_event_id': g_id, 'progress': lesson_prog, 'invoice_id': 0
                    }])
                    
                    df_sess = pd.concat([df_sess, new_lesson], ignore_index=True)
                    save_to_cloud("sessions", df_sess)
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
                        max_id_bulk = int(df_sess['id'].max()) if not df_sess.empty and 'id' in df_sess.columns else 0
                        
                        with st.spinner("正在為您精算區間日期並建立課表中..."):
                            while current_date <= r_end_date:
                                if current_date.weekday() in target_wday_nums:
                                    s_dt = datetime.combine(current_date, r_time)
                                    e_dt = s_dt + timedelta(hours=r_dur)
                                    
                                    g_id = ""
                                    if r_sync_gcal:
                                        g_id = create_google_event(f"家教: {student_name_map[r_stu_id]}", s_dt, e_dt)
                                        time.sleep(0.15) 
                                    
                                    max_id_bulk += 1
                                    new_lessons_bulk.append({
                                        'id': max_id_bulk, 'student_id': r_stu_id,
                                        'start_time': s_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                                        'end_time': e_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                                        'status': '已預約', 'actual_rate': target_rate,
                                        'google_event_id': g_id, 'progress': r_note, 'invoice_id': 0
                                    })
                                current_date += timedelta(days=1)
                        
                        if new_lessons_bulk:
                            df_sess = pd.concat([df_sess, pd.DataFrame(new_lessons_bulk)], ignore_index=True)
                            save_to_cloud("sessions", df_sess)
                            st.success(f"🎉 成功！已自動在區間內建立 {len(new_lessons_bulk)} 堂課程。")
                            time.sleep(1)
                            st.rerun()

        # --- 🔥 全新：批量刪除多堂課程 ---
        with st.expander("🧹 批量秒殺【多堂】課程 (清理錯誤排課)"):
            st.markdown("請在下方勾選你想一次刪除的課程：")
            if not df_sess.empty:
                df_del = df_sess.copy()
                df_del['dt_order'] = pd.to_datetime(df_del['start_time'], errors='coerce')
                df_del = df_del.dropna(subset=['dt_order']).sort_values('dt_order', ascending=False)
                
                del_options_map = {}
                for _, r in df_del.iterrows():
                    raw_sid = str(r['student_id']).strip().split('.')[0]
                    s_id_str = student_name_to_id.get(raw_sid, raw_sid)
                    s_name = student_name_map.get(s_id_str, "未知")
                    # 建立選單標籤: "2026-06-15 14:00 | 🧑‍🎓 沛倢 (ID: 15)"
                    label = f"{r['dt_order'].strftime('%Y-%m-%d %H:%M')} | 🧑‍🎓 {s_name} (編號:{r['id']})"
                    del_options_map[label] = r['id']
                
                selected_to_delete = st.multiselect("選取要刪除的課程 (可多選)", list(del_options_map.keys()))
                
                if st.button("🚨 確認批量刪除", type="primary"):
                    if selected_to_delete:
                        ids_to_del = [del_options_map[k] for k in selected_to_delete]
                        with st.spinner(f"🧹 正在批次清除 {len(ids_to_del)} 堂雲端資料與日曆，請稍候..."):
                            for del_id in ids_to_del:
                                row_to_del = df_sess[df_sess['id'] == del_id]
                                if not row_to_del.empty:
                                    raw_gid = row_to_del.iloc[0].get('google_event_id', "")
                                    gid = str(raw_gid).strip() if pd.notna(raw_gid) and str(raw_gid).strip().lower() not in ["", "nan", "none"] else ""
                                    if gid:
                                        delete_google_event(gid)
                                        time.sleep(0.15) # 🛡️ 給 Google API 喘息的冷卻時間
                            
                            df_sess = df_sess[~df_sess['id'].isin(ids_to_del)]
                            save_to_cloud("sessions", df_sess)
                            
                        st.success(f"✅ 成功秒殺 {len(ids_to_del)} 堂課程！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("請先從上方選單中選取至少一堂課喔！")
            else:
                st.info("目前沒有課程可供刪除。")

    # --- 課表列表總覽 ---
    st.divider()
    st.subheader("📋 未來所有課表總覽 (調課/請假中心)")
    
    if not df_sess.empty:
        df_list = df_sess.copy()
        df_list['dt_order'] = pd.to_datetime(df_list['start_time'], errors='coerce')
        df_list['dt_end_order'] = pd.to_datetime(df_list['end_time'], errors='coerce')
        
        df_list['dt_end_order'] = df_list['dt_end_order'].fillna(df_list['dt_order'] + timedelta(hours=1.5))
        df_list = df_list.dropna(subset=['dt_order']) 
        
        now_time = datetime.now()
        df_filtered = df_list[df_list['dt_end_order'] >= now_time]
        df_filtered = df_filtered.sort_values('dt_order', ascending=True)
        
        if not df_filtered.empty:
            for idx, row in df_filtered.iterrows():
                raw_sid = str(row['student_id']).strip().split('.')[0]
                s_id_str = student_name_to_id.get(raw_sid, raw_sid)
                name_display = student_name_map.get(s_id_str, "未知學生")
                color_display = student_color_map.get(s_id_str, "#3498DB")
                
                raw_gid = row.get('google_event_id')
                gid = ""
                if pd.notna(raw_gid) and str(raw_gid).strip().lower() not in ["", "nan", "none", "0", "0.0"]:
                    gid = str(raw_gid).strip()
                
                status_icon = "⚠️ (請假)" if row.get('status') == "請假" else "❌ (取消)" if row.get('status') == "已取消" else ""
                panel_title = f"{row['dt_order'].strftime('%m/%d %H:%M')} │ 🧑‍🎓 {name_display} {status_icon}"
                
                with st.expander(panel_title):
                    with st.form(f"edit_form_{row['id']}"):
                        n_dt = st.date_input("日期", row['dt_order'].date())
                        n_st = st.time_input("開始時間", row['dt_order'].time())
                        old_dur = (row['dt_end_order'] - row['dt_order']).total_seconds() / 3600
                        n_dur = st.slider("時數", 0.5, 4.0, float(old_dur) if float(old_dur)>=0.5 else 1.5, 0.5, key=f"d_{row['id']}")
                        
                        curr_status = row.get('status', '已預約')
                        if curr_status not in ["已預約", "請假", "已取消", "已完成"]: curr_status = "已預約"
                        n_stt = st.selectbox("狀態", ["已預約", "請假", "已取消", "已完成"], index=["已預約", "請假", "已取消", "已完成"].index(curr_status))
                        n_prog = st.text_area("進度/備註", value=row.get('progress', ''))
                        
                        col_upd, col_del = st.columns([3, 1])
                        
                        if col_upd.form_submit_button("💾 更新此堂課", type="primary"):
                            # 🛡️ 加上防護罩與轉圈圈
                            with st.spinner("💾 正在與雲端同步更新中，請稍候..."):
                                n_start_dt = datetime.combine(n_dt, n_st)
                                n_end_dt = n_start_dt + timedelta(hours=n_dur)
                                
                                df_sess.loc[df_sess['id']==row['id'], 'start_time'] = n_start_dt.strftime('%Y-%m-%dT%H:%M:%S')
                                df_sess.loc[df_sess['id']==row['id'], 'end_time'] = n_end_dt.strftime('%Y-%m-%dT%H:%M:%S')
                                df_sess.loc[df_sess['id']==row['id'], 'status'] = n_stt
                                df_sess.loc[df_sess['id']==row['id'], 'progress'] = n_prog
                                
                                if n_stt in ["請假", "已取消"]:
                                    if gid:
                                        delete_google_event(gid)
                                        df_sess.loc[df_sess['id']==row['id'], 'google_event_id'] = ""
                                else:
                                    if gid:
                                        update_google_event(gid, f"家教: {name_display}", n_start_dt, n_end_dt)
                                        
                                save_to_cloud("sessions", df_sess)
                                time.sleep(0.5) # 🛡️ 給 Google API 冷卻
                            st.rerun()
                            
                    if st.button("🗑️ 徹底刪除", key=f"delete_btn_{row['id']}"):
                        # 🛡️ 加上防護罩與轉圈圈
                        with st.spinner("🗑️ 正在清除雲端資料與日曆，請稍候..."):
                            if gid: delete_google_event(gid)
                            df_sess = df_sess[df_sess['id'] != row['id']]
                            save_to_cloud("sessions", df_sess)
                            time.sleep(0.5) # 🛡️ 給 Google API 冷卻
                        st.rerun()
        else:
            st.info("🎯 目前沒有任何未來的排課行程。")
    else:
        st.info("目前沒有任何排課紀錄。")

# ==========================================
# TAB 3: 帳單中心
# ==========================================
with tab3:
    st.subheader("💰 未結算課程與開單")
    if st.button("⚡ 智慧全自動分月開單", type="primary", use_container_width=True):
        if not df_sess.empty:
            df_sess['dt_safe'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
            df_sess['inv_safe'] = pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0).astype(int)
            now = datetime.now()
            
            pending_mask = (~df_sess['status'].isin(['請假', '已取消'])) & (df_sess['dt_safe'] < now) & (df_sess['inv_safe'] == 0)
            df_pending = df_sess[pending_mask].copy()
            
            if not df_pending.empty:
                df_pending['month_key'] = df_pending['dt_safe'].dt.strftime('%Y-%m')
                groups = df_pending.groupby(['student_id', 'month_key'])
                new_bill_count = 0
                max_inv_id = int(df_inv['id'].max()) if not df_inv.empty and 'id' in df_inv.columns else 0
                
                for (s_id, m_str), group in groups:
                    max_inv_id += 1
                    total_bill_amt = 0
                    for _, r in group.iterrows():
                        try:
                            s_time_p = pd.to_datetime(r['start_time'])
                            e_time_p = pd.to_datetime(r['end_time'], errors='coerce')
                            if pd.isna(e_time_p): e_time_p = s_time_p + timedelta(hours=1.5)
                            h = (e_time_p - s_time_p).total_seconds() / 3600
                            total_bill_amt += h * int(r.get('actual_rate', 500))
                        except: pass
                    
                    new_inv_row = pd.DataFrame([{
                        'id': max_inv_id, 'student_id': str(s_id).split('.')[0],
                        'total_amount': int(total_bill_amt), 'created_at': datetime.now().isoformat(),
                        'is_paid': 0, 'note': m_str
                    }])
                    df_inv = pd.concat([df_inv, new_inv_row], ignore_index=True)
                    df_sess.loc[group.index, 'invoice_id'] = max_inv_id
                    new_bill_count += 1
                
                save_to_cloud("invoices", df_inv)
                save_to_cloud("sessions", df_sess.drop(columns=['dt_safe', 'inv_safe']))
                st.toast(f"🧾 結算成功！已自動產出 {new_bill_count} 張帳單。")
                st.rerun()
            else: st.info("目前沒有需要結算的過期課程。")
        else: st.info("尚無排課資料可供結算。")

    st.divider()
    st.subheader("⏳ 未收賬單列表")
    
    if not df_inv.empty and 'is_paid' in df_inv.columns:
        df_unpaid = df_inv[pd.to_numeric(df_inv['is_paid'], errors='coerce').fillna(0) == 0]
        if not df_unpaid.empty:
            for _, row in df_unpaid.iterrows():
                raw_sid = str(row['student_id']).strip().split('.')[0]
                s_id_str = student_name_to_id.get(raw_sid, raw_sid)
                s_name = student_name_map.get(s_id_str, "未知學生")
                bill_month = row.get('note', '未知月份')
                
                with st.container(border=True):
                    st.markdown(f"### **{s_name} ({bill_month})**")
                    st.markdown(f"#### 💰 應繳總額：`${int(row['total_amount']):,}`")
                    
                    c_copy, c_pay = st.columns(2)
                    with c_pay:
                        if st.button("💵 確認已收款", key=f"pay_btn_{row['id']}", type="primary", use_container_width=True):
                            df_inv.loc[df_inv['id'] == row['id'], 'is_paid'] = 1
                            save_to_cloud("invoices", df_inv)
                            st.toast("💵 款項已確認入帳！")
                            st.rerun()
                            
                    with c_copy.expander("💬 複製 Line 請款文案"):
                        try:
                            my_lessons = df_sess[pd.to_numeric(df_sess.get('invoice_id', 0), errors='coerce').fillna(0).astype(int) == int(row['id'])].copy()
                            if not my_lessons.empty:
                                my_lessons['dt_safe'] = pd.to_datetime(my_lessons['start_time'])
                                my_lessons = my_lessons.sort_values('dt_safe')
                                msg = [f"【{s_name} {bill_month} 課程費用明細】\n家長您好，以下是本期課程的費用明細：\n"]
                                for _, ls in my_lessons.iterrows():
                                    dt_safe = ls['dt_safe'].strftime('%m/%d')
                                    ls_end = pd.to_datetime(ls.get('end_time'), errors='coerce')
                                    if pd.isna(ls_end): ls_end = ls['dt_safe'] + timedelta(hours=1.5)
                                    h_safe = (ls_end - ls['dt_safe']).total_seconds() / 3600
                                    cost = int(h_safe * int(ls.get('actual_rate', 500)))
                                    msg.append(f"📌 {dt_safe} ({h_safe:.1f}小時) : ${cost:,}")
                                msg.append(f"\n總計金額：${int(row['total_amount'])}元")
                                msg.append("再麻煩您空閒時留意，謝謝老師！")
                                st.code("\n".join(msg), language=None)
                            else: st.write("未找到關聯課程明細")
                        except: st.write("明細載入失敗")
        else: st.success("🎉 帳單全數結清！")
    else: st.info("無帳單紀錄。")

# ==========================================
# TAB 4: 學生戰情
# ==========================================
with tab4:
    st.subheader("🧑‍🎓 學生名冊管理")
    with st.expander("➕ 新增學生檔案"):
        with st.form("add_student_form"):
            c1, c2 = st.columns(2)
            new_sname = c1.text_input("學生姓名")
            new_srate = c2.number_input("預設每小時時薪", min_value=100, max_value=5000, value=700, step=50)
            new_scolor = st.selectbox("行事曆辨識顏色", ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)", "#9B59B6 (紫)"])
            
            if st.form_submit_button("儲存學生檔案"):
                if new_sname.strip() == "": st.error("請輸入學生姓名")
                else:
                    max_sid = int(df_stu['id'].max()) if not df_stu.empty and 'id' in df_stu.columns else 0
                    new_stu_row = pd.DataFrame([{
                        'id': max_sid + 1, 'name': new_sname, 'default_rate': new_srate, 'color': new_scolor.split(" ")[0]
                    }])
                    df_stu = pd.concat([df_stu, new_stu_row], ignore_index=True)
                    save_to_cloud("students", df_stu)
                    st.toast(f"🎓 已成功建立 {new_sname} 的檔案！")
                    st.rerun()

    st.divider()
    
    if not df_stu.empty:
        weekdays_tw = ["一", "二", "三", "四", "五", "六", "日"]
        for _, row in df_stu.iterrows():
            s_id_str = str(row['id']).split('.')[0]
            s_name = row['name']
            
            my_all_lessons = pd.DataFrame()
            if not df_sess.empty and 'student_id' in df_sess.columns:
                df_sess['resolved_sid'] = df_sess['student_id'].astype(str).str.strip().str.split('.').str[0].map(lambda x: student_name_to_id.get(x, x))
                my_all_lessons = df_sess[df_sess['resolved_sid'] == s_id_str].copy()
            
            with st.container(border=True):
                col_c, col_n, col_act = st.columns([0.5, 4, 1.5])
                col_c.markdown(f'<div style="width:25px;height:25px;background-color:{row.get("color", "#3498DB")};border-radius:50%;margin-top:5px;"></div>', unsafe_allow_html=True)
                col_n.markdown(f"### **{s_name}** (NT$ {row.get('default_rate', 500)}/hr)")
                
                if col_act.button("🗑️ 移除檔案", key=f"del_stu_{s_id_str}"):
                    df_stu = df_stu[df_stu['id'] != row['id']]
                    save_to_cloud("students", df_stu)
                    st.toast("學生檔案已刪除")
                    st.rerun()
                
                with st.expander("🪄 智慧續排 (一鍵展延常規課表)"):
                    if not my_all_lessons.empty:
                        try:
                            my_all_lessons['dt_safe'] = pd.to_datetime(my_all_lessons['start_time'], errors='coerce')
                            my_all_lessons = my_all_lessons.dropna(subset=['dt_safe'])
                            if not my_all_lessons.empty:
                                last_class_time = my_all_lessons['dt_safe'].max()
                                week_pattern = my_all_lessons[my_all_lessons['dt_safe'] >= (last_class_time - timedelta(days=6))]
                                
                                st.write("💡 系統偵測到該生最後一週的上課模式：")
                                for _, w_ls in week_pattern.iterrows():
                                    try:
                                        ls_end_p = pd.to_datetime(w_ls.get('end_time'), errors='coerce')
                                        if pd.isna(ls_end_p): ls_end_p = w_ls['dt_safe'] + timedelta(hours=1.5)
                                        h_diff = (ls_end_p - w_ls['dt_safe']).total_seconds() / 3600
                                        st.caption(f"📅 星期{weekdays_tw[w_ls['dt_safe'].weekday()]} {w_ls['dt_safe'].strftime('%H:%M')} ({h_diff:.1f} 小時)")
                                    except: pass
                                
                                extend_w = st.number_input("展延週數", min_value=1, max_value=8, value=4, key=f"ext_wk_{s_id_str}")
                                sync_g = st.checkbox("同步 Google 日曆", value=False, key=f"sync_g_{s_id_str}")
                                
                                if st.button("🚀 確定產生未來課表", key=f"renew_act_btn_{s_id_str}"):
                                    new_lessons_list = []
                                    max_sess_id = int(df_sess['id'].max()) if not df_sess.empty and 'id' in df_sess.columns else 0
                                    
                                    for week_idx in range(1, int(extend_w) + 1):
                                        for _, p_ls in week_pattern.iterrows():
                                            try:
                                                p_start = pd.to_datetime(p_ls['start_time'])
                                                p_end = pd.to_datetime(p_ls['end_time'], errors='coerce')
                                                if pd.isna(p_end): p_end = p_start + timedelta(hours=1.5)
                                                
                                                new_s = p_start + timedelta(weeks=week_idx)
                                                new_e = p_end + timedelta(weeks=week_idx)
                                                
                                                g_id = ""
                                                if sync_g:
                                                    g_id = create_google_event(f"家教: {s_name}", new_s, new_e)
                                                    time.sleep(0.15)
                                                    
                                                new_lessons_list.append({
                                                    'id': max_sess_id + len(new_lessons_list) + 1, 'student_id': s_id_str,
                                                    'start_time': new_s.strftime('%Y-%m-%dT%H:%M:%S'),
                                                    'end_time': new_e.strftime('%Y-%m-%dT%H:%M:%S'),
                                                    'status': '已預約', 'actual_rate': int(row.get('default_rate', 500)),
                                                    'google_event_id': g_id, 'progress': '', 'invoice_id': 0
                                                })
                                            except: pass
                                    if new_lessons_list:
                                        df_sess = pd.concat([df_sess, pd.DataFrame(new_lessons_list)], ignore_index=True)
                                        if 'sid_str' in df_sess.columns: df_sess = df_sess.drop(columns=['sid_str'])
                                        if 'resolved_sid' in df_sess.columns: df_sess = df_sess.drop(columns=['resolved_sid'])
                                        save_to_cloud("sessions", df_sess)
                                        st.toast("展延成功！")
                                        st.rerun()
                            else: st.info("尚無有效課表紀錄")
                        except: st.info("剖析失敗，請手動建課")
                    else: st.info("目前無歷史排課可供系統學習。")
                    
                with st.expander("📝 歷史上課進度查看"):
                    if not my_all_lessons.empty:
                        try:
                            my_all_lessons['dt_safe_p'] = pd.to_datetime(my_all_lessons['start_time'], errors='coerce')
                            past_ls = my_all_lessons[(my_all_lessons['dt_safe_p'] < datetime.now()) & (my_all_lessons['status'] != '已取消')].sort_values('dt_safe_p', ascending=False)
                            if not past_ls.empty:
                                for _, p_cls in past_ls.iterrows():
                                    st.markdown(f"**📅 {p_cls['start_time'].replace('T', ' ')}** {'(請假)' if p_cls.get('status') == '請假' else ''}")
                                    st.write(p_cls['progress'] if p_cls['progress'] else "（無紀錄）")
                                    st.divider()
                            else: st.write("尚無歷史授課紀錄。")
                        except: pass
                    else: st.write("尚無紀錄。")
                    
                with st.expander("💬 產生 Line 課表通知文案"):
                    if not my_all_lessons.empty:
                        try:
                            my_all_lessons['dt_safe_f'] = pd.to_datetime(my_all_lessons['start_time'], errors='coerce')
                            fut_ls = my_all_lessons[(my_all_lessons['dt_safe_f'] >= datetime.now()) & (my_all_lessons['status'] == '已預約')].sort_values('dt_safe_f')
                            if not fut_ls.empty:
                                line_msg = [f"【{s_name} 近期課程預告】\n家長您好，以下是接下來的排課時間：\n"]
                                for _, f_cls in fut_ls.iterrows():
                                    f_dt = f_cls['dt_safe_f']
                                    line_msg.append(f"📌 {f_dt.strftime('%m/%d')} ({weekdays_tw[f_dt.weekday()]}) {f_dt.strftime('%H:%M')}")
                                line_msg.append("\n再請您核對時間，謝謝老師！")
                                st.code("\n".join(line_msg), language=None)
                            else: st.write("沒有未來的預約課程")
                        except: pass
                    else: st.write("尚無紀錄。")
    else:
        st.info("目前名冊空空如也，請先點擊上方新增學生喔！")
