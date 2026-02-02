import streamlit as st
import gspread
import pandas as pd
import yfinance as yf
import requests
import urllib3
import os
from datetime import datetime
from google.oauth2.service_account import Credentials
from io import BytesIO

# --- 基礎配置 ---
st.set_page_config(page_title="台股交易值分析系統", page_icon="📊", layout="wide")
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

def get_stocks_from_twse_api():
    """使用台灣證交所官方 API 取得所有上市股票的當日交易資訊"""
    try:
        st.info("📡 正在從台灣證交所 API 抓取當日交易資訊...")
        url = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data'
        response = requests.get(url, timeout=30)
        
        if response.status_code != 200:
            raise Exception(f"HTTP 狀態碼: {response.status_code}")
        
        data = pd.read_csv(url)
        
        if data.empty:
            raise Exception("API 回傳資料為空")
        
        st.success(f"✅ 成功從證交所 API 獲取 {len(data)} 檔股票資料")
        return data
        
    except Exception as e:
        st.error(f"❌ 證交所 API 失敗: {e}")
        return None

def process_twse_data(data, limit=100):
    """處理證交所資料，計算交易值並排序"""
    try:
        st.info("🔄 正在處理資料...")
        
        results = []
        
        for idx, row in data.iterrows():
            try:
                stock_code = str(row.iloc[0]).strip()
                
                if not stock_code.isdigit() or len(stock_code) != 4:
                    continue
                
                close_price = str(row.iloc[7]).replace(',', '').replace('--', '0')
                if close_price == '' or close_price == '--':
                    continue
                close_price = float(close_price)
                
                volume = str(row.iloc[2]).replace(',', '').replace('--', '0')
                if volume == '' or volume == '--':
                    continue
                volume = float(volume)
                
                if close_price <= 0 or volume <= 0:
                    continue
                
                trading_value = (close_price * volume) / 1e8
                
                results.append({
                    "日期": datetime.now().strftime('%Y-%m-%d'),
                    "股票代號": f"{stock_code}.TW",
                    "股票名稱": str(row.iloc[1]).strip(),
                    "收盤價格": round(close_price, 2),
                    "成交股數": int(volume),
                    "交易值指標": round(trading_value, 4)
                })
                
            except:
                continue
        
        if not results:
            return None
        
        df = pd.DataFrame(results)
        df_sorted = df.sort_values(by="交易值指標", ascending=False)
        df_top = df_sorted.head(limit)
        
        st.success(f"✅ 成功處理 {len(results)} 檔股票，取前 {len(df_top)} 名")
        return df_top
        
    except Exception as e:
        st.error(f"❌ 資料處理失敗: {e}")
        return None

# --- 主程式 ---
st.title("📊 台股交易值分析系統")

# 創建分頁
tab1, tab2 = st.tabs(["🚀 市場掃描與排行", "📝 Excel 更新工具"])

# ===== 第一個分頁：市場掃描 =====
with tab1:
    st.header("🏆 台股交易值排行")
    st.write("**使用證交所官方開放資料 API**")
    
    st.info("""
    📡 **資料來源:** 台灣證券交易所官方開放資料 API  
    🔗 **API 網址:** https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data
    """)
    
    col1, col2 = st.columns(2)
    with col1:
        top_n = st.number_input("前 N 名股票", min_value=10, max_value=500, value=100, step=10, key="tab1_top_n")
    
    if st.button("🚀 開始分析", type="primary", key="tab1_analyze"):
        st.subheader("📡 步驟 1: 從證交所 API 獲取資料")
        twse_data = get_stocks_from_twse_api()
        
        if twse_data is None:
            st.error("❌ 無法取得證交所資料")
            st.stop()
        
        st.subheader("📊 步驟 2: 計算交易值並排序")
        df_top = process_twse_data(twse_data, limit=top_n)
        
        if df_top is None or len(df_top) == 0:
            st.error("❌ 無法計算交易值資料")
            st.stop()
        
        st.subheader(f"📊 交易值前 {len(df_top)} 名")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("股票數量", f"{len(df_top)} 檔")
        with col2:
            avg_value = df_top["交易值指標"].mean()
            st.metric("平均交易值", f"{avg_value:.2f} 億")
        with col3:
            max_value = df_top["交易值指標"].max()
            st.metric("最高交易值", f"{max_value:.2f} 億")
        
        st.dataframe(df_top, use_container_width=True)
        
        st.subheader("☁️ 步驟 3: 同步至 Google Sheets")
        
        client = get_gspread_client()
        
        if client:
            try:
                with st.spinner("正在寫入雲端..."):
                    sh = client.open(SHEET_NAME)
                    ws = sh.get_worksheet(0)
                    
                    if not ws.acell('A1').value:
                        ws.append_row(["日期", "股票代號", "收盤價格", "交易值指標"])
                    
                    upload_list = df_top[["日期", "股票代號", "收盤價格", "交易值指標"]].values.tolist()
                    ws.append_rows(upload_list)
                    
                    st.success(f"✅ 已成功同步 {len(df_top)} 筆資料至 Google Sheets！")
                    st.info(f"📄 工作表: {SHEET_NAME}")
                    
            except Exception as e:
                st.error(f"❌ Google Sheets 同步失敗: {e}")
        else:
            st.warning("⚠️ 未連接 Google Sheets")

