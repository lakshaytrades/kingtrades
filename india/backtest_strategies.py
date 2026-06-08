"""
backtest_strategies.py — Validate MULTIPLE alpha edges on real 15y data, build
improved exits, and COMBINE uncorrelated edges into a portfolio.

Honest goal: show the real path to higher win-rate + Sharpe (combining edges),
and measure how close that actually gets to the "1%/day, 60-70% WR" target.
All OUT-OF-SAMPLE (2020-2026). Daily-bar proxy of the live engine.
"""
import urllib.request, json, math, datetime as dt, statistics as st

NSE=["RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS","SBIN.NS","AXISBANK.NS",
     "LT.NS","ITC.NS","HINDUNILVR.NS","KOTAKBANK.NS","BHARTIARTL.NS","MARUTI.NS","SUNPHARMA.NS",
     "BAJFINANCE.NS","TITAN.NS","ASIANPAINT.NS","WIPRO.NS","TATASTEEL.NS"]
US=["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","JPM","V","UNH","HD","MA","XOM",
    "JNJ","WMT","PG","AVGO","COST","CRM","AMD"]
COST_RT=0.0010; START=500000.0; MAX_POS=6; OOS=dt.date(2020,1,1)
_C={}

def pull(s):
    if s in _C: return _C[s]
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=15y&interval=1d"
    try:
        r=urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=30)
        res=json.loads(r.read())["chart"]["result"][0];ts=res["timestamp"];q=res["indicators"]["quote"][0]
        b=[]
        for i in range(len(ts)):
            o,h,l,c,v=q["open"][i],q["high"][i],q["low"][i],q["close"][i],q["volume"][i]
            if None in (o,h,l,c,v):continue
            b.append({"d":dt.date.fromtimestamp(ts[i]),"o":o,"h":h,"l":l,"c":c,"v":v})
        _C[s]=b if len(b)>200 else None
    except Exception: _C[s]=None
    return _C[s]

def ema(v,s):
    k=2/(s+1);o=[v[0]]
    for x in v[1:]:o.append(x*k+o[-1]*(1-k))
    return o
def rsi(c,n=14):
    o=[50.]*len(c);ag=al=0
    for i in range(1,len(c)):
        ch=c[i]-c[i-1];g=max(ch,0);l=max(-ch,0)
        ag=(ag*(min(i,n)-1)+g)/min(i,n);al=(al*(min(i,n)-1)+l)/min(i,n)
        o[i]=100-100/(1+ag/al) if al>0 else 100.
    return o
def atr(b,n=14):
    tr=[b[0]["h"]-b[0]["l"]]
    for i in range(1,len(b)):tr.append(max(b[i]["h"]-b[i]["l"],abs(b[i]["h"]-b[i-1]["c"]),abs(b[i]["l"]-b[i-1]["c"])))
    a=tr[0];o=[a]
    for i in range(1,len(b)):a=(a*(n-1)+tr[i])/n if i>=n else (a*i+tr[i])/(i+1);o.append(a)
    return o
def adx(b,n=14):
    p=[0];m=[0];tr=[b[0]["h"]-b[0]["l"]]
    for i in range(1,len(b)):
        up=b[i]["h"]-b[i-1]["h"];dn=b[i-1]["l"]-b[i]["l"]
        p.append(up if(up>dn and up>0)else 0);m.append(dn if(dn>up and dn>0)else 0)
        tr.append(max(b[i]["h"]-b[i]["l"],abs(b[i]["h"]-b[i-1]["c"]),abs(b[i]["l"]-b[i-1]["c"])))
    def sm(x):
        s=x[0];o=[s]
        for i in range(1,len(x)):s=s-s/n+x[i];o.append(s)
        return o
    S,P,M=sm(tr),sm(p),sm(m);o=[0.]*len(b);dx=[]
    for i in range(len(b)):
        if S[i]>0:
            pd=100*P[i]/S[i];md=100*M[i]/S[i];d=100*abs(pd-md)/(pd+md) if(pd+md)>0 else 0;dx.append(d)
            o[i]=sum(dx[-n:])/min(len(dx),n)
    return o

