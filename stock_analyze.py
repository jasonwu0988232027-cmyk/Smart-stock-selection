import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import gspread
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from datetime import datetime
import time
import os
import random
import urllib3

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="AI 股市專家 v16.5", layout="wide")

# 環境參數設定
SHEET_NAME = "Stock_Predictions_History"
CREDENTIALS_JSON = "eco-precept-485904-j5-7ef3cdda1b03.json"

# AI 配置 (請在 Secrets 設定 GEMINI_API_KEY)
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "YOUR_KEY_HERE")
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

# ==================== 1. 雲端連線模組 (修復 Illegal header 報錯) ====================

def get_gspread_client():
    """修復非法字元導致的授權報錯"""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        if "gcp_service_account" in st.secrets:
            creds_info = dict(st.secrets["gcp_service_account"])
            # 關鍵修正：強制轉義換行符號，防止 Header 驗證失敗
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        elif os.path.exists(CREDENTIALS_JSON):
            creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=scopes)
        else:
            return None
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ 授權失敗: {e}")
        return None

def get_top_100_tickers():
    """步驟 1：抓取 EXCEL 第一頁的前 100 支股票"""
    client = get_gspread_client()
    if not client: return []
    try:
        sh = client.open(SHEET_NAME)
        ws = sh.get_worksheet(0)
        df = pd.DataFrame(ws.get_all_records())
        return df['股票代號'].dropna().astype(str).head(100).tolist()
    except Exception as e:
        st.error(f"讀取清單失敗: {e}")
        return []

# ==================== 2. 多維度分析與爬蟲模組 ====================

def crawl_news_for_ai(symbol):
    """步驟 2-二：爬蟲四大新聞網搜尋標的相關新聞"""
    stock_id = symbol.split('.')[0]
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 搜尋目標：FTNN、聚財網、鉅亨網、經濟日報 (範例整合網址)
    news_sources = [f"https://news.cnyes.com/news/cat/tw_stock_news"]
    combined_news = ""
    try:
        res = requests.get(news_sources[0], headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        titles = [t.get_text() for t in soup.find_all(['h3', 'a']) if stock_id in t.get_text()]
        combined_news = " ".join(titles[:5])
    except: pass
    return combined_news if combined_news else "無重大即時新聞"

def get_factor_score(ticker, ticker_df):
    """步驟 2-三：基本面與技術面積分分析"""
    score = 0
    try:
        # 1. 技術面：均線黃金交叉判定
        ma5 = ticker_df['Close'].rolling(5).mean().iloc[-1]
        ma20 = ticker_df['Close'].rolling(20).mean().iloc[-1]
        if ma5 > ma20: score += 2
        
        # 2. 基本面：本益比資訊 (yfinance)
        info = yf.Ticker(ticker).info
        if info.get('forwardPE', 100) < 15: score += 1
    except: pass
    return score

# ==================== 3. 主執行流程 (抗封鎖版) ====================

st.title("🛡️ AI 股市全能專家 v16.5")

if st.button("🚀 啟動 Top 100 全方位 AI 預測任務"):
    tickers = get_top_100_tickers()
    client = get_gspread_client()
    
    if client and tickers:
        sh = client.open(SHEET_NAME)
        ws = sh.get_worksheet(0)
        p_bar = st.progress(0)
        status = st.empty()
        
        # 批量獲取數據以減少請求次數
        status.text("正在批量同步市場歷史數據...")
        all_hist = yf.download(tickers, period="3mo", group_by='ticker', threads=True, progress=False)
        
        for idx, t in enumerate(tickers):
            try:
                status.text(f"分析中 ({idx+1}/100): {t}")
                
                # 提取個股數據
                df = all_hist[t].dropna() if isinstance(all_hist.columns, pd.MultiIndex) else all_hist.dropna()
                if df.empty: continue
                
                curr_p = round(float(df['Close'].iloc[-1]), 2)
                factor_score = get_factor_score(t, df)
                news_txt = crawl_news_for_ai(t)
                
                # 步驟 2-二：丟給 Gemini 分析積分並預測走勢
                prompt = f"分析{t}。現價{curr_p}。分析分{factor_score}。新聞：{news_txt}。請預測未來5日收盤價。請僅回傳：價1,價2,價3,價4,價5"
                response = ai_model.generate_content(prompt)
                preds = [float(p) for p in response.text.strip().split(',')]
                
                # 寫入 Excel E-J 欄
                # E-I: 預測價, J: 誤差% (設為待定)
                ws.update(f"E{idx+2}:J{idx+2}", [preds + ["-"]])
                
                # 智能冷卻預防封鎖
                time.sleep(random.uniform(1.0, 2.0))
                if (idx + 1) % 10 == 0:
                    status.text("冷卻中，避免觸發 Too Many Requests...")
                    time.sleep(15)
                    
            except Exception as e:
                st.warning(f"跳過 {t}: {e}")
                
            p_bar.progress((idx + 1) / len(tickers))
            
        status.text("✅ 全部任務執行完畢")
        st.success("🎉 預測結果已成功同步至 Excel E-J 欄位！")
