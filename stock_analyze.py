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
TZ = pytz.timezone('Asia/Taipei')

def get_gspread_client():
    """Google Sheets 授權"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            return gspread.authorize(creds)
        elif os.path.exists("eco-precept-485904-j5-7ef3cdda1b03.json"):
            creds = Credentials.from_service_account_file("eco-precept-485904-j5-7ef3cdda1b03.json", scopes=scopes)
            return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Google Auth 失敗: {e}")
    return None

def get_full_market_tickers():
    """步驟 1：獲取全市場代碼 (修正 StringIO)"""
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        res = requests.get(url, timeout=15, verify=False, headers=headers)
        res.encoding = 'big5'
        
        # 使用 StringIO 修復 FutureWarning
        html_data = StringIO(res.text)
        df = pd.read_html(html_content := html_data)[0]
        
        df.columns = df.iloc[0]
        # 證交所代號與名稱之間通常有兩個空格
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        
        tickers = []
        for t in df['有價證券代號及名稱']:
            symbol = t.split('  ')[0].strip()
            if len(symbol) == 4 and symbol.isdigit():
                tickers.append(f"{symbol}.TW")
        return tickers
    except Exception as e:
        st.error(f"無法從證交所獲取清單: {e}")
        return []

# --- UI ---
st.title("🏆 台股全市場資金排行系統")

if st.button("🚀 執行全市場深度掃描"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if not all_tickers:
        st.error("股票清單為空，請檢查網路連線或證交所 URL。")
        st.stop()
        
    if client:
        st.info(f"已獲取 {len(all_tickers)} 檔股票，開始分批抓取市場數據...")
        all_market_results = []
        p_bar = st.progress(0)
        status_text = st.empty()
        
        # 縮小批次以增加穩定性
        batch_size = 30 
        today_str = datetime.now(TZ).strftime('%Y-%m-%d')
        
        for i in range(0, len(all_tickers), batch_size):
            batch = all_tickers[i : i + batch_size]
            status_text.text(f"正在抓取: {i}/{len(all_tickers)} 檔...")
            
            try:
                # 下載數據 (增加 period 至 5d 確保資料不為空)
                data = yf.download(
                    batch, 
                    period="5d", 
                    interval="1d", 
                    group_by='ticker', 
                    threads=True, 
                    progress=False,
                    auto_adjust=True
                )
                
                for t in batch:
                    try:
                        # 嚴謹判斷資料結構
                        if isinstance(data.columns, pd.MultiIndex):
                            if t not in data.columns.levels[0]: continue
                            t_df = data[t].dropna(subset=['Close', 'Volume'])
                        else:
                            t_df = data.dropna(subset=['Close', 'Volume'])
                            
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            
                            if vol > 0: # 排除無成交量股票
                                val_billion = (price * vol) / 1e8
                                all_market_results.append({
                                    "日期": today_str,
                                    "股票代號": t,
                                    "收盤價格": round(price, 2),
                                    "交易值指標": round(val_billion, 4)
                                })
                    except: continue
            except Exception as e:
                st.warning(f"批次下載異常 ({i}): {e}")
                continue
            
            p_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
        
        # --- 最終檢查與寫入 ---
        if all_market_results:
            df_full = pd.DataFrame(all_market_results)
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            st.subheader(f"📊 {today_str} 交易值前 100 名")
            st.dataframe(df_top100, use_container_width=True)
            
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 若工作表完全沒內容，寫入表頭
                if ws.row_count == 0 or not ws.acell('A1').value:
                    ws.update('A1:D1', [["日期", "股票代號", "收盤價格", "交易值指標"]])
                
                # 寫入前 100 名資料
                upload_data = df_top100.values.tolist()
                ws.append_rows(upload_data)
                
                status_text.empty()
                st.success("✅ 資料同步成功！")
            except Exception as e:
                st.error(f"Google Sheets 寫入失敗: {e}")
        else:
            # 這是你要解決的核心報錯點，現在已加入更多過濾檢查
            st.error("未能成功調取任何市場資料。請檢查：1. 是否為非交易日 2. yfinance API 流量是否受限。")
