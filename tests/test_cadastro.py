import re
import unittest
from unittest.mock import Mock, patch

from run import app


class CadastroTests(unittest.TestCase):
    def setUp(self):
        self.original_config = app.config.copy()
        app.config.update(TESTING=True, SECRET_KEY="test-only-secret",
                          SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test")
        self.addCleanup(app.config.update, self.original_config)
        self.client = app.test_client()
        patcher = patch("run.create_client")
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.factory.side_effect = lambda *args, **kwargs: Mock()

    def token(self, client=None):
        response = (client or self.client).get("/cadastro")
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                         response.get_data(as_text=True)).group(1)

    def submit(self, **changes):
        data = dict(nome="  Ana Silva  ", email="ana@example.com", senha="abcdefgh",
                    csrf_token=self.token())
        data.update(changes)
        return self.client.post("/cadastro", data=data)

    def test_page_has_fields_without_calling_supabase(self):
        html = self.client.get("/cadastro").get_data(as_text=True)
        self.assertIn('<h1 id="cadastro-titulo">Criar conta</h1>', html)
        self.assertIn('<title>Criar conta — Adotaê</title>', html)
        for field in ("nome", "email", "senha", "csrf_token"):
            self.assertIn(f'name="{field}"', html)
        self.assertIn('autocomplete="new-password"', html)
        self.factory.assert_not_called()

    def test_invalid_inputs_do_not_call_supabase(self):
        cases = ({"nome": " "}, {"nome": "a" * 101}, {"email": "inválido"},
                 {"email": "ana@"}, {"email": "a b@example.com"},
                 {"senha": "1234567"}, {"senha": "        "},
                 {"nome": "", "email": "", "senha": ""})
        for fields in cases:
            with self.subTest(fields=fields):
                response = self.submit(**fields)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('value="abcdefgh"', response.get_data(as_text=True))
        self.factory.assert_not_called()

    def test_signup_sends_metadata_without_login(self):
        sdk = Mock()
        sdk.auth.sign_up.return_value = Mock(session=Mock(access_token="private-token"))
        self.factory.side_effect = None
        self.factory.return_value = sdk
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<h1 id="cadastro-titulo">Verifique seu e-mail</h1>', html)
        self.assertIn('<title>Verifique seu e-mail — Adotaê</title>', html)
        self.assertIn(
            "Se o cadastro foi realizado, você receberá um e-mail com um link de "
            "confirmação. Confira sua caixa de entrada e também a pasta de spam.", html)
        self.assertIn('href="/">Voltar ao início</a>', html)
        self.assertNotIn("private-token", response.get_data(as_text=True))
        sdk.auth.sign_up.assert_called_once_with({
            "email": "ana@example.com", "password": "abcdefgh",
            "options": {"data": {"nome": "Ana Silva"}},
        })
        options = self.factory.call_args.kwargs["options"]
        self.assertFalse(options.persist_session)
        self.assertFalse(options.auto_refresh_token)
        with self.client.session_transaction() as session:
            self.assertEqual(set(session), {"csrf_token"})

    def test_each_signup_creates_a_client(self):
        self.submit()
        self.submit(email="bia@example.com")
        self.assertEqual(self.factory.call_count, 2)

    def test_csrf_missing_invalid_or_from_another_browser(self):
        foreign_token = self.token(app.test_client())
        self.token()
        for token in (None, "invalid", foreign_token):
            with self.subTest(token=token):
                data = dict(nome="Ana", email="ana@example.com", senha="abcdefgh")
                if token:
                    data["csrf_token"] = token
                response = self.client.post("/cadastro", data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Recarregue", response.get_data(as_text=True))
        self.factory.assert_not_called()

    def test_missing_configuration(self):
        for field in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
            with self.subTest(field=field), patch.dict(app.config, {field: ""}):
                self.assertEqual(self.submit().status_code, 503)
        with patch.dict(app.config, SECRET_KEY=None):
            self.assertEqual(self.client.get("/cadastro").status_code, 503)
        self.factory.assert_not_called()

    def test_duplicate_email_has_same_message_as_success(self):
        from supabase import AuthApiError
        success = self.submit().get_data(as_text=True)
        sdk = Mock()
        sdk.auth.sign_up.side_effect = AuthApiError(
            "Email already registered", 422, "user_already_exists")
        self.factory.side_effect = None
        self.factory.return_value = sdk
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), success)

    def test_errors_do_not_reveal_provider_details(self):
        from httpx import ConnectError
        from supabase import AuthApiError
        for error, status in (
            (ConnectError("private details"), 503),
            (AuthApiError("private details", 429, "over_email_send_rate_limit"), 429),
            (AuthApiError("private details", 422, "weak_password"), 400),
            (AuthApiError("private details", 500, "unexpected_failure"), 503),
        ):
            with self.subTest(error=error):
                sdk = Mock()
                sdk.auth.sign_up.side_effect = error
                self.factory.side_effect = None
                self.factory.return_value = sdk
                response = self.submit()
                self.assertEqual(response.status_code, status)
                self.assertNotIn("private details", response.get_data(as_text=True))
                self.assertNotIn('value="abcdefgh"', response.get_data(as_text=True))

    def test_invalid_sdk_configuration_has_friendly_error(self):
        from supabase import SupabaseException
        self.factory.side_effect = SupabaseException("private configuration")
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private configuration", response.get_data(as_text=True))

    def test_expired_csrf_is_rejected(self):
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.factory.assert_not_called()

    def test_form_escapes_input_and_does_not_cache_responses(self):
        response = self.submit(nome='<script>alert("x")</script>', senha="short")
        html = response.get_data(as_text=True)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('value="short"', html)
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
