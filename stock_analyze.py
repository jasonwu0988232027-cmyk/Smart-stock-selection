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
        st.success(f"✅ 成功獲取 {len(tickers)} 檔股票代碼")
        return tickers
    except Exception as e:
        st.warning(f"⚠️ 從證交所獲取股票代碼失敗: {e}")
        st.info("使用預設股票代碼範圍...")
        # 使用預設範圍作為備案
        default_tickers = [f"{i:04d}.TW" for i in range(1101, 3000)]
        return default_tickers

# --- UI 與 執行 ---
st.title("🏆 台股全市場資金排行系統 (完整修正版)")
st.write("流程：1. 掃描全市場 -> 2. 篩選交易值前 100 名 -> 3. 同步至 Google Sheets")

# 設定選項
col1, col2 = st.columns(2)
with col1:
    test_mode = st.checkbox("🧪 測試模式 (僅掃描 50 檔)", value=False)
with col2:
    batch_size = st.selectbox("批次大小", [25, 50, 100], index=1)

if st.button("🚀 執行全市場深度掃描"):
    with st.spinner("正在獲取股票代碼列表..."):
        all_tickers = get_full_market_tickers()
    
    # 檢查是否成功獲取股票代碼
    if not all_tickers or len(all_tickers) == 0:
        st.error("❌ 無法獲取股票代碼列表！")
        st.info("**可能原因：**")
        st.write("• 無法連接台灣證券交易所網站")
        st.write("• 網站結構已改變")
        st.write("• 網路連線問題")
        st.stop()
    
    # 測試模式：僅處理前 50 檔
    if test_mode:
        all_tickers = all_tickers[:50]
        st.info(f"🧪 測試模式：僅掃描前 {len(all_tickers)} 檔股票")
    
    client = get_gspread_client()
    
    st.info(f"📊 開始掃描 {len(all_tickers)} 檔股票...")
    all_market_results = []
    
    # 診斷資訊容器
    error_log = []
    success_count = 0
    download_errors = 0
    
    # 進度追蹤
    p_bar = st.progress(0)
    status_text = st.empty()
    
    # 批次處理
    total_batches = (len(all_tickers) + batch_size - 1) // batch_size
    
    for batch_idx, i in enumerate(range(0, len(all_tickers), batch_size)):
        batch = all_tickers[i : i + batch_size]
        status_text.text(f"📥 正在處理批次 {batch_idx + 1}/{total_batches} (股票 {i+1}-{min(i+batch_size, len(all_tickers))})")
        
        try:
            # 下載 5 天資料確保獲取最新交易日
            data = yf.download(batch, period="5d", group_by='ticker', threads=True, progress=False)
            
            # 檢查是否成功下載資料
            if data.empty:
                error_log.append(f"批次 {batch_idx + 1}: 下載資料為空")
                download_errors += 1
                continue
            
            # 處理每一支股票
            for t in batch:
                try:
                    # 處理多標的下載的 DataFrame 結構
                    if len(batch) > 1 and isinstance(data.columns, pd.MultiIndex):
                        # 多標的情況
                        if t in data.columns.get_level_values(0):
                            t_df = data[t].dropna()
                        else:
                            error_log.append(f"{t}: 未在下載資料中")
                            continue
                    else:
                        # 單標的情況
                        t_df = data.dropna()
                    
                    # 檢查資料是否有效
                    if t_df.empty or len(t_df) == 0:
                        error_log.append(f"{t}: 資料為空")
                        continue
                    
                    last_row = t_df.iloc[-1]
                    
                    # 檢查必要欄位是否存在
                    if 'Close' not in t_df.columns or 'Volume' not in t_df.columns:
                        error_log.append(f"{t}: 缺少必要欄位")
                        continue
                    
                    price = float(last_row['Close'])
                    vol = float(last_row['Volume'])
                    
                    # 過濾無效資料
                    if price <= 0 or vol <= 0:
                        error_log.append(f"{t}: 價格或成交量無效")
                        continue
                    
                    # 計算交易值指標 (億)
                    val_billion = (price * vol) / 1e8
                    
                    all_market_results.append({
                        "日期": datetime.now().strftime('%Y-%m-%d'),
                        "股票代號": t,
                        "收盤價格": round(price, 2),
                        "交易值指標": round(val_billion, 4)
                    })
                    success_count += 1
                    
                except Exception as e:
                    error_log.append(f"{t}: {str(e)[:50]}")
                    continue
                    
        except Exception as e:
            error_msg = f"批次 {batch_idx + 1} 下載失敗: {str(e)[:100]}"
            error_log.append(error_msg)
            st.warning(f"⚠️ {error_msg}")
            download_errors += 1
        
        # 更新進度
        progress = min((i + batch_size) / len(all_tickers), 1.0)
        p_bar.progress(progress)
        
        # 每批次後暫停,避免 API 限制
        if batch_idx < total_batches - 1:  # 最後一批不需要暫停
            time.sleep(0.5)
    
    # 清除進度顯示
    p_bar.empty()
    status_text.empty()
    
    # 顯示診斷資訊
    st.subheader("📋 掃描診斷報告")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("✅ 成功抓取", f"{success_count} 檔")
    with col2:
        st.metric("❌ 失敗數量", f"{len(error_log)} 項")
    with col3:
        success_rate = (success_count/len(all_tickers)*100) if len(all_tickers) > 0 else 0
        st.metric("📈 成功率", f"{success_rate:.1f}%")
    with col4:
        st.metric("🔄 批次錯誤", f"{download_errors} 批")
    
    # 顯示錯誤日誌選項
    if error_log:
        with st.expander(f"⚠️ 查看錯誤詳情 ({len(error_log)} 項錯誤)"):
            st.write("**最近 30 項錯誤：**")
            for idx, err in enumerate(error_log[:30], 1):
                st.text(f"{idx}. {err}")
    
    # --- 步驟 2：取市場中「交易值指標」前 100 的股票 ---
    if all_market_results:
        df_full = pd.DataFrame(all_market_results)
        # 根據交易值指標降序排列並取前 100
        top_n = min(100, len(df_full))
        df_top100 = df_full.sort_values(by="交易值指標", ascending=False).head(top_n)
        
        st.success(f"✅ 成功分析 {len(df_full)} 檔股票")
        st.subheader(f"📊 全市場交易值前 {top_n} 名結果")
        st.dataframe(df_top100, use_container_width=True)
        
        # 準備上傳資料
        upload_list = df_top100[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
        
        # 寫入 Google Sheets
        if client:
            try:
                with st.spinner("正在同步至 Google Sheets..."):
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    # 若為空表則寫入表頭
                    if not ws.acell('A1').value:
                        ws.append_row(["日期", "股票代號", "收盤價格", "交易值指標"])
                    
                    ws.append_rows(upload_list)
                    st.success(f"✅ 已成功同步前 {top_n} 名至 Google Sheets！")
            except Exception as e:
                st.error(f"❌ Google Sheets 寫入失敗: {e}")
                st.info("資料已顯示於網頁，您可以手動複製使用")
        else:
            st.warning("⚠️ 未連接 Google Sheets，資料僅顯示於網頁")
            st.info("💡 提示：設定 Streamlit Secrets 或本地憑證以啟用雲端同步")
    else:
        st.error("❌ 未能成功調取任何市場資料")
        st.info("**建議診斷步驟：**")
        st.write("1. ✅ 確認網路連線正常")
        st.write("2. 🧪 先啟用「測試模式」僅掃描 50 檔")
        st.write("3. 📊 檢查是否為台股休市日")
        st.write("4. 🔄 嘗試更新 yfinance 套件: `pip install --upgrade yfinance`")
        st.write("5. 📝 查看上方錯誤詳情了解具體問題")
