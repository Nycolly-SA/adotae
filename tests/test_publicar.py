import copy
import json
import re
import unittest
from unittest.mock import patch

import httpx
from supabase import create_client

from run import app
from tests.test_login import jwt, user


ANIMAL_ID = "00000000-0000-0000-0000-000000000010"
URL = f"/anuncios/{ANIMAL_ID}/publicar"
SUCESSO = "Anúncio publicado com sucesso!"


class PublicarTests(unittest.TestCase):
    def setUp(self):
        original = app.config.copy()
        self.addCleanup(app.config.update, original)
        app.config.update(TESTING=True, SECRET_KEY="test-secret", SESSION_COOKIE_SECURE=False,
                          SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test")
        self.client = app.test_client()
        self.access = jwt()
        self.identity = user()
        self.client.set_cookie("adotae_access", self.access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        self.row = dict(id=ANIMAL_ID, usuario_id=self.identity["id"], nome="Fubá",
                        especie="Gato", distrito="Grussaí", status_publicacao="rascunho", status_adocao="disponivel", oculto_admin=False)
        self.calls = []
        self.reads = []
        self.rpc_failure = None
        self.rpc_result = None
        self.publish = True
        self.auth_failure = False

        def transport(request):
            self.calls.append(request)
            if request.url.path.endswith("/user"):
                if self.auth_failure:
                    return httpx.Response(503, json=dict(message="private", code="unexpected_failure"))
                return httpx.Response(200, json=self.identity)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json=dict(access_token=self.access, refresh_token="private-refresh",
                                     token_type="bearer", expires_in=3600, user=self.identity))
            if request.url.path == "/rest/v1/animais":
                self.assertEqual(request.method, "GET")
                result = [self.row]
                if "order" not in request.url.params and self.reads:
                    result = self.reads.pop(0)
                if result == "network":
                    raise httpx.ConnectError("private " + self.access)
                return httpx.Response(200, content=json.dumps(result),
                                      headers={"Content-Type": "application/json"})
            if request.url.path == "/rest/v1/rpc/publicar_anuncio":
                if self.publish:
                    self.row["status_publicacao"] = "publicado"
                if self.rpc_failure == "network":
                    raise httpx.ReadError("private " + self.access)
                if self.rpc_failure == "connection":
                    raise httpx.ConnectError("private " + self.access)
                if self.rpc_failure == "invalid-json":
                    return httpx.Response(200, text="private")
                if self.rpc_failure:
                    return httpx.Response(403 if self.rpc_failure == "42501" else 500,
                                          json=dict(code=self.rpc_failure, message="private " + self.access,
                                                    details=None, hint=None))
                return httpx.Response(200, json=self.rpc_result)
            raise AssertionError(f"Requisição inesperada: {request.method} {request.url.path}")

        def factory(url, key, options):
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            return create_client(url, key, options=options)

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.token = self.csrf(self.client)
        self.calls.clear()

    def csrf(self, client):
        page = client.get("/login")
        if page.status_code == 302:
            page = client.get("/minha-conta")
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)

    def rpcs(self):
        return [r for r in self.calls if "/rpc/" in r.url.path]

    def submit(self, **changes):
        response = self.client.post(URL, data=dict(csrf_token=self.token, **changes))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        page = self.client.get(response.location)
        for secret in (self.access, "private", "sb_publishable_test"):
            self.assertNotIn(secret, page.text)
        return page

    def test_button_only_for_drafts_and_existing_options(self):
        page = self.client.get("/minha-conta")
        self.assertIn(f'method="post" action="{URL}"', page.text)
        self.assertIn('>Publicar</button>', page.text)
        self.assertIn("pelo menos um contato e uma fotografia pronta", page.text)
        for action in ("editar", "foto"):
            self.assertIn(f'href="/anuncios/{ANIMAL_ID}/{action}"', page.text)
        self.row["status_publicacao"] = "publicado"
        self.assertNotIn(f'action="{URL}"', self.client.get("/minha-conta").text)

    def test_success_requires_authenticated_confirmation_and_single_rpc(self):
        page = self.submit(p_animal_id="forged", usuario_id="forged", status_publicacao="publicado")
        self.assertIn(SUCESSO, page.text)
        self.assertIn('role="status"', page.text)
        self.assertNotIn(SUCESSO, self.client.get("/minha-conta").text)
        self.assertEqual(len(self.rpcs()), 1)
        rpc = self.rpcs()[0]
        self.assertEqual(rpc.method, "POST")
        self.assertEqual(json.loads(rpc.content), {"p_animal_id": ANIMAL_ID})
        queries = [r for r in self.calls if r.url.path == "/rest/v1/animais" and "order" not in r.url.params]
        self.assertEqual(len(queries), 2)
        self.assertLess(self.calls.index(queries[0]), self.calls.index(rpc))
        self.assertLess(self.calls.index(rpc), self.calls.index(queries[1]))
        for req in [rpc, *queries]:
            self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
            self.assertEqual(req.headers["apikey"], "sb_publishable_test")
        for req in queries:
            self.assertEqual(req.url.params["id"], "eq." + ANIMAL_ID)
            self.assertEqual(req.url.params["usuario_id"], "eq." + self.identity["id"])

    def test_anonymous_unconfirmed_and_unavailable_auth(self):
        visitor = app.test_client()
        token = self.csrf(visitor)
        response = visitor.post(URL, data={"csrf_token": token})
        self.assertEqual(response.location, "/login")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).location, "/login")
        self.identity = user()
        self.client.set_cookie("adotae_access", self.access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        self.auth_failure = True
        self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).status_code, 503)
        self.assertEqual(self.rpcs(), [])

    def test_missing_configuration(self):
        for config in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}):
            with self.subTest(config=config), patch.dict(app.config, config):
                response = self.client.post(URL, data={"csrf_token": self.token})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.rpcs(), [])

    def test_missing_or_other_owner_does_not_call_rpc(self):
        for result in ([], [dict(self.row, usuario_id="other")], [dict(self.row, id="other")]):
            with self.subTest(result=result):
                self.reads = [result]
                self.assertNotIn(SUCESSO, self.submit().text)
                self.assertEqual(self.rpcs(), [])

    def test_already_published_does_not_call_rpc_or_claim_new_success(self):
        self.row["status_publicacao"] = "publicado"
        page = self.submit()
        self.assertIn("já está publicado", page.text)
        self.assertNotIn(SUCESSO, page.text)
        self.assertEqual(self.rpcs(), [])

    def test_refusal_prerequisites_and_lock_conflict(self):
        self.publish = False
        for code, text in (("42501", "Não foi possível publicar."), ("55P03", "Aguarde")):
            with self.subTest(code=code):
                self.calls.clear()
                self.rpc_failure = code
                page = self.submit()
                self.assertIn(text, page.text)
                self.assertIn('role="alert"', page.text)
                self.assertNotIn(SUCESSO, page.text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_lost_response_and_unexpected_rpc_response_confirm_publication(self):
        for failure in ("network", "invalid-json", "XX000"):
            with self.subTest(failure=failure):
                self.row["status_publicacao"] = "rascunho"
                self.calls.clear()
                self.rpc_failure = failure
                self.assertIn(SUCESSO, self.submit().text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_no_false_success_from_rpc_return_even_if_it_claims_published(self):
        self.publish = False
        for failure, result in ((None, None), (None, [dict(self.row, status_publicacao="publicado")]),
                                ("network", None), ("connection", None), ("invalid-json", None)):
            with self.subTest(failure=failure, result=result):
                self.calls.clear()
                self.rpc_failure, self.rpc_result = failure, result
                page = self.submit()
                self.assertNotIn(SUCESSO, page.text)
                self.assertIn("rascunho", page.text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_failed_or_invalid_confirmation_is_uncertain(self):
        for result in ("network", None, {}, "unexpected", [], [{}], [None], [self.row, self.row],
                       [dict(self.row, usuario_id="other")], [dict(self.row, id="other")],
                       [dict(self.row, status_publicacao="unknown")]):
            for failure in (None, "network"):
                with self.subTest(result=result, failure=failure):
                    self.row["status_publicacao"] = "rascunho"
                    self.reads = [copy.deepcopy([self.row]), result]
                    self.calls.clear()
                    self.rpc_failure = failure
                    page = self.submit()
                    self.assertIn("resultado da publicação é incerto", page.text)
                    self.assertIn("Meus anúncios", page.text)
                    self.assertNotIn(SUCESSO, page.text)
                    self.assertEqual(len(self.rpcs()), 1)

    def test_invalid_initial_read_blocks_rpc(self):
        for result in ("network", None, {}, "unexpected", [{}], [None], [self.row, self.row],
                       [dict(self.row, status_publicacao="unknown")]):
            with self.subTest(result=result):
                self.reads = [result]
                self.assertNotIn(SUCESSO, self.submit().text)
                self.assertEqual(self.rpcs(), [])

    def test_csrf_and_get_do_not_publish(self):
        other = app.test_client()
        for token in ("", "invalid", self.csrf(other)):
            response = self.client.post(URL, data={"csrf_token": token})
            self.assertEqual(response.status_code, 400)
            self.assertIn("Recarregue", response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).status_code, 400)
        self.assertEqual(self.client.get(URL).status_code, 405)
        self.assertEqual(self.rpcs(), [])

    def test_refreshed_token_is_used(self):
        self.client.set_cookie("adotae_access", jwt(expired=True))
        self.assertIn(SUCESSO, self.submit().text)
        self.assertEqual(self.rpcs()[0].headers["authorization"], "Bearer " + self.access)

    def test_second_user_uses_own_jwt_and_owner_filter(self):
        self.identity = dict(user(), id="00000000-0000-0000-0000-000000000002")
        self.row["usuario_id"] = self.identity["id"]
        second_access = jwt("bia")
        self.client.set_cookie("adotae_access", second_access)
        self.assertIn(SUCESSO, self.submit().text)
        self.assertEqual(self.rpcs()[0].headers["authorization"], "Bearer " + second_access)
        queries = [r for r in self.calls if r.url.path == "/rest/v1/animais"]
        for query in queries:
            self.assertEqual(query.url.params["usuario_id"], "eq." + self.identity["id"])
            self.assertEqual(query.headers["authorization"], "Bearer " + second_access)


if __name__ == "__main__":
    unittest.main()
