import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import urllib3
import gspread
import time
import random
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股資金選股同步系統", layout="wide")

# Google Sheets 配置 (使用您提供的金鑰檔案)
SHEET_NAME = "Stock_Predictions_History" 
CREDENTIALS_JSON = "eco-precept-485904-j5-7ef3cdda1b03.json" 

def get_gspread_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"授權失敗: {e}")
        return None

def save_to_sheets(new_data, headers):
    client = get_gspread_client()
    if not client: return False
    try:
        sh = client.open(SHEET_NAME)
        # 建立一個新的工作表以日期命名
        ws_name = f"Scan_{datetime.now().strftime('%Y%m%d')}"
        try:
            target_ws = sh.add_worksheet(title=ws_name, rows=200, cols=10)
        except:
            target_ws = sh.worksheet(ws_name)
        
        target_ws.clear()
        target_ws.append_row(headers)
        target_ws.append_rows(new_data)
        return True
    except Exception as e:
        st.error(f"雲端寫入失敗: {e}")
        return False

# --- 1. 您的選股方法 (全面獲取股票代碼) ---
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
        if len(tickers) > 800: return tickers
    except: pass
    return [f"{i:04d}.TW" for i in range(1101, 1201)] # 備用名單

# --- UI 介面 ---
st.title("🚀 台股資金排行掃描器")

if st.button("執行全市場掃描並同步至 Google Sheets"):
    tickers = get_full_market_tickers()
    st.write(f"已獲取 {len(tickers)} 檔股票代碼，開始下載數據...")
    
    res_rank = []
    upload_data = []
    p_bar = st.progress(0)
    
    # 執行掃描 (示範前 100 檔以符合系統效能)
    scan_list = tickers[:100]
    for i, t in enumerate(scan_list):
        try:
            data = yf.download(t, period="2d", progress=False)
            if not data.empty:
                last = data.iloc[-1]
                price = float(last['Close'])
                volume = float(last['Volume'])
                val = (price * volume) / 1e8
                
                res_rank.append({"代號": t, "收盤價": price, "成交值(億)": val})
                upload_data.append([datetime.now().strftime('%Y-%m-%d'), t, round(price, 2), round(val, 4)])
        except: continue
        p_bar.progress((i + 1) / len(scan_list))

    if res_rank:
        df_result = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False)
        st.dataframe(df_result)
        
        headers = ["掃描日期", "股票代碼", "收盤價", "成交值(億)"]
        if save_to_sheets(upload_data, headers):
            st.success("✅ 數據已成功同步至 Google Sheets!")
