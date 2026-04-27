import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time

# 外部套件
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# 1. 系統設定 (請填入你的日曆信箱)
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
# 2. 登入系統與變數初始化
# ==========================================
if 'current_user' not in st.session_state:
    st.session_state.current_user = None

if 'edit_session_id' not in st.session_state:
    st.session_state.edit_session_id = None

# 顯示登入畫面
if st.session_state.current_user is None:
    st.title("👋 歡迎使用排課系統")
    st.markdown("請先選擇您的身分以載入資料：")
    try:
        if "users" in st.secrets:
            user_dict = st.secrets["users"]
            user_list = list(user_dict.keys())
            col1, col2 = st.columns([3, 1])
            with col1:
                selected_login = st.selectbox("請選擇身分", user_list, label_visibility="collapsed")
            with col2:
                if st.button("🚀 進入系統", type="primary"):
                    st.session_state.current_user = selected_login
                    st.rerun()
        else:
            st.error("❌ Secrets 設定檔找不到 [users] 區塊")
    except Exception as e:
        st.error(f"讀取使用者失敗: {e}")
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
# 3. 小幫手函式 (🔥已加入自動補欄位功能)
# ==========================================
with st.sidebar:
    st.header(f"👤 您好，{CURRENT_USER}")
    st.caption(f"日曆同步中：{TARGET_CALENDAR_ID}")
    if st.button("🚪 登出 / 切換身分"):
        st.session_state.current_user = None
        st.cache_data.clear()
        st.rerun()

def get_data(worksheet_name):
    try:
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=600)
        
        # 1. 確保 ID 是數字
        if 'id' in df.columns: 
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        
        # 2. 自動補足缺失欄位 (這是解決 KeyError 的關鍵)
        if 'google_event_id' not in df.columns: df['google_event_id'] = ""
        if 'note' not in df.columns: df['note'] = ""  # <--- 這裡會幫舊帳單補上備註欄位
        
        return df
    except:
        return pd.DataFrame()

def update_data(worksheet_name, df):
    conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df)
    st.cache_data.clear()

# --- 日曆操作 (強制寫入指定信箱) ---
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

