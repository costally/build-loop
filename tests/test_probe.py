import hashlib
import unittest
import unicodedata
from pathlib import Path

from build_loop.probe import observe_user_reply
from build_loop.rpc import ReadOnlyCodex, RpcError


class FakeReadClient:
    def __init__(self, entries):
        self.entries = entries
        self.methods = []

    def call(self, method, params):
        self.methods.append(method)
        if method == "thread/read":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/turns/list":
            return {"data": [{"id": "turn-1"}]}
        if method == "thread/items/list":
            return {"data": self.entries}
        raise AssertionError(method)


def entry(kind="userMessage", text="승인", turn="turn-1"):
    return {"turnId": turn, "item": {"id": "item-1", "type": kind,
            "content": [{"type": "text", "text": text}]}}


class ProbeTests(unittest.TestCase):
    def test_current_protocol_entry_wrapper_and_user_source(self):
        client = FakeReadClient([entry()])
        receipt = observe_user_reply(client, "thread-1", "승인")
        self.assertEqual(receipt["item_id"], "item-1")
        self.assertNotIn("text", receipt)
        self.assertEqual(receipt["text_sha256"], hashlib.sha256("승인".encode()).hexdigest())
        self.assertEqual(client.methods, ["thread/read", "thread/turns/list", "thread/items/list"])

    def test_assistant_text_is_not_a_user_reply(self):
        self.assertIsNone(observe_user_reply(FakeReadClient([entry("agentMessage")]), "thread-1", "승인"))

    def test_unrelated_user_reply_is_not_accepted(self):
        self.assertIsNone(observe_user_reply(FakeReadClient([entry(text="다른 답")]), "thread-1", "승인"))

    def test_wrong_turn_is_rejected(self):
        with self.assertRaises(RpcError):
            observe_user_reply(FakeReadClient([entry(turn="another-turn")]), "thread-1", "승인")

    def test_hangul_nfc_is_normalized(self):
        client = FakeReadClient([entry(text=unicodedata.normalize("NFD", "승인"))])
        self.assertIsNotNone(observe_user_reply(client, "thread-1", "승인"))

    def test_model_turn_start_cannot_be_sent(self):
        client = ReadOnlyCodex(Path(__file__))
        with self.assertRaises(RpcError):
            client.call("turn/start", {})


if __name__ == "__main__":
    unittest.main()
