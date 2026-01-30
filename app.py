import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from streamlit_gsheets import GSheetsConnection
from streamlit_calendar import calendar  # 👈 這是剛剛漏掉的關鍵！

# 👇 Google API 專用套件
from google.oauth2 import service_account
from googleapiclient.discovery import build
# ==========================================
# 1. Google 服務連線設定 (自動啟動機器人)
# ==========================================
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]

# 嘗試從 secrets 讀取憑證並建立 service 物件
try:
    # 判斷 secrets 格式 (相容兩種常見寫法)
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = dict(st.secrets["connections"]["gsheets"])
    elif "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
    else:
        # 如果找不到，這裡會報錯
        creds_dict = st.secrets["text_key"]  # 備用方案，視你的設定而定

    # 建立憑證
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )

    # 🔥 關鍵：啟動 Google 日曆機器人 (service)
    service = build('calendar', 'v3', credentials=creds)
    # print("Google 日曆連線成功！")

except Exception as e:
    # st.error(f"⚠️ Google 日曆連線失敗 (僅排課功能受影響)：{e}")
    service = None


# ==========================================
# 2. Google 日曆小幫手函式 (時區修正版)
# ==========================================
def create_google_event(title, start_dt, end_dt):
    """建立日曆事件 (回傳 event_id)"""
    if service is None: return None  # 如果沒連線就直接跳過

    try:
        event_body = {
            'summary': title,
            'start': {
                'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Asia/Taipei',  # 🇹🇼 強制台灣時間
            },
            'end': {
                'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Asia/Taipei',
            },
        }
        event = service.events().insert(calendarId='primary', body=event_body).execute()
        return event.get('id')
    except Exception as e:
        st.toast(f"❌ 日曆建立失敗：{e}")
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    """更新日曆事件"""
    if service is None or not event_id: return False

    try:
        event_body = {
            'summary': title,
            'start': {
                'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Asia/Taipei',
            },
            'end': {
                'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'),
                'timeZone': 'Asia/Taipei',
            },
        }
        service.events().update(calendarId='primary', eventId=event_id, body=event_body).execute()
        return True
    except Exception as e:
        print(f"日曆更新失敗: {e}")
        return False


def delete_google_event(event_id):
    """刪除日曆事件"""
    if service is None or not event_id: return False

    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return True
    except Exception as e:
        print(f"日曆刪除失敗: {e}")
        return False


# ==========================================
# 3. Streamlit 頁面設定與資料庫連線
# ==========================================
st.set_page_config(page_title="家教排課系統", page_icon="📅", layout="centered")

# 連接 Google Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# 設定你的試算表網址 (請確認這裡有換成你的網址)
CURRENT_SHEET_URL = st.secrets["users"]["jiong"]["sheet_url"]