# --- Tab 1: 概況 (營收圖表版) ---
with tab1:
    c_title, c_refresh = st.columns([3, 1.5])
    c_title.subheader("📊 營收儀表板")
    
    if c_refresh.button("🔄 刷新數據"):
        st.cache_data.clear()
        st.rerun()

    df_sess = get_data("sessions")
    df_stu = get_data("students")
    
    if not df_sess.empty:
        df_sess['start_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')
        df_sess['actual_rate'] = pd.to_numeric(df_sess['actual_rate'], errors='coerce').fillna(0)
        df_sess['amount'] = ((df_sess['end_dt'] - df_sess['start_dt']).dt.total_seconds() / 3600) * df_sess['actual_rate']
        
        df_sess['safe_invoice_id'] = pd.to_numeric(df_sess['invoice_id'], errors='coerce').fillna(0).astype(int)
        current_time = datetime.now()
        pending_mask = (df_sess['end_dt'] < current_time) & (df_sess['safe_invoice_id'] == 0)
        pending_income = df_sess[pending_mask]['amount'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("本月預估", f"${int(df_sess[df_sess['start_dt'].dt.month == current_time.month]['amount'].sum()):,}")
        col2.metric("待結算", f"${int(pending_income):,}", delta="需開單")
        col3.metric("總收入", f"${int(df_sess['amount'].sum()):,}")

        st.divider()

        if not df_stu.empty:
            chart_df = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', how='left')
            chart_df['name'] = chart_df['name'].fillna("未知")
        else:
            chart_df = df_sess.copy()
            chart_df['name'] = "未知"

        st.subheader("📈 月營收趨勢")
        chart_df['month_str'] = chart_df['start_dt'].dt.strftime('%Y-%m')
        st.bar_chart(chart_df.groupby('month_str')['amount'].sum(), color="#3498DB")

        st.subheader("🏆 學生營收貢獻")
        st.bar_chart(chart_df.groupby('name')['amount'].sum().sort_values(ascending=False), horizontal=True, color="#FF5733")

        with st.expander("🔍 查看「待結算」詳細清單"):
            if pending_income > 0:
                pending_display = chart_df[pending_mask].copy()
                show_list = [{"日期": r['start_dt'].strftime('%m/%d'), "學生": r['name'], "金額": int(r['amount'])} for _, r in pending_display.iterrows()]
                st.dataframe(pd.DataFrame(show_list), use_container_width=True)
            else:
                st.info("目前沒有待結算課程")
    else:
        st.info("尚無課程資料，請先去排課！")

# --- Tab 2: 排課 (開關優化版) ---
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # A. 編輯模式
    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯或刪除課程")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]
        
        if not row.empty:
            row = row.iloc[0]
            s_dt = pd.to_datetime(row['start_time'])
            e_dt = pd.to_datetime(row['end_time'])
            cur_sid = int(row['student_id'])
            s_name = df_stu[df_stu['id'] == cur_sid]['name'].values[0] if cur_sid in df_stu['id'].values else "未知學生"
            old_prog = row['progress'] if 'progress' in row else ""
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
                    edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)
                    edit_prog = st.text_area("當日進度", value=old_prog)
                    submit_save = st.form_submit_button("💾 儲存變更", type="primary")

                col_del, col_cancel = st.columns([1, 1])
                if col_del.button("🗑️ 刪除此課程", key="btn_del_direct"):
                    with st.spinner("正在刪除中..."):
                        if pd.notna(gid) and str(gid) != "" and service:
                            delete_google_event(gid)
                        df_sess = df_sess[df_sess['id'] != edit_id]
                        update_data("sessions", df_sess)
                        time.sleep(1)
                    st.session_state.edit_session_id = None
                    st.toast("🗑️ 課程已刪除")
                    st.rerun()

                if col_cancel.button("❌ 取消返回"):
                    st.session_state.edit_session_id = None
                    st.rerun()

                if submit_save:
                    with st.spinner("更新中..."):
                        new_start = datetime.combine(edit_date, edit_time)
                        new_end = new_start + timedelta(hours=edit_dur)
                        new_sid = student_map[edit_stu]
                        rate = int(df_stu[df_stu['id'] == new_sid]['default_rate'].values[0])
                        idx = df_sess[df_sess['id'] == edit_id].index
                        df_sess.loc[idx, ['student_id', 'start_time', 'end_time', 'actual_rate', 'progress']] = \
                            [new_sid, new_start.strftime('%Y-%m-%dT%H:%M:%S'), new_end.strftime('%Y-%m-%dT%H:%M:%S'), rate, edit_prog]
                        if gid and service: update_google_event(gid, f"家教: {edit_stu}", new_start, new_end)
                        update_data("sessions", df_sess)
                        time.sleep(1)
                    st.session_state.edit_session_id = None
                    st.success("更新成功！")
                    st.rerun()
        else:
            st.error("查無此課程")
            st.session_state.edit_session_id = None
            st.rerun()

    # B. 新增模式 (🔥開關在表單外版)
    else:
        st.subheader("➕ 快速記課")
        
        with st.container(border=True):
            is_recurring = st.toggle("🔁 啟用週期性排課 (一次建立多堂)", value=False)
            
            if is_recurring:
                st.info("💡 您已開啟循環模式，將一次建立多筆課程。")

            with st.form(key="add_form"):
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("首堂日期", datetime.now())
                
                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始時間", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)
                
                repeat_type = "單次 (不重複)"
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

            if add_submit:
                with st.spinner(f"正在建立課程..."):
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
                            time.sleep(0.3)
                        
                        new_rows.append({
                            'id': int(df_sess['id'].max() + 1 + i) if not df_sess.empty else 1 + i,
                            'student_id': sid,
                            'start_time': current_start.strftime('%Y-%m-%dT%H:%M:%S'),
                            'end_time': current_end.strftime('%Y-%m-%dT%H:%M:%S'),
                            'status': '已完成' if current_start < datetime.now() else '已預約',
                            'actual_rate': rate,
                            'google_event_id': g_id,
                            'progress': n_prog
                        })
                    
                    if new_rows:
                        update_data("sessions", pd.concat([df_sess, pd.DataFrame(new_rows)], ignore_index=True))
                    time.sleep(1)
                
                st.success(f"成功建立！")
                st.rerun()

    # C. 日曆與列表區
    st.divider()
    c_cal, c_ref = st.columns([4, 1])
    c_cal.subheader("🗓️ 行事曆")
    if c_ref.button("重整", key="refresh_cal"): 
        st.cache_data.clear()
        st.rerun()
    
    events = []
    if not df_sess.empty and not df_stu.empty:
        try:
            merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
            for _, row in merged.iterrows():
                try:
                    s_iso = pd.to_datetime(row['start_time']).isoformat()
                    e_iso = pd.to_datetime(row['end_time']).isoformat()
                    events.append({
                        "id": str(row['id_x']), "title": row['name'],
                        "start": s_iso, "end": e_iso,
                        "backgroundColor": row['color'], "borderColor": row['color'], "textColor": "#FFFFFF"
                    })
                except: continue
        except: pass

    cal = calendar(events=events, options={"initialView": "dayGridMonth", "height": 650}, callbacks=['eventClick'], key="cal_v_final")
    if cal.get("eventClick"):
        cid = int(cal["eventClick"]["event"]["id"])
        if st.session_state.edit_session_id != cid:
            st.session_state.edit_session_id = cid
            st.rerun()

    with st.expander("📋 詳細列表 / 編輯 / 刪除", expanded=True):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time', ascending=False).head(20)
            for _, row in df_display.iterrows():
                sid = int(row['id_x'])
                gid = row.get('google_event_id', "")
                connected = pd.notna(gid) and str(gid) != ""
                with st.container(border=True):
                    c1, c2, c3 = st.columns([6, 1, 1], gap="small")
                    c1.markdown(f"**{row['name']}**")
                    c1.caption(f"{pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')} {'(✅已同步)' if connected else ''}")
                    with c2:
                        if st.button("✏️", key=f"ed{sid}"): st.session_state.edit_session_id = sid; st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del{sid}"):
                            if connected: delete_google_event(gid)
                            df_sess = df_sess[df_sess['id'].astype(int) != sid]
                            update_data("sessions", df_sess)
                            st.rerun()

