import akshare as ak
print("Testing AKShare import...")
try:
    df = ak.fund_etf_spot_em()
    print(f"Success! Got {len(df)} ETFs")
    print(df.head())
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