# 👇👇👇 下面接著原本的 def get_data... 👇👇👇
# --- 資料庫操作 (關鍵：要傳入 spreadsheet 參數) ---
# 👇 找到原本的 get_data，整段換成這個
def get_data(worksheet_name):
    try:
        # 讀取資料 (快取 10 分鐘)
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=600)

        # 🛡️ 針對不同分頁，進行嚴格的型別轉換
        if worksheet_name == 'students':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['default_rate'] = pd.to_numeric(df['default_rate'], errors='coerce').fillna(0).astype(int)

        elif worksheet_name == 'sessions':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['invoice_id'] = pd.to_numeric(df['invoice_id'], errors='coerce').astype('Int64')
            df['actual_rate'] = pd.to_numeric(df['actual_rate'], errors='coerce').fillna(0).astype(int)
            if 'google_event_id' not in df.columns: df['google_event_id'] = ""

            # 👇 新增這兩行：處理進度欄位 (如果沒填就是空字串)
            if 'progress' not in df.columns: df['progress'] = ""
            df['progress'] = df['progress'].fillna("").astype(str)

        elif worksheet_name == 'invoices':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            # 👇 關鍵修正：確保金額和付款狀態一定是數字
            df['total_amount'] = pd.to_numeric(df['total_amount'], errors='coerce').fillna(0).astype(int)
            df['is_paid'] = pd.to_numeric(df['is_paid'], errors='coerce').fillna(0).astype(int)

        return df
    except Exception as e:
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
# Tab 1: 🏠 概況 (加入刷新功能)
# ==========================================
with tab1:
    # 使用 columns 讓標題和按鈕排在同一排
    c_title, c_refresh = st.columns([3, 1.5])

    c_title.subheader("📊 本月速覽")

    # 👇 新增這個按鈕：強制清除快取，重新抓資料
    if c_refresh.button("🔄 刷新數據"):
        st.cache_data.clear()
        st.toast("正在同步最新資料...", icon="☁️")
        st.rerun()

    try:
        # 讀取資料
        df_sess = get_data("sessions")

        # 1. 計算待結算 (已經上完課，但還沒開發票)
        # 條件：狀態是「已完成」 且 (invoice_id 是空的 或 0)
        pending_mask = (df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)
        pending_sessions = df_sess[pending_mask]

        pending_count = len(pending_sessions)
        pending_income = 0

        for _, row in pending_sessions.iterrows():
            try:
                # 確保時間格式正確
                s = pd.to_datetime(row['start_time'])
                e = pd.to_datetime(row['end_time'])
                h = (e - s).total_seconds() / 3600
                # 確保費率是數字
                rate = int(row['actual_rate']) if pd.notna(row['actual_rate']) else 0
                pending_income += h * rate
            except:
                pass

        # 2. 計算本月已預約 (還沒上課的)
        # 條件：狀態是「已預約」
        # (這裡簡單抓所有已預約的，你也可以改成只抓本月的)
        future_mask = (df_sess['status'] == '已預約')
        future_count = len(df_sess[future_mask])

        # --- 顯示數據卡片 ---
        st.markdown("### 💰 財務狀況")
        col1, col2 = st.columns(2)

        # 顯示卡片 1
        col1.metric(
            label="待結算金額 (已上完)",
            value=f"${int(pending_income):,}",
            delta=f"{pending_count} 堂課",
            delta_color="normal"  # 綠色
        )

        # 顯示卡片 2
        col2.metric(
            label="未來預約 (未上課)",
            value=f"{future_count} 堂",
            delta="預排",
            delta_color="off"  # 灰色
        )

    except Exception as e:
        st.error(f"資料讀取錯誤: {e}")
        st.write("請嘗試按上方的「刷新數據」按鈕")

    st.divider()

    # --- 提示區塊 ---
    st.info("""
    💡 **小知識：**
    * 為了保護您的 Google 連線額度，**資料會每 10 分鐘自動更新一次**。
    * 如果您剛新增完課程，想馬上看到最新金額，請按上方的 **「🔄 刷新數據」** 按鈕。
    """)