# --- Tab 3: 帳單 (分月結算版 + 缺欄位修復) ---
with tab3:
    st.subheader("💰 帳單中心")
    df_inv = get_data("invoices")
    df_sess = get_data("sessions")
    
    if st.button("⚡ 一鍵結算 (自動分月開單)", type="primary"):
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')
        df_sess['safe_inv'] = pd.to_numeric(df_sess['invoice_id'], errors='coerce').fillna(0).astype(int)
        mask = ((df_sess['status'] == '已完成') | (df_sess['end_dt'] < datetime.now())) & (df_sess['safe_inv'] == 0)
        pending_df = df_sess[mask].copy()
        
        if not pending_df.empty:
            pending_df['month_str'] = pd.to_datetime(pending_df['start_time']).dt.strftime('%Y-%m')
            groups = pending_df.groupby(['student_id', 'month_str'])
            new_inv_count = 0
            
            for (sid, m_str), group in groups:
                total_amt = sum(((pd.to_datetime(r['end_time']) - pd.to_datetime(r['start_time'])).total_seconds() / 3600) * int(r['actual_rate']) for _, r in group.iterrows())
                inv_id = int(df_inv['id'].max()) + 1 if not df_inv.empty else 1
                new_inv = pd.DataFrame([{'id': inv_id, 'student_id': sid, 'total_amount': int(total_amt), 'created_at': datetime.now().isoformat(), 'is_paid': 0, 'note': m_str}])
                df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                df_sess.loc[group.index, 'invoice_id'] = inv_id
                new_inv_count += 1
            
            update_data("invoices", df_inv); update_data("sessions", df_sess)
            st.success(f"結算完成！產出 {new_inv_count} 張帳單。"); st.rerun()
        else:
            st.info("目前沒有未結算的課程")

    st.divider()
    if not df_inv.empty:
        unpaid = df_inv[df_inv['is_paid'] == 0]
        if not unpaid.empty:
            df_disp = pd.merge(unpaid, df_stu, left_on='student_id', right_on='id', how='left').sort_values('created_at', ascending=False)
            for _, row in df_disp.iterrows():
                inv_id = row['id_x']
                s_name = row['name'] if pd.notna(row['name']) else "未知"
                bill_month = str(row['note']) if pd.notna(row['note']) else "未知月份"
                
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{s_name} ({bill_month})**") 
                    c1.caption(f"💰 **${row['total_amount']:,}**")
                    if c2.button("收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        update_data("invoices", df_inv); st.success("已收款"); time.sleep(0.5); st.rerun()
                    with st.expander("📄 明細"):
                        my_ds = df_sess[pd.to_numeric(df_sess['invoice_id'], errors='coerce') == inv_id]
                        if not my_ds.empty:
                            show = [{"日期": pd.to_datetime(r['start_time']).strftime('%m/%d'), "時數": f"{((pd.to_datetime(r['end_time'])-pd.to_datetime(r['start_time'])).total_seconds()/3600):.1f}", "金額": int(((pd.to_datetime(r['end_time'])-pd.to_datetime(r['start_time'])).total_seconds()/3600)*r['actual_rate'])} for _, r in my_ds.iterrows()]
                            st.table(pd.DataFrame(show))
                            csv = pd.DataFrame(show).to_csv(index=False).encode('utf-8-sig')
                            st.download_button("📥 下載", csv, f"帳單_{inv_id}.csv", "text/csv", key=f"dl_{inv_id}")
        else:
            st.info("🎉 帳單全數結清！")
    else:
        st.info("尚無帳單")

# ================= Tab 4: 學生戰情室 (歷程+一鍵複製版) =================
with tab4:
    st.subheader("🧑‍🎓 學生戰情室")
    
    with st.expander("➕ 新增學生"):
        with st.form("add_stu_form"):
            c1, c2 = st.columns(2)
            n = c1.text_input("姓名"); r = c2.number_input("時薪", 500)
            color_opt = st.selectbox("顏色", ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)", "#9B59B6 (紫)"])
            if st.form_submit_button("新增"):
                final_color = color_opt.split(" ")[0]
                new_stu = pd.DataFrame([{'id': int(df_stu['id'].max()+1) if not df_stu.empty else 1, 'name': n, 'default_rate': r, 'color': final_color}])
                update_data("students", pd.concat([df_stu, new_stu], ignore_index=True)); st.rerun()
    
    st.divider()

    if not df_stu.empty and not df_sess.empty:
        full_data = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', how='left')
        full_data['start_dt'] = pd.to_datetime(full_data['start_time'])
        
        for _, row in df_stu.iterrows():
            sid = row['id']
            s_name = row['name']
            my_classes = full_data[full_data['student_id'] == sid].sort_values('start_dt', ascending=False)
            total_count = len(my_classes)
            next_class = my_classes[my_classes['start_dt'] >= datetime.now()].sort_values('start_dt').head(1)
            next_class_str = next_class.iloc[0]['start_dt'].strftime('%m/%d %H:%M') if not next_class.empty else "無待辦課程"
            
            with st.container(border=True):
                c_icon, c_info, c_action = st.columns([0.5, 4, 1.5])
                c_icon.markdown(f'<div style="width:30px;height:30px;background-color:{row["color"]};border-radius:50%;margin-top:5px;"></div>', unsafe_allow_html=True)
                with c_info:
                    st.markdown(f"**{s_name}**")
                    st.caption(f"📅 下次上課：{next_class_str} (累計 {total_count} 堂)")
                
                with st.expander("📝 查看學習歷程 (過去進度)"):
                    if not my_classes.empty:
                        past_classes = my_classes[my_classes['start_dt'] < datetime.now()]
                        if not past_classes.empty:
                            for _, cls in past_classes.iterrows():
                                st.markdown(f"**{cls['start_dt'].strftime('%Y/%m/%d')}**")
                                st.text(cls['progress'] if cls['progress'] else "（無紀錄）")
                                st.divider()
                        else:
                            st.info("尚無過去的上課紀錄")
                    else:
                        st.info("尚無課程資料")

                with st.expander("💬 生成 Line 課表通知"):
                    future_classes = my_classes[my_classes['start_dt'] >= datetime.now()].sort_values('start_dt')
                    
                    if not future_classes.empty:
                        msg_lines = [f"【{s_name} 課程預告】"]
                        msg_lines.append(f"家長您好，以下是接下來的課程安排：\n")
                        for _, cls in future_classes.iterrows():
                            d_str = cls['start_dt'].strftime('%m/%d (%a)')
                            t_str = cls['start_dt'].strftime('%H:%M')
                            msg_lines.append(f"📌 {d_str} {t_str}")
                        msg_lines.append(f"\n再請您確認時間，謝謝！")
                        final_msg = "\n".join(msg_lines)
                        
                        st.caption("👇 點擊區塊右上角的📄圖示，即可一鍵複製")
                        st.code(final_msg, language=None)
                    else:
                        st.warning("沒有未來的課程，無法生成預告。")

                if c_action.button("🗑️", key=f"ds_{sid}", help="刪除此學生"):
                    update_data("students", df_stu[df_stu['id']!=sid]); st.rerun()

    elif df_stu.empty:
        st.info("目前沒有學生，請先新增。")
