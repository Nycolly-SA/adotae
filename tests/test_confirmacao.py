import json
import logging
import unittest
from unittest.mock import patch

import httpx
from supabase import create_client

from run import app
from tests.test_login import user


SUCESSO = "E-mail confirmado com sucesso! Sua conta está pronta. Você já pode entrar no Adotaê."
INVALIDO = "Este link de confirmação é inválido, expirou ou já foi utilizado."
INDISPONIVEL = "Não foi possível confirmar seu e-mail agora. Tente novamente mais tarde."


class ConfirmacaoTests(unittest.TestCase):
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
        self.status = 200
        self.code = "otp_expired"
        self.network_failure = False
        self.confirmed = True

        def transport(request):
            self.calls.append(request)
            if self.network_failure:
                raise httpx.ConnectError("private-token-hash private-provider-details")
            if self.status != 200:
                return httpx.Response(self.status, json={
                    "msg": "private-token-hash private-provider-details", "code": self.code,
                }, headers={"X-Supabase-Api-Version": "2024-01-01"})
            return httpx.Response(200, json=dict(
                access_token="private-access", refresh_token="private-refresh",
                token_type="bearer", expires_in=3600, user=user(confirmed=self.confirmed)))

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

    def confirm(self, query=None):
        return self.client.get("/auth/confirm", query_string=query or {
            "token_hash": "private-token-hash", "type": "email",
        })

    def assert_result(self, response, message):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn(message, page.text)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        if message != SUCESSO:
            self.assertNotIn(SUCESSO, page.text)
        for secret in ("private-token-hash", "private-access", "private-refresh", "private-provider-details"):
            self.assertNotIn(secret, response.text + str(response.headers) + page.text + str(page.headers))
            with self.client.session_transaction() as session:
                self.assertNotIn(secret, str(dict(session)))
        for name in ("adotae_access", "adotae_refresh"):
            self.assertIsNone(self.client.get_cookie(name))
        self.assertEqual(self.client.get("/minha-conta").location, "/login")
        self.assertNotIn(message, self.client.get("/").text)
        return page

    def test_valid_confirmation_is_temporary_and_does_not_log_in(self):
        page = self.assert_result(self.confirm(), SUCESSO)
        self.assertIn('href="/login"', page.text)
        self.assertLess(page.text.index("</header>"), page.text.index(SUCESSO))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].method, "POST")
        self.assertEqual(self.calls[0].url.path, "/auth/v1/verify")
        payload = json.loads(self.calls[0].content)
        self.assertEqual(payload["token_hash"], "private-token-hash")
        self.assertEqual(payload["type"], "email")
        self.assertNotIn(SUCESSO, app.test_client().get("/").text)

    def test_invalid_expired_and_used_links(self):
        for status, code in ((400, "validation_failed"), (403, "otp_expired"), (403, "access_denied")):
            with self.subTest(code=code):
                self.status, self.code = status, code
                self.assert_result(self.confirm(), INVALIDO)

    def test_supabase_outage_and_network_failure(self):
        self.status = 503
        self.assert_result(self.confirm(), INDISPONIVEL)
        self.network_failure = True
        self.assert_result(self.confirm(), INDISPONIVEL)

    def test_only_expected_type_and_nonempty_hash_reach_supabase(self):
        for query in ({}, {"type": "email"}, {"token_hash": ""},
                      {"token_hash": "  ", "type": "email"},
                      *({"token_hash": "private-token-hash", "type": kind}
                        for kind in ("signup", "recovery", "invite", "email_change", "sms", "magiclink", ""))):
            with self.subTest(query=query):
                response = self.client.get("/auth/confirm", query_string=query)
                self.assert_result(response, INVALIDO)
        self.assertEqual(self.calls, [])

    def test_query_parameters_cannot_forge_success_or_redirect(self):
        self.assertNotIn(SUCESSO, self.client.get("/?confirmed=true&message=success&type=email").text)
        self.status = 403
        self.assert_result(self.confirm({
            "token_hash": "private-token-hash", "type": "email", "confirmed": "true",
            "next": "https://example.org", "message": SUCESSO,
        }), INVALIDO)

    def test_unconfirmed_provider_response_is_not_success(self):
        self.confirmed = False
        self.assert_result(self.confirm(), INDISPONIVEL)

    def test_latest_confirmation_replaces_pending_success(self):
        self.confirm()
        self.status = 403
        self.assert_result(self.confirm(), INVALIDO)

    def test_confirmation_clients_are_isolated(self):
        self.assert_result(self.confirm(), SUCESSO)
        self.assert_result(self.confirm(), SUCESSO)
        self.assertEqual(len(self.clients), 2)
        self.assertIsNot(self.clients[0], self.clients[1])

    def test_missing_configuration_does_not_report_success(self):
        with patch.dict(app.config, SUPABASE_URL=""):
            self.assert_result(self.confirm(), INDISPONIVEL)
        self.assertEqual(self.calls, [])

    def test_missing_secret_does_not_consume_confirmation(self):
        with patch.dict(app.config, SECRET_KEY=None):
            response = self.confirm()
        self.assertEqual(response.status_code, 503)
        self.assertIn(INDISPONIVEL, response.text)
        self.assertNotIn(SUCESSO, response.text)
        self.assertNotIn("private-token-hash", response.text)
        self.assertEqual(self.calls, [])

    def test_existing_auth_cookies_are_not_replaced_by_confirmation(self):
        self.client.set_cookie("adotae_access", "previous-access")
        self.client.set_cookie("adotae_refresh", "previous-refresh")
        response = self.confirm()
        self.assertEqual(response.location, "/")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.client.get_cookie("adotae_access").value, "previous-access")
        self.assertEqual(self.client.get_cookie("adotae_refresh").value, "previous-refresh")
        self.assertNotIn("adotae_access", str(response.headers))
        self.assertNotIn("adotae_refresh", str(response.headers))

    def test_tokens_and_provider_errors_are_not_logged(self):
        logger = logging.getLogger("werkzeug")
        with self.assertLogs(level="INFO") as captured:
            logger.info('"GET %s HTTP/1.1" %s',
                        "/auth/confirm?token_hash=private-token-hash&type=email", 302)
            self.network_failure = True
            self.assert_result(self.confirm(), INDISPONIVEL)
        logs = "\n".join(captured.output)
        self.assertIn("/auth/confirm?[oculto]", logs)
        for secret in ("private-token-hash", "private-access", "private-refresh", "private-provider-details"):
            self.assertNotIn(secret, logs)
