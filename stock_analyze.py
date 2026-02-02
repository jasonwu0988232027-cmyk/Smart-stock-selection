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

def get_hot_stocks_from_yahoo_bs4(limit=100):
    """使用 BeautifulSoup 解析 Yahoo 股市成交值排行榜 (不需要 html5lib)"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        st.error("缺少 beautifulsoup4 套件，請安裝: pip install beautifulsoup4")
        return None
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    hot_tickers = []
    
    try:
        st.info("🔍 正在從 Yahoo 股市抓取成交值排行榜...")
        url = "https://tw.stock.yahoo.com/rank/turnover?exchange=TAI"
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code != 200:
            raise Exception(f"HTTP 狀態碼: {r.status_code}")
        
        # 使用 BeautifulSoup 解析 (使用內建的 html.parser)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Yahoo 的排行榜通常在特定的 div 或 table 中
        # 嘗試找到所有包含股票代號的連結或文字
        
        # 方法 1: 找尋所有可能是股票代號的 4 位數字
        import re
        text_content = soup.get_text()
        # 找出所有 4 位數字
        potential_tickers = re.findall(r'\b(\d{4})\b', text_content)
        
        # 過濾：只保留台股常見的代號範圍 (1000-9999)
        for ticker in potential_tickers:
            ticker_num = int(ticker)
            if 1000 <= ticker_num <= 9999 and f"{ticker}.TW" not in hot_tickers:
                hot_tickers.append(f"{ticker}.TW")
                if len(hot_tickers) >= limit:
                    break
        
        if len(hot_tickers) > 0:
            st.success(f"✅ 成功從 Yahoo 抓取 {len(hot_tickers)} 檔股票")
            return hot_tickers
        else:
            raise Exception("未能解析出任何股票代號")
            
    except Exception as e:
        st.warning(f"⚠️ Yahoo 爬蟲失敗: {e}")
        return None

def get_hot_stocks_from_yahoo_lxml(limit=100):
    """使用 lxml 解析器 (pd.read_html 的替代方案)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        st.info("🔍 正在從 Yahoo 股市抓取成交值排行榜 (使用 lxml)...")
        url = "https://tw.stock.yahoo.com/rank/turnover?exchange=TAI"
        r = requests.get(url, headers=headers, timeout=10)
        
        # 使用 lxml 解析器
        dfs = pd.read_html(r.text, flavor='lxml')
        
        if not dfs or len(dfs) == 0:
            raise Exception("無法解析網頁表格")
        
        df = dfs[0]
        
        # 智慧偵測包含股名的欄位
        target_col = None
        for i, col_name in enumerate(df.columns):
            col_str = str(col_name).lower()
            if '股' in col_str or '名' in col_str or '代號' in col_str or 'symbol' in col_str:
                target_col = i
                break
        
        if target_col is None:
            target_col = 1  # 預設第二欄
        
        hot_tickers = []
        for item in df.iloc[:, target_col]:
            item_str = str(item).strip()
            
            # 嘗試切割出代號
            parts = item_str.split()
            ticker = parts[0] if parts else ""
            
            # 只要 4 位數字
            if ticker.isdigit() and len(ticker) == 4:
                hot_tickers.append(f"{ticker}.TW")
                if len(hot_tickers) >= limit:
                    break
        
        if hot_tickers:
            st.success(f"✅ 成功從 Yahoo 抓取 {len(hot_tickers)} 檔股票")
            return hot_tickers
        else:
            raise Exception("未能解析出任何股票代號")
            
    except Exception as e:
        st.warning(f"⚠️ Yahoo 爬蟲 (lxml) 失敗: {e}")
        return None

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

