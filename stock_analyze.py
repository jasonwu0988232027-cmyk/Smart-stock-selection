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

def get_stocks_from_twse_api():
    """
    使用台灣證交所官方 API 取得所有上市股票的當日交易資訊
    API: https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data
    """
    try:
        st.info("📡 正在從台灣證交所 API 抓取當日交易資訊...")
        
        # 證交所官方開放資料 API
        url = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data'
        
        # 發送請求
        response = requests.get(url, timeout=30)
        
        if response.status_code != 200:
            raise Exception(f"HTTP 狀態碼: {response.status_code}")
        
        # 直接用 pandas 讀取 CSV 格式
        data = pd.read_csv(url)
        
        # 檢查資料
        if data.empty:
            raise Exception("API 回傳資料為空")
        
        st.success(f"✅ 成功從證交所 API 獲取 {len(data)} 檔股票資料")
        
        return data
        
    except Exception as e:
        st.error(f"❌ 證交所 API 失敗: {e}")
        return None

def process_twse_data(data, limit=100):
    """
    處理證交所資料，計算交易值並排序
    """
    try:
        # 欄位名稱可能是中文，先檢查
        st.info("🔄 正在處理資料...")
        
        # 顯示欄位名稱以便調試
        with st.expander("📋 資料欄位"):
            st.write(f"欄位: {list(data.columns)}")
            st.write(f"前 3 筆資料:")
            st.dataframe(data.head(3))
        
        # 常見欄位名稱對應
        # ['證券代號', '證券名稱', '成交股數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '漲跌價差', '成交筆數']
        
        results = []
        
        for idx, row in data.iterrows():
            try:
                # 取得股票代號 (通常是第一欄)
                stock_code = str(row.iloc[0]).strip()
                
                # 過濾：只要 4 位數字的股票
                if not stock_code.isdigit() or len(stock_code) != 4:
                    continue
                
                # 取得收盤價 (通常是第7欄，索引6)
                close_price = str(row.iloc[7]).replace(',', '').replace('--', '0')
                if close_price == '' or close_price == '--':
                    continue
                close_price = float(close_price)
                
                # 取得成交股數 (通常是第2欄，索引2)
                volume = str(row.iloc[2]).replace(',', '').replace('--', '0')
                if volume == '' or volume == '--':
                    continue
                volume = float(volume)
                
                # 過濾無效資料
                if close_price <= 0 or volume <= 0:
                    continue
                
                # 計算交易值 (億元) = 價格 × 成交股數 / 1億
                trading_value = (close_price * volume) / 1e8
                
                results.append({
                    "日期": datetime.now().strftime('%Y-%m-%d'),
                    "股票代號": f"{stock_code}.TW",
                    "股票名稱": str(row.iloc[1]).strip(),
                    "收盤價格": round(close_price, 2),
                    "成交股數": int(volume),
                    "交易值指標": round(trading_value, 4)
                })
                
            except Exception as e:
                # 跳過有問題的資料
                continue
        
        if not results:
            return None
        
        # 轉換為 DataFrame 並按交易值排序
        df = pd.DataFrame(results)
        df_sorted = df.sort_values(by="交易值指標", ascending=False)
        
        # 取前 N 名
        df_top = df_sorted.head(limit)
        
        st.success(f"✅ 成功處理 {len(results)} 檔股票，取前 {len(df_top)} 名")
        
        return df_top
        
    except Exception as e:
        st.error(f"❌ 資料處理失敗: {e}")
        import traceback
        st.code(traceback.format_exc())
        return None

def get_fallback_list(limit):
    """備用股票清單"""
    fallback = [
        # --- 權值/半導體 ---
        "2330.TW", "2454.TW", "2317.TW", "2303.TW", "2308.TW", "2382.TW", "3231.TW", "3443.TW", "3661.TW", "3035.TW",
        # --- AI 伺服器/散熱 ---
        "2376.TW", "2356.TW", "6669.TW", "3017.TW", "3324.TW", "2421.TW", "3037.TW", "2368.TW", "2449.TW", "6271.TW",
        # --- 航運/傳產 ---
        "2603.TW", "2609.TW", "2615.TW", "2618.TW", "2610.TW", "1513.TW", "1519.TW", "1504.TW", "1605.TW", "2002.TW",
        # --- 金融 ---
        "2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW", "2887.TW", "2892.TW", "2880.TW", "2883.TW", "2890.TW",
        # --- 光電/面板 ---
        "2409.TW", "3481.TW", "3008.TW", "2481.TW", "2344.TW", "2408.TW", "6770.TW", "5347.TW", "4961.TW", "9958.TW",
        # --- 電子零組件 ---
        "2357.TW", "2379.TW", "2395.TW", "2412.TW", "2474.TW", "3189.TW", "3711.TW", "4904.TW", "6505.TW", "8046.TW",
        # --- 電腦周邊 ---
        "2301.TW", "2324.TW", "2353.TW", "2377.TW", "2392.TW", "3045.TW", "6239.TW", "6415.TW", "6669.TW", "8299.TW",
        # --- 通信網路 ---
        "2347.TW", "2393.TW", "2439.TW", "3044.TW", "3706.TW", "4938.TW", "6176.TW", "6531.TW", "8410.TW", "8454.TW",
        # --- 其他電子 ---
        "2323.TW", "2327.TW", "2337.TW", "2345.TW", "2351.TW", "2362.TW", "2371.TW", "2385.TW", "2404.TW", "2434.TW"
    ]
    st.info(f"🛡️ 使用備用清單: {len(fallback[:limit])} 檔精選股票")
    return fallback[:limit]

