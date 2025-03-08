import requests
import datetime
from bs4 import BeautifulSoup

import requests

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

response = requests.get('https://hotelchilli.com.br/api/get-guest-count', cookies=cookies, headers=headers)
now = datetime.datetime.now()
with open('data.json', 'a+') as f:
    f.write(now.strftime("%Y-%m-%d %H:%M:%S") + ',' + str(response.json()) + '\n')
