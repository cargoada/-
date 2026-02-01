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
# 👇 這裡已經幫你填好成功的信箱了
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
# 2. 登入系統
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
# 3. 側邊欄與小幫手函式
# ==========================================
with st.sidebar:
    st.header(f"👤 您好，{CURRENT_USER}")
    st.caption(f"日曆同步中：{TARGET_CALENDAR_ID}")  # 顯示目前同步的日曆
    if st.button("🚪 登出 / 切換身分"):
        st.session_state.current_user = None
        st.cache_data.clear()
        st.rerun()


def get_data(worksheet_name):
    try:
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=600)
        if 'id' in df.columns: df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        if 'google_event_id' not in df.columns: df['google_event_id'] = ""
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
    except:
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    if service is None or not event_id: return False
    try:
        service.events().update(calendarId=TARGET_CALENDAR_ID, eventId=event_id, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return True
    except:
        return False


def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        service.events().delete(calendarId=TARGET_CALENDAR_ID, eventId=event_id).execute()
        return True
    except:
        return False


# ==========================================
# 4. 主程式分頁
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# ================= Tab 1: 概況 (含營收統計圖表) =================
with tab1:
    c_title, c_refresh = st.columns([3, 1.5])
    c_title.subheader("📊 營收儀表板")

    if c_refresh.button("🔄 刷新數據"):
        st.cache_data.clear()
        st.rerun()

    df_sess = get_data("sessions")
    df_stu = get_data("students")

    if not df_sess.empty:
        # --- 1. 資料前處理 ---
        # 轉換時間
        df_sess['start_dt'] = pd.to_datetime(df_sess['start_time'], errors='coerce')
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')

        # 計算每堂課金額 (小時 * 時薪)
        # 確保 rate 是數字
        df_sess['actual_rate'] = pd.to_numeric(df_sess['actual_rate'], errors='coerce').fillna(0)
        df_sess['amount'] = ((df_sess['end_dt'] - df_sess['start_dt']).dt.total_seconds() / 3600) * df_sess[
            'actual_rate']

        # --- 2. 待結算區塊 (保持之前的邏輯) ---
        df_sess['safe_invoice_id'] = pd.to_numeric(df_sess['invoice_id'], errors='coerce').fillna(0).astype(int)
        current_time = datetime.now()
        pending_mask = (df_sess['end_dt'] < current_time) & (df_sess['safe_invoice_id'] == 0)
        pending_income = df_sess[pending_mask]['amount'].sum()

        # 顯示關鍵指標
        col1, col2, col3 = st.columns(3)
        col1.metric("本月預估收入",
                    f"${int(df_sess[df_sess['start_dt'].dt.month == current_time.month]['amount'].sum()):,}")
        col2.metric("目前待結算", f"${int(pending_income):,}", delta="需開帳單")
        col3.metric("歷史總收入", f"${int(df_sess['amount'].sum()):,}")

        st.divider()

        # --- 3. 統計圖表區 ---

        # 合併學生名字 (為了讓圖表顯示人名而不是 ID)
        if not df_stu.empty:
            chart_df = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id', how='left')
            chart_df['name'] = chart_df['name'].fillna("未知")
        else:
            chart_df = df_sess.copy()
            chart_df['name'] = "未知"

        # [圖表 A] 月營收趨勢
        st.subheader("📈 月營收趨勢")
        chart_df['month_str'] = chart_df['start_dt'].dt.strftime('%Y-%m')  # 變成 "2026-02" 格式

        # 依月份加總金額
        monthly_data = chart_df.groupby('month_str')['amount'].sum()

        # 繪製長條圖
        st.bar_chart(monthly_data, color="#3498DB")  # 藍色

        # [圖表 B] 學生貢獻佔比
        st.subheader("🏆 學生營收貢獻")

        # 依學生加總金額
        student_data = chart_df.groupby('name')['amount'].sum().sort_values(ascending=False)

        # 繪製橫向長條圖 (適合名字比較長的時候)
        st.bar_chart(student_data, horizontal=True, color="#FF5733")  # 橘色

        # --- 4. 待結算明細 (摺疊起來保持乾淨) ---
        with st.expander("🔍 查看「待結算」詳細清單"):
            if pending_income > 0:
                pending_display = chart_df[pending_mask].copy()
                show_list = []
                for _, row in pending_display.iterrows():
                    show_list.append({
                        "日期": row['start_dt'].strftime('%m/%d'),
                        "學生": row['name'],
                        "金額": int(row['amount'])
                    })
                st.dataframe(pd.DataFrame(show_list), use_container_width=True)
            else:
                st.info("目前沒有待結算課程")

    else:
        st.info("尚無課程資料，請先去排課！")
# ================= Tab 2: 排課 (支援週期性排課版) =================
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # -------------------------------------------------------
    # A. 編輯模式 (保持單堂編輯，邏輯不變)
    # -------------------------------------------------------
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
                            [new_sid, new_start.strftime('%Y-%m-%dT%H:%M:%S'), new_end.strftime('%Y-%m-%dT%H:%M:%S'),
                             rate, edit_prog]

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

    # -------------------------------------------------------
    # B. 新增模式 (🔥 升級：支援週期性排課)
    # -------------------------------------------------------
    else:
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                with st.form(key="add_form"):
                    c1, c2 = st.columns(2)
                    sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                    d_input = c2.date_input("首堂日期", datetime.now())  # 改名為首堂

                    c3, c4 = st.columns(2)
                    t_input = c3.time_input("開始時間", datetime.now().replace(minute=0, second=0))
                    dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                    # --- ✨ 新功能：重複設定 ---
                    st.markdown("---")
                    st.markdown("🔁 **週期設定**")
                    col_rep1, col_rep2 = st.columns(2)
                    repeat_type = col_rep1.selectbox("重複頻率", ["單次 (不重複)", "每週固定", "隔週固定 (雙週)"])
                    repeat_count = 1
                    if repeat_type != "單次 (不重複)":
                        repeat_count = col_rep2.number_input("重複幾次？", min_value=2, max_value=12, value=4,
                                                             help="例如設為 4，就會自動建立未來 4 週的課程")

                    st.markdown("---")
                    do_sync = st.checkbox("🔄 同步至 Google 日曆", value=False)
                    n_prog = st.text_area("預定進度 (所有課程將使用相同備註)")

                    add_submit = st.form_submit_button("✅ 建立課程", type="primary")

                if add_submit:
                    with st.spinner(f"正在建立 {repeat_count} 堂課程..."):

                        sid = student_map[sel_stu]
                        rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                        start_base = datetime.combine(d_input, t_input)

                        new_rows = []

                        # 迴圈建立多堂課
                        for i in range(repeat_count):
                            # 計算每堂課的時間偏移
                            if repeat_type == "每週固定":
                                offset = timedelta(weeks=i)
                            elif repeat_type == "隔週固定 (雙週)":
                                offset = timedelta(weeks=i * 2)
                            else:
                                offset = timedelta(0)

                            current_start = start_base + offset
                            current_end = current_start + timedelta(hours=dur)

                            # Google 日曆同步
                            g_id = ""
                            if do_sync and service:
                                g_id = create_google_event(f"家教: {sel_stu}", current_start, current_end)
                                # 稍微停一下避免 Google API 限制
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

                        # 一次性寫入資料庫
                        if new_rows:
                            update_data("sessions", pd.concat([df_sess, pd.DataFrame(new_rows)], ignore_index=True))

                        time.sleep(1)

                    st.success(f"成功建立 {repeat_count} 堂課程！")
                    st.rerun()

    # ==========================
    # C. 日曆與列表區 (保持不變)
    # ==========================
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
                        "id": str(row['id_x']),
                        "title": row['name'],
                        "start": s_iso, "end": e_iso,
                        "backgroundColor": row['color'], "borderColor": row['color'],
                        "textColor": "#FFFFFF"
                    })
                except:
                    continue
        except:
            pass

    calendar_options = {
        "initialView": "dayGridMonth",
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek,listMonth"},
        "height": 650,
    }
    cal = calendar(events=events, options=calendar_options, callbacks=['eventClick'], key="cal_v_final")

    if cal.get("eventClick"):
        cid = int(cal["eventClick"]["event"]["id"])
        if st.session_state.edit_session_id != cid:
            st.session_state.edit_session_id = cid
            st.rerun()

    with st.expander("📋 詳細列表 / 編輯 / 刪除", expanded=True):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                20)
            for _, row in df_display.iterrows():
                sid = int(row['id_x'])
                gid = row.get('google_event_id', "")
                connected = pd.notna(gid) and str(gid) != ""
                with st.container(border=True):
                    c1, c2, c3 = st.columns([6, 1, 1], gap="small")
                    c1.markdown(f"**{row['name']}**")
                    c1.caption(
                        f"{pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')} {'(✅已同步)' if connected else ''}")
                    with c2:
                        if st.button("✏️", key=f"ed{sid}"):
                            st.session_state.edit_session_id = sid
                            st.rerun()
                    with c3:
                        if st.button("🗑️", key=f"del{sid}"):
                            if connected: delete_google_event(gid)
                            df_sess = df_sess[df_sess['id'].astype(int) != sid]
                            update_data("sessions", df_sess)
                            st.rerun()