# ===== 第二個分頁：Excel 更新工具 =====
with tab2:
    st.header("📝 Excel 交易值更新工具")
    st.write("**上傳 Excel 檔案，自動填入今日股票的交易值指標到 D 欄**")
    
    # 說明
    with st.expander("ℹ️ 使用說明"):
        st.write("""
        **功能說明:**
        1. 上傳包含股票代號的 Excel 檔案
        2. 系統會自動識別今日的資料列
        3. 從證交所 API 或 yfinance 獲取最新交易資訊
        4. 自動計算並填入交易值指標到 D 欄
        
        **Excel 格式要求:**
        - A 欄: 日期 (格式: YYYY-MM-DD)
        - B 欄: 股票代號 (例如: 2330.TW 或 2330)
        - C 欄: 收盤價格 (可選，會被更新)
        - D 欄: 交易值指標 (將被更新)
        """)
    
    # 上傳檔案
    uploaded_file = st.file_uploader("上傳 Excel 檔案", type=['xlsx', 'xls'], key="excel_upload")
    
    if uploaded_file is not None:
        try:
            # 讀取 Excel
            df = pd.read_excel(uploaded_file)
            
            st.success(f"✅ 成功讀取 Excel，共 {len(df)} 列資料")
            
            # 顯示原始資料
            st.subheader("📊 原始資料預覽")
            st.dataframe(df.head(10), use_container_width=True)
            
            # 檢查欄位
            if len(df.columns) < 2:
                st.error("❌ Excel 至少需要 2 欄 (日期、股票代號)")
                st.stop()
            
            # 重新命名欄位
            if '日期' not in df.columns:
                col_names = ['日期', '股票代號', '收盤價格', '交易值指標'] if len(df.columns) >= 4 else ['日期', '股票代號'] + list(df.columns[2:])
                df.columns = col_names[:len(df.columns)]
            
            # 確保有必要的欄位
            if '收盤價格' not in df.columns:
                df['收盤價格'] = None
            if '交易值指標' not in df.columns:
                df['交易值指標'] = None
            
            # 選擇更新方式
            st.subheader("⚙️ 更新設定")
            
            col1, col2 = st.columns(2)
            with col1:
                data_source = st.radio(
                    "資料來源",
                    ["🏛️ 證交所 API (推薦)", "📈 yfinance"],
                    help="證交所 API 更快但僅限當日；yfinance 較慢但更靈活",
                    key="data_source"
                )
            
            with col2:
                date_filter = st.radio(
                    "更新範圍",
                    ["僅今日", "所有日期"],
                    help="僅今日：只更新今天的資料；所有日期：更新所有列",
                    key="date_filter"
                )
            
            if st.button("🚀 開始更新交易值指標", type="primary", key="tab2_update"):
                
                # 轉換日期欄位
                try:
                    df['日期'] = pd.to_datetime(df['日期'])
                except:
                    st.warning("⚠️ 日期欄位格式無法識別，將更新所有列")
                    date_filter = "所有日期"
                
                # 篩選今日資料
                today = datetime.now().strftime('%Y-%m-%d')
                
                if date_filter == "僅今日":
                    mask = df['日期'].dt.strftime('%Y-%m-%d') == today
                    rows_to_update = df[mask].index.tolist()
                    
                    if len(rows_to_update) == 0:
                        st.warning(f"⚠️ 沒有找到今日 ({today}) 的資料")
                        st.info("💡 您可以選擇「所有日期」來更新全部資料")
                        st.stop()
                    
                    st.info(f"📍 找到 {len(rows_to_update)} 列今日資料需要更新")
                else:
                    rows_to_update = df.index.tolist()
                    st.info(f"📍 將更新全部 {len(rows_to_update)} 列資料")
                
                # 獲取股票代號列表
                stock_codes = df.loc[rows_to_update, '股票代號'].unique().tolist()
                
                # 清理股票代號格式
                stock_codes_clean = []
                for code in stock_codes:
                    code_str = str(code).strip().replace('.TW', '').replace('.tw', '')
                    if code_str.replace('.', '').isdigit():
                        stock_codes_clean.append(code_str)
                
                st.write(f"需要查詢 {len(stock_codes_clean)} 支股票")
                
                # 根據選擇的資料來源獲取資料
                stock_value_map = {}
                
                if data_source == "🏛️ 證交所 API (推薦)":
                    with st.spinner("📡 正在從證交所 API 獲取資料..."):
                        try:
                            url = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data'
                            twse_data = pd.read_csv(url)
                            
                            for idx, row in twse_data.iterrows():
                                try:
                                    stock_code = str(row.iloc[0]).strip()
                                    
                                    if stock_code not in stock_codes_clean:
                                        continue
                                    
                                    close_price = str(row.iloc[7]).replace(',', '').replace('--', '0')
                                    if close_price == '' or close_price == '--':
                                        continue
                                    close_price = float(close_price)
                                    
                                    volume = str(row.iloc[2]).replace(',', '').replace('--', '0')
                                    if volume == '' or volume == '--':
                                        continue
                                    volume = float(volume)
                                    
                                    if close_price <= 0 or volume <= 0:
                                        continue
                                    
                                    trading_value = (close_price * volume) / 1e8
                                    
                                    stock_value_map[stock_code] = {
                                        'price': close_price,
                                        'value': round(trading_value, 4)
                                    }
                                    
                                except:
                                    continue
                            
                            st.success(f"✅ 成功獲取 {len(stock_value_map)} 支股票的資料")
                            
                        except Exception as e:
                            st.error(f"❌ 證交所 API 失敗: {e}")
                            st.stop()
                
                else:  # yfinance
                    with st.spinner("📈 正在從 yfinance 下載資料..."):
                        try:
                            tickers = [f"{code}.TW" for code in stock_codes_clean]
                            data = yf.download(tickers, period="5d", group_by='ticker', threads=True, progress=False)
                            
                            for code in stock_codes_clean:
                                ticker = f"{code}.TW"
                                try:
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
                                    
                                    stock_value_map[code] = {
                                        'price': round(price, 2),
                                        'value': round(trading_value, 4)
                                    }
                                    
                                except:
                                    continue
                            
                            st.success(f"✅ 成功獲取 {len(stock_value_map)} 支股票的資料")
                            
                        except Exception as e:
                            st.error(f"❌ yfinance 下載失敗: {e}")
                            st.stop()
                
                # 更新 DataFrame
                update_count = 0
                
                for idx in rows_to_update:
                    stock_code = str(df.loc[idx, '股票代號']).strip().replace('.TW', '').replace('.tw', '')
                    
                    if stock_code in stock_value_map:
                        df.loc[idx, '收盤價格'] = stock_value_map[stock_code]['price']
                        df.loc[idx, '交易值指標'] = stock_value_map[stock_code]['value']
                        update_count += 1
                
                st.success(f"✅ 成功更新 {update_count} 列的交易值指標！")
                
                # 顯示更新後的資料
                st.subheader("📊 更新後的資料")
                st.dataframe(df.head(20), use_container_width=True)
                
                # 統計資訊
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("總列數", len(df))
                with col2:
                    st.metric("已更新", update_count)
                with col3:
                    success_rate = (update_count / len(rows_to_update) * 100) if len(rows_to_update) > 0 else 0
                    st.metric("成功率", f"{success_rate:.1f}%")
                
                # 提供下載
                st.subheader("💾 下載更新後的檔案")
                
                output = BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Sheet1')
                
                output.seek(0)
                
                st.download_button(
                    label="📥 下載更新後的 Excel",
                    data=output,
                    file_name=f"updated_stock_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
        except Exception as e:
            st.error(f"❌ 讀取或處理 Excel 時發生錯誤: {e}")
    
    else:
        # 提供範例檔案
        st.info("👆 請上傳 Excel 檔案開始使用")
        
        with st.expander("📄 下載範例檔案"):
            sample_data = pd.DataFrame({
                '日期': [datetime.now().strftime('%Y-%m-%d')] * 5,
                '股票代號': ['2330.TW', '2454.TW', '2317.TW', '2303.TW', '2308.TW'],
                '收盤價格': [None] * 5,
                '交易值指標': [None] * 5
            })
            
            sample_output = BytesIO()
            with pd.ExcelWriter(sample_output, engine='openpyxl') as writer:
                sample_data.to_excel(writer, index=False, sheet_name='Sheet1')
            
            sample_output.seek(0)
            
            st.write("下載範例 Excel 檔案，了解正確的格式：")
            st.download_button(
                label="📥 下載範例 Excel",
                data=sample_output,
                file_name="sample_stock_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_sample"
            )
