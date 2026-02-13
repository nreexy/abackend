import urllib.request
import urllib.error
import sys

BASE_URL = "http://localhost:8000"
TEST_KEY = "my_super_secret_static_key"

def test_static_key():
    print(f"Testing access with Static Key: {TEST_KEY}")
    req = urllib.request.Request(f"{BASE_URL}/ping")
    req.add_header("Authorization", f"Bearer {TEST_KEY}")
    
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            print(f"Status Code: {response.getcode()}")
            if response.getcode() == 200:
                print("✅ SUCCESS: Static Key works!")
                return True
            else:
                print(f"❌ FAILED: Expected 200, got {response.getcode()}")
                return False
    except urllib.error.HTTPError as e:
        print(f"❌ FAILED: HTTP Error {e.code}: {e.reason}")
        return False
    except Exception as e:
        print(f"❌ FAILED: Connection error: {e}")
        return False

def test_no_key():
    print("\nTesting access with NO Key:")
    try:
        with urllib.request.urlopen(f"{BASE_URL}/ping", timeout=2) as response:
            print(f"❌ FAILED: Expected 401, got {response.getcode()}")
    except urllib.error.HTTPError as e:
        print(f"Status Code: {e.code}")
        if e.code == 401:
            print("✅ SUCCESS: Correctly denied access.")
        else:
            print(f"❌ FAILED: Expected 401, got {e.code}")
    except Exception as e:
        print(f"❌ FAILED: Connection error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        success = test_static_key()
        test_no_key()
        sys.exit(0 if success else 1)
    else:
        print("Usage: python verify_static_key.py test")