def get_hot_stocks(limit=100, force_fallback=False):
    """整合方法：嘗試多種解析方式"""
    
    if force_fallback:
        return get_fallback_list(limit)
    
    # 方法 1: 嘗試 lxml 解析器
    tickers = get_hot_stocks_from_yahoo_lxml(limit)
    if tickers and len(tickers) > 0:
        return tickers
    
    # 方法 2: 嘗試 BeautifulSoup
    tickers = get_hot_stocks_from_yahoo_bs4(limit)
    if tickers and len(tickers) > 0:
        return tickers
    
    # 方法 3: 使用備用清單
    st.warning("⚠️ 所有爬蟲方法均失敗，使用備用清單")
    return get_fallback_list(limit)

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
    errors = []
    
    for ticker in tickers:
        try:
            # 處理多標的下載的資料結構
            if isinstance(data.columns, pd.MultiIndex):
                if ticker not in data.columns.get_level_values(0):
                    errors.append(f"{ticker}: 未在下載資料中")
                    continue
                ticker_data = data[ticker].dropna()
            else:
                ticker_data = data.dropna()
            
            if ticker_data.empty:
                errors.append(f"{ticker}: 資料為空")
                continue
            
            # 取最新一筆資料
            last_row = ticker_data.iloc[-1]
            
            # 檢查必要欄位
            if 'Close' not in ticker_data.columns or 'Volume' not in ticker_data.columns:
                errors.append(f"{ticker}: 缺少必要欄位")
                continue
            
            price = float(last_row['Close'])
            volume = float(last_row['Volume'])
            
            # 過濾無效資料
            if price <= 0 or volume <= 0:
                errors.append(f"{ticker}: 價格或成交量無效")
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
            errors.append(f"{ticker}: {str(e)[:50]}")
            continue
    
    return results, errors

# --- Streamlit UI ---
st.title("🏆 台股熱門股資金排行系統")
st.write("**智慧流程：** Yahoo 成交榜 → 批次下載 → 計算交易值 → 排序前 100 → 同步雲端")

# 檢查套件狀態
with st.expander("🔧 套件檢查"):
    packages_status = []
    
    try:
        import lxml
        packages_status.append("✅ lxml - 已安裝")
    except:
        packages_status.append("❌ lxml - 未安裝 (建議安裝)")
    
    try:
        from bs4 import BeautifulSoup
        packages_status.append("✅ beautifulsoup4 - 已安裝")
    except:
        packages_status.append("❌ beautifulsoup4 - 未安裝 (建議安裝)")
    
    try:
        import html5lib
        packages_status.append("✅ html5lib - 已安裝")
    except:
        packages_status.append("⚠️ html5lib - 未安裝 (可選)")
    
    for status in packages_status:
        st.write(status)
    
    st.write("")
    st.write("**安裝指令：**")
    st.code("pip install lxml beautifulsoup4 html5lib")

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
    
    tickers = get_hot_stocks(target_count, force_fallback=use_fallback)
    
    if not tickers or len(tickers) == 0:
        st.error("❌ 無法獲取股票清單")
        st.stop()
    
    # 顯示股票清單預覽
    with st.expander(f"🔍 查看股票清單 ({len(tickers)} 檔)"):
        preview = [t.replace('.TW', '') for t in tickers[:50]]
        st.write(", ".join(preview))
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
        results, errors = calculate_trading_values(tickers, market_data)
    
    if not results:
        st.error("❌ 未能計算出任何有效資料")
        st.info("**可能原因：**")
        st.write("• 所有股票都沒有最新交易資料")
        st.write("• 今天可能是休市日")
        st.write("• 資料格式解析失敗")
        
        if errors:
            with st.expander("查看錯誤詳情"):
                for err in errors[:20]:
                    st.text(err)
        st.stop()
    
    # 轉換為 DataFrame 並排序
    df_results = pd.DataFrame(results)
    df_sorted = df_results.sort_values(by="交易值指標", ascending=False)
    
    # 取前 100 名
    top_n = min(100, len(df_sorted))
    df_top = df_sorted.head(top_n)
    
    # 顯示結果
    st.success(f"✅ 成功分析 {len(results)} 檔股票 ({len(errors)} 檔失敗)")
    
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
    
    # 錯誤資訊
    if errors:
        with st.expander(f"⚠️ 查看失敗記錄 ({len(errors)} 項)"):
            for idx, err in enumerate(errors[:30], 1):
                st.text(f"{idx}. {err}")
    
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
    - 備用: 手動維護的 100 檔熱門股清單
    
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
    
    st.header("🔧 套件安裝")
    st.write("執行此指令安裝所需套件:")
    st.code("pip install lxml beautifulsoup4 html5lib", language="bash")
