"""
watchlist_india.py — NSE liquid stock watchlist
~380 curated NSE stocks: Nifty 50 + Next 50 + Midcap 100 top picks + Smallcap select
filtered for liquidity, momentum, and Upstox/Groww API availability.
"""
import logging
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("watchlist_india")
IST = ZoneInfo("Asia/Kolkata")

# ── ~380-stock NSE universe ──────────────────────────────────────────────────
# Excluded: ADANIENT/ADANIPORTS (promoter manipulation/0% WR),
#           M&MFIN (broken ticker), SHREECEM (low vol), PAYTM (speculative),
#           HDFC (merged into HDFCBANK), YESBANK (too speculative)
_CORE_WATCHLIST: List[str] = [

    # ── T1: Ultra-liquid Nifty 50 heavyweights ─────────────────────────────
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN",     "AXISBANK",  "KOTAKBANK", "BAJFINANCE", "BHARTIARTL",
    "LT",       "ITC",       "TATASTEEL", "MARUTI",     "HCLTECH",
    "WIPRO",    "JSWSTEEL",  "M&M",       "BAJAJFINSV", "ZOMATO",
    "INDUSINDBK", "NTPC",   "TRENT",     "PERSISTENT", "POLYCAB",

    # ── T1 additions: Nifty 50 gap ────
    "TATAMOTORS",

    # ── T2: Nifty 50 remainder + top Nifty Next 50 ─────────────────────────
    "HINDUNILVR", "ASIANPAINT", "TITAN",     "ULTRACEMCO", "NESTLEIND",
    "TECHM",      "SUNPHARMA",  "POWERGRID", "ONGC",       "COALINDIA",
    "BPCL",       "GRASIM",     "DIVISLAB",  "CIPLA",      "EICHERMOT",
    "HEROMOTOCO", "TATACONSUM", "BRITANNIA", "APOLLOHOSP", "HDFCLIFE",
    "SBILIFE",    "PIDILITIND", "DRREDDY",   "SHRIRAMFIN", "NAUKRI",
    "IRCTC",      "DMART",      "HAVELLS",   "SIEMENS",    "ABB",
    "VOLTAS",     "LTIM",       "MPHASIS",   "BAJAJ-AUTO", "BALKRISIND",
    "TATAPOWER",  "HINDPETRO",  "IOC",       "PFC",        "RECLTD",
    "IDFCFIRSTB", "BANKBARODA", "CANBK",     "PNB",        "FEDERALBNK",
    "MUTHOOTFIN", "CHOLAFIN",   "GODREJCP",  "DABUR",      "CROMPTON",

    # ── T2 additions ────
    "GAIL", "BANDHANBNK", "HINDZINC", "ADANIGREEN", "SUZLON",
    "ANGELONE", "DIXON", "MACROTECH", "IRCON", "RITES",
    "SOBHA", "KARURVYSYA", "GODREJIND", "PHOENIXLTD", "ICICIGI",

    # ── T3: IT / Software ──────────────────────────────────────────────────
    "COFORGE",    "LTTS",       "TATAELXSI", "CYIENT",     "KPITTECH",
    "OFSS",       "ZENSARTECH",

    # ── T3: Banking & Finance ──────────────────────────────────────────────
    "AUBANK",     "SBICARD",    "MANAPPURAM", "LICHSGFIN", "CANFINHOME",
    "ABCAPITAL",  "IIFL",       "PNBHOUSING", "RBLBANK",   "EQUITASBNK",
    "UJJIVAN",    "MFSL",       "POLICYBZR",  "NYKAA",     "STARHEALTH",

    # ── T3: Auto & Ancillaries ─────────────────────────────────────────────
    "TVSMOTORS",  "MOTHERSON",  "ESCORTS",   "BOSCHLTD",  "BHARATFORG",
    "TIINDIA",    "CUMMINSIND",

    # ── T3: Pharma & Healthcare ────────────────────────────────────────────
    "LUPIN",      "AUROPHARMA", "ALKEM",     "TORNTPHARM", "SYNGENE",
    "LAURUSLABS", "GRANULES",   "BIOCON",    "LALPATHLAB", "IPCA",
    "NATCOPHARM", "GLENMARK",   "AJANTPHARM",

    # ── T3: FMCG & Consumer ────────────────────────────────────────────────
    "COLPAL",     "MARICO",     "EMAMILTD",  "BERGEPAINT", "KANSAIPO",
    "PAGEIND",    "VBL",        "UBL",       "ABFRL",      "MCDOWELL-N",
    "ZYDUSLIFE",  "JYOTHYLAB",  "JUBLFOOD",  "BIKAJI",

    # ── T3: Capital Goods, Defence & Industrials ───────────────────────────
    "HAL",        "BEL",        "BHEL",      "KEC",        "ENGINERSIN",
    "THERMAX",    "SCHAEFFLER", "SKFINDIA",  "CERA",       "KAJARIA",
    "BDL",        "MAZDOCK",    "NBCC",

    # ── T3: Real Estate ────────────────────────────────────────────────────
    "DLF",        "GODREJPROP", "PRESTIGE",  "OBEROIRLTY", "BRIGADE",

    # ── T3: Cement ─────────────────────────────────────────────────────────
    "AMBUJACEM",  "ACC",        "JKCEMENT",  "RAMCOCEM",   "DALBHARAT",

    # ── T3: Metals & Mining ────────────────────────────────────────────────
    "HINDALCO",   "VEDL",       "NMDC",      "JINDALSTEL", "SAIL",
    "NATIONALUM", "HINDCOPPER", "RATNAMANI", "APLAPOLLO",  "WELCORP",
    "JINDALSAW",

    # ── T3: Power & Energy ─────────────────────────────────────────────────
    "JSWENERGY",  "TORNTPOWER", "CESC",      "NHPC",       "SJVN",
    "TATAPOWER",  # duplicate guard handled by set in data_yfinance loader

    # ── T3: Specialty Chemicals ────────────────────────────────────────────
    "SRF",        "NAVINFLUOR", "DEEPAKNI",  "PIIND",      "ATUL",
    "AARTIIND",   "TATACHEM",   "GNFC",

    # ── T3: Building Materials & Consumer Durables ─────────────────────────
    "ASTRAL",     "SUPREMEIND", "VGUARD",

    # ── T3: Aviation, Hospitality & Travel ─────────────────────────────────
    "INDIGO",     "INDHOTEL",   "PVRINOX",   "EASEMYTRIP",

    # ── T3: Logistics & Telecom ────────────────────────────────────────────
    "CONCOR",     "DELHIVERY",  "INDUSTOWER", "TATACOMM",  "RAILTEL",
    "RVNL",       "IRFC",

    # ── T3: Media & Entertainment ──────────────────────────────────────────
    "SUNTV",      "ZEEL",

    # ── T3: Financial Exchanges & Services ─────────────────────────────────
    "MCX",        "CDSL",       "CAMS",

    # ── T3: Diversified / Others ───────────────────────────────────────────
    "UPL",        "BALRAMCHIN", "JSWINFRA",

    # ── T3 additions: Banking / Finance ────────────────────────────────────
    "DCBBANK",    "CITYUNIONBK", "IREDA",    "AAVAS",      "HOMEFIRST",
    "CREDITACC",  "KFINTECH",    "NIACL",    "GICRE",      "PAISALO",
    "UJJIVANSFB", "GUJGAS",

    # ── T3 additions: IT / Software ────────────────────────────────────────
    "BSOFT",      "RATEGAIN",   "TANLA",     "HAPPSTMNDS", "MASTEK",
    "KPIGLBL",    "XCHANGING",  "NIITMTS",   "NEWGEN",     "ROUTE",

    # ── T3 additions: Auto & Ancillaries ───────────────────────────────────
    "SUNDRMFAST", "ENDURANCE",  "LUMAXIND",  "MINDAIND",   "SUPRAJIT",
    "CRAFTSMAN",  "MAHINDCIE",  "SANSERA",

    # ── T3 additions: Pharma & Healthcare ──────────────────────────────────
    "JBCHEPHARM", "FORTIS",     "MAXHEALTH", "CAPLIPOINT", "ERIS",
    "ABBOTINDIA", "RAINBOW",    "GLOBALHLTH", "PFIZER",

    # ── T3 additions: Consumer Electronics & EMS ───────────────────────────
    "AMBERENTERP", "KAYNES",    "SYRMA",     "TITAGARH",

    # ── T3 additions: FMCG & Consumer ──────────────────────────────────────
    "HATSUN",     "WESTLIFE",   "VSTIND",    "RADICO",     "SAREGAMA",
    "BATAINDIA",  "KALYANKJIL", "SENCO",     "VMART",      "CAMPUS",
    "NAZARA",     "BLUESTAR",

    # ── T3 additions: Specialty Chemicals ──────────────────────────────────
    "CLEAN",      "FINEORG",    "SUMICHEM",  "SUDARSCHEM", "ASTEC",

    # ── T3 additions: Infrastructure & Engineering ──────────────────────────
    "NCC",        "PNCINFRA",   "KALPATPOW", "TDPOWERSYS", "KPIL",
    "GRINDWELL",  "ELECON",

    # ── T3 additions: Textiles ──────────────────────────────────────────────
    "VARDHMANTEXT", "ARVIND",   "WELSPUNLIV", "RAYMOND",

    # ── T3 additions: Metals & Mining ───────────────────────────────────────
    "GPIL",       "MOIL",       "GRAPHITE",  "TINPLATE",

    # ── T3 additions: Defence / PSU ─────────────────────────────────────────
    "COCHINSHIP", "MIDHANI",

    # ── T3 additions: Renewable Energy ──────────────────────────────────────
    "INOXWIND",

    # ── T3 additions: Agro / Specialty ──────────────────────────────────────
    "COROMANDEL", "KAVERI",

    # ── T3 additions: Logistics ──────────────────────────────────────────────
    "MAHLOG",     "SIS",

    # ── T3 additions: Exchange / Services ───────────────────────────────────
    "BSE",
]

