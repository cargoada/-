import streamlit as st
import pandas as pd
import time
from datetime import datetime, timedelta
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- 頁面設定 ---
st.set_page_config(page_title="超級家教系統 (多人版)", page_icon="🏫", layout="centered")

# --- CSS 美化 ---
st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 12px; height: 3em; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# --- 🟢 初始化 Session State (記錄誰登入了) ---
if 'current_user' not in st.session_state:
    st.session_state.current_user = None

# ==========================================
# 🚪 登入畫面 (如果還沒登入，就只顯示這裡)
# ==========================================
if not st.session_state.current_user:
    st.title("🏫 歡迎使用家教系統")
    st.info("請選擇你的身份登入")

    # 從 secrets 讀取使用者名單
    users_config = st.secrets["users"]
    user_keys = [k for k in users_config.keys() if k != "admin_password"]
    user_names = [users_config[k]["name"] for k in user_keys]

    with st.form("login_form"):
        selected_name = st.selectbox("你是誰？", user_names)
        password = st.text_input("輸入密碼", type="password")
        submitted = st.form_submit_button("登入")

        if submitted:
            if password == users_config["admin_password"]:
                # 找出對應的 key (例如 'jiong' 或 'friend')
                selected_key = user_keys[user_names.index(selected_name)]
                st.session_state.current_user = users_config[selected_key]
                st.toast(f"歡迎回來，{selected_name}！", icon="👋")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("密碼錯誤！")

    st.stop()  # ⛔ 停止執行下面的程式碼，直到登入成功

# ==========================================
# 👇 以下是登入後才會執行的主程式
# ==========================================

# 取得當前使用者的專屬設定
USER_CONFIG = st.session_state.current_user
CURRENT_SHEET_URL = USER_CONFIG["sheet_url"]
CURRENT_CALENDAR_ID = USER_CONFIG["calendar_id"]

st.title(f"☁️ {USER_CONFIG['name']}的家教系統")

if st.button("登出", type="secondary"):
    st.session_state.current_user = None
    st.rerun()

# --- 🟢 資料庫連線 (GSheets) ---
conn = st.connection("gsheets", type=GSheetsConnection)


# --- 🟢 Google 日曆連線 ---
def get_calendar_service():
    try:
        creds_info = st.secrets["connections"]["gsheets"]
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        return None


def create_google_event(title, start_dt, end_dt):
    service = get_calendar_service()
    if not service: return None
    event = {
        'summary': title,
        'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Taipei'},
        'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Taipei'},
    }
    try:
        # 使用當前登入者的 Calendar ID
        event = service.events().insert(calendarId=CURRENT_CALENDAR_ID, body=event).execute()
        return event.get('id')
    except:
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    service = get_calendar_service()
    if not service or not event_id: return
    try:
        event = service.events().get(calendarId=CURRENT_CALENDAR_ID, eventId=event_id).execute()
        event['summary'] = title
        event['start']['dateTime'] = start_dt.isoformat()
        event['end']['dateTime'] = end_dt.isoformat()
        service.events().update(calendarId=CURRENT_CALENDAR_ID, eventId=event_id, body=event).execute()
    except:
        pass


def delete_google_event(event_id):
    service = get_calendar_service()
    if not service or not event_id: return
    try:
        service.events().delete(calendarId=CURRENT_CALENDAR_ID, eventId=event_id).execute()
    except:
        pass


# --- 資料庫操作 (關鍵：要傳入 spreadsheet 參數) ---
# 👇 找到原本的 get_data，整段換成這個
def get_data(worksheet_name):
    try:
        # 🟢 關鍵修改：ttl=600 (快取 10 分鐘)
        # 這樣你一分鐘內操作 100 次，也只會算 1 次讀取，絕對不會被鎖！
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=600)

        # 資料清理 (保持不變)
        if worksheet_name == 'students':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        elif worksheet_name == 'sessions':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['invoice_id'] = pd.to_numeric(df['invoice_id'], errors='coerce').astype('Int64')
            if 'google_event_id' not in df.columns: df['google_event_id'] = ""
        elif worksheet_name == 'invoices':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
        return df
    except Exception as e:
        # 遇到錯誤時，回傳空表，並在右上角偷偷顯示警告就好，不要讓程式當掉
        st.toast(f"連線忙碌中，請稍後再試...", icon="⏳")
        return pd.DataFrame()


