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

try:
    CURRENT_USER = st.session_state.current_user
    CURRENT_SHEET_URL = st.secrets["users"][CURRENT_USER]
except:
    st.session_state.current_user = None
    st.rerun()

conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 3. 側邊欄
# ==========================================
with st.sidebar:
    st.header(f"👤 您好，{CURRENT_USER}")
    if st.button("🚪 登出 / 切換身分"):
        st.session_state.current_user = None
        st.cache_data.clear()
        st.rerun()


# ==========================================
# 4. 小幫手函式
# ==========================================
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


def create_google_event(title, start_dt, end_dt):
    if service is None: return None
    try:
        event = service.events().insert(calendarId='primary', body={
            'summary': title,
            'start': {'dateTime': start_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
            'end': {'dateTime': end_dt.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'Asia/Taipei'},
        }).execute()
        return event.get('id')
    except:
        return None


def delete_google_event(event_id):
    if service is None or not event_id: return False
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return True
    except:
        return False


# ==========================================
# 5. 主程式分頁
# ==========================================
tab1, tab2, tab3, tab4 = st.tabs(["🏠 概況", "📅 排課", "💰 帳單", "🧑‍🎓 學生"])

# ... Tab 1 概況 (保持不變) ...
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

# ... Tab 2 排課 (修改預設值) ...
with tab2:
    df_stu = get_data("students")
    df_sess = get_data("sessions")
    student_map = dict(zip(df_stu['name'], df_stu['id'])) if not df_stu.empty else {}

    st.subheader("➕ 快速記課")
    with st.container(border=True):
        if not df_stu.empty:
            c1, c2 = st.columns(2)
            sel_stu = c1.selectbox("選擇學生", df_stu['name'].tolist())
            d_input = c2.date_input("日期", datetime.now())
            c3, c4 = st.columns(2)
            t_input = c3.time_input("開始", datetime.now().replace(minute=0, second=0))
            dur = c4.slider("時數", 0.5, 3.0, 1.5, 0.5)

            # 👇 這裡改成了 False (預設不勾選)
            do_sync = st.checkbox("🔄 同步至 Google 日曆", value=False, help="勾選後才會建立日曆活動")

            n_prog = st.text_area("預定進度")

            if st.button("✅ 新增課程", type="primary"):
                start_p = datetime.combine(d_input, t_input)
                end_p = start_p + timedelta(hours=dur)
                sid = student_map[sel_stu]
                rate = int(df_stu[df_stu['id'] == sid]['default_rate'].values[0])

                g_id = ""
                # 只有當勾選 True 時，才執行建立日曆
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
                st.success("課程已紀錄！" + (" (已同步日曆)" if g_id else ""))
                time.sleep(1)
                st.rerun()

    # 日曆顯示區
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
                    "title": row['name'],
                    "start": row['start_time'], "end": row['end_time'],
                    "backgroundColor": row['color'], "borderColor": row['color']
                })
        except:
            pass
    calendar(events=events, options={"initialView": "dayGridMonth"}, key="cal_v_final")

    # 列表刪除區
    with st.expander("📋 詳細列表 / 刪除", expanded=True):
        if not df_sess.empty:
            df_display = pd.merge(df_sess, df_stu, left_on='student_id', right_on='id').sort_values('start_time',
                                                                                                    ascending=False).head(
                20)
            for _, row in df_display.iterrows():
                sid = int(row['id_x'])
                connected = pd.notna(row['google_event_id']) and str(row['google_event_id']) != ""
                with st.container(border=True):
                    c1, c2 = st.columns([5, 1])
                    c1.markdown(f"**{row['name']}** - {pd.to_datetime(row['start_time']).strftime('%m/%d %H:%M')}")
                    if connected: c1.caption("✅ 已同步日曆")
                    if c2.button("🗑️", key=f"d{sid}"):
                        if connected: delete_google_event(row['google_event_id'])
                        df_sess = df_sess[df_sess['id'].astype(int) != sid]
                        update_data("sessions", df_sess)
                        st.rerun()

# ... Tab 3 & 4 (保持不變) ...
with tab3:
    st.subheader("💰 帳單中心")
    df_inv = get_data("invoices")
    if st.button("⚡ 一鍵結算"):
        # (這裡省略重複代碼，功能與之前相同)
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

with tab4:
    st.subheader("🧑‍🎓 學生管理")
    with st.expander("➕ 新增"):
        with st.form("a_s"):
            n = st.text_input("名");
            r = st.number_input("薪", 500);
            c = st.selectbox("色", ["#FF5733", "#3498DB", "#2ECC71"]);
            sub = st.form_submit_button("存")
            if sub: update_data("students", pd.concat([df_stu, pd.DataFrame(
                [{'id': int(df_stu['id'].max() + 1) if not df_stu.empty else 1, 'name': n, 'default_rate': r,
                  'color': c}])], ignore_index=True)); st.rerun()
    for _, row in df_stu.iterrows():
        with st.container(border=True):
            c1, c2 = st.columns([4, 1]);
            c1.markdown(f"**{row['name']}**");
            if c2.button("刪", key=f"ds{row['id']}"): update_data("students",
                                                                  df_stu[df_stu['id'] != row['id']]); st.rerun()