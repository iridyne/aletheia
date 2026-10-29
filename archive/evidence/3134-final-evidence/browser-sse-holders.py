"""One bounded set of owned holders for the actual Command Center retry proof."""
import asyncio
import json
import httpx
from importlib.util import spec_from_file_location, module_from_spec

spec = spec_from_file_location('probe', '/var/tmp/aletheia-3134-preflight.Ppk25L/sse-capacity-probe.py')
probe = module_from_spec(spec)
spec.loader.exec_module(probe)

async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8787', trust_env=False) as client:
        baseline = await probe._infra(client, '/_infra/status')
        assert baseline['proxy_capacity']['stream_admissions'] == 0
        assert baseline['proxy_capacity']['streaming'] == 0
        tasks = []
        try:
            async with asyncio.timeout(14):
                opened = [asyncio.Event() for _ in range(10)]
                tasks = [asyncio.create_task(probe._holder(client, 'http://127.0.0.1:8787', e)) for e in opened]
                await asyncio.wait_for(asyncio.gather(*(e.wait() for e in opened)), 2)
                print(json.dumps({'event':'SATURATED','capacity':await probe._infra(client,'/_infra/status')}),flush=True)
                await asyncio.sleep(5)
                tasks[0].cancel()
                await asyncio.gather(tasks[0], return_exceptions=True)
                print(json.dumps({'event':'ONE_RELEASED'}),flush=True)
                await asyncio.sleep(6)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            print(json.dumps({'event':'OWNED_HOLDERS_CLOSED'}),flush=True)

asyncio.run(main())