# ================= Tab 3: 帳單 (分月結算版) =================
with tab3:
    st.subheader("💰 帳單中心")
    df_inv = get_data("invoices")
    df_sess = get_data("sessions")  # 確保讀到最新課程資料

    # -------------------------------------------------------
    # 1. 一鍵結算 (邏輯修改：按「學生 + 月份」分組)
    # -------------------------------------------------------
    if st.button("⚡ 一鍵結算 (自動分月開單)", type="primary"):
        # 1. 找出所有「已完成」且「未結算」的課程
        #    (這裡也包含了時間已過但還沒改狀態的課程，自動判定)
        df_sess['end_dt'] = pd.to_datetime(df_sess['end_time'], errors='coerce')
        df_sess['safe_inv'] = pd.to_numeric(df_sess['invoice_id'], errors='coerce').fillna(0).astype(int)

        # 條件：(狀態完成 OR 時間已過) AND (沒有帳單ID)
        mask = ((df_sess['status'] == '已完成') | (df_sess['end_dt'] < datetime.now())) & (df_sess['safe_inv'] == 0)
        pending_df = df_sess[mask].copy()

        if not pending_df.empty:
            # 2. 增加「月份」欄位 (例如 "2026-02")
            pending_df['month_str'] = pd.to_datetime(pending_df['start_time']).dt.strftime('%Y-%m')

            # 3. 根據 (學生ID, 月份) 進行分組
            #    這樣同一個學生，不同月份的課會被拆成兩張單
            groups = pending_df.groupby(['student_id', 'month_str'])

            new_inv_count = 0

            for (sid, m_str), group in groups:
                # 計算該月總金額
                total_amt = sum(
                    ((pd.to_datetime(r['end_time']) - pd.to_datetime(r['start_time'])).total_seconds() / 3600) * int(
                        r['actual_rate'])
                    for _, r in group.iterrows()
                )

                # 建立新帳單
                # (這裡不檢查舊帳單，直接開新單，避免邏輯混亂。因為篩選器已經確保這些課是沒算過的)
                inv_id = int(df_inv['id'].max()) + 1 if not df_inv.empty else 1

                new_inv = pd.DataFrame([{
                    'id': inv_id,
                    'student_id': sid,
                    'total_amount': int(total_amt),
                    'created_at': datetime.now().isoformat(),
                    'is_paid': 0,
                    'note': m_str  # 利用 note 欄位偷偷記住月份 (或者不記也可以，等等顯示會自動抓)
                }])

                df_inv = pd.concat([df_inv, new_inv], ignore_index=True)

                # 把課程標記為這張帳單
                df_sess.loc[group.index, 'invoice_id'] = inv_id
                new_inv_count += 1

            # 存檔
            update_data("invoices", df_inv)
            update_data("sessions", df_sess)
            st.success(f"結算完成！共產出 {new_inv_count} 張分月帳單。")
            time.sleep(1)
            st.rerun()
        else:
            st.info("👏 目前沒有未結算的課程")

    st.divider()

    # -------------------------------------------------------
    # 2. 顯示帳單列表 (顯示月份)
    # -------------------------------------------------------
    if not df_inv.empty:
        # 篩選未付款
        unpaid = df_inv[df_inv['is_paid'] == 0]

        if not unpaid.empty:
            # 合併學生名字
            df_disp = pd.merge(unpaid, df_stu, left_on='student_id', right_on='id', how='left')
            # 依照日期新到舊排序
            df_disp = df_disp.sort_values('created_at', ascending=False)

            for _, row in df_disp.iterrows():
                inv_id = row['id_x']
                s_name = row['name'] if pd.notna(row['name']) else "未知學生"

                # --- 找出這張帳單是屬於哪個月份的 ---
                # 技巧：去 sessions 找這張帳單底下第一堂課的時間
                my_sessions = df_sess[pd.to_numeric(df_sess['invoice_id'], errors='coerce') == inv_id]

                bill_month = "未知月份"
                if not my_sessions.empty:
                    # 抓第一筆資料的開始時間，轉成 YYYY-MM
                    first_date = pd.to_datetime(my_sessions.iloc[0]['start_time'])
                    bill_month = first_date.strftime('%Y年%m月')

                # --- 顯示區塊 ---
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])

                    # 標題顯示： 王小明 (2026年02月) - $5,000
                    c1.markdown(f"**{s_name} ({bill_month})**")
                    c1.markdown(f"💰 **${row['total_amount']:,}**")
                    c1.caption(f"開單日：{pd.to_datetime(row['created_at']).strftime('%Y/%m/%d')}")

                    # 收款按鈕
                    if c2.button("收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        update_data("invoices", df_inv)
                        st.success("已標記為收款！")
                        time.sleep(0.5)
                        st.rerun()

                    # 明細與下載
                    with st.expander("📄 查看明細 / 下載 Excel"):
                        if not my_sessions.empty:
                            show_list = []
                            for _, r in my_sessions.iterrows():
                                s = pd.to_datetime(r['start_time'])
                                e = pd.to_datetime(r['end_time'])
                                hrs = (e - s).total_seconds() / 3600
                                amt = hrs * r['actual_rate']
                                show_list.append({
                                    "日期": s.strftime('%m/%d'),
                                    "時間": f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}",
                                    "時數": f"{hrs:.1f}",
                                    "金額": int(amt)
                                })

                            st.table(pd.DataFrame(show_list))

                            # 下載按鈕
                            csv_data = pd.DataFrame(show_list).to_csv(index=False).encode('utf-8-sig')
                            file_name = f"{s_name}_{bill_month}_學費單.csv"
                            st.download_button("📥 下載帳單", csv_data, file_name, "text/csv", key=f"dl_{inv_id}")
        else:
            st.info("🎉 太棒了！所有帳單都已結清。")
    else:
        st.info("尚無帳單資料。")
