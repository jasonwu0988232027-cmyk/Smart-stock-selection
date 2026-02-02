import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
from io import StringIO
from datetime import datetime
import pytz
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

def get_gspread_client():
    """安全授權邏輯"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        elif os.path.exists("credentials.json"):
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
            return gspread.authorize(creds)
    except Exception as e:
        st.error(f"授權失敗: {e}")
    return None

@st.cache_data(ttl=86400)
def get_full_market_tickers():
    """調取台灣上市股票代碼 (含備援邏輯)"""
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        res = requests.get(url, timeout=10, verify=False, headers=headers)
        res.encoding = 'big5'
        # 使用 StringIO 修復 FutureWarning
        df = pd.read_html(StringIO(res.text))[0]
        df.columns = df.iloc[0]
        # 篩選四位數普通股
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] 
                   if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except Exception as e:
        st.warning(f"證交所連線失敗，啟動備援機制。錯誤: {e}")
        # 至少返回權值股名單確保系統不崩潰
        return ["2330.TW", "2317.TW", "2454.TW", "2303.TW", "2881.TW"]

# --- UI 與 執行 ---
st.set_page_config(page_title="台股全市場監控", layout="wide")
st.title("🏆 台股全市場資金排行系統")

if st.button("🚀 執行全市場深度掃描"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client and all_tickers:
        st.info(f"偵測到 {len(all_tickers)} 檔股票，開始分批下載行情...")
        all_market_results = []
        p_bar = st.progress(0)
        status_text = st.empty()
        
        # 縮小批次以提升穩定性，增加 threads 以提升速度
        batch_size = 50 
        today_str = datetime.now(TAIWAN_TZ).strftime('%Y-%m-%d')
        
        for i in range(0, len(all_tickers), batch_size):
            batch = all_tickers[i : i + batch_size]
            status_text.text(f"正在分析: {i} ~ {min(i+batch_size, len(all_tickers))} 檔...")
            
            try:
                # 增加 period 寬度至 5d，確保跨週末時能抓到資料
                data = yf.download(batch, period="5d", interval="1d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 檢查股票是否存在於下載結果中
                        if isinstance(data.columns, pd.MultiIndex):
                            if t not in data.columns.levels[0]: continue
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()
                            
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 過濾掉無成交量的資料 (如停牌)
                            if vol <= 0: continue 
                            
                            val_billion = (price * vol) / 1e8
                            all_market_results.append({
                                "日期": today_str,
                                "股票代號": t,
                                "收盤價格": round(price, 2),
                                "交易值指標": round(val_billion, 4)
                            })
                    except: continue
            except Exception as e:
                st.warning(f"批次下載中斷: {e}")
            
            p_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
        
        # --- 資料處理與寫入 ---
        if all_market_results:
            df_full = pd.DataFrame(all_market_results)
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            st.subheader(f"📊 {today_str} 交易值前 100 名結果")
            st.dataframe(df_top100, use_container_width=True)
            
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 更新機制：獲取表頭，若無則寫入
                if ws.row_count == 0 or not ws.acell('A1').value:
                    ws.update('A1:D1', [["日期", "股票代號", "收盤價格", "交易值指標"]])
                
                # 準備上傳資料
                upload_data = df_top100[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
                ws.append_rows(upload_data)
                
                status_text.empty()
                st.success(f"✅ 已成功篩選前 100 名並同步至 Google Sheets！")
            except Exception as e:
                st.error(f"Google Sheets 寫入異常: {e}")
        else:
            st.error("❌ 掃描完成但數據集為空。原因可能是：Yahoo Finance 阻擋或非交易時段無數據。")
    else:
        st.error("無法初始化 API 客戶端或獲取股票清單。")
