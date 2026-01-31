import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
import time
from datetime import datetime, timedelta
import pytz
from google.oauth2.service_account import Credentials

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
SHEET_NAME = "Stock_Predictions_History"
TZ = pytz.timezone('Asia/Taipei')

def is_market_closed():
    """判斷台股是否已收盤 (13:30) 且為工作日"""
    now = datetime.now(TZ)
    weekday = now.weekday()  # 0-4 為週一至週五
    close_time = now.replace(hour=13, minute=30, second=0, microsecond=0)
    
    if weekday > 4:
        return True, "今日為週末，顯示最後交易日數據。"
    if now < close_time:
        return False, f"台股尚未收盤。請於 13:30 之後再執行，當前時間: {now.strftime('%H:%M:%S')}"
    return True, "盤後時段，開始抓取今日數據。"

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

@st.cache_data(ttl=3600)
def get_full_market_tickers():
    """步驟 1-1：調取股票市場全部的股票代碼"""
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
        return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- UI 與 執行 ---
st.title("🏆 台股全市場資金排行系統 (增量更新版)")

market_status, message = is_market_closed()

if not market_status:
    st.warning(f"⚠️ 暫停執行：{message}")
else:
    st.success(f"✅ 狀態：{message}")
    if st.button("🚀 執行全市場深度掃描與更新"):
        all_tickers = get_full_market_tickers()
        client = get_gspread_client()
        
        if client:
            st.info(f"開始掃描全市場 {len(all_tickers)} 檔股票...")
            all_market_results = []
            p_bar = st.progress(0)
            status_text = st.empty()
            
            today_str = datetime.now(TZ).strftime('%Y-%m-%d')
            batch_size = 100
            
            for i in range(0, len(all_tickers), batch_size):
                batch = all_tickers[i : i + batch_size]
                status_text.text(f"正在抓取第 {i} 至 {min(i+batch_size, len(all_tickers))} 檔...")
                try:
                    data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                    for t in batch:
                        try:
                            t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                            if not t_df.empty:
                                last_row = t_df.iloc[-1]
                                price = float(last_row['Close'])
                                vol = float(last_row['Volume'])
                                val_billion = (price * vol) / 1e8
                                all_market_results.append({
                                    "日期": today_str,
                                    "股票代號": t,
                                    "收盤價格": round(price, 2),
                                    "交易值指標": round(val_billion, 4)
                                })
                        except: continue
                except: continue
                p_bar.progress(min((i + batch_size) / len(all_tickers), 1.0))
            
            if all_market_results:
                df_new = pd.DataFrame(all_market_results).sort_values(by="交易值指標", ascending=False).head(100)
                st.subheader(f"📊 {today_str} 交易值前 100 名")
                st.dataframe(df_new, use_container_width=True)
                
                # --- 寫入 Google Sheets (增量/更新邏輯) ---
                try:
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 讀取現有資料
                    existing_data = ws.get_all_records()
                    if existing_data:
                        df_history = pd.DataFrame(existing_data)
                        # 移除日期重複的舊資料 (避免同一天重複執行產生冗餘)
                        df_history = df_history[df_history['日期'] != today_str]
                        # 合併新舊資料
                        df_final = pd.concat([df_history, df_new], ignore_index=True)
                    else:
                        df_final = df_new
                    
                    # 清除並重寫 (或先清空再重新上傳以保持排序與整潔)
                    ws.clear()
                    # 包含標頭寫入
                    ws.update([df_final.columns.values.tolist()] + df_final.values.tolist())
                    
                    st.success(f"✅ 資料已更新！目前歷史總筆數: {len(df_final)}")
                except Exception as e:
                    st.error(f"雲端寫入失敗: {e}")
            else:
                st.error("未能獲取數據。")
