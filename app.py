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
# 1. 系統設定與 Google 服務連線
# ==========================================
st.set_page_config(page_title="家教排課系統", page_icon="📅", layout="centered")

# --- 設定 Google API 範圍 ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]

# --- 啟動 Google 日曆機器人 (Service) ---
service = None
try:
    # 嘗試抓取 secrets
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = dict(st.secrets["connections"]["gsheets"])
    elif "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
    else:
        creds_dict = None

    if creds_dict:
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        service = build('calendar', 'v3', credentials=creds)
    else:
        st.error("⚠️ 找不到 Google 憑證，日曆功能將無法使用。")
except Exception as e:
    # 這裡只印出警告，不阻擋程式執行
    print(f"Google 日曆連線失敗: {e}")

# --- 設定 Google Sheet 連線 ---
conn = st.connection("gsheets", type=GSheetsConnection)
# 請確認這裡的網址變數名稱跟你的 secrets.toml 一樣
# 如果你是多使用者版，請自行調整這裡讀取網址的邏輯
try:
    CURRENT_SHEET_URL = st.secrets["users"]["jiong"]["sheet_url"]
except:
    st.error("找不到 Sheet URL，請檢查 secrets.toml")
    st.stop()


# ==========================================
# 2. 小幫手函式 (資料庫與日曆操作)
# ==========================================

def get_data(worksheet_name):
    """讀取資料 (快取 10 分鐘防止 429 錯誤)"""
    try:
        # ttl=600 秒 (10分鐘)
        df = conn.read(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, ttl=600)

        # 強制型別轉換 (防呆)
        if worksheet_name == 'students':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['default_rate'] = pd.to_numeric(df['default_rate'], errors='coerce').fillna(0).astype(int)
        elif worksheet_name == 'sessions':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['actual_rate'] = pd.to_numeric(df['actual_rate'], errors='coerce').fillna(0).astype(int)
            # 確保文字欄位存在
            if 'google_event_id' not in df.columns: df['google_event_id'] = ""
            if 'progress' not in df.columns: df['progress'] = ""
            df['progress'] = df['progress'].fillna("").astype(str)
        elif worksheet_name == 'invoices':
            df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
            df['student_id'] = pd.to_numeric(df['student_id'], errors='coerce').fillna(0).astype(int)
            df['total_amount'] = pd.to_numeric(df['total_amount'], errors='coerce').fillna(0).astype(int)
            df['is_paid'] = pd.to_numeric(df['is_paid'], errors='coerce').fillna(0).astype(int)

        return df
    except Exception as e:
        st.error(f"讀取 {worksheet_name} 失敗，請稍後再試。錯誤: {e}")
        return pd.DataFrame()


def update_data(worksheet_name, df):
    """寫入資料並清除快取"""
    try:
        conn.update(spreadsheet=CURRENT_SHEET_URL, worksheet=worksheet_name, data=df)
        st.cache_data.clear()  # 關鍵：寫入後馬上清除記憶，確保下次讀到新的
    except Exception as e:
        st.error(f"寫入失敗：{e}")


def get_next_id(df):
    """產生新的 ID"""
    if df.empty: return 1
    # 確保 ID 是整數再找最大值
    return int(pd.to_numeric(df['id'], errors='coerce').max()) + 1


# --- Google 日曆操作 (含時區修正) ---
def create_google_event(title, start_dt, end_dt):
    if service is None: return None
    try:
        event_body = {
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }
        event = service.events().insert(calendarId='primary', body=event_body).execute()
        return event.get('id')
    except Exception as e:
        print(f"建立日曆失敗: {e}")
        return None


def update_google_event(event_id, title, start_dt, end_dt):
    if service is None or not event_id: return False
    try:
        event_body = {
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }
        service.events().update(calendarId='primary', eventId=event_id, body=event_body).execute()
        return True
    except:
        return False


def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return True
    except:
        return False


# ==========================================
# 3. 主程式介面
# ==========================================

# 初始化 Session State
if 'edit_session_id' not in st.session_state: st.session_state.edit_session_id = None

st.title("📅 家教排課小幫手")
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# ================= Tab 1: 概況 =================
with tab1:
    c_title, c_refresh = st.columns([3, 1.5])
    c_title.subheader("📊 本月速覽")
    if c_refresh.button("🔄 刷新數據"):
        st.cache_data.clear()
        st.rerun()

    df_sess = get_data("sessions")
    if not df_sess.empty:
        # 計算待結算
        pending_mask = (df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)
        pending_income = 0
        for _, row in df_sess[pending_mask].iterrows():
            try:
                s = pd.to_datetime(row['start_time'])
                e = pd.to_datetime(row['end_time'])
                h = (e - s).total_seconds() / 3600
                pending_income += h * int(row['actual_rate'])
            except:
                pass

        col1, col2 = st.columns(2)
        col1.metric("待結算金額", f"${int(pending_income):,}", f"{pending_mask.sum()} 堂")
        col2.metric("總課程數", f"{len(df_sess)} 堂")
    else:
        st.info("尚無資料，請先排課")

