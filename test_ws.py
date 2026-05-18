import asyncio
import hashlib
import base64
import json
import time
import aiohttp

WEBSOCKET_URL = "wss://websocket-bridge.freegen.app/ws"

def create_ws_auth(job_id: str, timestamp: int) -> str:
    message = f"{job_id}{timestamp}"
    digest = hashlib.sha256(message.encode()).digest()
    hex_str = digest.hex()
    return base64.b64encode(hex_str.encode()).decode()

async def test_ws():
    ts = int(time.time())
    auth = create_ws_auth("test-job-123", ts)
    print(f"Timestamp: {ts}")
    print(f"Auth: {auth}")
    print(f"Message hashed: {hashlib.sha256(f'test-job-123{ts}'.encode()).hexdigest()}")

    async with aiohttp.ClientSession() as session:
        ws = session.ws_connect(WEBSOCKET_URL, timeout=aiohttp.ClientTimeout(total=10))
        async with ws as wss:
            print("WS connected!")
            await wss.send_str(json.dumps({
                "type": "subscribe",
                "job_id": "test-job-123",
                "auth": auth
            }))
            print("Sent subscribe")
            msg = await wss.receive_str()
            print(f"Received: {msg}")
            await wss.close()

asyncio.run(test_ws())