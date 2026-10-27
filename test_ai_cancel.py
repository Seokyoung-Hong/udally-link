"""실제 로컬 HTTP 응답 대기 중 취소와 재요청 방지 검증."""
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import link

class CancelTests(unittest.TestCase):
    def test_interrupt_pending_response(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                entered.set()
                release.wait(5)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        timer = threading.Timer(0.1, lambda: (entered.wait(2), cancel.set()))
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(link.AiFail) as exc:
                link.ai_post(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", {}, "", 30, cancel=cancel)
            self.assertEqual(exc.exception.code, "cancelled")
            self.assertLess(time.monotonic() - started, 2)
        finally:
            release.set();timer.cancel();server.shutdown();server.server_close()

    def test_cancel_before_option_retry(self):
        job = link.AiJob({"id": 1, "url": "http://127.0.0.1:1/v1", "response_format": {"type": "json_object"}}, "test")
        def fail(*args, **kwargs):
            job.cancel.set()
            raise link.AiFail("http_4xx")
        with patch.object(link, "ai_post", side_effect=fail) as request:
            with self.assertRaises(link.AiFail) as exc:
                job.ask()
            self.assertEqual(exc.exception.code, "cancelled")
            self.assertEqual(request.call_count, 1)

if __name__ == "__main__": unittest.main()
