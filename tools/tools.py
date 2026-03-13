# tools/smoke_api.py
import argparse, sys, time, requests

def wait_for(base, timeout=20):
    url = f"{base}/openapi.json"
    for _ in range(int(timeout * 2)):
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    if not wait_for(base):
        print("✖ API không sẵn sàng trong thời gian chờ.")
        return 1

    print("✅ /openapi.json OK")
    # Thử các endpoint health phổ biến (nếu bạn đã tạo)
    for p in ("/healthz", "/health", "/ping"):
        try:
            r = requests.get(base + p, timeout=5)
            if r.status_code == 200:
                # in ngắn gọn để khỏi vỡ dòng PowerShell
                body = r.text[:120].replace("\n", " ")
                print(f"✅ {p} OK -> {body}")
                break
        except requests.RequestException:
            pass
    else:
        print("⚠️  Không có /healthz | /health | /ping (không bắt buộc).")

    r = requests.get(base + "/openapi.json", timeout=5)
    paths = list(r.json().get("paths", {}).keys())
    head = ", ".join(paths[:10])
    print(f"ℹ️  Số route: {len(paths)} | {head}{' ...' if len(paths)>10 else ''}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
