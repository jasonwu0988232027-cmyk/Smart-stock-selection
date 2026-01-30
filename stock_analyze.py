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
    """步驟 1-1：調取股票市場全部的股票代碼"""
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        # 僅擷取 4 位數代碼的普通股
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        return tickers
    except:
        return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- UI 與 執行 ---
st.title("🏆 台股全市場資金排行系統 (修正版)")
st.write("流程：1. 掃描全市場 (約1000+檔) -> 2. 篩選交易值前 100 名 -> 3. 同步至 Excel A-D 欄")

if st.button("🚀 執行全市場深度掃描"):
    all_tickers = get_full_market_tickers()
    client = get_gspread_client()
    
    if client:
        st.info(f"開始執行步驟 1：調取全市場 {len(all_tickers)} 檔股票資料...")
        all_market_results = []
        
        # 使用進度條監控全市場掃描進度
        p_bar = st.progress(0)
        status_text = st.empty()
        
        # 分批下載 (Batch Download) 以處理「全市場」資料
        # 每批次下載 100 檔以平衡速度與穩定性
        batch_size = 100
        for i in range(0, len(all_tickers), batch_size):
            batch = all_tickers[i : i + batch_size]
            status_text.text(f"正在抓取第 {i} 至 {min(i+batch_size, len(all_tickers))} 檔...")
            try:
                # 下載 2 天資料確保獲取最新交易日
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                
                for t in batch:
                    try:
                        # 處理多標的下載的 DataFrame 結構
                        if isinstance(data.columns, pd.MultiIndex):
                            t_df = data[t].dropna()
                        else:
                            t_df = data.dropna()
                            
                        if not t_df.empty:
                            last_row = t_df.iloc[-1]
                            price = float(last_row['Close'])
                            vol = float(last_row['Volume'])
                            # 計算交易值指標 (億)
                            val_billion = (price * vol) / 1e8
                            
                            all_market_results.append({
                                "日期": datetime.now().strftime('%Y-%m-%d'),
                                "股票代號": t,
                                "收盤價格": round(price, 2),
                                "交易值指標": round(val_billion, 4)
                            })
                    except: continue
            except Exception as e:
                st.warning(f"批次 {i} 下載異常，已自動跳過。")
                continue
            
            p_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
        
        status_text.text("步驟 1 完成！正在執行步驟 2：篩選前 100 名...")
        
        # --- 步驟 2：取市場中「交易值指標」前 100 的股票 ---
        if all_market_results:
            df_full = pd.DataFrame(all_market_results)
            # 根據交易值指標降序排列並取前 100
            df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(100)
            
            st.subheader("📊 全市場交易值前 100 名結果")
            st.dataframe(df_top100, use_container_width=True)
            
            # 準備上傳 (嚴格對應 A-D 欄位：日期, 股票代號, 收盤價格, 交易值指標)
            upload_list = df_top100[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
            
            # 寫入 Google Sheets
            try:
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 若為空表則寫入表頭
                if not ws.acell('A1').value:
                    ws.append_row(["日期", "股票代號", "收盤價格", "交易值指標"])
                
                ws.append_rows(upload_list)
                st.success(f"✅ 已成功從全市場篩選出前 100 名，並同步至雲端 A-D 欄！")
            except Exception as e:
                st.error(f"雲端寫入失敗: {e}")
        else:
            st.error("未能成功調取任何市場資料，請檢查網路連線或 API 狀態。")
