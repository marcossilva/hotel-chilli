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
argparser.add_argument('--next_action', help='Next Action Code', default="7f4d9e3dfd87839218e90688812e54b07b8e0bb814")

args = argparser.parse_args()

url = args.url
min_id = int(args.min_id)
max_id = int(args.max_id)
num_tickets = int(args.num_tickets)
next_action = args.next_action

headers = {
    'accept': 'text/x-component',
    'accept-language': 'en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7,es;q=0.6',
    'content-type': 'text/plain;charset=UTF-8',
    'dnt': '1',
    'next-action': next_action,
    'origin': 'https://shotgun.live',
    'priority': 'u=1, i',
    'referer': url,
    'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
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
