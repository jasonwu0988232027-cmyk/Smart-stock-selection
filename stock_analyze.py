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
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

def get_gspread_client():
    """
    驗證 Google 授權
    """
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        # 1. 檢查 Streamlit Cloud Secrets
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        # 2. 檢查本地 JSON 檔案
        elif os.path.exists("credentials.json"):
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            return gspread.authorize(creds)
        else:
            st.error("❌ 找不到認證資料：請在 Secrets 中設定 'gcp_service_account' 或上傳 'credentials.json'")
            return None
    except Exception as e:
        st.error(f"❌ Google 認證初始化失敗: {e}")
        return None

@st.cache_data(ttl=86400)
def get_full_market_tickers():
    """
    抓取台股代碼 (包含被阻擋時的自動備援機制)
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    try:
        res = requests.get(url, timeout=15, verify=False, headers=headers)
        res.encoding = 'big5'
        
        if res.status_code == 200:
            df = pd.read_html(StringIO(res.text))[0]
            df.columns = df.iloc[0]
            df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
            tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] 
                       if len(t.split('  ')[0].strip()) == 4]
            if tickers:
                return tickers
    except Exception as e:
        st.warning(f"⚠️ 證交所連線受阻，改用備援名單。")

    # --- 備援名單 (當爬蟲失效時，確保程式至少能跑這幾檔) ---
    return ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2303.TW", "2881.TW", "2882.TW", "2603.TW"]

# --- 執行介面 ---
st.title("🚀 台股資金流向監控系統")

if st.button("開始掃描並存入雲端"):
    client = get_gspread_client()
    all_tickers = get_full_market_tickers()
    
    # 這裡加入嚴格檢查
    if client is None:
        st.stop() # 停止執行
        
    if not all_tickers:
        st.error("❌ 無法獲取股票清單，請稍後再試。")
        st.stop()

    # --- 後續 yfinance 掃描邏輯 ---
    st.info(f"成功連線！準備掃描 {len(all_tickers)} 檔股票...")
    # (此處接續之前的 yf.download 邏輯...)
