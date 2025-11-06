# main.py — Süper Bot (MEXC + Binance, 15m/1h/4h/1d)
import os, time, requests, pandas as pd, numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========= Ayarlar / ENV =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID        = os.getenv("CHAT_ID")

# Coin evreni
SCAN_LIMIT_MEXC    = int(os.getenv("SCAN_LIMIT_MEXC", "200"))     # MEXC spot USDT ilk N
SCAN_LIMIT_BINANCE = int(os.getenv("SCAN_LIMIT_BINANCE", "200"))  # Binance spot USDT ilk N

# Zaman dilimleri
TIMEFRAMES = os.getenv("TIMEFRAMES", "15m,1h,4h,1d").split(",")

# Filtreler (default “mantıklı, ama kaçırmasın”):
MIN_TURNOVER      = float(os.getenv("MIN_TURNOVER", "100000"))  # binance/mexc spot quoteVolume eşiği
VOL_RATIO_BUY     = float(os.getenv("VOL_RATIO_BUY", "1.10"))   # buy için hacim oranı
VOL_RATIO_SELL    = float(os.getenv("VOL_RATIO_SELL", "0.90"))  # sell için hacim zayıflığı
RSI_BUY_MIN       = float(os.getenv("RSI_BUY_MIN", "50.0"))
RSI_SELL_MAX      = float(os.getenv("RSI_SELL_MAX", "60.0"))

# Piyasa koruma (BUY bastırma): BTC ve Total2 kötü ise BUY’ları durdur
MARKET_GUARD      = os.getenv("MARKET_GUARD", "true").lower() == "true"
# Sinyal sayısı limiti (spam engeli)
MAX_LINES_PER_SIDE = int(os.getenv("MAX_LINES_PER_SIDE", "15"))

# ========= API uçları =========
MEXC      = "https://api.mexc.com"
BINANCE   = "https://api.binance.com"
COINGECKO = "https://api.coingecko.com/api/v3/global"

def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# -------- HTTP ----------
def jget(url, params=None, retries=3, timeout=10):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.35)
    return None

def telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15
        )
    except: pass

