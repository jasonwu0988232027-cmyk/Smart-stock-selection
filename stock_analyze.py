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
# 忽略 SSL 安全警告 (針對政府網站可能有的憑證問題)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Google Sheet 名稱
SHEET_NAME = "Stock_Predictions_History"
# 設定時區
TZ = pytz.timezone('Asia/Taipei')

def check_execution_permission(force_run=False):
    """
    檢查當前時間是否允許執行並寫入資料
    依據：台股交易時間為週一至週五 09:00-13:30
    """
    if force_run:
        return True, "除錯模式：強制執行資料更新。"
        
    now = datetime.now(TZ)
    weekday = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    current_time = now.time()
    market_close_time = datetime.strptime("13:30", "%H:%M").time()

    # 1. 檢查是否為週末
    if weekday >= 5:
        return False, "今日為週末，台股未開盤，系統不執行資料寫入。"
    
    # 2. 檢查是否已收盤
    if current_time < market_close_time:
        return False, f"台股尚未收盤（13:30），當前時間 {current_time.strftime('%H:%M')}，不執行更新。"
    
    return True, "盤後時段，准許執行資料更新。"

def get_gspread_client():
    """
    初始化 Google Sheets API 用戶端
    支持 Streamlit Secrets 或 本地 JSON 檔案
    """
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    try:
        if "gcp_service_account" in st.secrets:
            # 雲端部署環境
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        elif os.path.exists("credentials.json"):
            # 本地開發環境 (請將你的 JSON 改名為 credentials.json)
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Google API 認證失敗: {e}")
    return None

@st.cache_data(ttl=3600)
def get_full_market_tickers():
    """
    從證交所抓取所有上市股票代碼
    修復說明: 使用 StringIO 避免 Pandas 2.0+ 的 FutureWarning
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False)
        res.encoding = 'big5'
        
        # 使用 StringIO 包裝 HTML 字串
        html_content = StringIO(res.text)
        df = pd.read_html(html_content)[0]
        
        # 整理 DataFrame 格式
        df.columns = df.iloc[0]
        # 篩選代號與名稱列，台股格式通常為 "2330  台積電"
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        
        # 提取 4 碼數字的代號並加上 .TW 後綴
        tickers = []
        for item in df['有價證券代號及名稱']:
            symbol = item.split('  ')[0].strip()
            if len(symbol) == 4 and symbol.isdigit():
                tickers.append(f"{symbol}.TW")
        return tickers
    except Exception as e:
        st.error(f"抓取證交所代碼表失敗: {e}")
        return []

# --- Streamlit 介面設計 ---
st.set_page_config(page_title="台股自動化同步系統", layout="wide")
st.title("📊 台股全市場資金監控 (增量寫入版)")

# 側邊欄：除錯選項
debug_mode = st.sidebar.checkbox("開發者除錯模式 (忽略時間限制)", value=False)

# 權限檢查
can_execute, status_msg = check_execution_permission(force_run=debug_mode)

if not can_execute:
    st.error(f"🚫 系統鎖定：{status_msg}")
else:
    st.success(f"✅ 系統就緒：{status_msg}")
    
    if st.button("🚀 開始掃描並存入 Excel (Google Sheets)"):
        client = get_gspread_client()
        all_tickers = get_full_market_tickers()
        
        if client and all_tickers:
            all_results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            today_str = datetime.now(TZ).strftime('%Y-%m-%d')
            
            # --- 數據抓取邏輯 ---
            # batch_size 設為 50 避免 yfinance 下載過多導致連線中斷
            batch_size = 50
            total_tickers = len(all_tickers)
            
            for i in range(0, total_tickers, batch_size):
                batch = all_tickers[i : i + batch_size]
                status_text.text(f"正在掃描股票: {i}/{total_tickers}...")
                
                # 下載最近 5 天資料確保能抓到最新收盤日
                data = yf.download(batch, period="5d", interval="1d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 處理 MultiIndex 結構 (多支股票下載時 yf 的預設格式)
                        if isinstance(data.columns, pd.MultiIndex):
                            if t not in data.columns.levels[0]: continue
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()
                            
                        if not t_df.empty:
                            row = t_df.iloc[-1]
                            close_price = float(row['Close'])
                            volume = float(row['Volume'])
                            # 計算交易值指標 (億元) = (收盤價 * 成交股數) / 10^8
                            turnover = round((close_price * volume) / 1e8, 4)
                            
                            all_results.append({
                                "日期": today_str,
                                "股票代號": t,
                                "收盤價格": round(close_price, 2),
                                "交易值指標": turnover
                            })
                    except Exception:
                        continue
                
                progress_bar.progress(min((i + batch_size) / total_tickers, 1.0))
            
            # --- 資料儲存邏輯 ---
            if all_results:
                # 排序並取交易值前 100 名
                df_new = pd.DataFrame(all_results).sort_values(by="交易值指標", ascending=False).head(100)
                
                try:
                    status_text.text("正在同步至 Google Sheets...")
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 讀取現有資料進行合併 (去重)
                    existing_data = ws.get_all_records()
                    if existing_data:
                        df_history = pd.DataFrame(existing_data)
                        # 轉換日期格式確保一致，並刪除今日已存在的紀錄 (覆蓋寫入)
                        df_history['日期'] = df_history['日期'].astype(str)
                        df_history = df_history[df_history['日期'] != today_str]
                        df_final = pd.concat([df_history, df_new], ignore_index=True)
                    else:
                        df_final = df_new
                    
                    # 清除並重新寫入 (Google Sheets 常用更新方式)
                    ws.clear()
                    data_to_upload = [df_final.columns.values.tolist()] + df_final.values.tolist()
                    ws.update(data_to_upload)
                    
                    status_text.empty()
                    st.dataframe(df_new)
                    st.success(f"🎊 {today_str} 資料同步成功！目前資料總筆數：{len(df_final)}")
                except Exception as e:
                    st.error(f"Excel 同步失敗: {e}")
        else:
            if not client: st.error("錯誤：無法取得 Google Sheets 授權，請檢查憑證。")
            if not all_tickers: st.error("錯誤：無法從證交所獲取股票名單。")

# --- Requirements ---
# streamlit
# gspread
# pandas
# yfinance
# requests
# lxml
# pytz
# google-auth
