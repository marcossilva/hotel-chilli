import argparse
import math
import asyncio
from typing import List
import datetime
import json
from aiohttp import ClientSession


argparser = argparse.ArgumentParser()

argparser.add_argument('--url', help='The URL to fetch')
argparser.add_argument('--min_id', help='The minimum ID to fetch')
argparser.add_argument('--max_id', help='The maximum ID to fetch')
argparser.add_argument('--num_tickets', help='The maximum ID to fetch')

args = argparser.parse_args()

url = args.url
min_id = int(args.min_id)
max_id = int(args.max_id)
num_tickets = int(args.num_tickets)

headers = {
    'accept': 'text/x-component',
    'accept-language': 'en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,es;q=0.6',
    'content-type': 'text/plain;charset=UTF-8',
    # 'cookie': '_scid=48cc8bc1-bfc0-457f-8cc3-68c79c660aef; _ga=GA1.1.930334868.1718987544; _tt_enable_cookie=1; _ttp=TZ_hrx9wCFFfLVKZgMrGnh_lzBp; token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoxMDI4NjA4LCJpYXQiOjE3MTg5ODc2MTB9.iz6UY8zwY9pTi4_kHP_yqBggAJKlDC3C00XRUUV9rno; __spdt=604b9c38c81e4456b38f87b651abf06b; __stripe_mid=fcc9a7cf-dbad-4fb3-a726-2eee2d227cecea39fa; _ga_VN3BLQZFTC=GS1.1.1722049826.1.0.1722049826.0.0.0; _ga_PG49M3YXH5=deleted; _gcl_au=1.1.1382681528.1726782241; auth_session=bzfssq4m2dmcb2jkh7sweqafkpjvegrz7rz6ufis; _scid_r=MVpIzIvBv8B_fwrDaMecZgrvBEVbRjzmI7-p8g; ph_phc_GoSnPhraOOMjhsibgegSkssTgpPUoeL9Dp4xAHwiR9A_posthog=%7B%22distinct_id%22%3A%221028608%22%2C%22%24sesid%22%3A%5B1732067267619%2C%2201934741-0a81-7f11-b08a-76e8cc0082bd%22%2C1732067265153%5D%2C%22%24epp%22%3Atrue%7D; _ga_PG49M3YXH5=GS1.1.1732067266.106.0.1732067269.57.0.0; CookieConsent={stamp:%27-1%27%2Cnecessary:true%2Cpreferences:true%2Cstatistics:true%2Cmarketing:true%2Cmethod:%27implied%27%2Cver:1%2Cutc:1732552140248%2Cregion:%27BR%27}; NEXT_LOCALE=pt-br; session=qkfloot4r77fzc5reaxiiu4jwtgu2v2z; _dd_s=rum=0&expire=1732818572527',
    'dnt': '1',
    'next-action': '7f76abbfecefeb23f2394afad644cf64129aa13add',
    'next-router-state-tree': '%5B%22%22%2C%7B%22children%22%3A%5B%5B%22locale%22%2C%22pt-br%22%2C%22d%22%5D%2C%7B%22children%22%3A%5B%22(main)%22%2C%7B%22children%22%3A%5B%22events%22%2C%7B%22children%22%3A%5B%5B%22slug%22%2C%22as-posicoes-de-sabrina-carpenter-sexta-29-11-zig%22%2C%22d%22%5D%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fpt-br%2Fevents%2Fas-posicoes-de-sabrina-carpenter-sexta-29-11-zig%22%2C%22refresh%22%5D%7D%5D%7D%5D%7D%5D%7D%5D%7D%2Cnull%2Cnull%2Ctrue%5D',
    'origin': 'https://shotgun.live',
    'priority': 'u=1, i',
    'referer': 'https://shotgun.live/pt-br/events/as-posicoes-de-sabrina-carpenter-sexta-29-11-zig',
    'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'x-deployment-id': 'dpl_FfhZxrYRjE9YQ8kmJrwDunDYAg4s',
}
async def my_post(session: ClientSession, id_val):
    params = {
        'ids': str(id_val),
    }
    slug = url.split('/')[-1]
    data = '[{"slug":"' + slug +'","bundleFilter":{"utmSource":null,"visibilities":["public"]},"dealFilter":{"excludeShopifyMerch":false,"salesChannel":"online","currentlyVisible":true,"withResales":false,"ids":['+ str(id_val) + '],"utmSource":null,"visibilities":["public"]}},"$undefined"]'
    async with session.post(url+f'ids={id_val}', headers=headers, params=params, data=data) as response:
        r = await response.text()
        for line in r.split('\n'):
            if line.startswith('1:'):
                clean_data = json.loads(line[2:])
                break
        num_tickets_temp = len(clean_data[0]['event']['deals'])
        if (num_tickets_temp != num_tickets):
            print(f"{url}?ids={id_val}")
        return {'id': id_val, 'num_tickets' : num_tickets_temp, 'status': response.status}


async def run(offer_ids: List[str]):
    async with ClientSession() as session:
        tasks = [my_post(session, offer_id) for offer_id in offer_ids]
        return await asyncio.gather(*tasks)

def convert_to_sec(x):
    return str(datetime.timedelta(seconds=round((x.total_seconds()))))

offer_ids_list = list(range(min_id, max_id))

n_semaphores = 100
total_time = 0
loop = asyncio.get_event_loop()
urls = offer_ids_list
df_out = []
n_loops = int(math.ceil(len(offer_ids_list)/n_semaphores))
for i in range(1, math.ceil(len(urls)/n_semaphores)+1):
    start_time = datetime.datetime.now()
    result = loop.run_until_complete(run(urls[(i-1)*n_semaphores: i*n_semaphores]))
    df_out+= result
    total_time += (datetime.datetime.now() - start_time).total_seconds()
    print("Processado {:>04}/{:>04} ({:>06.2f}%) ofertas em {}/{}/{} Total: {}\t Remaining: {}".format(
        i,
        n_loops,
        (i/n_loops)*100,
        convert_to_sec(datetime.datetime.now() - start_time), 
        convert_to_sec(datetime.timedelta(seconds=round(total_time))),
        convert_to_sec(datetime.timedelta(seconds=round(total_time/i))),
        convert_to_sec(datetime.timedelta(seconds=round(math.ceil(n_loops*(total_time/i))))),
        convert_to_sec(datetime.timedelta(seconds=round(math.ceil(n_loops*(total_time/i)) - total_time)))
    ))
