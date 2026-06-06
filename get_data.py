import requests
import datetime
import time
from zoneinfo import ZoneInfo

MAX_RETRIES = 5
RETRY_DELAY = 10  # seconds between retries
TIMEOUT = 15      # seconds per request

cookies = {
    '_ga': 'GA1.1.1733788075.1738855457',
    '_ga_LJELMPC45K': 'GS1.1.1741443001.2.1.1741443155.60.0.961606891',
}

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,es;q=0.6',
    'cache-control': 'no-cache',
    'dnt': '1',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://hotelchilli.com.br/',
    'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
    # 'cookie': '_ga=GA1.1.1733788075.1738855457; _ga_LJELMPC45K=GS1.1.1741443001.2.1.1741443155.60.0.961606891',
}

data = None
for attempt in range(1, MAX_RETRIES + 1):
    try:
        response = requests.get(
            'https://hotelchilli.com.br/api/get-guest-count',
            cookies=cookies,
            headers=headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        candidate = response.json()
        if isinstance(candidate, (int, float)) and candidate != 0:
            data = candidate
            break
        print(f"Attempt {attempt}/{MAX_RETRIES}: invalid response ({candidate!r}), retrying...")
    except Exception as e:
        print(f"Attempt {attempt}/{MAX_RETRIES}: error ({e}), retrying...")
    if attempt < MAX_RETRIES:
        time.sleep(RETRY_DELAY)

if data is None:
    print("All retries exhausted, skipping.")
    exit(1)

now = datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
with open('data.json', 'a+') as f:
    f.seek(0, 2)
    if f.tell() > 0:
        f.seek(f.tell() - 1)
        if f.read(1) != '\n':
            f.write('\n')
    f.write(now.strftime("%Y-%m-%d %H:%M:%S") + ',' + str(data) + '\n')
