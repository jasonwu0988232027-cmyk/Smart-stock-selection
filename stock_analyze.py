import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import time
import random
import requests
import urllib3
import json
import os
from datetime import datetime

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股多因子決策系統 (加碼止損版)", layout="wide")

DB_FILE = "portfolio.json"

# 持倉管理
def load_portfolio():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_portfolio(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=4)

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = load_portfolio()

# --- 1. 全面獲取股票代碼 (全面模式) ---
@st.cache_data(ttl=86400)
def get_full_market_tickers():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        if len(tickers) > 800: return tickers
    except: pass
    return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- 2. 交易決策邏輯 (整合回測標準) ---
def analyze_stock_advanced(ticker, weights, params):
    """
    多因子量化分析核心函數
    
    評分機制：
    - RSI < 30 (超賣): +40分
    - MA5 黃金交叉 MA10: +30分  
    - 單日漲跌幅 >= 7%: +20分
    - 成交量爆量 (>平均2倍): +10分
    
    動作判定邏輯：
    1. 持倉時：依 ROI 與 RSI 判斷止損/獲利
    2. 空倉時：總分達標且未超過最大加碼次數則建議買入
    """
    try:
        df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['MA5'] = ta.sma(df['Close'], length=5)
        df['MA10'] = ta.sma(df['Close'], length=10)

        curr, prev = df.iloc[-1], df.iloc[-2]
        c_price = float(curr['Close'])
        c_rsi = float(curr['RSI'])
        
        # 評分邏輯 + 記錄原因
        score = 0
        reasons = []
        
        # RSI 超賣檢查
        if c_rsi < 30: 
            score += weights['rsi']
            reasons.append(f"RSI超賣({c_rsi:.1f}<30, +{weights['rsi']}分)")
        
        # 均線交叉檢查
        if float(prev['MA5']) < float(prev['MA10']) and float(curr['MA5']) > float(curr['MA10']): 
            score += weights['ma']
            reasons.append(f"MA5黃金交叉MA10(+{weights['ma']}分)")
        
        # 價格波動檢查
        chg = ((c_price - float(prev['Close'])) / float(prev['Close'])) * 100
        if abs(chg) >= 7.0: 
            score += weights['vol']
            reasons.append(f"單日波動{chg:+.2f}%(+{weights['vol']}分)")
        
        # 成交量檢查
        vol_avg = df['Volume'].mean()
        vol_ratio = float(curr['Volume']) / vol_avg
        if vol_ratio > 2: 
            score += weights['vxx']
            reasons.append(f"成交量爆增{vol_ratio:.1f}倍(+{weights['vxx']}分)")

        # 動作判定 (結合持倉與回測參數)
        holdings = st.session_state.portfolio.get(ticker, [])
        action = "觀望"
        action_reason = ""
        
        if holdings:
            avg_cost = sum([h['price'] for h in holdings]) / len(holdings)
            roi = (c_price - avg_cost) / avg_cost
            roi_pct = roi * 100
            
            # 止損判定
            if roi <= -params['stop_loss_pct']: 
                action = "🚨 止損賣出"
                action_reason = f"虧損{roi_pct:.2f}%達止損線(-{params['stop_loss_pct']*100:.1f}%)"
            
            # RSI 獲利調節
            elif c_rsi > params['profit_take_rsi']: 
                action = "🟠 部分調節"
                action_reason = f"RSI={c_rsi:.1f}超過調節線({params['profit_take_rsi']}), 獲利{roi_pct:+.2f}%"
            
            # RSI 全清倉
            elif c_rsi > params['overbought_rsi']: 
                action = "🔵 獲利清倉"
                action_reason = f"RSI={c_rsi:.1f}極度超買(>{params['overbought_rsi']}), 獲利{roi_pct:+.2f}%"
            
            else:
                action_reason = f"持倉{len(holdings)}批, 報酬{roi_pct:+.2f}%, 等待訊號"
        
        # 買入/加碼判定 (檢查最大加碼次數)
        if action == "觀望" and score >= params['buy_threshold']:
            if len(holdings) < params['max_entries']:
                if len(holdings) > 0:
                    action = "🟢 建議加碼"
                    action_reason = f"評分{score}分達標(≥{params['buy_threshold']}), 可加碼第{len(holdings)+1}批(上限{params['max_entries']}批)"
                else:
                    action = "🟢 建議買入"
                    action_reason = f"評分{score}分達標(≥{params['buy_threshold']}), 符合建倉條件"
            else:
                action_reason = f"評分{score}分達標但已達加碼上限({params['max_entries']}批)"
        
        # 組合技術指標理由
        if reasons:
            tech_reasons = " | ".join(reasons)
        else:
            tech_reasons = f"評分{score}分未達標(需≥{params['buy_threshold']})"
        
        # 最終建議理由
        if action_reason:
            final_reason = f"{action_reason} [{tech_reasons}]"
        else:
            final_reason = tech_reasons

        return {
            "代碼": ticker, 
            "總分": score, 
            "現價": round(c_price, 2),
            "RSI": round(c_rsi, 1), 
            "建議動作": action, 
            "建議理由": final_reason,
            "持倉批數": len(holdings)
        }
    except Exception as e:
        return None