# ==========================================
# Tab 2: 📅 排課 (終極完整版)
# ==========================================
with tab2:
    # 1. 讀取資料
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # --- 2. 編輯模式 ---
    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯課程 / 紀錄進度")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]

        if not row.empty:
            row = row.iloc[0]
            s_dt = pd.to_datetime(row['start_time'])
            e_dt = pd.to_datetime(row['end_time'])
            current_sid = int(row['student_id'])
            s_name = df_stu[df_stu['id'] == current_sid]['name'].values[0] if current_sid in df_stu['id'].values else ""
            # 讀取原本的進度
            old_progress = row['progress'] if 'progress' in row else ""

            with st.container(border=True):
                c1, c2 = st.columns(2)
                s_idx = list(student_map.keys()).index(s_name) if s_name in student_map else 0
                edit_stu = c1.selectbox("學生", list(student_map.keys()), index=s_idx)
                edit_date = c2.date_input("日期", s_dt.date())

                c3, c4 = st.columns(2)
                edit_time = c3.time_input("時間", s_dt.time())
                old_dur = (e_dt - s_dt).total_seconds() / 3600
                edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)

                # 進度輸入框
                edit_progress = st.text_area("📖 當日進度 / 聯絡簿", value=old_progress,
                                             placeholder="例如：數學 Ch3-2, 作業 p.45")

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
                        df_sess.loc[idx, 'progress'] = edit_progress

                        g_event_id = row['google_event_id'] if 'google_event_id' in row and pd.notna(
                            row['google_event_id']) else None
                        if g_event_id:
                            update_google_event(g_event_id, f"家教: {edit_stu}", new_start, new_end)

                        update_data("sessions", df_sess)
                        st.session_state.edit_session_id = None
                        st.toast("修改成功！", icon="✅")
                        st.rerun()
                with col_cancel:
                    if st.button("❌ 取消"):
                        st.session_state.edit_session_id = None
                        st.rerun()
        else:
            st.warning("查無此課程")
            if st.button("返回"):
                st.session_state.edit_session_id = None
                st.rerun()

    else:
        # --- ➕ 新增模式 ---
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())

                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                # 進度輸入框
                n_progress = st.text_area("📖 預定進度 / 備註 (選填)", height=68, placeholder="可先填寫預計要教什麼...")

                if st.button("✅ 確認新增", type="primary"):
                    start_p = datetime.combine(d_input, t_input)
                    end_p = start_p + timedelta(hours=dur)
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                    status = '已完成' if start_p < datetime.now() else '已預約'

                    g_event_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    new_id = get_next_id(df_sess)
                    new_row = pd.DataFrame([{
                        'id': new_id, 'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': status, 'actual_rate': rate, 'invoice_id': None,
                        'google_event_id': g_event_id,
                        'progress': n_progress
                    }])

                    df_sess = pd.concat([df_sess, new_row], ignore_index=True)
                    update_data("sessions", df_sess)
                    st.toast("新增成功！", icon="🎉")
                    st.rerun()
            else:
                st.warning("請先到「學生」分頁新增學生資料！")

    st.divider()
    # 👇👇👇 請插入這段「智慧修復區塊」 👇👇👇
    with st.expander("🛠️ 日曆連線診斷與修復", expanded=False):
        st.caption("如果發現有些課程沒出現在 Google 日曆上，請按下方按鈕進行檢查。")

        if st.button("🔍 掃描並修復所有漏掉的日曆", type="primary"):
            # 1. 讀取最新資料
            df_fix = get_data("sessions")
            df_stu_fix = get_data("students")

            # 2. 找出「未來」且「還沒取消」的課程
            # 條件：狀態不是「已完成」 (簡單判斷：只要還沒上完的都檢查)
            # 並且 google_event_id 是空的 (代表漏掉了)

            # 先確保欄位存在
            if 'google_event_id' not in df_fix.columns:
                df_fix['google_event_id'] = ""

            # 篩選出問題課程：(未來課程) AND (沒有 ID 或 ID 是空的)
            now_str = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            mask_missing = (df_fix['start_time'] > now_str) & \
                           ((df_fix['google_event_id'].isna()) | (df_fix['google_event_id'] == ""))

            missing_count = mask_missing.sum()

            if missing_count == 0:
                st.success("🎉 太棒了！檢查完畢，所有未來課程都已經連接日曆，沒有漏掉的！")
            else:
                st.warning(f"⚠️ 發現 {missing_count} 筆課程漏掉日曆，正在自動補建中...")
                progress_bar = st.progress(0)

                # 準備修復
                # 建立臨時的 ID 對照表方便查找學生名字
                stu_map = dict(zip(df_stu_fix['id'], df_stu_fix['name']))

                # 逐筆修復
                fixed_rows = df_fix[mask_missing].index
                for i, idx in enumerate(fixed_rows):
                    row = df_fix.loc[idx]
                    sid = int(row['student_id'])
                    s_name = stu_map.get(sid, "未知學生")

                    s_dt = pd.to_datetime(row['start_time'])
                    e_dt = pd.to_datetime(row['end_time'])

                    # 呼叫 API 補建日曆
                    new_eid = create_google_event(f"家教: {s_name}", s_dt, e_dt)

                    if new_eid:
                        # 把新 ID 寫回資料表
                        df_fix.loc[idx, 'google_event_id'] = new_eid

                    # 更新進度條
                    progress_bar.progress((i + 1) / missing_count)

                # 最後一次性存檔
                update_data("sessions", df_fix)
                st.success(f"✅ 成功修復 {missing_count} 筆日曆！請查看 Google 日曆。")
                time.sleep(2)
                st.rerun()
    # 👆👆👆 插入結束 👆👆👆
    # --- 3. 顯示日曆 (獨立區塊，確保永遠顯示) ---
    c_cal, c_refresh = st.columns([4, 1])
    c_cal.subheader("🗓️ 課程行事曆")
    if c_refresh.button("🔄 重新整理"):
        st.cache_data.clear()
        st.rerun()

    events = []
    if not df_sess.empty and not df_stu.empty:
        try:
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
        except Exception as e:
            st.error("日曆讀取錯誤")

    calendar_options = {
        "headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth,timeGridWeek"},
        "initialView": "dayGridMonth",
        "timeZone": "local",
        "locale": "zh-tw",
        "selectable": True,
    }

    cal = calendar(events=events, options=calendar_options, callbacks=['eventClick'], key="main_calendar")

    if cal.get("eventClick"):
        clicked_event = cal["eventClick"]["event"]
        clicked_id = int(clicked_event["id"])
        if st.session_state.edit_session_id != clicked_id:
            st.session_state.edit_session_id = clicked_id
            st.toast("👆 已選取，請至上方編輯")
            time.sleep(0.5)
            st.rerun()

    # --- 5. 列表模式 (包含：詳細列表、刪除、補連日曆、顯示進度) ---
    with st.expander("📋 詳細列表 / 刪除 / 補建日曆", expanded=True):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                20)

            for _, row in df_display.iterrows():
                sess_id = int(row['id_x'])
                name = row['name']
                t_str = pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')

                # 檢查進度
                prog = row['progress'] if 'progress' in row and pd.notna(row['progress']) else ""
                # 檢查日曆連線
                g_id = row['google_event_id'] if 'google_event_id' in row else ""
                is_connected = pd.notna(g_id) and str(g_id).strip() != ""

                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([4, 1.5, 1, 1])

                    # 1. 資訊欄
                    c1.markdown(f"**{name}** - {t_str}")
                    if prog: c1.caption(f"📖 {prog}")  # 顯示進度
                    if not is_connected: c1.caption("⚠️ **未連接日曆**")

                    # 2. 補連按鈕
                    if not is_connected:
                        if c2.button("🔗 補連", key=f"link_{sess_id}"):
                            s_dt = pd.to_datetime(row['start_time'])
                            e_dt = pd.to_datetime(row['end_time'])
                            new_g_id = create_google_event(f"家教: {name}", s_dt, e_dt)
                            if new_g_id:
                                df_sess.loc[df_sess['id'] == sess_id, 'google_event_id'] = new_g_id
                                update_data("sessions", df_sess)
                                st.rerun()
                    else:
                        c2.write("")

                        # 3. 編輯
                    if c3.button("✏️", key=f"ed_{sess_id}"):
                        st.session_state.edit_session_id = sess_id
                        st.rerun()

                    # 4. 刪除
                    if c4.button("🗑️", key=f"del_{sess_id}"):
                        if is_connected:
                            try:
                                delete_google_event(str(g_id))
                            except:
                                pass
                        df_sess = df_sess[df_sess['id'].astype(int) != sess_id]
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
            # 1. 找出「已完成」且「還沒綁定 invoice_id」的課程
            # 使用 fillna(0) 確保不會因為空值而漏抓
            pending_mask = (df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)
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

                    # 計算這次新增的金額
                    total_new = 0
                    for _, r in my_sessions.iterrows():
                        s = pd.to_datetime(r['start_time'])
                        e = pd.to_datetime(r['end_time'])
                        hours = (e - s).total_seconds() / 3600
                        total_new += hours * r['actual_rate']

                    # 檢查該學生是否有「未付款」的舊帳單 (要合併)
                    inv_id = None

                    if not df_inv.empty:
                        # 嚴格篩選：is_paid 必須等於 0
                        unpaid_mask = (df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)

                        if unpaid_mask.any():
                            # --- 合併模式 ---
                            # 抓出最新的一張未付帳單
                            target_inv = df_inv[unpaid_mask].sort_values('created_at', ascending=False).iloc[0]
                            inv_id = target_inv['id']

                            # 確保舊金額是數字，避免出錯
                            old_amount = int(target_inv['total_amount'])
                            new_total = old_amount + int(total_new)

                            # 更新 DataFrame
                            df_inv.loc[df_inv['id'] == inv_id, 'total_amount'] = new_total
                            df_inv.loc[df_inv['id'] == inv_id, 'created_at'] = datetime.now().isoformat()
                            # 顯示訊息幫助除錯
                            # st.toast(f"合併帳單 #{inv_id}: ${old_amount} + ${int(total_new)}")

                    # 如果沒找到舊帳單 (inv_id 還是 None)，就新增一張
                    if inv_id is None:
                        # --- 新增模式 ---
                        inv_id = get_next_id(df_inv)
                        new_inv = pd.DataFrame([{
                            'id': inv_id,
                            'student_id': sid,
                            'total_amount': int(total_new),
                            'created_at': datetime.now().isoformat(),
                            'is_paid': 0
                        }])
                        df_inv = pd.concat([df_inv, new_inv], ignore_index=True)

                    # 關鍵：把這些課程的 invoice_id 更新為這張帳單的 ID
                    # 確保 inv_id 格式正確
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