import base64
import json
import logging
import re
import time
import unittest
from unittest.mock import patch

import httpx
from supabase import create_client

from run import app


def jwt(subject="ana", expired=False):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode({"sub": subject, "exp": int(time.time()) + (-60 if expired else 3600)})}.c2lnbmF0dXJl'


def user(name="Ana", confirmed=True):
    return dict(id="00000000-0000-0000-0000-000000000001", aud="authenticated",
                role="authenticated", email="ana@example.com", app_metadata={},
                user_metadata={"nome": name}, created_at="2026-01-01T00:00:00Z",
                email_confirmed_at="2026-01-01T00:00:00Z" if confirmed else None)


class LoginTests(unittest.TestCase):
    def setUp(self):
        original = app.config.copy()
        self.addCleanup(app.config.update, original)
        app.config.update(TESTING=True, SECRET_KEY="test-secret",
                          SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test",
                          SESSION_COOKIE_SECURE=False)
        self.client = app.test_client()
        self.calls = []
        self.clients = []
        self.failure = None
        self.logout_failure = False
        self.confirmed = True
        self.name = "Ana"
        self.access = jwt()
        self.refresh = "private-refresh-token"

        def transport(request):
            self.calls.append(request)
            if self.failure:
                status, code = self.failure
                return httpx.Response(status, json={"msg": "private provider details", "code": code},
                                      headers={"X-Supabase-Api-Version": "2024-01-01"})
            if request.url.path.endswith("/user"):
                return httpx.Response(200, json=user(self.name, self.confirmed))
            if request.url.path.endswith("/logout"):
                if self.logout_failure:
                    raise httpx.ConnectError("private network details")
                return httpx.Response(204)
            return httpx.Response(200, json=dict(
                access_token=self.access, refresh_token=self.refresh,
                token_type="bearer", expires_in=3600, user=user(self.name, self.confirmed)))

        def factory(url, key, options):
            self.assertFalse(options.persist_session)
            self.assertFalse(options.auto_refresh_token)
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            sdk = create_client(url, key, options=options)
            self.clients.append(sdk)
            return sdk

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def csrf(self, path="/login", client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text).group(1)

    def login(self, **changes):
        data = dict(email=" ana@example.com ", senha="private-password", csrf_token=self.csrf())
        data.update(changes)
        return self.client.post("/login", data=data)

    def cookies(self, access=None, refresh=None, client=None):
        client = client or self.client
        client.set_cookie("adotae_access", access or self.access)
        client.set_cookie("adotae_refresh", refresh or self.refresh)

    def test_login_and_persistent_account_without_exposing_credentials(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        sent = json.loads(self.calls[0].content)
        self.assertEqual(sent["email"], "ana@example.com")
        self.assertEqual(sent["password"], "private-password")
        for name, value in (("adotae_access", self.access), ("adotae_refresh", self.refresh)):
            cookie = self.client.get_cookie(name)
            self.assertEqual(cookie.value, value)
            self.assertTrue(cookie.http_only)
            self.assertEqual(cookie.same_site, "Lax")
            self.assertTrue(cookie.origin_only)
        for path in ("/minha-conta", "/", "/animais"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertIn("Minha conta", page.text)
            self.assertEqual(page.headers["Cache-Control"], "no-store")
            for secret in (self.access, self.refresh, "private-password"):
                self.assertNotIn(secret, page.text)
                self.assertNotIn(secret, page.headers.get("Location", ""))
        self.assertIn("Olá, Ana", self.client.get("/minha-conta").text)
        with self.client.session_transaction() as session:
            self.assertEqual(set(session), {"csrf_token"})
        self.assertGreater(len(self.clients), 1)

    def test_invalid_and_unconfirmed_credentials(self):
        for code, message in (("invalid_credentials", "E-mail ou senha"),
                              ("email_not_confirmed", "Confirme seu e-mail")):
            self.failure = (400, code)
            response = self.login()
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.text)
            self.assertNotIn("private-password", response.text)
            self.assertNotIn("private provider details", response.text)
            self.assertIsNone(self.client.get_cookie("adotae_access"))

    def test_server_validation(self):
        for fields in ({"email": "bad"}, {"email": ""}, {"senha": ""}, {"senha": "   "}):
            response = self.login(**fields)
            self.assertEqual(response.status_code, 400)
            self.assertIn('role="alert"', response.text)
        self.assertEqual(self.calls, [])

    def test_login_accepts_test_domain_but_rejects_malformed_addresses(self):
        for email in ("teste.aexample.test", "teste.a@@example.test", "teste.a@.test"):
            with self.subTest(email=email):
                response = self.login(email=email)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Informe um e-mail válido.", response.text)
        self.assertEqual(self.calls, [])

        response = self.login(email="teste.a@example.test")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(json.loads(self.calls[0].content)["email"], "teste.a@example.test")

    def test_unconfirmed_user_even_if_provider_allows_password_login(self):
        self.confirmed = False
        response = self.login()
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.client.get_cookie("adotae_access"))

    def test_requires_remote_validated_identity(self):
        self.assertEqual(self.client.get("/minha-conta").location, "/login")
        self.cookies()
        self.failure = (401, "bad_jwt")
        response = self.client.get("/minha-conta")
        self.assertEqual(response.location, "/login")
        self.assertIsNone(self.client.get_cookie("adotae_access"))
        self.assertIsNone(self.client.get_cookie("adotae_refresh"))

    def test_expired_session_refreshes_and_validates_new_identity(self):
        self.cookies(access=jwt(expired=True), refresh="old-refresh")
        response = self.client.get("/minha-conta")
        self.assertEqual(response.status_code, 200)
        refresh_call = next(r for r in self.calls if r.url.path.endswith("/token"))
        self.assertEqual(json.loads(refresh_call.content), {"refresh_token": "old-refresh"})
        self.assertTrue(any(r.url.path.endswith("/user") for r in self.calls))
        self.assertEqual(self.client.get_cookie("adotae_access").value, self.access)
        self.assertEqual(self.client.get_cookie("adotae_refresh").value, self.refresh)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_invalid_refresh_and_malformed_cookies_require_login(self):
        for access in (jwt(expired=True), "bad", "a.b.c"):
            self.cookies(access=access)
            self.failure = (400, "refresh_token_not_found")
            response = self.client.get("/minha-conta")
            self.assertEqual(response.location, "/login")
            self.assertIsNone(self.client.get_cookie("adotae_refresh"))

    def test_outage_does_not_grant_access_or_expose_details(self):
        self.failure = (503, "unexpected_failure")
        response = self.login()
        self.assertEqual(response.status_code, 503)
        self.cookies()
        response = self.client.get("/minha-conta")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private provider details", response.text)
        self.assertNotIn("Olá,", response.text)
        # Uma falha temporária não precisa destruir uma sessão recuperável.
        self.assertIsNotNone(self.client.get_cookie("adotae_refresh"))

    def test_logout_post_only_local_scope_and_deletes_all_local_cookies(self):
        self.cookies()
        token = self.csrf("/minha-conta")
        self.assertEqual(self.client.get("/logout").status_code, 405)
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        logout = next(r for r in self.calls if r.url.path.endswith("/logout"))
        self.assertEqual(logout.url.params["scope"], "local")
        for name in ("adotae_access", "adotae_refresh", "session"):
            self.assertIsNone(self.client.get_cookie(name))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_logout_clears_cookies_during_provider_outage(self):
        self.cookies()
        token = self.csrf("/minha-conta")
        self.failure = (503, "unexpected_failure")
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn("neste navegador", response.text)
        self.assertIsNone(self.client.get_cookie("adotae_refresh"))

    def test_csrf_on_login_and_logout_does_not_render_signup(self):
        foreign = self.csrf(client=app.test_client())
        self.csrf()
        for path in ("/login", "/logout"):
            for token in ("", "invalid", foreign):
                response = self.client.post(path, data={"csrf_token": token})
                self.assertEqual(response.status_code, 400)
                self.assertIn("Recarregue", response.text)
                self.assertNotIn('name="nome"', response.text)
        self.assertEqual(self.calls, [])

    def test_users_are_isolated_and_names_escaped(self):
        other = app.test_client()
        bia_access = jwt("bia")
        self.cookies()
        self.cookies(access=bia_access, refresh="bia-refresh", client=other)
        self.assertIn("Olá, Ana", self.client.get("/minha-conta").text)
        self.name = "<script>Bia</script>"
        page = other.get("/minha-conta")
        self.assertIn("&lt;script&gt;Bia&lt;/script&gt;", page.text)
        self.assertNotIn("<script>Bia</script>", page.text)
        authorizations = [r.headers["authorization"] for r in self.calls if r.url.path.endswith("/user")]
        self.assertIn(f"Bearer {bia_access}", authorizations)
        self.assertIn(f"Bearer {self.access}", authorizations)
        self.assertEqual(len(self.clients), 2)

    def test_secure_cookies_in_production(self):
        with patch.dict(app.config, SESSION_COOKIE_SECURE=True):
            response = self.login()
        for cookie in response.headers.getlist("Set-Cookie"):
            self.assertIn("Secure", cookie)

    def test_logout_network_failure_still_clears_cookies(self):
        self.cookies()
        token = self.csrf("/minha-conta")
        self.logout_failure = True
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn("neste navegador", response.text)
        self.assertNotIn("private network details", response.text)
        for name in ("adotae_access", "adotae_refresh", "session"):
            self.assertIsNone(self.client.get_cookie(name))

    def test_invalid_logout_csrf_preserves_session(self):
        self.cookies()
        self.csrf("/minha-conta")
        response = self.client.post("/logout", data={"csrf_token": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(self.client.get_cookie("adotae_refresh"))
        self.assertFalse(any(r.url.path.endswith("/logout") for r in self.calls))

    def test_expired_csrf_and_prelogin_csrf_are_rejected(self):
        token = self.csrf()
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            response = self.client.post("/login", data=dict(
                csrf_token=token, email="ana@example.com", senha="private-password"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])
        self.login()
        response = self.client.post("/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 400)

    def test_unconfirmed_session_and_partial_cookies_are_rejected(self):
        self.cookies()
        self.confirmed = False
        self.assertEqual(self.client.get("/minha-conta").location, "/login")
        self.assertIsNone(self.client.get_cookie("adotae_access"))
        self.client.set_cookie("adotae_refresh", "orphan")
        self.assertEqual(self.client.get("/minha-conta").location, "/login")
        self.assertIsNone(self.client.get_cookie("adotae_refresh"))

    def test_credentials_do_not_appear_in_logs_or_flask_cookie(self):
        # Captura também os logs HTTP do SDK, sem exigir que existam logs.
        messages = []
        class Capture(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())
        handler = Capture()
        logger = logging.getLogger()
        level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            self.login()
            self.client.get("/minha-conta")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        signed = self.client.get_cookie("session").value
        decoded = app.session_interface.get_signing_serializer(app).loads(signed)
        self.assertEqual(set(decoded), {"csrf_token"})
        for secret in (self.access, self.refresh, "private-password"):
            self.assertNotIn(secret, "\n".join(messages))

    def test_missing_configuration_is_friendly(self):
        with patch.dict(app.config, SUPABASE_URL=""):
            self.assertEqual(self.login().status_code, 503)
        with patch.dict(app.config, SECRET_KEY=None):
            response = self.client.get("/login")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('name="senha"', response.text)


if __name__ == "__main__":
    unittest.main()

