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

def check_execution_permission(force_run=False):
    """
    檢查執行權限，提供 force_run 選項用於除錯
    """
    if force_run:
        return True, "除錯模式：強制執行資料更新。"
        
    now = datetime.now(TZ)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    current_time = now.time()
    market_close_time = datetime.strptime("13:30", "%H:%M").time()

    if weekday >= 5:
        return False, "今日為週末，台股未開盤。"
    if current_time < market_close_time:
        return False, f"台股尚未收盤（13:30），當前時間 {current_time.strftime('%H:%M')}。"
    
    return True, "盤後時段，准許執行。"

def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        elif os.path.exists("credentials.json"): # 建議統一命名
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            return gspread.authorize(creds)
    except Exception as e:
        st.error(f"認證初始化失敗: {e}")
    return None

@st.cache_data(ttl=3600)
def get_full_market_tickers():
    """
    抓取台股上市公司代碼
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False)
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        # 過濾股票（四碼且包含空格分割）
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] 
                   if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except Exception as e:
        st.error(f"獲取代碼表失敗: {e}")
        return []

# --- Streamlit UI ---
st.set_page_config(page_title="台股自動化同步系統", layout="wide")
st.title("📊 台股全市場資金監控")

# 增加除錯開關
debug_mode = st.sidebar.checkbox("開發者除錯模式 (忽略時間限制)")

can_execute, status_msg = check_execution_permission(force_run=debug_mode)

if not can_execute:
    st.error(f"🚫 系統鎖定：{status_msg}")
else:
    st.success(f"✅ 系統就緒：{status_msg}")
    
    if st.button("🚀 開始掃描並存入 Excel"):
        client = get_gspread_client()
        all_tickers = get_full_market_tickers()
        
        if not client:
            st.error("找不到有效的 Google Service Account 憑證")
            st.stop()

        if all_tickers:
            all_results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            today_str = datetime.now(TZ).strftime('%Y-%m-%d')
            
            # 分批處理
            batch_size = 50 # 縮小批次以提升穩定性
            total = len(all_tickers)
            
            for i in range(0, total, batch_size):
                batch = all_tickers[i : i + batch_size]
                status_text.text(f"正在抓取第 {i} 至 {i+batch_size} 檔股票...")
                
                # 下載數據，調整為 5d 確保跨週末有資料
                data = yf.download(batch, period="5d", interval="1d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 處理 MultiIndex 結構
                        if isinstance(data.columns, pd.MultiIndex):
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()

                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            close_price = float(last_row['Close'])
                            volume = float(last_row['Volume'])
                            all_results.append({
                                "日期": today_str,
                                "股票代號": t,
                                "收盤價格": round(close_price, 2),
                                "交易值指標": round((close_price * volume) / 1e8, 4)
                            })
                    except:
                        continue
                progress_bar.progress(min((i + batch_size) / total, 1.0))
            
            if all_results:
                df_new = pd.DataFrame(all_results).sort_values(by="交易值指標", ascending=False).head(100)
                
                try:
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 獲取舊資料
                    existing_data = ws.get_all_records()
                    if existing_data:
                        df_history = pd.DataFrame(existing_data)
                        # 排除同日資料
                        df_history = df_history[df_history['日期'].astype(str) != today_str]
                        df_final = pd.concat([df_history, df_new], ignore_index=True)
                    else:
                        df_final = df_new
                    
                    # 執行寫入
                    ws.clear()
                    # 轉換為 List of Lists 格式
                    output_data = [df_final.columns.values.tolist()] + df_final.values.tolist()
                    ws.update(output_data)
                    
                    st.dataframe(df_new)
                    st.success(f"🎊 {today_str} 資料更新成功！Excel 總筆數：{len(df_final)}")
                except Exception as e:
                    st.error(f"Excel 同步失敗: {e}")