# 👇 找到原本的 update_data，整段換成這個
def update_data(worksheet_name, df):
    try:
        # 1. 寫入 Google Sheet
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df)

        # 2. 🟢 關鍵動作：寫入成功後，清除快取！
        # 這樣下次讀取時才會抓到最新的，確保你剛加的學生馬上出現
        st.cache_data.clear()
        st.cache_resource.clear()

    except Exception as e:
        st.error(f"寫入失敗：{e}")


def get_next_id(df):
    if df.empty: return 1
    return int(df['id'].max()) + 1


# --- 初始化 ---
if 'edit_session_id' not in st.session_state:
    st.session_state.edit_session_id = None

# --- 分頁 ---
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# (以下內容與之前相同，但所有 get_data/update_data 都會自動使用上面的 URL 設定)
# 為了篇幅，我保留核心邏輯，直接貼上整合好的部分：

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
        st.write("連線中...")

# ==========================================
# Tab 2: 📅 排課 (點擊日曆可編輯)
# ==========================================
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # --- 1. 處理日曆事件資料 (這段移到最前面，為了讓點擊能馬上反應) ---
    events = []
    if not df_sess.empty and not df_stu.empty:
        # 合併資料表，保留 session 的 id
        # id_x = session_id (課程ID), id_y = student_id (學生ID)
        merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')

        for _, row in merged.iterrows():
            events.append({
                "id": str(row['id_x']),  # 關鍵！把課程 ID 埋進去
                "title": row['name'],
                "start": row['start_time'],
                "end": row['end_time'],
                "backgroundColor": row['color'],
                "borderColor": row['color'],
                # 設定游標變成手指，暗示可點擊
                "classNames": ["cursor-pointer"]
            })

    # --- 2. 判斷現在是「新增」還是「編輯」模式 ---
    # 如果 Session State 裡有 ID，代表現在要編輯
    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯課程")
        edit_id = st.session_state.edit_session_id

        # 找出這堂課的資料
        row = df_sess[df_sess['id'] == edit_id]

        if not row.empty:
            row = row.iloc[0]
            s_dt = pd.to_datetime(row['start_time'])
            e_dt = pd.to_datetime(row['end_time'])
            current_sid = int(row['student_id'])

            # 找出學生名字
            s_name = df_stu[df_stu['id'] == current_sid]['name'].values[0] if current_sid in df_stu['id'].values else ""

            with st.container(border=True):
                st.info(f"正在修改：{s_name} 的課程")
                c1, c2 = st.columns(2)
                # 預設選中該學生
                edit_stu = c1.selectbox("學生", list(student_map.keys()),
                                        index=list(student_map.keys()).index(s_name) if s_name in student_map else 0)
                edit_date = c2.date_input("日期", s_dt.date())

                c3, c4 = st.columns(2)
                edit_time = c3.time_input("時間", s_dt.time())
                old_dur = (e_dt - s_dt).total_seconds() / 3600
                edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)

                new_start = datetime.combine(edit_date, edit_time)
                new_end = new_start + timedelta(hours=edit_dur)

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
                        if g_event_id: update_google_event(g_event_id, f"家教: {edit_stu}", new_start, new_end)

                        update_data("sessions", df_sess)
                        st.session_state.edit_session_id = None
                        st.toast("修改成功！", icon="✅")
                        st.rerun()
                with col_cancel:
                    if st.button("❌ 取消 / 返回新增"):
                        st.session_state.edit_session_id = None
                        st.rerun()
        else:
            st.warning("找不到這堂課資料，可能已被刪除。")
            st.session_state.edit_session_id = None
            if st.button("返回"): st.rerun()

    else:
        # --- 新增模式 (平常看到的樣子) ---
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())

                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                if st.button("✅ 確認新增", type="primary"):
                    start_p = datetime.combine(d_input, t_input)
                    end_p = start_p + timedelta(hours=dur)
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                    status = '已完成' if start_p < datetime.now() else '已預約'

                    # Google 日曆同步
                    g_event_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    new_id = get_next_id(df_sess)
                    new_row = pd.DataFrame([{
                        'id': new_id, 'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': status, 'actual_rate': rate, 'invoice_id': None,
                        'google_event_id': g_event_id
                    }])

                    df_sess = pd.concat([df_sess, new_row], ignore_index=True)
                    update_data("sessions", df_sess)
                    st.toast("新增成功！", icon="🎉")
                    st.rerun()
            else:
                st.warning("請先到「學生」分頁新增學生資料！")

    st.divider()

    # --- 3. 顯示日曆 (修改版：沒資料也要顯示) ---
    st.subheader("🗓️ 課程行事曆 (點擊課程可編輯)")

    # 準備事件資料
    events = []
    # 只有當有資料時才去跑迴圈，不然就是空的列表
    if not df_sess.empty and not df_stu.empty:
        merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
        for _, row in merged.iterrows():
            events.append({
                "id": str(row['id_x']),
                "title": row['name'],
                "start": row['start_time'],
                "end": row['end_time'],
                "backgroundColor": row['color'],
                "borderColor": row['color'],
                "classNames": ["cursor-pointer"]
            })

    # 設定日曆選項
    calendar_options = {
        "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
        "initialView": "dayGridMonth",
        "timeZone": "local",
        "locale": "zh-tw",
        "selectable": True,
    }

    # 👇 關鍵修改：加上 key="my_calendar"，確保點擊反應靈敏
    cal = calendar(
        events=events,
        options=calendar_options,
        callbacks=['eventClick'],
        key="my_calendar"
    )

    # --- 4. 監聽點擊事件 ---
    if cal.get("eventClick"):
        clicked_event = cal["eventClick"]["event"]
        clicked_id = int(clicked_event["id"])

        # 如果點擊的跟現在的不一樣，才重新整理
        if st.session_state.edit_session_id != clicked_id:
            st.session_state.edit_session_id = clicked_id
            st.toast(f"已選取課程，請至上方編輯", icon="👆")  # 跳出提示告訴你要往上看
            time.sleep(0.5)
            st.rerun()

    # --- 5. 列表模式 ---
    with st.expander("📋 詳細列表 / 刪除"):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                10)
            for _, row in df_display.iterrows():
                sess_id = row['id_x']
                name = row['name']
                t_str = pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')

                with st.container(border=True):
                    c1, c2, c3 = st.columns([5, 1.5, 1.5])
                    c1.markdown(f"**{name}** - {t_str}")
                    if c2.button("✏️", key=f"ed_{sess_id}"):
                        st.session_state.edit_session_id = sess_id
                        st.rerun()
                    if c3.button("🗑️", key=f"del_{sess_id}"):
                        if 'google_event_id' in row and pd.notna(row['google_event_id']):
                            delete_google_event(row['google_event_id'])
                        df_sess = df_sess[df_sess['id'] != sess_id]
                        update_data("sessions", df_sess)
                        st.toast("已刪除", icon="🗑️")
                        st.rerun()