# ================= Tab 2: 排課 (核心功能) =================
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # --- A. 表單區 (新增/編輯) ---
    if st.session_state.edit_session_id:
        st.subheader("✏️ 編輯課程 / 紀錄進度")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]
        if not row.empty:
            row = row.iloc[0]
            # 載入舊資料
            s_dt = pd.to_datetime(row['start_time'])
            e_dt = pd.to_datetime(row['end_time'])
            cur_sid = int(row['student_id'])
            s_name = df_stu[df_stu['id'] == cur_sid]['name'].values[0] if cur_sid in df_stu['id'].values else ""
            old_prog = row['progress'] if 'progress' in row else ""

            with st.container(border=True):
                c1, c2 = st.columns(2)
                s_idx = list(student_map.keys()).index(s_name) if s_name in student_map else 0
                edit_stu = c1.selectbox("學生", list(student_map.keys()), index=s_idx)
                edit_date = c2.date_input("日期", s_dt.date())
                c3, c4 = st.columns(2)
                edit_time = c3.time_input("時間", s_dt.time())
                old_dur = (e_dt - s_dt).total_seconds() / 3600
                edit_dur = c4.slider("時數", 0.5, 3.0, float(old_dur), 0.5)
                edit_prog = st.text_area("📖 當日進度", value=old_prog)

                col_save, col_cancel = st.columns(2)
                if col_save.button("💾 儲存", type="primary"):
                    new_start = datetime.combine(edit_date, edit_time)
                    new_end = new_start + timedelta(hours=edit_dur)
                    new_sid = student_map[edit_stu]
                    rate = int(df_stu[df_stu['id'] == new_sid]['default_rate'].values[0])
                    status = '已完成' if new_start < datetime.now() else '已預約'

                    idx = df_sess[df_sess['id'] == edit_id].index
                    df_sess.loc[idx, ['student_id', 'start_time', 'end_time', 'status', 'actual_rate', 'progress']] = \
                        [new_sid, new_start.strftime('%Y-%m-%dT%H:%M:%S'), new_end.strftime('%Y-%m-%dT%H:%M:%S'),
                         status, rate, edit_prog]

                    # 更新日曆
                    g_id = row['google_event_id'] if 'google_event_id' in row else None
                    if g_id: update_google_event(g_id, f"家教: {edit_stu}", new_start, new_end)

                    update_data("sessions", df_sess)
                    st.session_state.edit_session_id = None
                    st.rerun()

                if col_cancel.button("❌ 取消"):
                    st.session_state.edit_session_id = None
                    st.rerun()
    else:
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())
                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)
                n_prog = st.text_area("預定進度", placeholder="選填...")

                if st.button("✅ 新增課程", type="primary"):
                    start_p = datetime.combine(d_input, t_input)
                    end_p = start_p + timedelta(hours=dur)
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])
                    status = '已完成' if start_p < datetime.now() else '已預約'

                    # 建立日曆
                    g_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    new_row = pd.DataFrame([{
                        'id': get_next_id(df_sess), 'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': status, 'actual_rate': rate, 'invoice_id': None,
                        'google_event_id': g_id, 'progress': n_prog
                    }])
                    df_sess = pd.concat([df_sess, new_row], ignore_index=True)
                    update_data("sessions", df_sess)
                    st.rerun()

    # --- B. 日曆區 (安全顯示) ---
    st.divider()
    c_cal, c_ref = st.columns([4, 1])
    c_cal.subheader("🗓️ 行事曆")
    if c_ref.button("重整"): st.rerun()

    events = []
    if not df_sess.empty and not df_stu.empty:
        try:
            merged = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id')
            for _, row in merged.iterrows():
                events.append({
                    "id": str(row['id_x']), "title": row['name'],
                    "start": row['start_time'], "end": row['end_time'],
                    "backgroundColor": row['color'], "borderColor": row['color']
                })
        except:
            pass

    cal = calendar(events=events,
                   options={"headerToolbar": {"left": "title", "right": "dayGridMonth,listMonth,prev,next"},
                            "initialView": "dayGridMonth"}, callbacks=['eventClick'], key="cal")
    if cal.get("eventClick"):
        cid = int(cal["eventClick"]["event"]["id"])
        if st.session_state.edit_session_id != cid:
            st.session_state.edit_session_id = cid
            st.rerun()

    # --- C. 列表與修復區 ---
    with st.expander("📋 詳細列表 / 補建日曆 / 刪除", expanded=True):
        # 一鍵修復按鈕
        if st.button("🔍 掃描並補建漏掉的日曆"):
            fixed = 0
            if not df_sess.empty:
                for idx, row in df_sess.iterrows():
                    # 如果沒有 ID 且是未來課程
                    if (pd.isna(row['google_event_id']) or row['google_event_id'] == "") and row[
                        'start_time'] > datetime.now().isoformat():
                        sid = int(row['student_id'])
                        s_name = df_stu[df_stu['id'] == sid]['name'].values[0] if sid in df_stu['id'].values else "未知"
                        s_dt = pd.to_datetime(row['start_time'])
                        e_dt = pd.to_datetime(row['end_time'])
                        new_gid = create_google_event(f"家教: {s_name}", s_dt, e_dt)
                        if new_gid:
                            df_sess.loc[idx, 'google_event_id'] = new_gid
                            fixed += 1
                if fixed > 0:
                    update_data("sessions", df_sess)
                    st.success(f"已修復 {fixed} 筆！")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.info("沒有發現漏掉的日曆。")

        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                20)
            for _, row in df_display.iterrows():
                sid = int(row['id_x'])
                connected = pd.notna(row['google_event_id']) and str(row['google_event_id']) != ""
                with st.container(border=True):
                    c1, c2, c3 = st.columns([4, 1, 1])
                    c1.markdown(f"**{row['name']}** - {pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')}")
                    if not connected: c1.caption("⚠️ 未連線")
                    if row['progress']: c1.caption(f"📖 {row['progress']}")

                    if c2.button("✏️", key=f"e{sid}"):
                        st.session_state.edit_session_id = sid
                        st.rerun()
                    if c3.button("🗑️", key=f"d{sid}"):
                        if connected: delete_google_event(row['google_event_id'])
                        df_sess = df_sess[df_sess['id'].astype(int) != sid]
                        update_data("sessions", df_sess)
                        st.rerun()