# --- UI 導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉"])

# 參數設定
st.sidebar.divider()
st.sidebar.header("⚙️ 交易策略參數")
max_e = st.sidebar.number_input("最大加碼次數", 1, 10, 5)
sl_pct = st.sidebar.slider("止損百分比 (%)", 5.0, 30.0, 10.0) / 100.0
pt_rsi = st.sidebar.slider("部分調節 RSI", 40, 70, 60)
ob_rsi = st.sidebar.slider("獲利清倉 RSI", 70, 95, 80)

# --- 頁面 1：選股 ---
if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 執行深度掃描"):
        all_list = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0, text="分批下載中...")
        
        batch_size = 50
        for i in range(0, len(all_list), batch_size):
            batch = all_list[i : i + batch_size]
            try:
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                for t in batch:
                    try:
                        t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                        if not t_df.empty:
                            last = t_df.iloc[-1]
                            val = (float(last['Close']) * float(last['Volume'])) / 1e8
                            res_rank.append({"股票代號": t, "收盤價": float(last['Close']), "成交值(億)": val})
                    except: continue
            except: pass
            p_bar.progress(min((i + batch_size) / len(all_list), 1.0))
            time.sleep(random.uniform(0.5, 1.0))
        
        if res_rank:
            top_100 = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)
            st.session_state.top_100_list = top_100['股票代號'].tolist()
            st.dataframe(top_100, use_container_width=True)

