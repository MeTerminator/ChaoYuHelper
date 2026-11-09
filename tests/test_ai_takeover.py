import asyncio
import unittest

from app.ai_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    format_questions_for_ai,
    get_ai_config,
    normalize_base_url,
    parse_ai_answers,
    save_ai_config,
)
from app.auto_answer import AutoAnswer
from app.providers.base import ProviderError


class DummyStore:
    def __init__(self):
        self.data = {}

    def get(self, kind, key, default=None):
        return self.data.get((kind, key), default)

    def put(self, kind, key, value):
        self.data[(kind, key)] = value


class TestAITakeover(unittest.TestCase):
    def test_normalize_base_url(self):
        self.assertEqual(normalize_base_url("https://api.openai.com/v1/"), "https://api.openai.com/v1")
        self.assertEqual(normalize_base_url("https://api.openai.com/v1/chat/completions"), "https://api.openai.com/v1")
        self.assertEqual(normalize_base_url(""), DEFAULT_BASE_URL)

    def test_ai_config_get_and_save(self):
        store = DummyStore()
        cfg = get_ai_config(store)
        self.assertEqual(cfg["base_url"], DEFAULT_BASE_URL)
        self.assertEqual(cfg["model"], DEFAULT_MODEL)
        self.assertEqual(cfg["api_key"], "")

        save_ai_config(store, "https://my-proxy.com/v1/", "gpt-4o", "sk-test123456")
        cfg2 = get_ai_config(store)
        self.assertEqual(cfg2["base_url"], "https://my-proxy.com/v1")
        self.assertEqual(cfg2["model"], "gpt-4o")
        self.assertEqual(cfg2["api_key"], "sk-test123456")

    def test_format_questions_for_ai(self):
        questions = [
            {
                "id": "q1",
                "type": 0,
                "content": "<p>消费者预算线向内旋转的原因是？</p>",
                "answer": [
                    {"name": "A", "content": "价格上升"},
                    {"name": "B", "content": "价格下降"},
                ],
            },
            {
                "id": "q2",
                "type": 1,
                "content": "<p>多选题测试</p>",
                "answer": [
                    {"name": "A", "content": "选项1"},
                    {"name": "B", "content": "选项2"},
                    {"name": "C", "content": "选项3"},
                ],
            },
            {
                "id": "q3",
                "type": 2,
                "content": "<p>填空题（  ）和（  ）</p>",
                "answer": [{"name": "1"}, {"name": "2"}],
            },
            {
                "id": "q4",
                "type": 16,
                "content": "<p>判断题</p>",
                "answer": [{"name": "1", "content": "对"}, {"name": "2", "content": "错"}],
            },
            {
                "id": "q5",
                "type": 4,
                "content": "<p>简答题分析供求关系</p>",
            },
        ]

        formatted = format_questions_for_ai(questions, "微观经济学", "随堂练习1")
        self.assertEqual(len(formatted), 5)
        self.assertEqual(formatted[0]["type_name"], "单选题")
        self.assertEqual(formatted[0]["options"], ["A: 价格上升", "B: 价格下降"])
        self.assertEqual(formatted[1]["type_name"], "多选题")
        self.assertEqual(formatted[2]["type_name"], "填空题")
        self.assertEqual(formatted[2]["blank_count"], 2)
        self.assertEqual(formatted[3]["type_name"], "判断题")
        self.assertEqual(formatted[4]["type_name"], "简答题")

    def test_parse_ai_answers_all_types(self):
        questions = [
            {
                "id": "101",
                "type": 0,
                "answer": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
            },
            {
                "id": "102",
                "type": 1,
                "answer": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}, {"name": "E"}],
            },
            {
                "id": "103",
                "type": 2,
                "answer": [{"name": "1"}, {"name": "2"}, {"name": "3"}],
            },
            {
                "id": "104",
                "type": 16,
                "answer": [{"name": "1", "content": "对"}, {"name": "2", "content": "错"}],
            },
            {
                "id": "105",
                "type": 4,
            },
        ]

        llm_output = """```json
        {
          "answers": {
            "101": "C",
            "102": ["A", "B", "D"],
            "103": ["4", "9", "5"],
            "104": "错",
            "105": "供求关系影响均衡价格和均衡数量。"
          }
        }
        ```"""

        answers = parse_ai_answers(llm_output, questions)
        self.assertEqual(answers["101"], "C")
        self.assertEqual(answers["102"], ["A", "B", "D"])
        self.assertEqual(answers["103"], ["4", "9", "5"])
        self.assertEqual(answers["104"], "2")  # Mapped "错" to option key "2"
        self.assertEqual(answers["105"], "供求关系影响均衡价格和均衡数量。")

    def test_strict_quick_answer_protection(self):
        # 抢答 MUST BE REJECTED
        self.assertTrue(AutoAnswer.is_quick_answer(4, "课堂互动"))
        self.assertTrue(AutoAnswer.is_quick_answer("4", "活动"))
        self.assertTrue(AutoAnswer.is_quick_answer(42, "本节随堂抢答练习"))
        self.assertFalse(AutoAnswer.is_quick_answer("42", "随堂练习"))
        self.assertFalse(AutoAnswer.is_quick_answer(43, "课堂投票"))
        self.assertFalse(AutoAnswer.is_quick_answer(14, "调查问卷"))

    def test_auto_answer_refuses_quick_answer(self):
        class DummyService:
            def __init__(self):
                self.store = DummyStore()

            def settings(self, *args):
                return {"aiTakeover": True}

        svc = DummyService()
        aa = AutoAnswer(svc)

        with self.assertRaises(ProviderError):
            asyncio.run(aa.answer_now("acc1", {"courseId": "1"}, {"id": "act1", "type": 4, "name": "抢答"}))

        with self.assertRaises(ProviderError):
            asyncio.run(aa.answer_now("acc1", {"courseId": "1"}, {"id": "act2", "type": 42, "name": "课堂抢答题"}))


if __name__ == "__main__":
    unittest.main()