# De-duplicate while preserving order (TATAPOWER listed twice as guard above)
_seen: set = set()
_CORE_WATCHLIST_DEDUP: List[str] = []
for _s in _CORE_WATCHLIST:
    if _s not in _seen:
        _seen.add(_s)
        _CORE_WATCHLIST_DEDUP.append(_s)
_CORE_WATCHLIST = _CORE_WATCHLIST_DEDUP

# ── Momentum tier classification ─────────────────────────────────────────────
_MOMENTUM_TIER: Dict[str, str] = {
    # T1 — 26 ultra-liquid symbols (index heavyweights + strongest intraday movers)
    "RELIANCE":   "T1", "TCS":        "T1", "HDFCBANK":   "T1",
    "INFY":       "T1", "ICICIBANK":  "T1", "SBIN":       "T1",
    "AXISBANK":   "T1", "KOTAKBANK":  "T1", "BAJFINANCE": "T1",
    "BHARTIARTL": "T1", "LT":         "T1", "ITC":        "T1",
    "TATASTEEL":  "T1", "MARUTI":     "T1", "HCLTECH":    "T1",
    "WIPRO":      "T1", "JSWSTEEL":   "T1", "M&M":        "T1",
    "BAJAJFINSV": "T1", "ZOMATO":     "T1", "INDUSINDBK": "T1",
    "NTPC":       "T1", "TRENT":      "T1", "PERSISTENT": "T1",
    "POLYCAB":    "T1",
    # T1 additions
    "TATAMOTORS": "T1",

    # T2 — 65 large/large-mid cap symbols
    "HINDUNILVR": "T2", "ASIANPAINT": "T2", "TITAN":      "T2",
    "ULTRACEMCO": "T2", "NESTLEIND":  "T2", "TECHM":      "T2",
    "SUNPHARMA":  "T2", "POWERGRID":  "T2", "ONGC":       "T2",
    "COALINDIA":  "T2", "BPCL":       "T2", "GRASIM":     "T2",
    "DIVISLAB":   "T2", "CIPLA":      "T2", "EICHERMOT":  "T2",
    "HEROMOTOCO": "T2", "TATACONSUM": "T2", "BRITANNIA":  "T2",
    "APOLLOHOSP": "T2", "HDFCLIFE":   "T2", "SBILIFE":    "T2",
    "PIDILITIND": "T2", "DRREDDY":    "T2", "SHRIRAMFIN": "T2",
    "NAUKRI":     "T2", "IRCTC":      "T2", "DMART":      "T2",
    "HAVELLS":    "T2", "SIEMENS":    "T2", "ABB":        "T2",
    "VOLTAS":     "T2", "LTIM":       "T2", "MPHASIS":    "T2",
    "BAJAJ-AUTO": "T2", "BALKRISIND": "T2", "TATAPOWER":  "T2",
    "HINDPETRO":  "T2", "IOC":        "T2", "PFC":        "T2",
    "RECLTD":     "T2", "IDFCFIRSTB": "T2", "BANKBARODA": "T2",
    "CANBK":      "T2", "PNB":        "T2", "FEDERALBNK": "T2",
    "MUTHOOTFIN": "T2", "CHOLAFIN":   "T2", "GODREJCP":   "T2",
    "DABUR":      "T2", "CROMPTON":   "T2",
    # T2 additions
    "GAIL":       "T2", "BANDHANBNK": "T2", "HINDZINC":   "T2",
    "ADANIGREEN": "T2", "SUZLON":     "T2", "ANGELONE":   "T2",
    "DIXON":      "T2", "MACROTECH":  "T2", "IRCON":      "T2",
    "RITES":      "T2", "SOBHA":      "T2", "KARURVYSYA": "T2",
    "GODREJIND":  "T2", "PHOENIXLTD": "T2", "ICICIGI":    "T2",
}
# All others default to T3

