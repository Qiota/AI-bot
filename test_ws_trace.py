import asyncio
import base64
import hashlib
import json
import time
import aiohttp

SIGNER_URL = "https://prompt-signer.freegen.app"
GENERATOR_URL = "https://image-generator.freegen.app"
WEBSOCKET_URL = "wss://websocket-bridge.freegen.app/ws"

PROMPT = "a cute cat sitting on a windowsill, photorealistic"

def create_ws_auth(job_id: str, timestamp: int) -> str:
    message = f"{job_id}{timestamp}"
    digest = hashlib.sha256(message.encode()).digest()
    return base64.b64encode(digest).decode()

async def test_full_flow():
    async with aiohttp.ClientSession() as session:
        print("Step 1: Signing prompt...")
        async with session.post(SIGNER_URL, json={"prompt": PROMPT}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"  Signer status: {resp.status}")
            signed = await resp.json()
            print(f"  Signed response: {signed}")

        ts = str(signed.get("ts", ""))
        sig = str(signed.get("sig", ""))

        print("\nStep 2: Requesting generation...")
        async with session.post(GENERATOR_URL, json={"prompt": PROMPT, "ts": ts, "sig": sig, "ratio_id": "1:1"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"  Generator status: {resp.status}")
            job_data = await resp.json()
            print(f"  Job data: {job_data}")

        job_id = job_data.get("job_id")
        if not job_id:
            print("ERROR: No job_id")
            return

        print(f"\nStep 3: Connecting to WS for job {job_id}...")
        ws_auth = create_ws_auth(job_id, int(time.time()))

        ws = await session.ws_connect(WEBSOCKET_URL, timeout=aiohttp.ClientTimeout(total=300))

        await ws.send_str(json.dumps({"type": "subscribe", "job_id": job_id, "auth": ws_auth}))
        print("  Subscribed, waiting for messages...")

        start = time.time()
        received_types = set()
        image_data = None

        while time.time() - start < 120:
            try:
                msg = await asyncio.wait_for(ws.receive_str(), timeout=5)
                print(f"\n  MESSAGE ({time.time() - start:.1f}s): {msg}")

                try:
                    data = json.loads(msg)
                    t = data.get("type", "?")
                    received_types.add(t)
                    print(f"  Type: {t}, Keys: {list(data.keys())}")

                    if t == "result":
                        image_data = data.get("image_data")
                        print(f"  image_data present: {bool(image_data)}, len: {len(image_data) if image_data else 0}")
                    elif t == "error":
                        print(f"  Error message: {data.get('message', data.get('error', ''))}")
                    elif t == "status":
                        print(f"  Status message: {data.get('message', '')}")

                except json.JSONDecodeError:
                    print(f"  Raw text: {msg[:200]}")

                if t == "result":
                    break

            except asyncio.TimeoutError:
                print("  ... (waiting)")
                continue
            except Exception as e:
                print(f"  WS error: {e}")
                break

        print(f"\nMessage types received: {received_types}")

        if image_data:
            print(f"\nDecoding image_data...")
            if image_data.startswith("data:"):
                b64 = image_data.split(",", 1)[1]
            else:
                b64 = image_data

            try:
                img_bytes = base64.b64decode(b64)
                print(f"Decoded bytes: {len(img_bytes)}")
                with open("data/test_output.png", "wb") as f:
                    f.write(img_bytes)
                print("Saved to data/test_output.png")
            except Exception as e:
                print(f"Decode error: {e}")

        await ws.close()

asyncio.run(test_full_flow())