def download_and_calculate_fallback(tickers, period="5d"):
    """使用 yfinance 下載備用清單的資料並計算"""
    try:
        with st.spinner(f"📥 正在下載 {len(tickers)} 檔股票資料..."):
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
        
        results = []
        
        for ticker in tickers:
            try:
                # 處理多標的下載
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker not in data.columns.get_level_values(0):
                        continue
                    ticker_data = data[ticker].dropna()
                else:
                    ticker_data = data.dropna()
                
                if ticker_data.empty:
                    continue
                
                last_row = ticker_data.iloc[-1]
                
                if 'Close' not in ticker_data.columns or 'Volume' not in ticker_data.columns:
                    continue
                
                price = float(last_row['Close'])
                volume = float(last_row['Volume'])
                
                if price <= 0 or volume <= 0:
                    continue
                
                trading_value = (price * volume) / 1e8
                
                results.append({
                    "日期": datetime.now().strftime('%Y-%m-%d'),
                    "股票代號": ticker,
                    "收盤價格": round(price, 2),
                    "交易值指標": round(trading_value, 4)
                })
                
            except:
                continue
        
        if not results:
            return None
        
        df = pd.DataFrame(results)
        df_sorted = df.sort_values(by="交易值指標", ascending=False)
        
        st.success(f"✅ 成功分析 {len(results)} 檔股票")
        
        return df_sorted
        
    except Exception as e:
        st.error(f"下載失敗: {e}")
        return None

# --- Streamlit UI ---
st.title("🏆 台股交易值排行系統 (證交所官方 API)")
st.write("**使用證交所官方開放資料 API - 無需爬蟲，100% 可靠！**")

# 說明
st.info("""
📡 **資料來源:** 台灣證券交易所官方開放資料 API
🔗 **API 網址:** https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data
✅ **優點:** 官方資料、格式穩定、無需解析網頁、不會被封鎖
""")

# 參數設定
col1, col2 = st.columns(2)
with col1:
    top_n = st.number_input("前 N 名股票", min_value=10, max_value=500, value=100, step=10)
with col2:
    use_fallback = st.checkbox("使用備用清單 (yfinance)", value=False)

if st.button("🚀 開始分析", type="primary"):
    
    if use_fallback:
        # 使用備用清單
        st.subheader("🛡️ 使用備用清單模式")
        tickers = get_fallback_list(top_n)
        df_top = download_and_calculate_fallback(tickers, period="5d")
        
    else:
        # 使用證交所 API
        st.subheader("📡 步驟 1: 從證交所 API 獲取資料")
        twse_data = get_stocks_from_twse_api()
        
        if twse_data is None:
            st.error("❌ 無法取得證交所資料")
            st.info("💡 您可以勾選「使用備用清單」改用 yfinance 方式")
            st.stop()
        
        # 處理資料
        st.subheader("📊 步驟 2: 計算交易值並排序")
        df_top = process_twse_data(twse_data, limit=top_n)
    
    if df_top is None or len(df_top) == 0:
        st.error("❌ 無法計算交易值資料")
        st.stop()
    
    # 顯示結果
    st.subheader(f"📊 交易值前 {len(df_top)} 名")
    
    # 統計資訊
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("股票數量", f"{len(df_top)} 檔")
    with col2:
        avg_value = df_top["交易值指標"].mean()
        st.metric("平均交易值", f"{avg_value:.2f} 億")
    with col3:
        max_value = df_top["交易值指標"].max()
        st.metric("最高交易值", f"{max_value:.2f} 億")
    
    # 顯示表格
    st.dataframe(df_top, use_container_width=True)
    
    # 步驟 3: 同步至 Google Sheets
    st.subheader("☁️ 步驟 3: 同步至 Google Sheets")
    
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
                
                st.success(f"✅ 已成功同步 {len(df_top)} 筆資料至 Google Sheets！")
                st.info(f"📄 工作表: {SHEET_NAME}")
                
        except Exception as e:
            st.error(f"❌ Google Sheets 同步失敗: {e}")
            st.info("資料已顯示在上方表格，您可以手動複製使用")
    else:
        st.warning("⚠️ 未連接 Google Sheets")
        st.info("💡 設定 Streamlit Secrets 或本地憑證檔案以啟用雲端同步功能")

# 側邊欄
with st.sidebar:
    st.header("ℹ️ 關於此應用")
    
    st.write("**資料來源:**")
    st.write("• 主要：台灣證券交易所官方 API")
    st.write("• 備用：yfinance + 精選股票清單")
    
    st.write("")
    st.write("**API 說明:**")
    st.write("證交所每日更新所有上市股票的交易資訊，包含:")
    st.write("• 證券代號、證券名稱")
    st.write("• 成交股數、成交金額")
    st.write("• 開盤價、最高價、最低價、收盤價")
    st.write("• 漲跌價差、成交筆數")
    
    st.write("")
    st.write("**注意事項:**")
    st.write("• 資料為當日最新資訊")
    st.write("• 休市日無法取得資料")
    st.write("• API 回應時間約 10-30 秒")
    
    st.divider()
    
    st.write("**技術資訊:**")
    st.code("""API URL:
https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data""", language="text")