# ── Sector mapping ────────────────────────────────────────────────────────────
_SECTOR_MAP: Dict[str, str] = {
    # Energy & Oil
    "RELIANCE":   "Energy",    "ONGC":       "Energy",    "BPCL":       "Energy",
    "IOC":        "Energy",    "HINDPETRO":  "Energy",    "TATACHEM":   "Chemicals",
    # Power
    "TATAPOWER":  "Power",     "PFC":        "Power",     "RECLTD":     "Power",
    "NTPC":       "Power",     "JSWENERGY":  "Power",     "TORNTPOWER": "Power",
    "CESC":       "Power",     "NHPC":       "Power",     "SJVN":       "Power",
    # IT
    "TCS":        "IT",        "INFY":       "IT",        "WIPRO":      "IT",
    "HCLTECH":    "IT",        "TECHM":      "IT",        "LTIM":       "IT",
    "MPHASIS":    "IT",        "PERSISTENT": "IT",        "COFORGE":    "IT",
    "LTTS":       "IT",        "TATAELXSI":  "IT",        "CYIENT":     "IT",
    "KPITTECH":   "IT",        "OFSS":       "IT",        "ZENSARTECH": "IT",
    # Banking
    "HDFCBANK":   "Banking",   "ICICIBANK":  "Banking",   "SBIN":       "Banking",
    "KOTAKBANK":  "Banking",   "AXISBANK":   "Banking",   "INDUSINDBK": "Banking",
    "BANKBARODA": "Banking",   "PNB":        "Banking",   "CANBK":      "Banking",
    "FEDERALBNK": "Banking",   "IDFCFIRSTB": "Banking",   "AUBANK":     "Banking",
    "RBLBANK":    "Banking",   "EQUITASBNK": "Banking",   "UJJIVAN":    "Banking",
    # Finance & Insurance
    "BAJFINANCE": "Finance",   "BAJAJFINSV": "Finance",   "HDFCLIFE":   "Insurance",
    "SBILIFE":    "Insurance", "MUTHOOTFIN": "Finance",   "CHOLAFIN":   "Finance",
    "LICHSGFIN":  "Finance",   "CANFINHOME": "Finance",   "ABCAPITAL":  "Finance",
    "IIFL":       "Finance",   "PNBHOUSING": "Finance",   "MFSL":       "Insurance",
    "SBICARD":    "Finance",   "MANAPPURAM": "Finance",   "STARHEALTH": "Insurance",
    "POLICYBZR":  "Finance",   "CAMS":       "Finance",   "CDSL":       "Finance",
    "MCX":        "Finance",
    # Infrastructure
    "LT":         "Infra",     "POWERGRID":  "Infra",     "SIEMENS":    "Infra",
    "ABB":        "Infra",     "HAVELLS":    "Infra",     "KEC":        "Infra",
    "ENGINERSIN": "Infra",     "NBCC":       "Infra",     "JSWINFRA":   "Infra",
    # Metals
    "TATASTEEL":  "Metal",     "JSWSTEEL":   "Metal",     "VEDL":       "Metal",
    "HINDALCO":   "Metal",     "NMDC":       "Mining",    "JINDALSTEL": "Metal",
    "SAIL":       "Metal",     "NATIONALUM": "Metal",     "HINDCOPPER": "Metal",
    "RATNAMANI":  "Metal",     "APLAPOLLO":  "Metal",     "WELCORP":    "Metal",
    "JINDALSAW":  "Metal",
    # Auto & Ancillaries
    "MARUTI":     "Auto",      "EICHERMOT":  "Auto",      "HEROMOTOCO": "Auto",
    "BAJAJ-AUTO": "Auto",      "M&M":        "Auto",      "BALKRISIND": "Auto",
    "TVSMOTORS":  "Auto",      "MOTHERSON":  "Auto",      "ESCORTS":    "Auto",
    "BOSCHLTD":   "Auto",      "BHARATFORG": "Auto",      "TIINDIA":    "Auto",
    # Pharma & Healthcare
    "SUNPHARMA":  "Pharma",    "CIPLA":      "Pharma",    "DIVISLAB":   "Pharma",
    "DRREDDY":    "Pharma",    "LUPIN":      "Pharma",    "AUROPHARMA": "Pharma",
    "ALKEM":      "Pharma",    "TORNTPHARM": "Pharma",    "SYNGENE":    "Pharma",
    "LAURUSLABS": "Pharma",    "GRANULES":   "Pharma",    "BIOCON":     "Pharma",
    "IPCA":       "Pharma",    "NATCOPHARM": "Pharma",    "GLENMARK":   "Pharma",
    "AJANTPHARM": "Pharma",
    "LALPATHLAB": "Healthcare","APOLLOHOSP": "Healthcare",
    # FMCG
    "HINDUNILVR": "FMCG",      "ITC":        "FMCG",      "NESTLEIND":  "FMCG",
    "BRITANNIA":  "FMCG",      "TATACONSUM": "FMCG",      "DABUR":      "FMCG",
    "GODREJCP":   "FMCG",      "COLPAL":     "FMCG",      "MARICO":     "FMCG",
    "EMAMILTD":   "FMCG",      "JYOTHYLAB":  "FMCG",      "BIKAJI":     "FMCG",
    "BALRAMCHIN": "FMCG",      "VBL":        "FMCG",      "UBL":        "FMCG",
    "MCDOWELL-N": "FMCG",      "JUBLFOOD":   "FMCG",
    # Consumer
    "TITAN":      "Consumer",  "ABFRL":      "Retail",    "ZYDUSLIFE":  "Pharma",
    "PAGEIND":    "Consumer",  "TRENT":      "Retail",    "DMART":      "Retail",
    "PVRINOX":    "Entertainment",
    # Paints & Chemicals
    "ASIANPAINT": "Paints",    "BERGEPAINT": "Paints",    "KANSAIPO":   "Paints",
    "PIDILITIND": "Chemicals", "UPL":        "Agro",      "SRF":        "Chemicals",
    "NAVINFLUOR": "Chemicals", "DEEPAKNI":   "Chemicals", "PIIND":      "Chemicals",
    "ATUL":       "Chemicals", "AARTIIND":   "Chemicals", "GNFC":       "Chemicals",
    # Cement
    "ULTRACEMCO": "Cement",    "GRASIM":     "Cement",    "AMBUJACEM":  "Cement",
    "ACC":        "Cement",    "JKCEMENT":   "Cement",    "RAMCOCEM":   "Cement",
    "DALBHARAT":  "Cement",
    # Consumer Durables
    "VOLTAS":     "ConsumerDurables", "CROMPTON": "ConsumerDurables",
    "CUMMINSIND": "Industrials",      "THERMAX":  "Industrials",
    "SCHAEFFLER": "Industrials",      "SKFINDIA":  "Industrials",
    "HAVELLS":    "ConsumerDurables", "VGUARD":   "ConsumerDurables",
    # Building Materials
    "ASTRAL":     "BuildingMat",  "SUPREMEIND": "BuildingMat",
    "KAJARIA":    "BuildingMat",  "CERA":       "BuildingMat",
    "POLYCAB":    "Industrials",
    # Real Estate
    "DLF":        "RealEstate",  "GODREJPROP": "RealEstate",
    "PRESTIGE":   "RealEstate",  "OBEROIRLTY": "RealEstate",
    "BRIGADE":    "RealEstate",
    # Defence & PSU
    "HAL":        "Defence",    "BEL":        "Defence",   "BDL":        "Defence",
    "MAZAGON":    "Defence",    "MAZDOCK":    "Defence",   "BHEL":       "Industrials",
    # Telecom & Media
    "BHARTIARTL": "Telecom",    "INDUSTOWER": "Telecom",   "TATACOMM":   "Telecom",
    "SUNTV":      "Media",      "ZEEL":       "Media",
    # Travel & Hospitality
    "IRCTC":      "Travel",     "INDIGO":     "Aviation",  "INDHOTEL":   "Hospitality",
    "EASEMYTRIP": "Travel",
    # Logistics
    "CONCOR":     "Logistics",  "DELHIVERY":  "Logistics",
    # Internet
    "NAUKRI":     "Internet",   "ZOMATO":     "Internet",  "NYKAA":      "Internet",
    # PSU / Railway
    "RVNL":       "Railways",   "IRFC":       "Railways",  "RAILTEL":    "Railways",
    "COALINDIA":  "Mining",     "SHRIRAMFIN": "Finance",

    # ── New T1/T2 sector mappings ──────────────────────────────────────────
    "TATAMOTORS": "Auto",
    "GAIL":       "Energy",     "HINDZINC":   "Metal",     "ADANIGREEN": "Power",
    "SUZLON":     "Power",      "BANDHANBNK": "Banking",   "KARURVYSYA": "Banking",
    "ANGELONE":   "Finance",    "ICICIGI":    "Insurance",
    "DIXON":      "ConsumerDurables",
    "MACROTECH":  "RealEstate", "SOBHA":      "RealEstate","PHOENIXLTD": "RealEstate",
    "IRCON":      "Railways",   "RITES":      "Railways",  "GODREJIND":  "Industrials",

    # ── New T3 sector mappings: Banking / Finance ──────────────────────────
    "DCBBANK":    "Banking",    "CITYUNIONBK":"Banking",   "UJJIVANSFB": "Banking",
    "IREDA":      "Finance",    "AAVAS":      "Finance",   "HOMEFIRST":  "Finance",
    "CREDITACC":  "Finance",    "KFINTECH":   "Finance",   "PAISALO":    "Finance",
    "NIACL":      "Insurance",  "GICRE":      "Insurance",
    "GUJGAS":     "Energy",

    # ── New T3 sector mappings: IT / Software ─────────────────────────────
    "BSOFT":      "IT",         "RATEGAIN":   "IT",        "TANLA":      "IT",
    "HAPPSTMNDS": "IT",         "MASTEK":     "IT",        "KPIGLBL":    "IT",
    "XCHANGING":  "IT",         "NIITMTS":    "IT",        "NEWGEN":     "IT",
    "ROUTE":      "Telecom",

    # ── New T3 sector mappings: Auto & Ancillaries ────────────────────────
    "SUNDRMFAST": "Auto",       "ENDURANCE":  "Auto",      "LUMAXIND":   "Auto",
    "MINDAIND":   "Auto",       "SUPRAJIT":   "Auto",      "CRAFTSMAN":  "Auto",
    "MAHINDCIE":  "Auto",       "SANSERA":    "Auto",

    # ── New T3 sector mappings: Pharma & Healthcare ───────────────────────
    "JBCHEPHARM": "Pharma",     "CAPLIPOINT": "Pharma",    "ERIS":       "Pharma",
    "ABBOTINDIA": "Pharma",     "PFIZER":     "Pharma",    "ASTEC":      "Pharma",
    "FORTIS":     "Healthcare", "MAXHEALTH":  "Healthcare","RAINBOW":    "Healthcare",
    "GLOBALHLTH": "Healthcare",

    # ── New T3 sector mappings: Consumer Electronics & EMS ───────────────
    "AMBERENTERP":"ConsumerDurables", "KAYNES":  "Industrials",
    "SYRMA":      "Industrials",      "TITAGARH":"Industrials",

    # ── New T3 sector mappings: FMCG & Consumer ──────────────────────────
    "HATSUN":     "FMCG",       "WESTLIFE":   "FMCG",      "VSTIND":     "FMCG",
    "RADICO":     "FMCG",       "BATAINDIA":  "Consumer",  "KALYANKJIL": "Consumer",
    "SENCO":      "Consumer",   "CAMPUS":     "Consumer",  "VMART":      "Retail",
    "SAREGAMA":   "Media",      "NAZARA":     "IT",        "BLUESTAR":   "ConsumerDurables",

    # ── New T3 sector mappings: Specialty Chemicals ───────────────────────
    "CLEAN":      "Chemicals",  "FINEORG":    "Chemicals", "SUMICHEM":   "Chemicals",
    "SUDARSCHEM": "Chemicals",

    # ── New T3 sector mappings: Infrastructure & Engineering ─────────────
    "NCC":        "Infra",      "PNCINFRA":   "Infra",     "KPIL":       "Infra",
    "KALPATPOW":  "Power",      "TDPOWERSYS": "Power",
    "GRINDWELL":  "Industrials","ELECON":     "Industrials",

    # ── New T3 sector mappings: Textiles ─────────────────────────────────
    "VARDHMANTEXT":"Textiles",  "ARVIND":     "Textiles",  "WELSPUNLIV": "Textiles",
    "RAYMOND":    "Textiles",

    # ── New T3 sector mappings: Metals & Mining ───────────────────────────
    "GPIL":       "Metal",      "GRAPHITE":   "Metal",     "TINPLATE":   "Metal",
    "MOIL":       "Mining",

    # ── New T3 sector mappings: Defence / PSU ────────────────────────────
    "COCHINSHIP": "Defence",    "MIDHANI":    "Defence",

    # ── New T3 sector mappings: Renewable Energy ──────────────────────────
    "INOXWIND":   "Power",

    # ── New T3 sector mappings: Agro / Specialty ─────────────────────────
    "COROMANDEL": "Agro",       "KAVERI":     "Agro",

    # ── New T3 sector mappings: Logistics ────────────────────────────────
    "MAHLOG":     "Logistics",  "SIS":        "Logistics",

    # ── New T3 sector mappings: Exchange / Services ───────────────────────
    "BSE":        "Finance",
}


