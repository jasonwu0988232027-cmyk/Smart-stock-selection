import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import random
import requests
import urllib3
import json
import os
from datetime import datetime, timedelta

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. 回測核心類別 (由 stock_analyze(1).py 移植) ---
class RSITradingStrategy:
    def __init__(self, df, rsi_period=14, oversold=20, overbought=80, 
                 profit_take_rsi=60, profit_take_pct=0.3, 
                 initial_capital=1000000, entry_pct=0.1,
                 buy_fee_rate=0.001425, sell_fee_rate=0.004425,
                 max_entries=5, stop_loss_pct=0.10):
        self.df = df.copy()
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.profit_take_rsi = profit_take_rsi
        self.profit_take_pct = profit_take_pct
        self.initial_capital = initial_capital
        self.entry_pct = entry_pct
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.max_entries = max_entries
        self.stop_loss_pct = stop_loss_pct
        self.trades = []

    def calculate_indicators(self):
        if isinstance(self.df.columns, pd.MultiIndex):
            self.df.columns = self.df.columns.get_level_values(0)
        delta = self.df['Close'].diff()
        gain = (delta.where(delta > 0, 0))
        loss = (-delta.where(delta < 0, 0))
        avg_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss
        self.df['RSI'] = 100 - (100 / (1 + rs))
        self.df.dropna(inplace=True)

    def run_backtest(self):
        self.trades = []
        cash = self.initial_capital
        holdings = 0
        inventory = [] 
        equity_curve = [] 
        actions = [] 
        entry_amount = self.initial_capital * self.entry_pct

        for index, row in self.df.iterrows():
            rsi_val = row['RSI']
            price = row['Close']
            action_code = 0
            
            # 止損檢查
            if holdings > 0:
                total_cost_basis = sum(item['shares'] * item['price'] for item in inventory)
                avg_cost = total_cost_basis / holdings
                if (price - avg_cost) / avg_cost <= -self.stop_loss_pct:
                    revenue = holdings * price * (1 - self.sell_fee_rate)
                    cash += revenue
                    self.trades.append({'類型': '止損賣出', '時間': index, '買入時間': inventory[-1]['time'], '股數': holdings, '買入均價': avg_cost, '賣出價格': price, '獲利金額': revenue - total_cost_basis*(1+self.buy_fee_rate), '報酬率': (price-avg_cost)/avg_cost})
                    holdings, inventory, action_code = 0, [], -2

            if action_code != -2:
                if rsi_val > self.overbought and holdings > 0:
                    revenue = holdings * price * (1 - self.sell_fee_rate)
                    cash += revenue
                    holdings, inventory, action_code = 0, [], -1
                elif rsi_val > self.profit_take_rsi and len(inventory) > 1:
                    batches_to_sell = max(1, int(round(len(inventory) * self.profit_take_pct)))
                    if batches_to_sell < len(inventory):
                        sold_shares = 0
                        for _ in range(batches_to_sell):
                            batch = inventory.pop(0)
                            sold_shares += batch['shares']
                        cash += sold_shares * price * (1 - self.sell_fee_rate)
                        holdings -= sold_shares
                        action_code = 2
                elif rsi_val < self.oversold and len(inventory) < self.max_entries:
                    shares = int(min(entry_amount, cash) / (price * (1 + self.buy_fee_rate)))
                    if shares > 0:
                        cash -= shares * price * (1 + self.buy_fee_rate)
                        holdings += shares
                        inventory.append({'shares': shares, 'price': price, 'time': index})
                        action_code = 1
            
            actions.append(action_code)
            equity_curve.append(cash + (holdings * price))

        self.df['Action'] = actions
        if not self.trades: return 0, 0, 0, 0, 0, equity_curve[-1]
        
        final_equity = equity_curve[-1]
        total_return = (final_equity - self.initial_capital) / self.initial_capital
        equity_series = pd.Series(equity_curve)
        mdd = ((equity_series - equity_series.cummax()) / equity_series.cummax()).min()
        win_rate = len([t for t in self.trades if t['報酬率'] > 0]) / len(self.trades)
        return total_return, 0, win_rate, mdd, 0, final_equity

    def plot_results(self, ticker):
        plot_df = self.df.copy()
        plot_df['DateStr'] = plot_df.index.strftime('%Y-%m-%d %H:%M')
        plot_df['x_index'] = np.arange(len(plot_df))
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
        fig.add_trace(go.Candlestick(x=plot_df['x_index'], open=plot_df['Open'], high=plot_df['High'], low=plot_df['Low'], close=plot_df['Close'], name='K線'), row=1, col=1)
        
        # 買賣標記
        for act, sym, col, nm in [(1, 'triangle-up', 'green', '買入'), (2, 'circle', 'orange', '調節'), (-1, 'triangle-down', 'blue', '清倉'), (-2, 'x', 'purple', '止損')]:
            sigs = plot_df[plot_df['Action'] == act]
            fig.add_trace(go.Scatter(x=sigs['x_index'], y=sigs['Close'], mode='markers', marker=dict(symbol=sym, size=10, color=col), name=nm), row=1, col=1)
        
        fig.add_trace(go.Scatter(x=plot_df['x_index'], y=plot_df['RSI'], name='RSI', line=dict(color='purple')), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        return fig

# --- 2. 持倉管理與選股邏輯 (來自 stock_analyze.py) ---
DB_FILE = "portfolio.json"
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

def analyze_stock_advanced(ticker, weights, params):
    try:
        df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['MA5'] = ta.sma(df['Close'], length=5)
        df['MA10'] = ta.sma(df['Close'], length=10)
        curr, prev = df.iloc[-1], df.iloc[-2]
        score = 0
        if float(curr['RSI']) < 30: score += weights['rsi']
        if float(prev['MA5']) < float(prev['MA10']) and float(curr['MA5']) > float(curr['MA10']): score += weights['ma']
        
        holdings = st.session_state.portfolio.get(ticker, [])
        action = "觀望"
        if holdings:
            avg_cost = sum([h['price'] for h in holdings]) / len(holdings)
            roi = (float(curr['Close']) - avg_cost) / avg_cost
            if roi <= -params['stop_loss_pct']: action = "🚨 止損賣出"
            elif float(curr['RSI']) > params['profit_take_rsi']: action = "🟠 部分調節"
        elif score >= params['buy_threshold']: action = "🟢 建議買入"

        return {"代碼": ticker, "總分": score, "現價": round(float(curr['Close']), 2), "RSI": round(float(curr['RSI']), 1), "建議動作": action, "持倉批數": len(holdings)}
    except: return None

# --- 3. UI 導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉", "3. 策略參數回測優化"])

# --- 頁面 1 & 2 保持原有邏輯 ---
if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 執行深度掃描"):
        all_list = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0)
        batch_size = 50
        for i in range(0, len(all_list), batch_size):
            batch = all_list[i : i + batch_size]
            data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
            for t in batch:
                try:
                    t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                    if not t_df.empty:
                        last = t_df.iloc[-1]
                        val = (float(last['Close']) * float(last['Volume'])) / 1e8
                        res_rank.append({"股票代號": t, "收盤價": float(last['Close']), "成交值(億)": val})
                except: continue
            p_bar.progress(min((i + batch_size) / len(all_list), 1.0))
        if res_rank:
            top_100 = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)
            st.session_state.top_100_list = top_100['股票代號'].tolist()
            st.dataframe(top_100, use_container_width=True)

elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    if 'top_100_list' not in st.session_state: st.warning("請先執行第一頁掃描。")
    else:
        weights = {'rsi': 40, 'ma': 30, 'vol': 20, 'vxx': 10}
        params = {'max_entries': 5, 'stop_loss_pct': 0.1, 'profit_take_rsi': 60, 'overbought_rsi': 80, 'buy_threshold': 30}
        signals = []
        p_check = st.progress(0)
        for idx, t in enumerate(st.session_state.top_100_list):
            res = analyze_stock_advanced(t, weights, params)
            if res: signals.append(res)
            p_check.progress((idx + 1) / len(st.session_state.top_100_list))
        if signals:
            df_sig = pd.DataFrame(signals).sort_values("總分", ascending=False)
            st.dataframe(df_sig, use_container_width=True)
            st.divider()
            c1, c2 = st.columns(2)
            with c1: t_in = st.selectbox("選股代號", [s['代碼'] for s in signals])
            with c2: p_in = st.number_input("買入價格", value=0.0)
            if st.button("➕ 更新持倉"):
                if t_in not in st.session_state.portfolio: st.session_state.portfolio[t_in] = []
                st.session_state.portfolio[t_in].append({"price": p_in, "date": str(datetime.now().date())})
                save_portfolio(st.session_state.portfolio)
                st.rerun()

