import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
import pytz
from io import StringIO
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"
TZ = pytz.timezone('Asia/Taipei')

def check_execution_permission(force_run=False):
    """
    檢查執行權限邏輯
    依據：台股交易日 13:30 後准許寫入
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
        return False, f"台股尚未收盤（13:30），目前時間 {current_time.strftime('%H:%M')}。"
    
    return True, "盤後時段，准許執行。"

def get_gspread_client():
    """
    Google Sheets API 認證
    """
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        # 優先讀取 Streamlit Secrets (雲端環境)
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        # 次之讀取本地 JSON (開發環境)
        elif os.path.exists("credentials.json"):
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            return gspread.authorize(creds)
    except Exception as e:
        st.error(f"⚠️ Google 認證失敗: {e}")
    return None

@st.cache_data(ttl=3600)
def get_full_market_tickers():
    """
    從證交所抓取代碼 (修復 StringIO 與 Header 阻擋問題)
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        # 使用 verify=False 並加上 headers 確保連線
        res = requests.get(url, headers=headers, timeout=15, verify=False)
        res.encoding = 'big5'
        
        if res.status_code != 200:
            st.error(f"證交所連線失敗，狀態碼: {res.status_code}")
            return []

        # 修復 FutureWarning: 使用 StringIO 包裝
        html_data = StringIO(res.text)
        dfs = pd.read_html(html_data)
        
        if not dfs:
            return []
            
        df = dfs[0]
        df.columns = df.iloc[0] # 設定標題列
        
        # 關鍵過濾：尋找包含 "  " (兩個空格) 的行，這通常是代碼與名稱的分隔
        target_col = '有價證券代號及名稱'
        if target_col not in df.columns:
            st.error("無法定位表格欄位，請檢查證交所頁面結構。")
            return []

        df = df[df[target_col].str.contains("  ", na=False)]
        
        tickers = []
        for val in df[target_col]:
            symbol = val.split('  ')[0].strip()
            # 僅保留 4 位數純數字股票 (排除權證、ETF)
            if len(symbol) == 4 and symbol.isdigit():
                tickers.append(f"{symbol}.TW")
        
        return tickers

    except Exception as e:
        st.error(f"抓取證交所清單時發生錯誤: {e}")
        return []

# --- Streamlit UI ---
st.set_page_config(page_title="台股自動化同步系統", layout="wide")
st.title("📊 台股全市場資金監控系統")

# 側邊欄配置
with st.sidebar:
    st.header("系統控制")
    debug_mode = st.checkbox("開發者除錯模式 (忽略時間限制)", value=False)
    st.info("本系統會抓取全台股資料，計算交易值並儲存前 100 名至 Google Sheets。")

can_execute, status_msg = check_execution_permission(force_run=debug_mode)

if not can_execute:
    st.error(f"🚫 系統未就緒：{status_msg}")
else:
    st.success(f"✅ 系統就緒：{status_msg}")
    
    if st.button("🚀 開始掃描並存入 Google Sheets"):
        client = get_gspread_client()
        all_tickers = get_full_market_tickers()
        
        if not client:
            st.error("找不到 API 憑證 (credentials.json)，請檢查部署環境。")
            st.stop()

        if all_tickers:
            st.write(f"已獲取 {len(all_tickers)} 檔股票，開始分批下載數據...")
            all_results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            today_str = datetime.now(TZ).strftime('%Y-%m-%d')
            
            # 分批下載 (避免 yfinance 被鎖或記憶體溢出)
            batch_size = 50 
            total = len(all_tickers)
            
            for i in range(0, total, batch_size):
                batch = all_tickers[i : i + batch_size]
                status_text.text(f"掃描進度: {i}/{total} (Batch: {len(batch)})")
                
                # 下載最近 5 天資料確保跨週末數據完整
                data = yf.download(batch, period="5d", interval="1d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 處理 yfinance 多股下載的 MultiIndex 結構
                        if isinstance(data.columns, pd.MultiIndex):
                            if t not in data.columns.levels[0]: continue
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()

                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            close = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 計算交易值指標 (億元)
                            turnover = round((close * vol) / 1e8, 4)
                            
                            all_results.append({
                                "日期": today_str,
                                "股票代號": t,
                                "收盤價格": round(close, 2),
                                "交易值指標": turnover
                            })
                    except:
                        continue
                progress_bar.progress(min((i + batch_size) / total, 1.0))
            
            # --- 寫入 Google Sheets ---
            if all_results:
                df_new = pd.DataFrame(all_results).sort_values(by="交易值指標", ascending=False).head(100)
                
                try:
                    status_text.text("正在更新 Google Sheets...")
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 讀取舊資料並去重 (保留歷史，更新今日)
                    existing_data = ws.get_all_records()
                    if existing_data:
                        df_history = pd.DataFrame(existing_data)
                        df_history['日期'] = df_history['日期'].astype(str)
                        df_history = df_history[df_history['日期'] != today_str]
                        df_final = pd.concat([df_history, df_new], ignore_index=True)
                    else:
                        df_final = df_new
                    
                    # 覆蓋寫入
                    ws.clear()
                    output_list = [df_final.columns.values.tolist()] + df_final.values.tolist()
                    ws.update(output_list)
                    
                    status_text.empty()
                    st.success(f"🎊 {today_str} 資料更新成功！Excel 總筆數：{len(df_final)}")
                    st.dataframe(df_new)
                except Exception as e:
                    st.error(f"Google Sheets 寫入失敗: {e}")
            else:
                st.warning("掃描完成，但未抓取到有效數據，請檢查 yfinance 連線。")
        else:
            st.error("股票名單為空，請檢查網路連線或證交所 URL 是否有效。")

# --- requirements.txt ---
# streamlit
# gspread
# pandas
# yfinance
# requests
# lxml
# pytz
# google-auth
