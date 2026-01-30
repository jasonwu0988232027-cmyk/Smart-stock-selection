import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"

def get_gspread_client():
    """
    安全授權邏輯：
    1. 優先尋找 Streamlit Secrets (雲端環境)
    2. 若無，則尋找本地 JSON (本地開發環境)
    """
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # 方案 A: 雲端運行時使用 Secrets
    if "gcp_service_account" in st.secrets:
        try:
            creds_info = st.secrets["gcp_service_account"]
            # 必須處理 private_key 中的換行符號
            creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            st.error(f"Cloud Auth Error: {e}")
            return None
            
    # 方案 B: 本地運行時使用檔案 (記得將檔案加入 .gitignore)
    elif os.path.exists("eco-precept-485904-j5-7ef3cdda1b03.json"):
        creds = Credentials.from_service_account_file("eco-precept-485904-j5-7ef3cdda1b03.json", scopes=scopes)
        return gspread.authorize(creds)
        
    return None

# --- 選股邏輯 (您指定的方法) ---
@st.cache_data(ttl=86400)
def get_full_market_tickers():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except:
        return [f"{i:04d}.TW" for i in range(1101, 1201)]

# --- UI 與 執行 ---
st.title("🚀 台股資金選股同步系統 (安全版)")

if st.button("開始掃描並同步至雲端"):
    tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        # 下載數據邏輯 (範例取前 50 檔)
        scan_list = tickers[:50]
        upload_data = []
        for t in scan_list:
            try:
                stock = yf.Ticker(t)
                hist = stock.history(period="1d")
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    vol = hist['Volume'].iloc[-1]
                    val = (price * vol) / 1e8
                    upload_data.append([datetime.now().strftime('%Y-%m-%d'), t, round(price, 2), round(val, 2)])
            except: continue
        
        # 寫入 Google Sheets
        try:
            sh = client.open(SHEET_NAME)
            ws = sh.get_worksheet(0)
            ws.append_rows(upload_data)
            st.success("✅ 資料已安全同步至 Google Sheets")
        except Exception as e:
            st.error(f"同步失敗: {e}")
