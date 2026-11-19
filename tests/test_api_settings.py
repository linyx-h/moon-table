"""公开版配置、模型请求与单／双人私有视角回归，不调用真实付费 API。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from game import Engine, GameError, REQUIRED_TARGETS
from catalog import MODES, ROLES

TEST_KEY = "test-only-not-a-real-api-key"


class PublicReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = tempfile.TemporaryDirectory(prefix="moon-settings-test-")
        self.e = Engine(self.data.name)

    async def asyncTearDown(self):
        await self.e.pause()
        await self.e.client.aclose()
        self.e.db.close()
        self.data.cleanup()

    def configure(self, **extra):
        self.e.configure({"api_key": TEST_KEY, **extra})

    async def test_first_install_requires_user_key(self):
        self.assertFalse(self.e.settings()["configured"])
        with self.assertRaises(GameError):
            self.e.new(6, "classic", 1)
        self.assertNotIn("api_key", self.e.settings())

    async def test_key_persists_only_in_settings(self):
        self.configure()
        stored = json.loads(self.e.config_path.read_text())
        self.assertEqual(stored["api_key"], TEST_KEY)
        self.assertTrue(stored["user_api"])
        self.assertNotIn(TEST_KEY, json.dumps(self.e.settings()))
        restored = Engine(self.data.name)
        self.assertEqual(restored.config["api_key"], TEST_KEY)
        await restored.client.aclose()
        restored.db.close()

    async def test_legacy_credentials_not_inherited(self):
        self.e.config_path.write_text(json.dumps({"key_revision": 2, "api_key": TEST_KEY}))
        restored = Engine(self.data.name)
        self.assertFalse(restored.settings()["configured"])
        await restored.client.aclose()
        restored.db.close()

    async def test_provider_change_needs_corresponding_key(self):
        self.configure()
        with self.assertRaises(GameError):
            self.e.configure({"base_url": "https://api.agnes-ai.cn/v1"})
        self.assertEqual(self.e.settings()["base_url"], "https://api.deepseek.com/v1")
        self.configure(base_url="https://api.agnes-ai.cn/v1", model="agnes-3.0-flash")
        self.assertEqual(self.e.settings()["model"], "agnes-3.0-flash")

    async def test_blank_key_not_accepted_and_omission_preserves_key(self):
        self.configure()
        with self.assertRaises(GameError):
            self.e.configure({"api_key": "  "})
        self.e.configure({"model": "deepseek-chat"})
        self.assertEqual(self.e.config["api_key"], TEST_KEY)

    async def test_invalid_configuration_does_not_replace_working_settings(self):
        self.configure()
        for values in ({"base_url": "http://example.com"}, {"base_url": "https://user:password@example.com"},
                       {"base_url": "https://example.com/?key=test"}, {"base_url": "https://example.com/#test"},
                       {"model": ""}, {"api_key": "test\nkey"}):
            with self.subTest(values=values):
                with self.assertRaises(GameError):
                    self.e.configure(values)
                self.assertEqual(self.e.config["api_key"], TEST_KEY)
                self.assertEqual(self.e.settings()["base_url"], "https://api.deepseek.com/v1")

    async def test_all_fixed_boards_and_human_modes(self):
        self.configure()
        for mode, info in MODES.items():
            for count in range(info["min"], 16):
                for humans in (1, 2):
                    self.e.new(count, mode, 1, human_count=humans)
                    await self.e.pause()
                    self.assertEqual(len(self.e.g["players"]), count)
                    self.assertEqual(len(self.e.humans()), humans)
                    self.assertTrue(all(p["role"] in ROLES for p in self.e.g["players"]))
                    view = self.e.view()
                    self.assertNotIn(TEST_KEY, json.dumps(view))
                    if humans == 2:
                        self.assertTrue(view["locked"])
                        self.assertFalse(view["my_role"])
                        self.assertEqual(view["private"], [])

    async def test_double_player_handoff_and_saved_privacy(self):
        self.configure()
        self.e.new(6, "classic", 1, human_count=2)
        await asyncio.wait_for(self.e.task, 1)
        self.assertEqual(self.e.view()["handoff"], 1)
        await self.e.handoff(True)
        self.assertEqual(self.e.view()["human"], 1)
        await self.e.submit({})
        await asyncio.wait_for(self.e.task, 1)
        self.assertTrue(self.e.view()["locked"])
        self.assertEqual(self.e.view()["handoff"], 2)
        await self.e.handoff(True)
        self.assertEqual(self.e.view()["human"], 2)
        await self.e.pause()
        restored = Engine(self.data.name)
        self.assertTrue(restored.view()["locked"])
        self.assertFalse(restored.view()["my_role"])
        await restored.client.aclose()
        restored.db.close()

    async def test_hiding_dealt_card_completes_handoff(self):
        self.configure()
        self.e.new(6, "classic", 1, human_count=2)
        await asyncio.wait_for(self.e.task, 1)
        await self.e.handoff(True)
        await self.e.handoff(False)
        await asyncio.wait_for(self.e.task, 1)
        self.assertEqual(self.e.view()["handoff"], 2)
        self.assertEqual(self.e.g["queue"][0]["kind"], "deal")
        self.assertEqual(self.e.g["queue"][0]["seat"], 2)
        # 上一位重复发送遮屏，不得替尚未接过手机的下一位确认身份。
        await self.e.handoff(False)
        self.assertEqual(self.e.g["queue"][0]["seat"], 2)

    async def test_hiding_during_skill_or_pause_does_not_submit_action(self):
        self.configure()
        self.e.new(6, "classic", 1, human_count=2)
        await asyncio.wait_for(self.e.task, 1)
        await self.e.handoff(True)
        await self.e.pause()
        await self.e.handoff(False)
        self.assertEqual(self.e.g["queue"][0]["kind"], "deal")
        self.e.g["paused"] = False
        self.e.g["human"] = 1
        self.e.g["queue"] = [self.e.action("inspect", 1)]
        self.e.g["waiting"] = self.e.request(self.e.g["queue"][0])
        await self.e.handoff(False)
        self.assertEqual(self.e.g["queue"][0]["kind"], "inspect")
        self.assertTrue(self.e.view()["locked"])
        await self.e.handoff(True)
        self.assertEqual(self.e.view()["waiting"]["kind"], "inspect")

    async def test_both_handoff_buttons_reach_first_dawn(self):
        self.configure()
        for count in (6, 10, 15):
            for hide_button in (False, True):
                with self.subTest(count=count, hide_button=hide_button):
                    async def ai_choice(action):
                        targets = self.e.request(action)["targets"]
                        return {"target": targets[0] if action["kind"] in REQUIRED_TARGETS else None,
                                "choice": "pass", "text": "继续观察。", "plan": "继续观察。"}

                    # 只替换测试中的模型与提示音等待，保留真实队列、技能和交接结算。
                    with patch.object(self.e, "ai", side_effect=ai_choice), patch("game.asyncio.sleep", new=AsyncMock()):
                        self.e.new(count, "classic", 1, human_count=2)
                        deals = []
                        for _ in range(40):
                            await asyncio.wait_for(self.e.task, 1)
                            self.assertFalse(self.e.g["paused"], self.e.g["error"])
                            if self.e.g["phase"] != "night":
                                break
                            view = self.e.view()
                            if not view["locked"]:
                                self.assertEqual(view["waiting"]["kind"], "review")
                                await self.e.handoff(False)
                                continue
                            self.assertTrue(view["locked"])
                            self.assertIn(view["handoff"], (1, 2))
                            await self.e.handoff(True)
                            waiting = self.e.view()["waiting"]
                            if waiting["kind"] == "deal":
                                deals.append(waiting["seat"])
                            if waiting["kind"] == "review" or hide_button and waiting["kind"] in ("deal", "night_pass"):
                                await self.e.handoff(False)
                            else:
                                targets = waiting["targets"]
                                await self.e.submit({"choice": "pass", "target": targets[0] if waiting["kind"] in REQUIRED_TARGETS else None})
                        else:
                            self.fail("双人交接没有在首夜结束前收敛。")
                        self.assertEqual(deals, [1, 2])
                        self.assertIn(self.e.g["phase"], ("day", "election"))
                        self.assertTrue(any("天亮请睁眼" in event["text"] for event in self.e.g["events"]))
                        await self.e.pause()

    async def test_deepseek_and_agnes_tool_request_payload(self):
        self.configure()
        self.e.new(6, "classic", 1)
        await self.e.pause()
        action = self.e.action("vote", 2)
        for provider, model in (("https://api.deepseek.com/v1", "deepseek-chat"),
                                ("https://api.agnes-ai.cn/v1", "agnes-3.0-flash")):
            self.configure(base_url=provider, model=model)
            self.e.request_at = 0
            requests = []

            def reply(request):
                requests.append(request)
                return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
                    {"function": {"name": "act", "arguments": json.dumps({
                        "target": None, "choice": "act", "text": "这轮先弃票。", "plan": "继续观察发言。"
                    }, ensure_ascii=False)}}
                ]}}]})

            await self.e.client.aclose()
            self.e.client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
            result = await self.e.ai(action)
            self.assertEqual(result["target"], None)
            self.assertEqual(str(requests[0].url), provider + "/chat/completions")
            self.assertEqual(requests[0].headers["Authorization"], "Bearer " + TEST_KEY)
            payload = json.loads(requests[0].content)
            self.assertEqual(payload["model"], model)
            self.assertEqual("chat_template_kwargs" in payload, "agnes" in provider)


if __name__ == "__main__":
    unittest.main()