def get_priority_watchlist() -> List[str]:
    """
    Return full 380-stock watchlist ordered by tier: T1 first, then T2, then T3.
    Within each tier preserves _CORE_WATCHLIST insertion order.
    """
    t1 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T1"]
    t2 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) == "T2"]
    t3 = [s for s in _CORE_WATCHLIST if _MOMENTUM_TIER.get(s) not in ("T1", "T2")]
    return t1 + t2 + t3


def get_tier(symbol: str) -> str:
    """Return momentum tier ('T1', 'T2', or 'T3') for a symbol."""
    return _MOMENTUM_TIER.get(symbol.upper(), "T3")


def get_active_watchlist(dhan_client=None) -> List[str]:
    """
    Return watchlist filtered by minimum liquidity, ordered by priority tier.
    Falls back to full priority list if data unavailable (fail-open).
    """
    ordered = get_priority_watchlist()

    if dhan_client is None:
        logger.info(f"Watchlist (no LTP filter): {len(ordered)} symbols")
        return ordered

    try:
        from data_fetch_upstox import get_multiple_ltp
        ltp_map = get_multiple_ltp(_CORE_WATCHLIST, dhan_client)

        # Filter: price > Rs.50, has LTP data — preserve priority order
        filtered = [s for s in ordered if ltp_map.get(s, 0) >= 50]

        if len(filtered) < 10:
            logger.warning("LTP filter too aggressive — using full watchlist")
            return ordered

        logger.info(f"Watchlist: {len(filtered)} symbols (filtered from {len(ordered)})")
        return filtered
    except Exception as e:
        logger.warning(f"Watchlist filter failed: {e} — using full list")
        return ordered


def get_sector(symbol: str) -> str:
    return _SECTOR_MAP.get(symbol.upper(), "Unknown")


def get_nifty_symbol() -> str:
    """NSE symbol for Nifty 50 index (used as market benchmark)."""
    return "NIFTY 50"
