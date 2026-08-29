# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from core import mailnest_client


class MailNestClientTests(unittest.TestCase):
    def setUp(self):
        mailnest_client._CONTEXT_CACHE.clear()

    def test_pick_account_requires_api_key(self):
        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "", create=True):
            with self.assertRaisesRegex(mailnest_client.MailNestClientError, "MailNest API Key"):
                mailnest_client.pick_account()

    @patch("core.mailnest_client.requests.request")
    def test_pick_account_buys_exclusive_mailbox(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {"code": "00000", "data": [{"email": "fresh@mailnest.test"}]}
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True), patch.object(
            mailnest_client._email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001", create=True
        ):
            account = mailnest_client.pick_account()

        self.assertEqual(account.email, "fresh@mailnest.test")
        self.assertIs(mailnest_client.get_account_context("fresh@mailnest.test"), account)
        request.assert_called_once()
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer key-123")
        self.assertEqual(request.call_args.args[1], "https://mailnest.top/api/v1/email/exclusive/buy")
        self.assertEqual(kwargs["json"], {"count": 1})

    @patch("core.mailnest_client.requests.request")
    def test_pick_account_buys_a_new_exclusive_mailbox_each_time(self, request):
        response = Mock(status_code=200)
        response.json.side_effect = [
            {"code": "00000", "data": [{"email": "first@mailnest.test"}]},
            {"code": "00000", "data": [{"email": "second@mailnest.test"}]},
        ]
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            first = mailnest_client.pick_account()
            second = mailnest_client.pick_account()

        self.assertEqual((first.email, second.email), ("first@mailnest.test", "second@mailnest.test"))
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.args[1], "https://mailnest.top/api/v1/email/exclusive/buy")
            self.assertEqual(call.kwargs["json"], {"count": 1})

    @patch("core.mailnest_client.time.sleep")
    @patch("core.mailnest_client.requests.request")
    def test_fetch_latest_otp_reads_code_match(self, request, sleep):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [{"code_match": "654321", "subject": "OpenAI code", "from": "noreply@openai.com", "timestamp": 205}],
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            code = mailnest_client.fetch_latest_otp(
                "fresh@mailnest.test",
                after_ts=200,
                max_wait=1,
                poll_interval=1,
                settle_seconds=0,
            )

        self.assertEqual(code, "654321")


if __name__ == "__main__":
    unittest.main()
