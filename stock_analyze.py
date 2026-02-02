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
    過濾條件：僅保留 4 位數代碼之普通股
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        # 篩選代碼與名稱欄位，並過濾出標準 4 位數代碼
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        raw_tickers = df['有價證券代號及名稱'].str.split('  ').str[0].str.strip()
        tickers = [f"{t}.TW" for t in raw_tickers if len(t) == 4]
        return tickers
    except Exception as e:
        st.error(f"Ticker Fetch Error: {e}")
        # 備援機制：返回常用範圍（不建議長期依賴）
        return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- UI 與 執行邏輯 ---
st.title("🏆 台股全市場資金排行系統")
st.markdown("""
**功能說明**：
1. 獲取全市場（約 1000+ 檔）代碼。
2. 分批抓取最新 2 日交易數據。
3. 計算 **收盤價 × 成交量**（交易值）。
4. 篩選全市場前 100 名並上傳。
""")

if st.button("🚀 執行全市場深度掃描"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        st.info(f"掃描開始：共計 {len(all_tickers)} 檔標的")
        all_market_results = []
        p_bar = st.progress(0)
        status_text = st.empty()
        
        batch_size = 50  # 縮小批次大小以提高 yfinance 穩定性
        total_len = len(all_tickers)
        
        for i in range(0, total_len, batch_size):
            batch = all_tickers[i : i + batch_size]
            status_text.text(f"正在分析第 {i} 至 {min(i+batch_size, total_len)} 檔...")
            
            try:
                # 下載最新 2 天資料，threads=True 加速下載
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 處理 DataFrame 結構
                        if t in data.columns.levels[0]:
                            t_df = data[t].dropna()
                        else:
                            continue
                            
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 計算交易值指標 (單位：億台幣)
                            val_billion = (price * vol) / 100000000
                            
                            all_market_results.append({
                                "日期": datetime.now().strftime('%Y-%m-%d'),
                                "股票代號": t,
                                "收盤價格": round(price, 2),
                                "交易值指標": round(val_billion, 4)
                            })
                    except:
                        continue
            except Exception as e:
                st.warning(f"批次 {i} 發生跳轉：{e}")
                continue
            
            p_bar.progress(min((i + batch_size) / total_len, 1.0))
        
        # --- 數據處理與上傳 ---
        if all_market_results:
            df_full = pd.DataFrame(all_market_results)
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            st.subheader("📊 當前市場成交金額前 100 名")
            st.dataframe(df_top100, use_container_width=True)
            
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 初始化表頭
                if not ws.acell('A1').value:
                    ws.append_row(["日期", "股票代號", "收盤價格", "交易值指標"])
                
                # 批次寫入資料
                upload_data = df_top100.values.tolist()
                ws.append_rows(upload_data)
                st.success("✅ 數據已成功同步至 Google Sheets A-D 欄位")
            except Exception as e:
                st.error(f"Google Sheets 寫入異常: {e}")
        else:
            st.error("未能成功調取市場資料，請檢查 API 限制或網路狀態。")