def enrich(s):
    b=pull(s)
    if not b:return None
    c=[x["c"] for x in b];e9,e21,e50=ema(c,9),ema(c,21),ema(c,50);r=rsi(c);a=atr(b);ad=adx(b)
    for i,x in enumerate(b):
        x["e9"],x["e21"],x["e50"],x["rsi"],x["atr"],x["adx"],x["i"]=e9[i],e21[i],e50[i],r[i],a[i],ad[i],i
        win=b[max(0,i-20):i]
        x["hh"]=max(y["h"] for y in win) if win else x["h"]
        x["ll"]=min(y["l"] for y in win) if win else x["l"]
        x["vsma"]=sum(y["v"] for y in win)/len(win) if win else x["v"]
        x["sma20"]=sum(y["c"] for y in win)/len(win) if win else x["c"]
        sd=(st.pstdev([y["c"] for y in win]) if len(win)>1 else 0)
        x["bbl"]=x["sma20"]-2*sd; x["bbu"]=x["sma20"]+2*sd
    return b

# ── Strategy signal functions: (prev_bar)->bool LONG ──────────────────────────
def s_momentum(b):  # trend breakout
    return b["e9"]>b["e21"]>b["e50"] and 50<=b["rsi"]<=72 and b["c"]>=b["hh"]*0.999 and b["v"]>=1.5*b["vsma"] and b["adx"]>=20
def s_pullback(b):  # uptrend pullback to EMA21
    return b["e21"]>b["e50"] and b["c"]>b["e50"] and b["l"]<=b["e21"]*1.01 and 40<=b["rsi"]<=58 and b["adx"]>=18
def s_meanrev(b):   # oversold bounce (not in downtrend)
    return b["c"]<=b["bbl"] and b["rsi"]<=32 and b["c"]>b["e50"]*0.95
def s_breakout_vol(b):  # high-volume 20d-high breakout regardless of EMA stack
    return b["c"]>=b["hh"]*1.001 and b["v"]>=2.0*b["vsma"] and b["rsi"]<75

STRATS={"momentum":s_momentum,"pullback":s_pullback,"meanrev":s_meanrev,"breakout":s_breakout_vol}

