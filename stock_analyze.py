import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
import io
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"

def get_gspread_client():
    """安全性授權邏輯，支援 Streamlit Secrets 與本地 JSON"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    if "gcp_service_account" in st.secrets:
        try:
            creds_info = st.secrets["gcp_service_account"]
            creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            st.error(f"Cloud Auth Error: {e}")
            return None
    elif os.path.exists("eco-precept-485904-j5-7ef3cdda1b03.json"):
        creds = Credentials.from_service_account_file("eco-precept-485904-j5-7ef3cdda1b03.json", scopes=scopes)
        return gspread.authorize(creds)
    return None

@st.cache_data(ttl=86400)
def get_full_market_tickers():
    """
    從證交所調取所有上市股票代碼
    修正點：使用 io.StringIO 解決 pandas 棄用警告
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        # 修正 Future Warning: 使用 io.StringIO 包裝
        html_data = io.StringIO(res.text)
        df = pd.read_html(html_data)[0]
        
        df.columns = df.iloc[0]
        # 篩選標準：包含兩個全形空格的通常是股票名稱項目
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        raw_tickers = df['有價證券代號及名稱'].str.split('  ').str[0].str.strip()
        # 僅取 4 位數代碼
        tickers = [f"{t}.TW" for t in raw_tickers if len(t) == 4]
        return tickers
    except Exception as e:
        st.error(f"Ticker Fetch Error: {e}")
        return [f"{i:04d}.TW" for i in range(1101, 1200)] # 縮小備援範圍避免超時

# --- UI 與 執行邏輯 ---
st.title("🏆 台股全市場資金排行系統 (v2.1)")

if st.button("🚀 執行全市場深度掃描"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        st.info(f"掃描開始：共計 {len(all_tickers)} 檔標的")
        all_market_results = []
        p_bar = st.progress(0)
        status_text = st.empty()
        
        batch_size = 50 
        total_len = len(all_tickers)
        
        for i in range(0, total_len, batch_size):
            batch = all_tickers[i : i + batch_size]
            status_text.text(f"正在分析第 {i} 至 {min(i+batch_size, total_len)} 檔...")
            
            try:
                # 下載資料
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 檢查標的是否存在於回傳結果中
                        if isinstance(data.columns, pd.MultiIndex):
                            if t not in data.columns.levels[0]: continue
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()
                            
                        if not t_df.empty and len(t_df) >= 1:
                            last_row = t_df.iloc[-1]
                            # 確保 Close 與 Volume 存在
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            val_billion = (price * vol) / 1e8
                            
                            all_market_results.append({
                                "日期": datetime.now().strftime('%Y-%m-%d'),
                                "股票代號": t,
                                "收盤價格": round(price, 2),
                                "交易值指標": round(val_billion, 4)
                            })
                    except: continue
            except Exception as e:
                continue
            
            p_bar.progress(min((i + batch_size) / total_len, 1.0))
        
        # --- 數據排行與上傳 ---
        if all_market_results:
            df_full = pd.DataFrame(all_market_results)
            # 排序：交易值由高到低
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            st.subheader("📊 當前市場成交金額前 100 名")
            st.dataframe(df_top100, use_container_width=True)
            
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 自動檢查與寫入表頭
                header = ["日期", "股票代號", "收盤價格", "交易值指標"]
                if not ws.acell('A1').value:
                    ws.append_row(header)
                
                # 上傳資料
                ws.append_rows(df_top100[header].values.tolist())
                st.success(f"✅ 已成功分析並同步 {len(df_top100)} 筆數據至雲端")
            except Exception as e:
                st.error(f"Google Sheets 寫入異常: {e}")
        else:
            st.error("掃描完成但未獲取有效數據。")