# --- 頁面 2：決策 ---
elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    
    # 詳細交易策略說明
    with st.expander("📖 **交易策略詳細說明**", expanded=False):
        st.markdown("""
        ### 🎯 **多因子評分系統**
        
        本系統採用 **四大技術指標** 進行綜合評分（滿分100分）：
        
        | 指標 | 觸發條件 | 配分 | 說明 |
        |------|---------|------|------|
        | **RSI 相對強弱** | RSI < 30 | 40分 | 判斷超賣區間，反轉機會高 |
        | **均線交叉** | MA5 黃金交叉 MA10 | 30分 | 短期趨勢向上突破 |
        | **價格波動** | 單日漲跌幅 ≥ 7% | 20分 | 捕捉異常波動機會 |
        | **成交爆量** | 當日量 > 平均量 2倍 | 10分 | 資金大量湧入訊號 |
        
        ---
        
        ### 📊 **買入/加碼策略**
        
        - **初次建倉**：總分 ≥ 30分 且無持倉 → 🟢 建議買入
        - **分批加碼**：總分 ≥ 30分 且持倉批數 < 最大加碼次數 → 🟢 建議加碼
        - **加碼上限**：系統會依據設定的「最大加碼次數」自動控制風險
        
        ---
        
        ### 🛡️ **風險控制機制**
        
        #### **止損條件** (優先級最高)
        - 當 **投資報酬率(ROI) ≤ -止損百分比** 時 → 🚨 **立即止損賣出**
        - 例如：設定止損 10%，持倉平均成本 100元，當價格跌至 90元以下觸發
        
        #### **獲利調節** (動態減倉)
        - 當 **RSI > 部分調節RSI** (預設60) → 🟠 **部分減倉鎖定利潤**
        - 適用於持倉已獲利但 RSI 尚未過熱
        
        #### **獲利清倉** (全數退出)
        - 當 **RSI > 獲利清倉RSI** (預設80) → 🔵 **全部清倉獲利了結**
        - 適用於極度超買區，避免獲利回吐
        
        ---
        
        ### ⚙️ **參數設定建議**
        
        - **保守型**：止損8%、加碼3次、部分調節RSI 55
        - **平衡型**：止損10%、加碼5次、部分調節RSI 60 (預設)
        - **積極型**：止損15%、加碼8次、部分調節RSI 65
        
        > ⚠️ **風險提示**：本策略為量化輔助工具，實際交易前請結合基本面分析與市場情緒判斷。
        """)
    
    st.divider()
    
    if 'top_100_list' not in st.session_state:
        st.warning("⚠️ 請先執行第一頁掃描以獲取股票池。")
    else:
        weights = {'rsi': 40, 'ma': 30, 'vol': 20, 'vxx': 10}
        params = {
            'max_entries': max_e, 'stop_loss_pct': sl_pct,
            'profit_take_rsi': pt_rsi, 'overbought_rsi': ob_rsi, 'buy_threshold': 30
        }
        
        signals = []
        p_check = st.progress(0, text="計算指標中...")
        for idx, t in enumerate(st.session_state.top_100_list):
            res = analyze_stock_advanced(t, weights, params)
            if res: signals.append(res)
            p_check.progress((idx + 1) / 100)
        
        if signals:
            # 顯示表格，包含建議理由欄位
            df_signals = pd.DataFrame(signals).sort_values("總分", ascending=False)
            st.dataframe(
                df_signals,
                use_container_width=True,
                column_config={
                    "建議理由": st.column_config.TextColumn(
                        "建議理由",
                        width="large",
                        help="系統分析後的詳細建議說明"
                    )
                }
            )
            
            # 手動記錄持倉
            st.divider()
            st.subheader("📝 手動記錄持倉")
            c1, c2 = st.columns(2)
            
            with c1: 
                t_in = st.text_input(
                    "輸入股票代碼", 
                    placeholder="例如：2330.TW",
                    help="請輸入完整股票代碼，例如：2330.TW 或 1101.TW"
                )
            with c2: 
                p_in = st.number_input(
                    "買入價格", 
                    value=0.0, 
                    min_value=0.0,
                    help="請輸入實際買入價格"
                )
            
            if st.button("➕ 更新持倉"):
                if t_in and p_in > 0:
                    if t_in not in st.session_state.portfolio: 
                        st.session_state.portfolio[t_in] = []
                    st.session_state.portfolio[t_in].append({
                        "price": p_in, 
                        "date": str(datetime.now().date())
                    })
                    save_portfolio(st.session_state.portfolio)
                    st.success(f"✅ 成功記錄持倉：{t_in} @ ${p_in}")
                    st.rerun()
                else:
                    st.error("❌ 請填寫有效的股票代碼和價格！")

    # --- 持倉顯示 ---
    st.divider()
    st.subheader("💼 我的持倉紀錄")
    p_summary = []
    for k, v in st.session_state.portfolio.items():
        if v:
            avg = sum([i['price'] for i in v])/len(v)
            p_summary.append({"代號": k, "持倉批數": len(v), "平均成本": round(avg, 2)})
    
    if p_summary:
        st.table(pd.DataFrame(p_summary))
        t_del = st.selectbox("移除標的", [d['代號'] for d in p_summary])
        if st.button("🗑️ 移除"):
            st.session_state.portfolio[t_del] = []
            save_portfolio(st.session_state.portfolio)
            st.success(f"✅ 已移除 {t_del}")
            st.rerun()
    else:
        st.info("📭 目前沒有持倉記錄")