# --- 頁面 3：回測功能 (新增，對應圖片功能) ---
elif page == "3. 策略參數回測優化":
    st.title("📈 策略參數回測與調節優化")
    
    # 選取區
    col_t1, col_t2 = st.columns([1, 1])
    held_tickers = list(st.session_state.portfolio.keys())
    with col_t1:
        ticker_sel = st.selectbox("快捷選取持倉股票", ["手動輸入"] + held_tickers)
    with col_t2:
        manual_ticker = st.text_input("或手動輸入股票代碼 (例: 2330.TW)", value="2330.TW" if ticker_sel == "手動輸入" else ticker_sel)
    
    st.divider()
    
    # 參數設定區 (對應 image_d02f0a.png)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("📊 RSI 設定")
        r_period = st.slider("RSI 週期", 5, 30, 14)
        r_buy = st.number_input("超賣買入線", value=20)
        r_sell = st.number_input("超買清倉線", value=80)
    with c2:
        st.subheader("⚙️ 調節與止損")
        r_pt_rsi = st.slider("部分調節 RSI 門檻", 40, 75, 60)
        r_pt_pct = st.slider("調節比例 (%)", 10, 90, 30) / 100.0
        r_sl = st.slider("強制止損 (%)", 5.0, 30.0, 10.0) / 100.0
    with c3:
        st.subheader("💰 資金管理")
        init_cap = st.number_input("初始資金", value=1000000)
        max_e = st.number_input("最大加碼次數", 1, 10, 5)
        test_days = st.selectbox("回測時間範圍 (天)", [90, 180, 365, 730], index=1)

    if st.button("🚀 執行精密回測", type="primary"):
        with st.spinner("正在下載數據並執行分析..."):
            raw_df = yf.download(manual_ticker, start=datetime.now()-timedelta(days=test_days), interval="1h", progress=False)
            if raw_df.empty:
                st.error("無法下載數據。")
            else:
                strategy = RSITradingStrategy(raw_df, rsi_period=r_period, oversold=r_buy, overbought=r_sell, profit_take_rsi=r_pt_rsi, profit_take_pct=r_pt_pct, initial_capital=init_cap, max_entries=max_e, stop_loss_pct=r_sl)
                strategy.calculate_indicators()
                ret, _, win, mdd, _, final_e = strategy.run_backtest()
                
                # 績效摘要
                st.subheader("📊 帳戶績效總覽")
                m1, m2, m3 = st.columns(3)
                m1.metric("期末淨值", f"${final_e:,.0f}", delta=f"${final_e-init_cap:,.0f}")
                m2.metric("累積報酬率", f"{ret:.2%}")
                m3.metric("最大回撤 (MDD)", f"{mdd:.2%}", delta_color="inverse")
                
                # 圖表顯示
                st.plotly_chart(strategy.plot_results(manual_ticker), use_container_width=True)
