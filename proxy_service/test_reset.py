
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

# Add project root to path
sys.path.append("/Users/tree/Developer/95_testProjects/audio-metadata-server/proxy_service")

# We mock app.database before importing app.auth if possible, or patch it where it is used.
# Since app.auth imports clear_stored_password_hash, we can patch 'app.auth.clear_stored_password_hash'

from app.auth import check_password_reset_file

async def test_reset_logic_mocked():
    print("--- Testing Password Reset Logic (Mocked) ---")
    
    reset_file = ".passreset"

    # CASE 1: No file
    print("\n1. Testing without .passreset file...")
    if os.path.exists(reset_file): os.remove(reset_file)
    
    with patch("app.auth.clear_stored_password_hash", new_callable=AsyncMock) as mock_clear:
        result = await check_password_reset_file()
        assert result is False
        mock_clear.assert_not_called()
        print("   ✅ Correctly did nothing.")

    # CASE 2: With file, but logic enabled via mocking the config
    # Note: We depend on imports. If logic checks ENABLE_PASSWORD_RESET_FILE from imported module, we patch that.
    
    # CASE 3: Feature Disabled (Toggle Test)
    print("\n3. Testing with Feature DISABLED (Env Var)...")
    with open(reset_file, "w") as f: f.write("")
    
    # Patch the imported variable in app.auth
    with patch("app.auth.ENABLE_PASSWORD_RESET_FILE", False):
        with patch("app.auth.clear_stored_password_hash", new_callable=AsyncMock) as mock_clear:
                result = await check_password_reset_file()
                assert result is False
                mock_clear.assert_not_called()
                print("   ✅ Reset did NOT happen (feature disabled).")
                
    # CASE 4: Feature Enabled (Toggle Test)
    print("\n4. Testing with Feature ENABLED (Env Var)...")
    # File exists from step 3
    
    with patch("app.auth.ENABLE_PASSWORD_RESET_FILE", True):       
        with patch.dict(os.environ, {}, clear=True): 
             with patch("app.auth.ADMIN_PASSWORD_HASH", None):
                with patch("app.auth.clear_stored_password_hash", new_callable=AsyncMock) as mock_clear:
                    result = await check_password_reset_file()
                    assert result is True
                    mock_clear.assert_called_once()
                    print("   ✅ Reset happened (feature enabled).")

    # cleanup from step 4 (file is deleted by logic if successful, but let's be safe)
    if os.path.exists(reset_file): os.remove(reset_file)

    print("\n--- Test Complete: SUCCESS ---")

if __name__ == "__main__":
    asyncio.run(test_reset_logic_mocked())