def run(symbols, sigfns, P, d_from=OOS):
    """sigfns: dict name->fn (combined if >1). Returns stats + per-day equity."""
    data={s:e for s in symbols if (e:=enrich(s))}
    if not data: return None
    idx={s:{x["d"]:x for x in d} for s,d in data.items()}
    dates=sorted({x["d"] for d in data.values() for x in d if not d_from or x["d"]>=d_from})
    eq=START;peak=eq;mdd=0;op={};tr=[];curve=[]
    for day in dates:
        for s in list(op.keys()):
            b=idx[s].get(day)
            if not b:continue
            p=op[s];p["held"]+=1;ex=None
            if P["trail"]:
                # move stop to lock gains once past 1R
                if b["c"]>=p["entry"]+(p["entry"]-p["istop"]):
                    p["stop"]=max(p["stop"],b["c"]-P["sl"]*b["atr"])
            if b["l"]<=p["stop"]:ex=p["stop"]
            elif not p.get("part") and P["partial"] and b["h"]>=p["entry"]+(p["entry"]-p["istop"]):
                # take half off at +1R, move stop to breakeven, ride rest
                half=p["qty"]/2;g=half*(p["entry"]+(p["entry"]-p["istop"])-p["entry"]);eq+=g-COST_RT*half*p["entry"]
                p["qty"]-=half;p["stop"]=max(p["stop"],p["entry"]);p["part"]=True
            elif b["h"]>=p["tgt"]:ex=p["tgt"]
            elif p["held"]>=P["ts"]:ex=b["c"]
            if ex is not None:
                g=p["qty"]*(ex-p["entry"]);cost=COST_RT*p["qty"]*(p["entry"]+ex)/2;pnl=g-cost+p.get("booked",0);eq+=g-cost
                tr.append({"R":(p["realR"]+(pnl)/p["risk0"]) if p["risk0"]>0 else 0,"pnl":pnl+p.get("bookedpnl",0),"win":(g-cost+p.get("bookedpnl",0))>0,"y":day.year,"strat":p["strat"]})
                del op[s]
        if len(op)<MAX_POS:
            for s,d in data.items():
                if s in op or len(op)>=MAX_POS:continue
                pos=idx[s].get(day)
                if not pos or pos["i"]<60:continue
                sg=d[pos["i"]-1]
                for nm,fn in sigfns.items():
                    try: hit=fn(sg) and sg["atr"]>0
                    except Exception: hit=False
                    if hit:
                        entry=pos["o"];stop=entry-P["sl"]*sg["atr"];tgt=entry+P["tp"]*sg["atr"];rps=entry-stop
                        if rps<=0:break
                        qty=(P["risk"]*eq)/rps
                        op[s]={"entry":entry,"stop":stop,"istop":stop,"tgt":tgt,"qty":qty,"risk0":P["risk"]*eq,
                               "held":0,"strat":nm,"realR":0,"booked":0,"bookedpnl":0}
                        break
        peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak);curve.append((day,eq))
    n=len(tr)
    if n==0:return {"n":0}
    wins=[t for t in tr if t["win"]];los=[t for t in tr if not t["win"]]
    rets=[(curve[i][1]-curve[i-1][1])/curve[i-1][1] for i in range(1,len(curve)) if curve[i-1][1]>0]
    yrs=(dates[-1]-dates[0]).days/365.25
    pos_days=sum(1 for x in rets if x>0); day_wr=pos_days/len(rets)*100 if rets else 0
    return {"n":n,"wr":len(wins)/n*100,"exp":sum(t["R"] for t in tr)/n,
            "pf":(sum(t["pnl"] for t in wins)/abs(sum(t["pnl"] for t in los))) if los and sum(t["pnl"] for t in los)!=0 else 99,
            "cagr":((eq/START)**(1/yrs)-1)*100 if eq>0 else -100,"mdd":mdd*100,
            "sharpe":(st.mean(rets)/st.pstdev(rets)*math.sqrt(252)) if len(rets)>2 and st.pstdev(rets)>0 else 0,
            "avg_day":st.mean(rets)*100 if rets else 0,"day_wr":day_wr}

P_BASE={"risk":0.005,"sl":1.5,"tp":3.0,"ts":12,"trail":False,"partial":False}
P_BETTER={"risk":0.0075,"sl":1.5,"tp":5.0,"ts":20,"trail":True,"partial":True}

def show(t,r):
    if not r or r["n"]==0:print(f"  {t:26} no trades");return
    print(f"  {t:26} n={r['n']:4} WR={r['wr']:4.1f}% exp={r['exp']:+.3f}R PF={r['pf']:.2f} "
          f"Sharpe={r['sharpe']:.2f} CAGR={r['cagr']:+5.1f}% MDD={r['mdd']:4.1f}% "
          f"avgday={r['avg_day']:+.3f}% day-WR={r['day_wr']:.0f}%")

if __name__=="__main__":
    print("Loading real 15y data...\n")
    print("===== EACH EDGE, OUT-OF-SAMPLE (2020-2026), US market, base exits =====")
    survivors={}
    for nm,fn in STRATS.items():
        r=run(US,{nm:fn},P_BASE)
        show(nm,r)
        if r and r["n"]>=20 and r["pf"]>1.10: survivors[nm]=fn

    print("\n===== MOMENTUM: base exits vs IMPROVED exits (partial+trail) =====")
    show("momentum base",     run(US,{"momentum":s_momentum},P_BASE))
    show("momentum improved",  run(US,{"momentum":s_momentum},P_BETTER))

    print(f"\n===== COMBINED PORTFOLIO of {len(survivors)} positive-edge edges (US, improved exits) =====")
    show("combined US", run(US,survivors,P_BETTER))
    print("  --- same combined edges on NSE ---")
    show("combined NSE", run(NSE,survivors,P_BETTER))

    print("\n  ⚠ OOS, daily-bar proxy. 'avgday'/'day-WR' = real daily-return profile.")
    print("    Target was 1%/day @ 60-70% WR — compare 'avgday' & 'day-WR' to that.")
