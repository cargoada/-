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

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]

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

# 🔥 關鍵修復：在這裡初始化編輯狀態變數
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


# ==========================================
# 請直接覆蓋 app.py 裡面的這三個函式
# ==========================================

# 👇 請填入你的 Gmail (記得保留前後引號)
MY_CALENDAR_ID = 'cargoada@gmail.com'

def create_google_event(title, start_dt, end_dt):
    if service is None: return None
    try:
        # 指定寫入你的日曆
        event = service.events().insert(calendarId=MY_CALENDAR_ID, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return event.get('id')
    except Exception as e:
        print(f"建立失敗: {e}")
        return None

def update_google_event(event_id, title, start_dt, end_dt):
    if service is None or not event_id: return False
    try:
        # 指定更新你的日曆
        service.events().update(calendarId=MY_CALENDAR_ID, eventId=event_id, body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return True
    except: return False

def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        # 指定從你的日曆刪除
        service.events().delete(calendarId=MY_CALENDAR_ID, eventId=event_id).execute()
        return True
    except: return False

# ==========================================
# 4. 主程式分頁
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# --- Tab 1: 概況 ---
with tab1:
    st.subheader("📊 本月速覽")
    if st.button("🔄 刷新數據"): st.cache_data.clear(); st.rerun()
    df_sess = get_data("sessions")
    if not df_sess.empty:
        pending = df_sess[(df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)]
        amt = sum(((pd.to_datetime(r['end_time']) - pd.to_datetime(r['start_time'])).total_seconds() / 3600) * int(
            r['actual_rate']) for _, r in pending.iterrows())
        c1, c2 = st.columns(2)
        c1.metric("待結算金額", f"${int(amt):,}", f"{len(pending)} 堂")
        c2.metric("總課程數", f"{len(df_sess)} 堂")

# --- Tab 2: 排課 (包含編輯、同步選項、日曆) ---
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    # 判斷是編輯模式還是新增模式
    if st.session_state.edit_session_id:
        # [編輯模式]
        st.subheader("✏️ 編輯課程")
        edit_id = st.session_state.edit_session_id
        row = df_sess[df_sess['id'] == edit_id]

        if not row.empty:
            row = row.iloc[0]
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
                edit_prog = st.text_area("當日進度", value=old_prog)

                col_save, col_cancel = st.columns(2)
                if col_save.button("💾 儲存變更", type="primary"):
                    new_start = datetime.combine(edit_date, edit_time)
                    new_end = new_start + timedelta(hours=edit_dur)
                    new_sid = student_map[edit_stu]
                    rate = int(df_stu[df_stu['id'] == new_sid]['default_rate'].values[0])

                    idx = df_sess[df_sess['id'] == edit_id].index
                    df_sess.loc[idx, ['student_id', 'start_time', 'end_time', 'actual_rate', 'progress']] = \
                        [new_sid, new_start.strftime('%Y-%m-%dT%H:%M:%S'), new_end.strftime('%Y-%m-%dT%H:%M:%S'), rate,
                         edit_prog]

                    # 嘗試更新日曆
                    gid = row.get('google_event_id', "")
                    if gid and service: update_google_event(gid, f"家教: {edit_stu}", new_start, new_end)

                    update_data("sessions", df_sess)
                    st.session_state.edit_session_id = None
                    st.success("更新成功！")
                    st.rerun()

                if col_cancel.button("❌ 取消"):
                    st.session_state.edit_session_id = None
                    st.rerun()
        else:
            st.error("查無此課程")
            st.session_state.edit_session_id = None
            st.rerun()
    else:
        # [新增模式]
        st.subheader("➕ 快速記課")
        with st.container(border=True):
            if not df_stu.empty:
                c1, c2 = st.columns(2)
                sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
                d_input = c2.date_input("日期", datetime.now())
                c3, c4 = st.columns(2)
                t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
                dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

                # 同步選項 (預設 False)
                do_sync = st.checkbox("🔄 同步至 Google 日曆", value=False)

                n_prog = st.text_area("預定進度")

                if st.button("✅ 新增課程", type="primary"):
                    start_p = datetime.combine(d_input, t_input)
                    end_p = start_p + timedelta(hours=dur)
                    sid = student_map[sel_stu]
                    rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])

                    g_id = ""
                    if do_sync and service:
                        g_id = create_google_event(f"家教: {sel_stu}", start_p, end_p)

                    new_row = pd.DataFrame([{
                        'id': int(df_sess['id'].max() + 1) if not df_sess.empty else 1,
                        'student_id': sid,
                        'start_time': start_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end_time': end_p.strftime('%Y-%m-%dT%H:%M:%S'),
                        'status': '已完成' if start_p < datetime.now() else '已預約',
                        'actual_rate': rate,
                        'google_event_id': g_id,
                        'progress': n_prog
                    }])
                    update_data("sessions", pd.concat([df_sess, new_row], ignore_index=True))
                    st.success("已新增！")
                    time.sleep(1)
                    st.rerun()

    # 日曆顯示
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
                    "id": str(row['id_x']),
                    "title": row['name'],
                    "start": row['start_time'], "end": row['end_time'],
                    "backgroundColor": row['color'], "borderColor": row['color']
                })
        except:
            pass

    cal = calendar(events=events, options={"initialView": "dayGridMonth"}, callbacks=['eventClick'], key="cal_main")
    if cal.get("eventClick"):
        cid = int(cal["eventClick"]["event"]["id"])
        if st.session_state.edit_session_id != cid:
            st.session_state.edit_session_id = cid
            st.rerun()

    # 列表與刪除 (防呆版)
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
                    c1, c2, c3 = st.columns([4, 1, 1])
                    c1.markdown(f"**{row['name']}** - {pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')}")
                    if connected: c1.caption("✅ 已同步")

                    if c2.button("✏️", key=f"ed{sid}"):
                        st.session_state.edit_session_id = sid
                        st.rerun()
                    if c3.button("🗑️", key=f"del{sid}"):
                        if connected: delete_google_event(gid)
                        df_sess = df_sess[df_sess['id'].astype(int) != sid]
                        update_data("sessions", df_sess)
                        st.rerun()

