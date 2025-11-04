import os, time, requests, pandas as pd, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# === Ayarlar ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")
BINANCE        = "https://api.binance.com"
COINGECKO      = "https://api.coingecko.com/api/v3/global"

def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# --- HTTP yardımcı ---
def jget(url, params=None, retries=2, timeout=5):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
        except: time.sleep(0.25)
    return None

def telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID: 
        print(text); return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})
    except: pass

# --- İndikatörler ---
def ema(x,n): return x.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / (dn.ewm(alpha=1/n, adjust=False).mean() + 1e-12)
    return 100 - (100/(1+rs))
def adx(df, n=14):
    up = df['high'].diff(); dn = -df['low'].diff()
    plus  = np.where((up>dn)&(up>0), up, 0.0)
    minus = np.where((dn>up)&(dn>0), dn, 0.0)
    tr = pd.DataFrame({
        'a': df['high']-df['low'],
        'b': (df['high']-df['close'].shift()).abs(),
        'c': (df['low']-df['close'].shift()).abs()
    }).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di  = 100*pd.Series(plus).ewm(alpha=1/n, adjust=False).mean()  / (atr+1e-12)
    minus_di = 100*pd.Series(minus).ewm(alpha=1/n, adjust=False).mean() / (atr+1e-12)
    dx = ((plus_di - minus_di).abs() / ((plus_di + minus_di)+1e-12)) * 100
    return dx.ewm(alpha=1/n, adjust=False).mean()

def volume_spike(turnover, n=10, r_buy=1.10, r_sell=0.90):
    base = turnover.ewm(span=n, adjust=False).mean()
    ratio = float(turnover.iloc[-1] / (base.iloc[-2] + 1e-12))
    return ratio, (ratio >= r_buy), (ratio <= r_sell)

# --- Veri kaynakları ---
def binance_top_symbols(limit=120):
    t = jget(f"{BINANCE}/api/v3/ticker/24hr")
    if not t: return []
    rows = [x for x in t if x.get("symbol","").endswith("USDT")]
    rows.sort(key=lambda x: float(x.get("quoteVolume","0")), reverse=True)
    return [x["symbol"] for x in rows[:limit]]

def klines(symbol, interval="1h", limit=120):
    d = jget(f"{BINANCE}/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not d: return None
    try:
        df = pd.DataFrame(d, columns=[
            "open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_base","taker_quote","ignore"
        ])
        df = df.astype({"open":"float64","high":"float64","low":"float64","close":"float64",
                        "volume":"float64","quote_volume":"float64"})
        df.rename(columns={"close":"c","high":"high","low":"low"}, inplace=True)
        df["turnover"] = df["quote_volume"]
        return df[["c","high","low","turnover"]]
    except: return None

def market_note():
    g = jget(COINGECKO)
    btcd = usdt_d = None; total_pct = None
    try:
        total_pct = float(g["data"]["market_cap_change_percentage_24h_usd"])
        btcd = float(g["data"]["market_cap_percentage"]["btc"])
        usdt_d = float(g["data"]["market_cap_percentage"].get("usdt", 0.0))
    except: pass
    btc24 = jget(f"{BINANCE}/api/v3/ticker/24hr", {"symbol":"BTCUSDT"})
    btc_pct = float(btc24["priceChangePercent"]) if btc24 and "priceChangePercent" in btc24 else None
    arrow = "→"
    if btc_pct is not None and total_pct is not None:
        arrow = "↑" if btc_pct > total_pct else ("↓" if btc_pct < total_pct else "→")
    dirb = "→"
    if btc_pct is not None:
        dirb = "↑" if btc_pct>0 else ("↓" if btc_pct<0 else "→")
    t2 = "→ (Karışık)"
    if arrow=="↓" and (total_pct is not None and total_pct>=0): t2="↑ (Altlara giriş)"
    if arrow=="↑" and (total_pct is not None and total_pct<=0): t2="↓ (Çıkış)"
    usdt_note = f"{usdt_d:.1f}%" if usdt_d is not None else "?"
    if usdt_d is not None:
        if usdt_d>=7: usdt_note += " (riskten kaçış)"
        elif usdt_d<=5: usdt_note += " (risk alımı)"
    btcd_note = f"{btcd:.1f}%" if btcd is not None else "?"
    return f"Piyasa: BTC {dirb} + BTC.D {arrow} (BTC.D {btcd_note}) | Total2: {t2} | USDT.D: {usdt_note}"

# --- Analiz ---
def analyze_one(symbol, interval):
    df = klines(symbol, interval, 120)
    if df is None or len(df) < 60: return None
    if df["turnover"].iloc[-1] < 200_000: return None
    c, h, l, t = df["c"], df["high"], df["low"], df["turnover"]
    rr = float(rsi(c).iloc[-1]); e20 = float(ema(c,20).iloc[-1]); e50 = float(ema(c,50).iloc[-1])
    trend_up = e20 > e50
    ratio, v_buy, v_sell = volume_spike(t)

    # --- BUY (güvenli) ---
    if trend_up and rr > 50 and v_buy:
        a = float(adx(pd.DataFrame({"high":h,"low":l,"close":c}),14).iloc[-1])
        return f"{symbol} | {interval.upper()} | BUY | RSI:{rr:.1f} | ADX:{a:.0f} | Hacim x{ratio:.2f}"

    # --- SELL (gevşetilmiş) ---
    if (not trend_up) and rr < 60 and v_sell:
        a = float(adx(pd.DataFrame({"high":h,"low":l,"close":c}),14).iloc[-1])
        return f"{symbol} | {interval.upper()} | SELL | RSI:{rr:.1f} | ADX:{a:.0f} | Hacim x{ratio:.2f}"

    return None

# --- Ana akış ---
def main():
    symbols = binance_top_symbols(limit=120)
    if not symbols:
        telegram("⛔ Sembol alınamadı (Binance)."); return

    timeframes = ["15m","1h","4h","1d"]
    signals = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(analyze_one, s, tf) for s in symbols for tf in timeframes]
        for f in as_completed(futures):
            try:
                r = f.result()
                if r: signals.append(r)
            except: pass

    if signals:
        note = market_note()
        buys  = [x for x in signals if " | BUY | "  in x]
        sells = [x for x in signals if " | SELL | " in x]
        head = f"⚡ *Çoklu Zaman Dilimi Sinyalleri*\n⏱ {ts()}\n{note}\n🟢 BUY:{len(buys)} | 🔴 SELL:{len(sells)} | Toplam:{len(signals)}\n"
        body = "\n".join(signals[:70])
        telegram(head + "\n" + body)
    else:
        print("ℹ️ sinyal yok (sessiz).")

if __name__ == "__main__":
    main()
