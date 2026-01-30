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
    """安全授權邏輯"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    # 優先從 Streamlit Secrets 讀取 (雲端環境)
    if "gcp_service_account" in st.secrets:
        try:
            creds_info = st.secrets["gcp_service_account"]
            creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
            return gspread.authorize(creds)
        except Exception as e:
            st.error(f"Cloud Auth Error: {e}")
            return None
    # 本地測試備案
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
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except:
        return [f"{i:04d}.TW" for i in range(1101, 2000)]

# --- UI 與 執行 ---
st.title("🏆 台股全市場交易值排行系統")
st.write("目標：掃描全市場標的，篩選交易值前 100 名並同步至 Google Sheets A-D 欄。")

if st.button("🚀 執行全市場掃描並同步"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        st.info(f"正在分析全市場 {len(all_tickers)} 檔股票數據，請稍候...")
        all_market_data = []
        
        # 使用分批下載提高效率
        batch_size = 50
        p_bar = st.progress(0)
        
        for i in range(0, len(all_tickers), batch_size):
            batch = all_tickers[i : i + batch_size]
            try:
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 計算交易值指標 (億)
                            val_billion = (price * vol) / 1e8
                            
                            all_market_data.append({
                                "日期": datetime.now().strftime('%Y-%m-%d'),
                                "股票代號": t,
                                "收盤價格": round(price, 2),
                                "交易值指標": round(val_billion, 2)
                            })
                    except: continue
            except: continue
            p_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
        
        if all_market_data:
            # 依交易值指標降序排列，取前 100
            df_full = pd.DataFrame(all_market_data)
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            # 顯示結果
            st.subheader("📊 本日交易值前 100 名 (預覽)")
            st.dataframe(df_top100, use_container_width=True)
            
            # 準備上傳資料 (轉換為符合 A, B, C, D 順序的 List)
            # 順序：日期, 股票代號, 收盤價格, 交易值指標
            upload_list = df_top100[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
            headers = ["日期", "股票代號", "收盤價格", "交易值指標"]
            
            # 寫入 Google Sheets
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 若工作表為空，先寫入表頭
                if not ws.acell('A1').value:
                    ws.append_row(headers)
                
                ws.append_rows(upload_list)
                st.success(f"✅ 已成功將前 100 名資料寫入 Google Sheets A-D 欄！")
            except Exception as e:
                st.error(f"雲端寫入失敗: {e}")
        else:
            st.error("掃描失敗，未取得任何數據。")
