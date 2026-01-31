import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
import pytz
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"
TZ = pytz.timezone('Asia/Taipei')

def check_execution_permission():
    """
    檢查當前時間是否允許執行並寫入 Excel
    回傳: (可否執行 bool, 提示訊息 str)
    """
    now = datetime.now(TZ)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    current_time = now.time()
    market_close_time = datetime.strptime("13:30", "%H:%M").time()

    # 1. 檢查是否為週末
    if weekday >= 5:
        return False, "今日為週末，台股未開盤，系統不執行資料寫入。"
    
    # 2. 檢查是否已收盤
    if current_time < market_close_time:
        return False, f"台股尚未收盤（收盤時間 13:30），當前時間 {current_time.strftime('%H:%M')}，不執行更新。"
    
    return True, "盤後時段，准許執行資料更新。"

def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(creds)
    elif os.path.exists("eco-precept-485904-j5-7ef3cdda1b03.json"):
        creds = Credentials.from_service_account_file("eco-precept-485904-j5-7ef3cdda1b03.json", scopes=scopes)
        return gspread.authorize(creds)
    return None

@st.cache_data(ttl=3600)
def get_full_market_tickers():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False)
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        return [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
    except:
        return []

# --- Streamlit UI ---
st.set_page_config(page_title="台股自動化同步系統", layout="wide")
st.title("📊 台股全市場資金監控 (增量寫入版)")

can_execute, status_msg = check_execution_permission()

if not can_execute:
    st.error(f"🚫 系統鎖定：{status_msg}")
else:
    st.success(f"✅ 系統就緒：{status_msg}")
    
    if st.button("🚀 開始掃描並存入 Excel"):
        client = get_gspread_client()
        all_tickers = get_full_market_tickers()
        
        if client and all_tickers:
            all_results = []
            progress_bar = st.progress(0)
            today_str = datetime.now(TZ).strftime('%Y-%m-%d')
            
            # --- 分批抓取數據 ---
            batch_size = 100
            for i in range(0, len(all_tickers), batch_size):
                batch = all_tickers[i : i + batch_size]
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                        if not t_df.empty:
                            row = t_df.iloc[-1]
                            all_results.append({
                                "日期": today_str,
                                "股票代號": t,
                                "收盤價格": round(float(row['Close']), 2),
                                "交易值指標": round((float(row['Close']) * float(row['Volume'])) / 1e8, 4)
                            })
                    except: continue
                progress_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
            
            # --- 資料處理與寫入 ---
            if all_results:
                df_new = pd.DataFrame(all_results).sort_values(by="交易值指標", ascending=False).head(100)
                
                try:
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 獲取舊資料進行合併
                    existing_data = ws.get_all_records()
                    if existing_data:
                        df_history = pd.DataFrame(existing_data)
                        # 核心邏輯：若是同一天，則刪除舊記錄，確保不重疊
                        df_history = df_history[df_history['日期'].astype(str) != today_str]
                        df_final = pd.concat([df_history, df_new], ignore_index=True)
                    else:
                        df_final = df_new
                    
                    # 執行寫入
                    ws.clear()
                    ws.update([df_final.columns.values.tolist()] + df_final.values.tolist())
                    
                    st.dataframe(df_new)
                    st.success(f"🎊 {today_str} 資料更新成功！Excel 總筆數：{len(df_final)}")
                except Exception as e:
                    st.error(f"Excel 同步失敗: {e}")
