import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"

def get_gspread_client():
    """安全授權邏輯"""
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
    """擷取台股全部股票代碼"""
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        # 根據雙空格篩選正式股票標的
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except:
        return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- UI 與 執行 ---
st.title("🏆 台股全市場資金排行掃描器")

if st.button("🚀 開始全市場掃描並同步前100名"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        st.write(f"正在分析全市場 {len(all_tickers)} 檔股票資料...")
        all_market_data = []
        
        # 分批下載以提升效率 (每批 50 檔)
        batch_size = 50
        progress_bar = st.progress(0)
        
        for i in range(0, len(all_tickers), batch_size):
            batch = all_tickers[i : i + batch_size]
            try:
                # 獲取最新 2 天數據以確保能抓到最後一個交易日
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 判斷 DataFrame 結構並擷取最後一列數據
                        t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 計算交易值指標 (億)
                            val_billion = (price * vol) / 1e8
                            
                            all_market_data.append({
                                "Date": datetime.now().strftime('%Y-%m-%d'),
                                "Ticker": t,
                                "Price": round(price, 2),
                                "Value_Billion": round(val_billion, 2)
                            })
                    except: continue
            except: continue
            
            # 更新進度條
            progress = min((i + batch_size) / len(all_tickers), 1.0)
            progress_bar.progress(progress)
        
        # --- 排序並篩選前 100 名 ---
        if all_market_data:
            df_full = pd.DataFrame(all_market_data)
            # 依據成交值指標降序排列，取前 100 名
            df_top100 = df_full.sort_values(by="Value_Billion", ascending=False).head(100)
            
            st.subheader("📊 本日交易值前 100 名榜單")
            st.dataframe(df_top100, use_container_width=True)
            
            # 轉回 List 格式準備上傳
            upload_list = df_top100.values.tolist()
            
            # 寫入 Google Sheets
            try:
                sh = client.open(SHEET_NAME)
                # 取得第一張工作表
                ws = sh.get_worksheet(0)
                # 寫入數據 (接在現有資料之後)
                ws.append_rows(upload_list)
                st.success(f"✅ 已成功將前 100 名資料同步至 Google Sheets！")
            except Exception as e:
                st.error(f"雲端寫入失敗: {e}")
        else:
            st.warning("未能獲取到任何有效市場數據，請稍後再試。")
