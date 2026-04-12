# 修复溢价为空和熔断器问题的代码片段
# 1. 把异步溢价采集改成同步批量
def fetch_premium_sync_batch(codes, batch_size=200, delay=0.5):
    import requests
    results = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        print(f"[INFO] 采集溢价批次 {i//batch_size + 1}/{(len(codes)-1)//batch_size +1}")
        try:
            codes_str = ",".join([f"0.{c}" if c.startswith('1') or c.startswith('5') else f"1.{c}" for c in batch])
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fields": "f12,f20",
                "secids": codes_str,
                "_": int(time.time() * 1000)
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('data'):
                    for item in data['data']:
                        code = item.get('f12')
                        premium = item.get('f20')
                        if code and premium is not None:
                            try:
                                results[code] = float(premium)
                            except:
                                pass
        except Exception as e:
            print(f"[WARNING] 批次采集失败: {e}")
        time.sleep(delay)
    return results

# 2. 调整熔断器参数
CIRCUIT_BREAKER_THRESHOLD = 10
CIRCUIT_BREAKER_TIMEOUT = 30
