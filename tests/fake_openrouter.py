"""A fake OpenRouter for the translation tests: a server on a port of its own (never 7860 or 8765) that
answers POST /api/v1/chat/completions the way the real one does. It "translates" a cue by writing
"[<language code>] <text>". `script` is a list of behaviours that are used up one request at a time
(the rest of the requests are answered normally): "ok", "http500", "http429", "http401", "garbage",
"missing" (drops one id), "extra" (adds an id), "empty" (an empty text), "number" (a number as a text),
"fenced" (the JSON in a markdown fence).

A request with "translate" is a translation; a request with "shorten" is the follow-up that asks for a shorter
text (SPECS 9.9). `translations` maps (language code, English text) to the translation to give instead of
"[<language code>] <text>". For a follow-up, `shorten_mode` says what the fake does: "cut" (the default: the
translation cut to max_chars, a model that obeys), "same" (the translation unchanged, a model that cannot shorten),
"longer" (the translation with a tail, a model that makes it worse) or "given" (the text in `shortenings`, a map from
(language code, English text) to the shorter text, else the translation cut). `usage` lists the (prompt, completion)
tokens that each answer reported."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOpenRouter:
    def __init__(self, key="sk-or-test-key", script=(), translations=None, shorten_mode="cut", shortenings=None):
        self.key = key
        self.script = list(script)
        self.translations = dict(translations or {})
        self.shorten_mode = shorten_mode
        self.shortenings = dict(shortenings or {})
        self.usage = []
        self.requests = []
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                with outer.lock:
                    behaviour = outer.script.pop(0) if outer.script else "ok"
                    outer.requests.append({"path": self.path, "body": body,
                                           "auth_ok": self.headers.get("Authorization") == f"Bearer {outer.key}",
                                           "behaviour": behaviour})
                if self.path != "/api/v1/chat/completions":
                    return self._send(404, {"error": {"message": "not found"}})
                if self.headers.get("Authorization") != f"Bearer {outer.key}" or behaviour == "http401":
                    return self._send(401, {"error": {"message": "No auth credentials found"}})
                if behaviour == "http500":
                    return self._send(500, {"error": {"message": "upstream error"}})
                if behaviour == "http429":
                    return self._send(429, {"error": {"message": "rate limited"}})
                user = json.loads(body["messages"][-1]["content"])
                lang = user["target_language"]
                if "shorten" in user:
                    answer = {c["id"]: outer.shorter(lang, c) for c in user["shorten"]}
                else:
                    answer = {c["id"]: outer.translations.get((lang, c["text"]), f"[{lang}] {c['text']}")
                              for c in user["translate"]}
                if behaviour == "missing" and answer:
                    answer.pop(next(iter(answer)))
                elif behaviour == "extra":
                    answer["c999"] = "stray"
                elif behaviour == "empty" and answer:
                    answer[next(iter(answer))] = "  "
                elif behaviour == "number" and answer:
                    answer[next(iter(answer))] = 5
                content = json.dumps(answer, ensure_ascii=False)
                if behaviour == "garbage":
                    content = "I cannot help with that {"
                elif behaviour == "fenced":
                    content = "```json\n" + content + "\n```"
                tokens = max(1, len(json.dumps(user)) // 4)
                with outer.lock:
                    outer.usage.append((tokens + 100, tokens))
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                                 "usage": {"prompt_tokens": tokens + 100, "completion_tokens": tokens}})

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/api/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def shorter(self, lang, item):
        """The answer to one item of a follow-up request, by `shorten_mode`."""
        current, limit = item["translation"], int(item["max_chars"])
        if self.shorten_mode == "same":
            return current
        if self.shorten_mode == "longer":
            return current + "ですよ"
        if self.shorten_mode == "given" and (lang, item["english"]) in self.shortenings:
            return self.shortenings[(lang, item["english"])]
        return current[:limit]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def count(self):
        return len(self.requests)
