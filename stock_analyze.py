import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import time
import random
import requests
import urllib3
import json
import os
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股多因子決策系統 (雲端同步版)", layout="wide")

# Google Sheets 配置
SHEET_NAME = "Stock_Predictions_History" 
# 讀取上傳的金鑰檔案名稱
CREDENTIALS_JSON = "eco-precept-485904-j5-7ef3cdda1b03.json" 

# --- Google Sheets 授權邏輯 ---
def get_gspread_client():
    """
    建立 Google Sheets API 授權客戶端
    """
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # 優先從 Streamlit Secrets 讀取，否則讀取本地 JSON
    if "gcp_service_account" in st.secrets:
        try:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            st.error(f"Secrets Authorization Failed: {e}")
            return None
    elif os.path.exists(CREDENTIALS_JSON):
        try:
            creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            st.error(f"Local JSON Authorization Failed: {e}")
            return None
    return None

def save_to_sheets(new_data, sheet_index=0):
    """
    將資料寫入 Google Sheets
    """
    client = get_gspread_client()
    if client is None:
        st.error("⚠️ Cannot connect to Google Sheets. Check credentials.")
        return False
        
    try:
        sh = client.open(SHEET_NAME)
        all_ws = sh.worksheets()
        if len(all_ws) > sheet_index:
            target_ws = all_ws[sheet_index]
        else:
            target_ws = sh.add_worksheet(title=f"Market_Scan_{datetime.now().strftime('%Y%m%d')}", rows=1000, cols=10)
        
        # 檢查並寫入表頭 (針對本次全市場掃描格式)
        if not target_ws.acell('A1').value:
            headers = ["掃描日期", "股票代號", "收盤價", "成交值(億)"]
            target_ws.append_row(headers)
             
        target_ws.append_rows(new_data)
        return True
    except Exception as e:
        st.error(f"❌ Cloud Sync Failed: {str(e)}")
        return False

# --- 股票分析邏輯 ---
@st.cache_data(ttl=86400)
def get_full_market_tickers():
    """
    獲取台股上市股票代號
    """
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
        return [f"{i:04d}.TW" for i in range(1101, 1200)] # 失敗時的回退機制

# --- UI 介面 ---
st.title("🏆 全市場資金指標排行與雲端同步")

if st.button("🚀 執行深度掃描並同步至雲端"):
    all_list = get_full_market_tickers()
    res_rank = []
    upload_data = [] # 準備上傳至 Sheets 的格式
    
    p_bar = st.progress(0, text="正在分析全市場成交值...")
    
    # 為了演示與速度，範例僅抓取前 50 檔，正式使用可移除切片 [:50]
    scan_list = all_list[:50] 
    batch_size = 10
    
    for i in range(0, len(scan_list), batch_size):
        batch = scan_list[i : i + batch_size]
        try:
            # 批量下載數據
            data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            for t in batch:
                try:
                    t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                    if not t_df.empty:
                        last = t_df.iloc[-1]
                        price = float(last['Close'])
                        val = (price * float(last['Volume'])) / 1e8
                        
                        res_rank.append({"股票代號": t, "收盤價": price, "成交值(億)": val})
                        # 構建 Google Sheets 列資料
                        upload_data.append([current_date, t, price, round(val, 2)])
                except: continue
        except: pass
        p_bar.progress(min((i + batch_size) / len(scan_list), 1.0))
        time.sleep(random.uniform(0.1, 0.5))

    if res_rank:
        df_result = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False)
        st.subheader("本日掃描結果 (Top 50)")
        st.dataframe(df_result, use_container_width=True)
        
        # 執行雲端同步
        st.info("正在同步資料至 Google Sheets...")
        if save_to_sheets(upload_data):
            st.success(f"✅ 已成功將 {len(upload_data)} 筆資料同步至試算表: {SHEET_NAME}")
