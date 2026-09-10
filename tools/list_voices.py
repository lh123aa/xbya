import asyncio, edge_tts

async def main():
    voices = await edge_tts.list_voices()
    zh = [v for v in voices if v["Locale"].startswith("zh-")]
    for v in sorted(zh, key=lambda x: x["ShortName"]):
        gender = "F" if v["Gender"] == "Female" else "M"
        name = v.get("LocalName", "")
        print(f"{gender}  {v['ShortName']:40s}  {name}")

asyncio.run(main())
