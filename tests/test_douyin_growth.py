import io
import json
import unittest
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from admin.backend import AccountSession
from admin.growth_plans import classify, plan_for


class DouyinGrowthTests(unittest.TestCase):
    def test_named_task_is_actionable(self):
        task = classify({"task_code": "expert_use_douyinyin", "reward_credit": 50})
        self.assertEqual(task["level"], "auto")
        self.assertTrue(task["actionable"])
        self.assertEqual(plan_for(task["task_code"]).firer, "fire_douyin_expert")

    def test_other_expert_tasks_are_registered(self):
        for code, firer in [("expert_use_maidehao", "fire_maidehao_expert"),
                            ("expert_use_shangjia_xuanchuan", "fire_shangjia_xuanchuan_expert"),
                            ("expert_use_bowangwang", "fire_bowangwang_expert"),
                            ("WorkBuddy_Valorant", "fire_valorant_expert")]:
            task = classify({"task_code": code, "reward_credit": 50})
            self.assertTrue(task["actionable"], code)
            self.assertEqual(task["level"], "auto")
            self.assertTrue(callable(getattr(AccountSession, firer)), firer)

    def test_unknown_and_unrewarded_tasks_remain_disabled(self):
        self.assertFalse(plan_for("expert_use_unknown").actionable)
        self.assertFalse(classify({"task_code": "expert_use_douyinyin", "reward_credit": 0})["actionable"])

    def session(self, *, link=True, expert=True, complete=True, reply=True,
                code="expert_use_douyinyin", listed=None):
        s = AccountSession.__new__(AccountSession)
        task = {"task_code": code, "title": "体验「无畏契约拼豆专家」"}
        if link == "bare":
            task["jump_url"] = "workbuddy://expert"
        elif link:
            task["jump_url"] = "https://www.workbuddy.cn/work/launch/?deeplink=workbuddy%3A%2F%2Fexperts%3FexpertId%3Dex_target"
        s.growth_tasks = Mock(return_value=[task])
        item = {"expert_id": "ex_target" if expert else "ex_wrong", "agent_name": "douyin-strategist",
                "display_name_zh": "斗音音", "profession_zh": "抖音策略师", "version": "1.0.2"}
        def api(path, body):
            if path.endswith("/list"):
                self.assertEqual(body["keyword"], "无畏契约拼豆专家")
                return {"experts": listed or []}
            if path.endswith("get-by-ids"):
                self.assertEqual(body["expert_ids"], ["ex_target"])
                return {"experts": [item]}
            self.assertEqual(body["expert_id"], "ex_target")
            return {"download_url": "https://openplatform-cdn.codebuddy.cn/expert.zip"}
        s._chat_json = Mock(side_effect=api)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("agents/douyin-strategist.md", "You are the Douyin strategy expert.")
        response = Mock(content=archive.getvalue())
        stream = Mock(status_code=200)
        chunks = [{"id": "chatcmpl-real", "model": "hy3", "choices": [{"delta": {"content": "先确定账号定位。" if reply else ""}}]}]
        if complete:
            chunks.append({"id": "chatcmpl-real", "choices": [{"delta": {}, "finish_reason": "stop"}]})
        stream.iter_lines.return_value = ["data: " + json.dumps(x) for x in chunks]
        calls = []
        client = Mock()
        client.get.return_value = response
        @contextmanager
        def stream_call(*args, **kwargs):
            calls.append(("chat", kwargs["json"]))
            yield stream
        client.stream.side_effect = stream_call
        @contextmanager
        def http(*args, **kwargs):
            yield client
        s.http_client = http
        s.cm = SimpleNamespace(get_headers=lambda: {})
        s._uid = lambda: "account-test"
        def report(events):
            calls.append(("report", events))
            return {"ok": True, "status": 200}
        s.report_billing_event = Mock(side_effect=report)
        return s, calls

    def test_missing_link_fails_without_report(self):
        s, calls = self.session(link=False)
        self.assertFalse(s.fire_douyin_expert()["ok"])
        self.assertEqual(calls, [])

    def test_same_title_different_id_is_rejected(self):
        s, calls = self.session(expert=False)
        self.assertFalse(s.fire_douyin_expert()["ok"])
        self.assertEqual(calls, [])

    def test_incomplete_dialogue_does_not_report_usage(self):
        s, calls = self.session(complete=False)
        self.assertFalse(s.fire_douyin_expert()["ok"])
        self.assertEqual([x[0] for x in calls], ["chat"])

    def test_empty_dialogue_does_not_report_usage(self):
        s, calls = self.session(reply=False)
        self.assertFalse(s.fire_douyin_expert()["ok"])
        self.assertEqual([x[0] for x in calls], ["chat"])

    def test_real_dialogue_precedes_correct_expert_report(self):
        s, calls = self.session()
        result = s.fire_douyin_expert()
        self.assertTrue(result["ok"])
        self.assertEqual([x[0] for x in calls], ["chat", "report"])
        body, event = calls[0][1], calls[1][1][0]
        self.assertIn("Douyin strategy expert", body["messages"][0]["content"])
        self.assertGreater(body["max_tokens"], 1)
        self.assertEqual(event["id"], "ex_target")
        self.assertEqual(event["requestId"], "chatcmpl-real")
        self.assertEqual(event["characterCount"], len(result["reply"]))


    def test_bare_link_uses_unique_exact_name_match(self):
        listed = [{"expert_id": "ex_target", "display_name_zh": "无畏契约拼豆专家"},
                  {"expert_id": "ex_other", "display_name_zh": "无畏契约拼豆专家（同人）"}]
        s, calls = self.session(link="bare", code="WorkBuddy_Valorant", listed=listed)
        result = s.fire_valorant_expert()
        self.assertTrue(result["ok"])
        self.assertEqual(calls[1][1][0]["id"], "ex_target")

    def test_bare_link_with_ambiguous_name_is_rejected(self):
        listed = [{"expert_id": "ex_target", "display_name_zh": "无畏契约拼豆专家"},
                  {"expert_id": "ex_copy", "display_name_zh": "无畏契约拼豆专家"}]
        s, calls = self.session(link="bare", code="WorkBuddy_Valorant", listed=listed)
        self.assertFalse(s.fire_valorant_expert()["ok"])
        self.assertEqual(calls, [])

    def test_wrapper_uses_its_own_task_link(self):
        s, calls = self.session(code="expert_use_maidehao")
        self.assertTrue(s.fire_maidehao_expert()["ok"])
        self.assertFalse(s.fire_bowangwang_expert()["ok"])


if __name__ == "__main__":
    unittest.main()