# --- Tab 3: 帳單 ---
with tab3:
    st.subheader("💰 帳單中心")
    df_inv = get_data("invoices")
    if st.button("⚡ 一鍵結算"):
        pending_mask = (df_sess['status'] == '已完成') & (df_sess['invoice_id'].fillna(0) == 0)
        p_sids = df_sess[pending_mask]['student_id'].unique()
        if len(p_sids) > 0:
            for sid in p_sids:
                sub_df = df_sess[(df_sess['student_id'] == sid) & pending_mask]
                amt = sum(
                    ((pd.to_datetime(r['end_time']) - pd.to_datetime(r['start_time'])).total_seconds() / 3600) * r[
                        'actual_rate'] for _, r in sub_df.iterrows())
                inv_id = None
                if not df_inv.empty:
                    unpaid = df_inv[(df_inv['student_id'] == sid) & (df_inv['is_paid'] == 0)]
                    if not unpaid.empty: inv_id = unpaid.iloc[0]['id']; df_inv.loc[
                        df_inv['id'] == inv_id, 'total_amount'] += int(amt)
                if inv_id is None:
                    inv_id = int(df_inv['id'].max() + 1) if not df_inv.empty else 1
                    df_inv = pd.concat([df_inv, pd.DataFrame(
                        [{'id': inv_id, 'student_id': sid, 'total_amount': int(amt),
                          'created_at': datetime.now().isoformat(), 'is_paid': 0}])], ignore_index=True)
                df_sess.loc[sub_df.index, 'invoice_id'] = inv_id
            update_data("invoices", df_inv);
            update_data("sessions", df_sess);
            st.success("結算完成");
            st.rerun()
        else:
            st.info("無未結算課程")

    if not df_inv.empty:
        unpaid = df_inv[df_inv['is_paid'] == 0]
        if not unpaid.empty:
            df_disp = pd.merge(unpaid, df_stu, left_on='student_id', right_on='id').sort_values('created_at',
                                                                                                ascending=False)
            for _, row in df_disp.iterrows():
                inv_id = row['id_x']
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    c1.markdown(f"**{row['name']}** - ${row['total_amount']:,}")
                    if c2.button("收款", key=f"p{inv_id}"):
                        df_inv.loc[df_inv['id'] == inv_id, 'is_paid'] = 1;
                        update_data("invoices", df_inv);
                        st.rerun()
                    with st.expander("查看明細"):
                        my_ds = df_sess[(pd.to_numeric(df_sess['invoice_id'], errors='coerce') == inv_id)].copy()
                        if not my_ds.empty:
                            show = [{"日期": pd.to_datetime(r['start_time']).strftime('%m/%d'), "金額": int(((
                                                                                                                         pd.to_datetime(
                                                                                                                             r[
                                                                                                                                 'end_time']) - pd.to_datetime(
                                                                                                                     r[
                                                                                                                         'start_time'])).total_seconds() / 3600) *
                                                                                                            r[
                                                                                                                'actual_rate'])}
                                    for _, r in my_ds.iterrows()]
                            st.table(show)
                            # 下載 CSV
                            csv = pd.DataFrame(show).to_csv(index=False).encode('utf-8-sig')
                            st.download_button("📥 下載 Excel", csv, f"{row['name']}_帳單.csv", "text/csv")

# --- Tab 4: 學生 (詳細資訊版) ---
with tab4:
    st.subheader("🧑‍🎓 學生管理")
    with st.expander("➕ 新增學生"):
        with st.form("add_stu_form"):
            c1, c2 = st.columns(2)
            n = c1.text_input("姓名");
            r = c2.number_input("時薪", 500)
            color_opt = st.selectbox("顏色", ["#FF5733 (紅)", "#3498DB (藍)", "#2ECC71 (綠)", "#F1C40F (黃)"])
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

    # 👇 測試專用：放在程式碼最下面
st.divider()
st.subheader("🔧 日曆連線測試區")
if st.button("測試連線"):
    if service:
        try:
            # 1. 測試讀取
            colors = service.colors().get().execute()
            st.success("✅ 1. 連線成功 (機器人活著)")

            # 2. 測試寫入權限
            test_event = {
                'summary': '測試連線 (可刪除)',
                'start': {'dateTime': datetime.now().isoformat(), 'timeZone': 'Asia/Taipei'},
                'end': {'dateTime': (datetime.now() + timedelta(minutes=10)).isoformat(), 'timeZone': 'Asia/Taipei'},
            }
            res = service.events().insert(calendarId='primary', body=test_event).execute()
            st.success(f"✅ 2. 寫入成功！請看日曆上有沒有出現「測試連線」")
            st.json(res)
        except Exception as e:
            st.error(f"❌ 發生錯誤：{e}")
            st.info("如果顯示 '403 Forbidden'，代表你沒開權限給機器人。")
    else:
        st.error("❌ Service 變數是空的 (Secrets 設定有錯)")