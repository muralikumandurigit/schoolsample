import asyncio
import websockets
import json

async def test():
    uri = "ws://localhost:8765/"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"id": 1, "method": "list_tools"}))
        resp = await ws.recv()
        print(resp)

asyncio.run(test())
