import argparse
import math
import asyncio
from typing import List
import datetime
import re
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
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'accept': 'text/x-component',
    'accept-language': 'en-US,en;q=0.5',
    'referer': url,
    'content-type': 'text/plain;charset=UTF-8',
    'next-action': '1fdce2a0a62496a3bfd9cb206bfeaa3541a43cc6',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'priority': 'u=4',
}
async def my_post(session: ClientSession, id_val):
    params = {
        'ids': str(id_val),
    }
    slug = url.split('/')[-1]
    data = '[{"slug":"' + slug + '","dealFilter":{"salesChannel":"online","currentlyVisible":true,"withResales":false,"ids":[' + str(id_val) + '],"utmSource":null,"visibilities":["public"]}},"$undefined"]'
    try:
        async with session.post(url+f'ids={id_val}', headers=headers, params=params, data=data) as response:
            r = await response.text()
            # use regex to capture anythin after the string '1:'
            clean_text = re.search(r'1:(.*)', r).group(1)
            clean_data = json.loads(clean_text)
            num_tickets_temp = len(clean_data[0]['event']['deals'])
            if (num_tickets_temp != num_tickets):
                print(f"{url}?ids={id_val}")
            return {'id': id_val, 'num_tickets' : num_tickets_temp, 'status': response.status}
    except:
        pass
    return {'id': id_val, 'num_tickets' : -1, 'status': -1}

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