# ================= Tab 3: 帳單 =================
with tab3:
    st.subheader("💰 帳單中心")
    if st.button("⚡ 一鍵結算 (產生本月帳單)", type="primary"):
        df_inv = get_data("invoices")
        pending_mask = (df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)
        p_sids = df_sess[pending_mask]['student_id'].unique()

        if len(p_sids) > 0:
            for sid in p_sids:
                # 計算金額
                sub_df = df_sess[(df_sess['student_id'] == sid) & pending_mask]
                amt = sum(
                    ((pd.to_datetime(r['end_time']) - pd.to_datetime(r['start_time'])).total_seconds() / 3600) * r[
                        'actual_rate'] for _, r in sub_df.iterrows())

                # 找舊帳單或開新單
                inv_id = None
                if not df_inv.empty:
                    unpaid = df_inv[(df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)]
                    if not unpaid.empty:
                        inv_id = unpaid.iloc[0]['id']
                        df_inv.loc[df_inv['id'] == inv_id, 'total_amount'] += int(amt)

                if inv_id is None:
                    inv_id = get_next_id(df_inv)
                    new_inv = pd.DataFrame([{'id': inv_id, 'student_id': sid, 'total_amount': int(amt),
                                             'created_at': datetime.now().isoformat(), 'is_paid': 0}])
                    df_inv = pd.concat([df_inv, new_inv], ignore_index=True)

                # 更新課程
                df_sess.loc[sub_df.index, 'invoice_id'] = inv_id

            update_data("invoices", df_inv)
            update_data("sessions", df_sess)
            st.success("結算完成！")
            st.rerun()
        else:
            st.info("沒有未結算的課程")

    # 顯示未付款
    df_inv = get_data("invoices")
    if not df_inv.empty:
        unpaid = df_inv[df_inv['is_paid'] == 0]
        if not unpaid.empty:
            df_disp = pd.merge(unpaid, df_stu, left_on='student_id', right_on='id')
            for _, row in df_disp.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{row['name']}** - ${row['total_amount']:,}")
                    if c2.button("收款", key=f"pay{row['id_x']}"):
                        df_inv.loc[df_inv['id'] == row['id_x'], 'is_paid'] = 1
                        update_data("invoices", df_inv)
                        st.rerun()
        else:
            st.write("👏 目前沒有待收款項")

# ================= Tab 4: 學生 =================
with tab4:
    st.subheader("🧑‍🎓 學生管理")
    with st.expander("➕ 新增學生"):
        with st.form("add_stu"):
            n = st.text_input("姓名")
            r = st.number_input("時薪", value=500, step=50)
            c = st.selectbox("顏色", ["#FF5733", "#3498DB", "#2ECC71", "#FFC300"])
            if st.form_submit_button("新增"):
                new_stu = pd.DataFrame(
                    [{'id': get_next_id(df_stu), 'name': n, 'default_rate': r, 'color': c, 'parent_contact': ''}])
                df_stu = pd.concat([df_stu, new_stu], ignore_index=True)
                update_data("students", df_stu)
                st.rerun()

    for _, row in df_stu.iterrows():
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{row['name']}** (${row['default_rate']}/hr)")
            if c2.button("刪除", key=f"ds{row['id']}"):
                df_stu = df_stu[df_stu['id'] != row['id']]
                update_data("students", df_stu)
                st.rerun()