"""
下载 Q版 VRM 模型（chibi girl）
从 VRoid Hub API 搜索并下载免费 Q版角色模型

用法: python tools/download_chibi_vrm.py [output_path]
"""
import os
import sys
import json
import requests

VRD_API = "https://api.vroid.com/api/1.0/models"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "xbyaPet/2.0"
}


def search_chibi_models(query="chibi female free", limit=5):
    """搜索 Q版角色"""
    params = {
        "q": query,
        "limit": limit,
        "sort": "popular",
        "include_licensed": "true",
    }
    try:
        r = requests.get(VRD_API, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        models = data.get("data", [])
        return [
            {
                "id": m["id"],
                "name": m.get("name", "Unnamed"),
                "creator": m.get("creator", {}).get("name", "Unknown"),
                "thumbnail": m.get("thumbnailUrl", ""),
                "licensed": m.get("licensedType", "unknown"),
                "download_url": f"https://hub.vroid.com/en/models/{m['id']}",
            }
            for m in models
        ]
    except Exception as e:
        print(f"搜索失败: {e}")
        return []


def download_model(model_id: str, output_path: str) -> bool:
    """下载指定模型"""
    url = f"https://api.vroid.com/api/1.0/models/{model_id}/download"
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = os.path.getsize(output_path) // 1024
        print(f"下载成功: {output_path} ({size_kb} KB)")
        return True
    except Exception as e:
        print(f"下载失败: {e}")
        return False


def main():
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "vrm")
    output_path = os.path.join(output_dir, "chibi.vrm")
    if len(sys.argv) > 1:
        output_path = sys.argv[1]
        output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    print("正在搜索 Q版角色 (chibi female free)...")
    models = search_chibi_models("chibi female free", limit=8)

    if not models:
        print("未找到结果，尝试更宽松的搜索...")
        models = search_chibi_models("chibi girl", limit=8)

    if not models:
        print("❌ 无法获取模型列表")
        sys.exit(1)

    print(f"\n找到 {len(models)} 个模型:")
    for i, m in enumerate(models):
        print(f"  [{i+1}] {m['name']} by {m['creator']}")
        print(f"      链接: {m['download_url']}")
        print(f"      授权: {m['licensed']}")

    # 自动选择第一个免费的 chibi 模型
    for m in models:
        print(f"\n正在下载: {m['name']}...")
        if download_model(m["id"], output_path):
            print(f"\n✅ 已保存到: {output_path}")
            print(f"   请在 config.yaml 中更新:")
            print(f"   ui.vrm_model: assets/vrm/chibi.vrm")
            return

    print("\n❌ 所有下载均失败")
    sys.exit(1)


if __name__ == "__main__":
    main()
