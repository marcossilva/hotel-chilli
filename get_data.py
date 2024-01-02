import requests
import datetime
from bs4 import BeautifulSoup

url = 'https://cuidax.com.br/contador-chilli/'
r = requests.get(url)
soup = BeautifulSoup(r.text, 'html.parser')
now = datetime.datetime.now()
with open('data.json', 'a+') as f:
    f.write(now.strftime("%Y-%m-%d %H:%M:%S") + ',' + soup.find_all('div')[0].text.strip() + '\n')
