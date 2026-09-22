import unittest
from cleaner import transform_text, should_process_message

class TestCleanerUpgrades(unittest.TestCase):
    def test_global_url_replacement(self):
        custom = {
            "replace_all_urls": True,
            "global_replacement_url": "https://t.me/my_promo_admin",
            "url_rules": []
        }
        text = "Visit https://old-casino.com/promo or https://t.me/old_channel for bonus!"
        transformed = transform_text(text, custom)
        self.assertNotIn("https://old-casino.com/promo", transformed)
        self.assertNotIn("https://t.me/old_channel", transformed)
        self.assertIn("https://t.me/my_promo_admin", transformed)

    def test_specific_url_mapping(self):
        custom = {
            "replace_all_urls": False,
            "url_rules": [
                {"match": "https://old-link.com", "replace": "https://my-affiliate.com"}
            ]
        }
        text = "Check out https://old-link.com and https://neutral-link.com"
        transformed = transform_text(text, custom)
        self.assertIn("https://my-affiliate.com", transformed)
        self.assertIn("https://neutral-link.com", transformed)
        self.assertNotIn("https://old-link.com", transformed)

    def test_global_username_replacement(self):
        custom = {
            "replace_all_usernames": True,
            "global_replacement_username": "@my_admin_support"
        }
        text = "Contact @old_owner or @original_bot for VIP access."
        transformed = transform_text(text, custom)
        self.assertNotIn("@old_owner", transformed)
        self.assertNotIn("@original_bot", transformed)
        self.assertIn("@my_admin_support", transformed)

    def test_specific_username_mapping(self):
        custom = {
            "replace_all_usernames": False,
            "username_rules": [
                {"match": "@old_support", "replace": "@new_support"}
            ]
        }
        text = "For help message @old_support or @general_chat"
        transformed = transform_text(text, custom)
        self.assertIn("@new_support", transformed)
        self.assertIn("@general_chat", transformed)
        self.assertNotIn("@old_support", transformed)

    def test_header_footer_injection(self):
        custom = {
            "header": "🔥 BREAKING SIGNAL 🔥",
            "footer": "👉 Contact Admin: @my_admin"
        }
        text = "BTC crossed 70,000 USD."
        transformed = transform_text(text, custom)
        self.assertTrue(transformed.startswith("🔥 BREAKING SIGNAL 🔥"))
        self.assertTrue(transformed.endswith("👉 Contact Admin: @my_admin"))

    def test_keyword_filters(self):
        custom = {
            "blacklist_keywords": ["scam", "phishing"],
            "whitelist_keywords": ["vip", "signal"]
        }
        # Blocked by blacklist
        allowed, reason = should_process_message("This is a scam offer", custom)
        self.assertFalse(allowed)

        # Blocked because doesn't match whitelist
        allowed, reason = should_process_message("Hello regular world", custom)
        self.assertFalse(allowed)

        # Allowed because matches whitelist and no blacklist
        allowed, reason = should_process_message("New VIP crypto alert", custom)
        self.assertTrue(allowed)

class TestCloudDeployment(unittest.TestCase):
    def test_healthz_endpoint(self):
        from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
        from web_server import create_app
        import asyncio

        async def _test():
            app = create_app()
            from aiohttp.test_utils import TestClient, TestServer
            client = TestClient(TestServer(app))
            await client.start_server()
            resp = await client.get("/healthz")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data.get("status"), "healthy")
            await client.close()

        asyncio.run(_test())

    def test_settings_session_string(self):
        from settings_manager import settings_manager
        tg = settings_manager.get("telegram", {})
        self.assertIn("session_string", tg)

if __name__ == "__main__":
    unittest.main()