# -------- Göstergeler ----------
def ema(x,n): return x.ewm(span=n, adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    rs=up.ewm(alpha=1/n, adjust=False).mean()/(dn.ewm(alpha=1/n, adjust=False).mean()+1e-12)
    return 100-(100/(1+rs))
def adx(df,n=14):
    up=df['high'].diff(); dn=-df['low'].diff()
    plus=np.where((up>dn)&(up>0),up,0.0); minus=np.where((dn>up)&(dn>0),dn,0.0)
    tr1=df['high']-df['low']; tr2=(df['high']-df['close'].shift()).abs(); tr3=(df['low']-df['close'].shift()).abs()
    tr=pd.DataFrame({'a':tr1,'b':tr2,'c':tr3}).max(axis=1)
    atr=tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di=100*pd.Series(plus).ewm(alpha=1/n, adjust=False).mean()/(atr+1e-12)
    minus_di=100*pd.Series(minus).ewm(alpha=1/n, adjust=False).mean()/(atr+1e-12)
    dx=((plus_di-minus_di).abs()/((plus_di+minus_di)+1e-12))*100
    return dx.ewm(alpha=1/n, adjust=False).mean()

def volume_ratio(turnover, n=10):
    base = turnover.ewm(span=n, adjust=False).mean()
    return float(turnover.iloc[-1] / (base.iloc[-2] + 1e-12))

# -------- Coin listesi ----------
def mexc_spot_symbols(limit=200):
    d=jget(f"{MEXC}/api/v3/ticker/24hr")
    if not d: return []
    rows=[x for x in d if x.get("symbol","").endswith("USDT")]
    rows.sort(key=lambda x: float(x.get("quoteVolume","0")), reverse=True)
    return [x["symbol"] for x in rows[:limit]]

def binance_spot_symbols(limit=200):
    d=jget(f"{BINANCE}/api/v3/ticker/24hr")
    if not d: return []
    rows=[x for x in d if x.get("symbol","").endswith("USDT")]
    rows.sort(key=lambda x: float(x.get("quoteVolume","0")), reverse=True)
    return [x["symbol"] for x in rows[:limit]]

# -------- Kline ----------
def klines_mexc(sym, interval="1h", limit=200):
    d=jget(f"{MEXC}/api/v3/klines", {"symbol":sym, "interval":interval, "limit":limit})
    if not d: return None
    try:
        df=pd.DataFrame(d, columns=["t","o","h","l","c","v","qv","n","t1","t2","ig","ib"]).astype(float)
        df.rename(columns={"c":"close","h":"high","l":"low","qv":"turnover"}, inplace=True)
        return df[["close","high","low","turnover"]]
    except: return None

def klines_binance(sym, interval="1h", limit=200):
    d=jget(f"{BINANCE}/api/v3/klines", {"symbol":sym, "interval":interval, "limit":limit})
    if not d: return None
    try:
        df=pd.DataFrame(d, columns=[
            "ot","o","h","l","c","v","ct","qv","tr","tb","tq","ig"
        ]).astype(float)
        df.rename(columns={"c":"close","h":"high","l":"low","qv":"turnover"}, inplace=True)
        return df[["close","high","low","turnover"]]
    except: return None

# -------- Piyasa notu / guard ----------
def market_note_and_guard():
    # Coingecko Total2 & USDT.D
    total_pct = 0.0; usdt_d = None; btcd=None
    g=jget(COINGECKO)
    if g and "data" in g:
        try:
            total_pct = float(g["data"]["market_cap_change_percentage_24h_usd"])  # total market
            usdt_d    = float(g["data"]["market_cap_percentage"].get("usdt",0.0))
            btcd      = float(g["data"]["market_cap_percentage"].get("btc",0.0))
        except: pass

    # BTC 1h ve 4h eğilim
    def coin_state(symbol, interval):
        d=jget(f"{BINANCE}/api/v3/klines",{"symbol":symbol,"interval":interval,"limit":200})
        if not d: return "NÖTR"
        df=pd.DataFrame(d,columns=["t","o","h","l","c","v","ct","a","b","c2","d","e"]).astype(float)
        c=df['c']; e20,e50=ema(c,20).iloc[-1], ema(c,50).iloc[-1]; rr=rsi(c,14).iloc[-1]
        if e20>e50 and rr>50: return "GÜÇLÜ"
        if e20<e50 and rr<50: return "ZAYIF"
        return "NÖTR"

    btc1 = coin_state("BTCUSDT","1h")
    btc4 = coin_state("BTCUSDT","4h")

    # Guard: BTC(1h/4h) ikisi de ZAYIF ve Total2 <=0 ise BUY bastır
    buy_blocked = False
    if MARKET_GUARD and btc1=="ZAYIF" and btc4=="ZAYIF" and total_pct <= 0:
        buy_blocked = True

    t2 = "↑ (Altlara giriş)" if total_pct > 0 else ("↓ (Çıkış)" if total_pct < 0 else "→ (Karışık)")
    usdt_note = f"{usdt_d:.1f}%" if usdt_d is not None else "?"
    if usdt_d is not None:
        if usdt_d>=7: usdt_note += " (riskten kaçış)"
        elif usdt_d<=5: usdt_note += " (risk alımı)"
    note = f"Piyasa: BTC(1H) {btc1} | BTC(4H) {btc4} | BTC.D {btcd if btcd is not None else '?'}% | Total2: {t2} | USDT.D: {usdt_note}"
    return note, buy_blocked

# -------- Analiz ----------
def analyze_one(source, symbol, interval):
    df = klines_mexc(symbol, interval, 200) if source=="MEXC" else klines_binance(symbol, interval, 200)
    if df is None or len(df) < 60: return None

    # likidite tabanı
    if float(df["turnover"].iloc[-1]) < MIN_TURNOVER: return None

    c,h,l,t = df["close"], df["high"], df["low"], df["turnover"]
    rr = float(rsi(c).iloc[-1]); e20=float(ema(c,20).iloc[-1]); e50=float(ema(c,50).iloc[-1])
    trend_up = e20 > e50
    ratio = volume_ratio(t)
    a = float(adx(pd.DataFrame({"high":h,"low":l,"close":c}),14).iloc[-1])

    side = None
    if trend_up and rr > RSI_BUY_MIN and ratio >= VOL_RATIO_BUY:
        side="BUY"
    elif (not trend_up) and rr < RSI_SELL_MAX and ratio <= VOL_RATIO_SELL:
        side="SELL"
    else:
        return None

    # Güven puanı (0-100)
    conf = int(min(100, (ratio*25) + (a/3) + (rr/5)))

    return {
        "ex": source,
        "symbol": symbol,
        "tf": interval.upper(),
        "side": side,
        "ratio": ratio,
        "rsi": rr,
        "adx": a,
        "trend": "↑" if trend_up else "↓",
        "conf": conf
    }

def main():
    note, buy_blocked = market_note_and_guard()

    # Coin evreni (MEXC + Binance) -> birleşik set
    mexc_syms = mexc_spot_symbols(SCAN_LIMIT_MEXC)
    bin_syms  = binance_spot_symbols(SCAN_LIMIT_BINANCE)
    sources = [("MEXC", s) for s in mexc_syms] + [("BINANCE", s) for s in bin_syms]

    if not sources:
        telegram("⛔ Hiç sembol alınamadı (MEXC & Binance).")
        return

    start=time.time()
    results=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures=[ex.submit(analyze_one, src, sym, tf) for (src,sym) in sources for tf in TIMEFRAMES]
        for f in as_completed(futures):
            try:
                r=f.result()
                if r: results.append(r)
            except: pass

    # Guard uygula (BUY’ları bastır)
    if buy_blocked:
        results = [x for x in results if x["side"]!="BUY"]

    buys  = [x for x in results if x["side"]=="BUY"]
    sells = [x for x in results if x["side"]=="SELL"]

    # Güven ortalaması
    conf_vals=[x["conf"] for x in results]
    conf_avg = int(sum(conf_vals)/max(1,len(conf_vals)))

    # Mesaj
    lines = [
        "⚡ *Süper Bot — MEXC+Binance 15m/1h/4h/1d*",
        f"⏱ {ts()} | ⏳ Süre: {int(time.time()-start)} sn",
        f"🌐 MEXC:{len(mexc_syms)} | BINANCE:{len(bin_syms)} | Toplam: {len(sources)} parite",
        f"🛡️ Güven Ort.: {conf_avg}/100",
        f"🧭 Guard: {'AKTİF — BUY bastırılıyor' if buy_blocked else 'PASİF'}",
        note
    ]

    if buys or sells:
        lines.append("\n📈 *Sinyaller*")
        if buys:
            lines.append("🟢 *BUY:*")
            for x in sorted(buys, key=lambda z:z["conf"], reverse=True)[:MAX_LINES_PER_SIDE]:
                lines.append(f"- [{x['ex']}] {x['symbol']} | {x['tf']} | Güven:{x['conf']} | RSI:{x['rsi']:.1f} | ADX:{x['adx']:.0f} | Vol x{ x['ratio']:.2f }")
        if sells:
            lines.append("🔴 *SELL:*")
            for x in sorted(sells, key=lambda z:z["conf"], reverse=True)[:MAX_LINES_PER_SIDE]:
                lines.append(f"- [{x['ex']}] {x['symbol']} | {x['tf']} | Güven:{x['conf']} | RSI:{x['rsi']:.1f} | ADX:{x['adx']:.0f} | Vol x{ x['ratio']:.2f }")
    else:
        lines.append("\nℹ️ Kriterlere uyan sinyal yok.")

    telegram("\n".join(lines))

if __name__ == "__main__":
    main()