# --- Tab 4: 學生 ---
with tab4:
    st.subheader("🧑‍🎓 學生管理")
    with st.expander("➕ 新增學生"):
        with st.form("add_stu_form"):
            c1, c2 = st.columns(2)
            n = c1.text_input("姓名");
            r = c2.number_input("時薪", 500)
            color_opt = st.selectbox("顏色",
                                     ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)", "#9B59B6 (紫)"])
            if st.form_submit_button("新增"):
                final_color = color_opt.split(" ")[0]
                new_stu = pd.DataFrame(
                    [{'id': int(df_stu['id'].max() + 1) if not df_stu.empty else 1, 'name': n, 'default_rate': r,
                      'color': final_color}])
                update_data("students", pd.concat([df_stu, new_stu], ignore_index=True))
                st.rerun()

    if not df_stu.empty:
        for _, row in df_stu.iterrows():
            with st.container(border=True):
                c_icon, c_info, c_del = st.columns([0.5, 4, 1])
                c_icon.markdown(
                    f'<div style="width:25px;height:25px;background-color:{row["color"]};border-radius:50%;margin-top:10px;"></div>',
                    unsafe_allow_html=True)
                with c_info:
                    st.markdown(f"**{row['name']}**")
                    st.caption(f"💰 時薪：${row['default_rate']}")
                if c_del.button("🗑️", key=f"ds_{row['id']}"):
                    update_data("students", df_stu[df_stu['id'] != row['id']])
                    st.rerun()