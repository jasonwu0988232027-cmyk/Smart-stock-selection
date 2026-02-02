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

def get_hot_stocks_from_yahoo(limit=100):
    """從 Yahoo 股市成交值排行榜抓取熱門股票"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    hot_tickers = []
    
    try:
        st.info("🔍 正在從 Yahoo 股市抓取成交值排行榜...")
        url = "https://tw.stock.yahoo.com/rank/turnover?exchange=TAI"
        r = requests.get(url, headers=headers, timeout=10)
        
        # 讀取網頁表格
        dfs = pd.read_html(r.text)
        if not dfs or len(dfs) == 0:
            raise Exception("無法解析網頁表格")
        
        df = dfs[0]  # 抓取第一個表格
        
        # 智慧偵測包含股名的欄位
        target_col = None
        for i, col_name in enumerate(df.columns):
            if '股' in str(col_name) or '名' in str(col_name) or '代號' in str(col_name):
                target_col = i
                break
        
        if target_col is None:
            target_col = 1  # 預設第二欄
        
        # 提取股票代號
        count = 0
        for item in df.iloc[:, target_col]:
            item_str = str(item).strip()
            
            # 嘗試切割出代號 (例如 "2330 台積電" -> "2330")
            parts = item_str.split(' ')
            ticker = parts[0]
            
            # 過濾邏輯：只要 4 位數字
            if ticker.isdigit() and len(ticker) == 4:
                hot_tickers.append(f"{ticker}.TW")
                count += 1
            
            if count >= limit:
                break
        
        if len(hot_tickers) > 0:
            st.success(f"✅ 成功從 Yahoo 抓取 {len(hot_tickers)} 檔熱門股票")
            return hot_tickers
        else:
            raise Exception("未能解析出任何股票代號")
            
    except Exception as e:
        st.warning(f"⚠️ Yahoo 爬蟲失敗: {e}")
        st.info("🛡️ 啟動備用股票清單...")
        return get_fallback_list(limit)

def get_fallback_list(limit):
    """備用股票清單 - 手動維護的熱門股"""
    fallback = [
        # --- 權值/半導體 ---
        "2330.TW", "2454.TW", "2317.TW", "2303.TW", "2308.TW", "2382.TW", "3231.TW", "3443.TW", "3661.TW", "3035.TW",
        # --- AI 伺服器/散熱 ---
        "2376.TW", "2356.TW", "6669.TW", "3017.TW", "3324.TW", "2421.TW", "3037.TW", "2368.TW", "2449.TW", "6271.TW",
        # --- 航運/傳產 ---
        "2603.TW", "2609.TW", "2615.TW", "2618.TW", "2610.TW", "1513.TW", "1519.TW", "1504.TW", "1605.TW", "2002.TW",
        # --- 金融 ---
        "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW",
        # --- 光電/面板/其他 ---
        "2409.TW", "3481.TW", "3008.TW", "2481.TW", "2344.TW", "2408.TW", "6770.TW", "5347.TW", "4961.TW", "9958.TW",
        # --- 額外補充 ---
        "2357.TW", "2379.TW", "2395.TW", "2412.TW", "2474.TW", "3008.TW", "3189.TW", "3711.TW", "4904.TW", "6505.TW"
    ]
    return fallback[:limit]

def download_stock_data(tickers, period="1y"):
    """批次下載股票資料"""
    try:
        with st.spinner(f"📥 正在下載 {len(tickers)} 檔股票資料 (period={period})..."):
            data = yf.download(
                tickers, 
                period=period, 
                group_by='ticker', 
                auto_adjust=True, 
                threads=True,
                progress=False
            )
        
        if data.empty:
            st.error("下載的資料為空")
            return None
        
        # 移除完全空白的欄位
        data = data.dropna(axis=1, how='all')
        st.success(f"✅ 成功下載 {len(tickers)} 檔股票資料")
        return data
        
    except Exception as e:
        st.error(f"下載失敗: {e}")
        return None

def calculate_trading_values(tickers, data):
    """計算每支股票的交易值指標"""
    results = []
    
    for ticker in tickers:
        try:
            # 處理多標的下載的資料結構
            if isinstance(data.columns, pd.MultiIndex):
                if ticker not in data.columns.get_level_values(0):
                    continue
                ticker_data = data[ticker].dropna()
            else:
                ticker_data = data.dropna()
            
            if ticker_data.empty:
                continue
            
            # 取最新一筆資料
            last_row = ticker_data.iloc[-1]
            
            # 檢查必要欄位
            if 'Close' not in ticker_data.columns or 'Volume' not in ticker_data.columns:
                continue
            
            price = float(last_row['Close'])
            volume = float(last_row['Volume'])
            
            # 過濾無效資料
            if price <= 0 or volume <= 0:
                continue
            
            # 計算交易值 (億元)
            trading_value = (price * volume) / 1e8
            
            results.append({
                "日期": datetime.now().strftime('%Y-%m-%d'),
                "股票代號": ticker,
                "收盤價格": round(price, 2),
                "成交量": int(volume),
                "交易值指標": round(trading_value, 4)
            })
            
        except Exception as e:
            st.warning(f"處理 {ticker} 時發生錯誤: {str(e)[:50]}")
            continue
    
    return results

# --- Streamlit UI ---
st.title("🏆 台股熱門股資金排行系統 (Yahoo 版)")
st.write("**智慧流程：** Yahoo 成交榜 → 批次下載 → 計算交易值 → 排序前 100 → 同步雲端")

# 參數設定
col1, col2, col3 = st.columns(3)
with col1:
    target_count = st.number_input("目標股票數量", min_value=10, max_value=200, value=100, step=10)
with col2:
    data_period = st.selectbox("資料期間", ["5d", "1mo", "3mo", "6mo", "1y"], index=0)
with col3:
    use_fallback = st.checkbox("強制使用備用清單", value=False)

if st.button("🚀 開始執行分析"):
    # 步驟 1: 獲取股票清單
    st.subheader("📋 步驟 1: 獲取股票清單")
    
    if use_fallback:
        tickers = get_fallback_list(target_count)
        st.info(f"使用備用清單: {len(tickers)} 檔股票")
    else:
        tickers = get_hot_stocks_from_yahoo(target_count)
    
    if not tickers:
        st.error("❌ 無法獲取股票清單")
        st.stop()
    
    # 顯示股票清單預覽
    with st.expander(f"🔍 查看股票清單 ({len(tickers)} 檔)"):
        st.write(", ".join([t.replace('.TW', '') for t in tickers[:50]]))
        if len(tickers) > 50:
            st.write(f"... 還有 {len(tickers) - 50} 檔")
    
    # 步驟 2: 下載股票資料
    st.subheader("📥 步驟 2: 下載股票資料")
    market_data = download_stock_data(tickers, period=data_period)
    
    if market_data is None:
        st.error("❌ 資料下載失敗")
        st.stop()
    
    # 步驟 3: 計算交易值
    st.subheader("📊 步驟 3: 計算交易值指標")
    with st.spinner("正在計算..."):
        results = calculate_trading_values(tickers, market_data)
    
    if not results:
        st.error("❌ 未能計算出任何有效資料")
        st.info("**可能原因：**")
        st.write("• 所有股票都沒有最新交易資料")
        st.write("• 今天可能是休市日")
        st.write("• 資料格式解析失敗")
        st.stop()
    
    # 轉換為 DataFrame 並排序
    df_results = pd.DataFrame(results)
    df_sorted = df_results.sort_values(by="交易值指標", ascending=False)
    
    # 取前 100 名
    top_n = min(100, len(df_sorted))
    df_top = df_sorted.head(top_n)
    
    # 顯示結果
    st.success(f"✅ 成功分析 {len(results)} 檔股票")
    
    # 統計資訊
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("成功分析", f"{len(results)} 檔")
    with col2:
        st.metric("前 100 名", f"{top_n} 檔")
    with col3:
        avg_value = df_top["交易值指標"].mean()
        st.metric("平均交易值", f"{avg_value:.2f} 億")
    with col4:
        max_value = df_top["交易值指標"].max()
        st.metric("最高交易值", f"{max_value:.2f} 億")
    
    # 顯示前 100 名表格
    st.subheader(f"📊 交易值前 {top_n} 名")
    st.dataframe(df_top, use_container_width=True)
    
    # 步驟 4: 同步至 Google Sheets
    st.subheader("☁️ 步驟 4: 同步至 Google Sheets")
    
    client = get_gspread_client()
    
    if client:
        try:
            with st.spinner("正在寫入雲端..."):
                sh = client.open(SHEET_NAME)
                ws = sh.get_worksheet(0)
                
                # 檢查並寫入表頭
                if not ws.acell('A1').value:
                    ws.append_row(["日期", "股票代號", "收盤價格", "交易值指標"])
                
                # 準備上傳資料 (只上傳 A-D 欄)
                upload_list = df_top[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
                
                # 批次寫入
                ws.append_rows(upload_list)
                
                st.success(f"✅ 已成功同步 {top_n} 筆資料至 Google Sheets！")
                st.info(f"📄 工作表: {SHEET_NAME}")
                
        except Exception as e:
            st.error(f"❌ Google Sheets 同步失敗: {e}")
            st.info("資料已顯示在上方表格，您可以手動複製使用")
    else:
        st.warning("⚠️ 未連接 Google Sheets")
        st.info("💡 設定 Streamlit Secrets 或本地憑證檔案以啟用雲端同步功能")

# 側邊欄說明
with st.sidebar:
    st.header("ℹ️ 使用說明")
    st.write("""
    **資料來源:**
    - 主要: Yahoo 股市成交值排行榜
    - 備用: 手動維護的熱門股清單
    
    **分析流程:**
    1. 抓取熱門股票代號
    2. 批次下載股價資料
    3. 計算交易值指標 (價格 × 成交量)
    4. 排序並取前 100 名
    5. 同步至 Google Sheets
    
    **注意事項:**
    - 建議使用 5d 或 1mo 期間以獲取最新資料
    - 休市日可能無法取得資料
    - 首次執行建議先測試較小數量
    """)
    
    st.header("🔧 進階設定")
    st.write("在 Streamlit Secrets 中設定:")
    st.code("""
[gcp_service_account]
type = "service_account"
project_id = "your-project"
private_key_id = "..."
private_key = "..."
client_email = "..."
    """)