# ==========================================
# Tab 3: 💰 帳單中心 (詳細明細版)
# ==========================================
with tab3:
    st.subheader("💰 帳單中心")

    # 重新讀取資料 (確保是最新的)
    df_sess = get_data("sessions")
    df_inv = get_data("invoices")
    df_stu = get_data("students")

    # --- 1. 檢查過期未結課程 (防呆) ---
    now_str = datetime.now().isoformat()
    # 篩選條件：時間已過 + 狀態是已預約
    missed_mask = (df_sess['end_time'] < now_str) & (df_sess['status'] == '已預約')

    if missed_mask.any():
        st.warning(f"⚠️ 偵測到 {missed_mask.sum()} 堂「時間已過」但狀態仍為「已預約」的課程。")
        st.info("這些課程不會被算入帳單，請先按下方按鈕修正。")
        if st.button("✅ 一鍵將這些課程改為「已完成」", key="fix_missed"):
            df_sess.loc[missed_mask, 'status'] = '已完成'
            update_data("sessions", df_sess)
            st.toast("狀態已更新！", icon="✅")
            st.rerun()

    # --- 2. 結算按鈕 ---
    with st.expander("⚡ 生成帳單 (結算本月學費)", expanded=True):
        st.caption("系統會自動將同一個學生的未結課程合併成一張帳單。")
        if st.button("⚡ 一鍵結算", type="primary"):
            # 找出「已完成」且「還沒綁定 invoice_id」的課程
            pending_mask = (df_sess['status'] == '已完成') & (
                        (df_sess['invoice_id'].isna()) | (df_sess['invoice_id'] == 0))
            pending_sids = df_sess[pending_mask]['student_id'].unique()

            if len(pending_sids) == 0:
                st.warning("目前沒有需要結算的課程！")
            else:
                bar = st.progress(0)
                for idx, sid in enumerate(pending_sids):
                    bar.progress((idx + 1) / len(pending_sids))

                    # 抓出該學生這次要結算的課
                    s_mask = (df_sess['student_id'] == sid) & pending_mask
                    my_sessions = df_sess[s_mask]

                    # 計算總金額
                    total = 0
                    for _, r in my_sessions.iterrows():
                        s = pd.to_datetime(r['start_time'])
                        e = pd.to_datetime(r['end_time'])
                        hours = (e - s).total_seconds() / 3600
                        total += hours * r['actual_rate']

                    # 檢查該學生是否有「未付款」的舊帳單 (要合併)
                    inv_id = get_next_id(df_inv)  # 預設新 ID

                    if not df_inv.empty:
                        unpaid_mask = (df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)
                        if unpaid_mask.any():
                            # --- 合併模式 ---
                            target_inv = df_inv[unpaid_mask].sort_values('created_at', ascending=False).iloc[0]
                            inv_id = target_inv['id']
                            # 更新金額與日期
                            df_inv.loc[df_inv['id'] == inv_id, 'total_amount'] += int(total)
                            df_inv.loc[df_inv['id'] == inv_id, 'created_at'] = datetime.now().isoformat()
                        else:
                            # --- 新增模式 ---
                            new_inv = pd.DataFrame([{
                                'id': inv_id, 'student_id': sid, 'total_amount': int(total),
                                'created_at': datetime.now().isoformat(), 'is_paid': 0
                            }])
                            df_inv = pd.concat([df_inv, new_inv], ignore_index=True)
                    else:
                        # --- 第一筆資料模式 ---
                        new_inv = pd.DataFrame([{
                            'id': inv_id, 'student_id': sid, 'total_amount': int(total),
                            'created_at': datetime.now().isoformat(), 'is_paid': 0
                        }])
                        df_inv = pd.concat([df_inv, new_inv], ignore_index=True)

                    # 關鍵：把這些課程的 invoice_id 更新為這張帳單的 ID
                    df_sess.loc[s_mask, 'invoice_id'] = inv_id

                # 寫入資料庫
                update_data("invoices", df_inv)
                update_data("sessions", df_sess)
                st.balloons()
                st.success("結算完成！")
                time.sleep(1)
                st.rerun()

    st.divider()

    # --- 3. 待收款列表 (含詳細明細) ---
    st.subheader("💵 待收款帳單")

    if not df_inv.empty:
        # 篩選未付款
        df_unpaid = df_inv[df_inv['is_paid'] == 0].copy()

        if df_unpaid.empty:
            st.success("太棒了！目前沒有待收款項。")
        else:
            # 合併學生名字
            df_unpaid = pd.merge(df_unpaid, df_stu, left_on='student_id', right_on='id', suffixes=('_inv', '_stu'))
            # 依照日期排序
            df_unpaid = df_unpaid.sort_values('created_at', ascending=False)

            for _, row in df_unpaid.iterrows():
                inv_id = row['id_inv']
                name = row['name']
                amt = row['total_amount']
                date_obj = pd.to_datetime(row['created_at'])
                date_str = date_obj.strftime('%Y/%m/%d')

                # 檔名範例：王小明_20260130_學費帳單.csv
                csv_filename = f"{name}_{date_obj.strftime('%Y%m%d')}_學費帳單.csv"

                with st.container(border=True):
                    # 上半部：簡要資訊
                    c1, c2, c3 = st.columns([2, 2, 1.5])
                    c1.markdown(f"**{name}**")
                    c1.caption(f"📅 出帳：{date_str}")
                    c2.markdown(f"### ${amt:,}")

                    if c3.button("✅ 收款", key=f"pay_{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1
                        update_data("invoices", df_inv)
                        st.toast(f"收到 {name} 的款項囉！", icon="💰")
                        time.sleep(0.5)
                        st.rerun()

                    # 下半部：詳細明細 (Expander)
                    with st.expander("📄 查看上課時間 / 下載明細"):
                        # 1. 找出這張帳單包含的所有課程
                        # 這裡的邏輯是：去 sessions 表找 invoice_id 等於這張單子的課程
                        details_mask = (df_sess['invoice_id'] == inv_id)
                        my_details = df_sess[details_mask].copy()

                        if not my_details.empty:
                            # 資料整理，準備顯示和下載
                            display_rows = []
                            csv_rows = []

                            for _, d_row in my_details.iterrows():
                                s_dt = pd.to_datetime(d_row['start_time'])
                                e_dt = pd.to_datetime(d_row['end_time'])
                                h = (e_dt - s_dt).total_seconds() / 3600
                                cost = h * d_row['actual_rate']

                                # 顯示用的格式
                                date_fmt = s_dt.strftime('%m/%d (%a)')  # 月/日 (星期)
                                time_range = f"{s_dt.strftime('%H:%M')}~{e_dt.strftime('%H:%M')}"

                                display_rows.append({
                                    "日期": date_fmt,
                                    "時間": time_range,
                                    "時數": f"{h} hr",
                                    "金額": f"${int(cost)}"
                                })

                                # CSV 用的格式 (更完整)
                                csv_rows.append({
                                    "學生": name,
                                    "日期": s_dt.strftime('%Y/%m/%d'),
                                    "開始時間": s_dt.strftime('%H:%M'),
                                    "結束時間": e_dt.strftime('%H:%M'),
                                    "時數": h,
                                    "時薪": d_row['actual_rate'],
                                    "小計": int(cost)
                                })

                            # A. 顯示表格
                            st.table(pd.DataFrame(display_rows))

                            # B. 下載按鈕
                            df_csv = pd.DataFrame(csv_rows)
                            # 加總行 (選用)
                            total_row = pd.DataFrame([{"學生": "總計", "小計": int(amt)}])
                            df_csv = pd.concat([df_csv, total_row], ignore_index=True)

                            st.download_button(
                                label="📥 下載完整明細 (Excel/CSV)",
                                data=df_csv.to_csv(index=False).encode('utf-8-sig'),
                                file_name=csv_filename,
                                mime='text/csv',
                                key=f"dl_{inv_id}"
                            )
                        else:
                            st.write("查無明細資料 (可能是舊資料格式)")

    # --- 4. 歷史記錄 (也可查看明細) ---
    with st.expander("📂 查看已結案歷史記錄"):
        if not df_inv.empty:
            df_paid = df_inv[df_inv['is_paid'] == 1].copy()
            if not df_paid.empty:
                df_paid = pd.merge(df_paid, df_stu, left_on='student_id', right_on='id')
                df_paid = df_paid.sort_values('created_at', ascending=False)

                for _, row in df_paid.iterrows():
                    inv_id = row['id_x']
                    name = row['name']
                    amt = row['total_amount']
                    date_str = pd.to_datetime(row['created_at']).strftime('%Y/%m/%d')

                    # 這裡也加入明細查看功能
                    st.markdown(f"**{date_str} - {name} (${amt:,})**")
                    with st.expander(f"查看 {name} 的歷史明細"):
                        # 同樣的撈取邏輯
                        details_mask = (df_sess['invoice_id'] == inv_id)
                        my_details = df_sess[details_mask].copy()
                        if not my_details.empty:
                            hist_rows = []
                            for _, d_row in my_details.iterrows():
                                s_dt = pd.to_datetime(d_row['start_time'])
                                e_dt = pd.to_datetime(d_row['end_time'])
                                h = (e_dt - s_dt).total_seconds() / 3600
                                hist_rows.append({
                                    "日期": s_dt.strftime('%m/%d'),
                                    "時間": f"{s_dt.strftime('%H:%M')}~{e_dt.strftime('%H:%M')}",
                                    "金額": f"${int(h * d_row['actual_rate'])}"
                                })
                            st.table(pd.DataFrame(hist_rows))
                        else:
                            st.write("無詳細資料")
                    st.divider()
            else:
                st.write("尚無歷史收款記錄")
        else:
            st.write("尚無帳單資料")
# ==========================================
# Tab 4: 🧑‍🎓 學生名冊 (修復刪除功能)
# ==========================================
with tab4:
    st.subheader("🧑‍🎓 學生名冊")
    df_stu = get_data("students")

    # --- 新增學生區塊 ---
    with st.expander("➕ 新增一位學生", expanded=False):
        with st.form("add_student_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            n_name = c1.text_input("學生姓名", placeholder="例如：王小明")
            n_rate = c2.number_input("預設時薪", value=500, step=50)
            n_contact = st.text_input("家長聯絡方式 (選填)")

            colors = {"🔴 熱情紅": "#FF5733", "🔵 穩重藍": "#3498DB", "🟢 清新綠": "#2ECC71", "🟠 活力橘": "#FFC300"}
            c_name = st.selectbox("代表顏色", list(colors.keys()))

            if st.form_submit_button("確認新增"):
                if n_name:
                    new_id = get_next_id(df_stu)
                    new_row = pd.DataFrame([{
                        'id': new_id,
                        'name': n_name,
                        'parent_contact': n_contact,
                        'default_rate': int(n_rate),
                        'color': colors[c_name]
                    }])
                    # 合併並存檔
                    df_stu = pd.concat([df_stu, new_row], ignore_index=True)
                    update_data("students", df_stu)
                    st.toast(f"🎉 已新增：{n_name}", icon="✅")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.warning("⚠️ 請輸入學生姓名")

    st.divider()

    # --- 學生列表與刪除功能 ---
    if df_stu.empty:
        st.info("目前還沒有學生資料，趕快新增一位吧！")
    else:
        # 為了美觀，我們用迴圈把每一位學生畫出來
        for index, row in df_stu.iterrows():
            with st.container(border=True):
                # 切分成：顏色圖示(1) | 姓名資訊(4) | 刪除按鈕(1.5)
                c1, c2, c3 = st.columns([1, 4, 1.5])

                # 1. 顯示顏色圓點
                with c1:
                    st.markdown(
                        f"<div style='width:30px;height:30px;background-color:{row['color']};border-radius:50%;margin-top:5px;'></div>",
                        unsafe_allow_html=True)

                # 2. 顯示姓名與時薪
                with c2:
                    st.markdown(f"**{row['name']}**")
                    st.caption(f"💰 ${row['default_rate']}/hr | 📞 {row['parent_contact']}")

                # 3. 刪除按鈕
                with c3:
                    # 這裡的 key 非常重要，要加上 row['id'] 確保每個按鈕都是獨一無二的
                    if st.button("🗑️", key=f"del_stu_{row['id']}"):
                        # 邏輯：保留 id 「不等於」這一位的，其他的都留下來 (等於刪除這一位)
                        new_df = df_stu[df_stu['id'] != row['id']]

                        # 更新資料庫
                        update_data("students", new_df)

                        st.toast(f"已刪除 {row['name']}", icon="👋")
                        time.sleep(1)
                        st.rerun()