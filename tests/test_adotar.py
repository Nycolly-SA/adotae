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
URL = f"/anuncios/{ANIMAL_ID}/adotar"
SUCESSO = "Adoção confirmada com sucesso!"


class AdotarTests(unittest.TestCase):
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
                        especie="Gato", distrito="Grussaí", status_publicacao="publicado", status_adocao="disponivel", oculto_admin=False)
        self.calls = []
        self.reads = []
        self.rpc_failure = None
        self.rpc_result = None
        self.adopt = True
        self.auth_failure = False
        self.invalid_auth = False

        def transport(request):
            self.calls.append(request)
            if request.url.path.endswith("/user"):
                if self.invalid_auth:
                    return httpx.Response(401, json=dict(message="private", code="bad_jwt"))
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
            if request.url.path == "/rest/v1/rpc/marcar_adotado":
                if self.adopt:
                    self.row["status_adocao"] = "adotado"
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
        response = self.client.post(URL, data=dict(csrf_token=self.token, confirmar_adocao="sim", **changes))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        page = self.client.get(response.location)
        for secret in (self.access, "private", "sb_publishable_test"):
            self.assertNotIn(secret, page.text)
        return page

    def test_cards_actions_photographs_and_visibility(self):
        for publication, adoption, hidden in (
            ("rascunho", "disponivel", False), ("publicado", "disponivel", False),
            ("publicado", "disponivel", True), ("publicado", "adotado", False),
            ("publicado", "adotado", True),
        ):
            with self.subTest(publication=publication, adoption=adoption, hidden=hidden):
                self.row.update(status_publicacao=publication, status_adocao=adoption,
                                oculto_admin=hidden, animal_fotos=[dict(id="photo", estado="pronta")])
                page = self.client.get("/minha-conta")
                self.assertEqual(page.status_code, 200)
                self.assertIn('class="account-animals"', page.text)
                self.assertIn('class="account-animal-content"', page.text)
                status = "adotado" if adoption == "adotado" else publication
                self.assertIn(f'class="account-animal-status account-animal-status--{status}"', page.text)
                self.assertIn(f'src="/anuncios/{ANIMAL_ID}/miniatura"', page.text)
                self.assertEqual(f'action="{URL}"' in page.text,
                                 publication == "publicado" and adoption == "disponivel")
                self.assertEqual(f'href="/animais/{ANIMAL_ID}"' in page.text,
                                 publication == "publicado" and adoption == "disponivel" and not hidden)
                for action in ("editar", "foto"):
                    self.assertEqual(f'href="/anuncios/{ANIMAL_ID}/{action}"' in page.text,
                                     publication == "rascunho")
                if adoption == "adotado":
                    self.assertIn("Adotado", page.text)
                elif publication == "publicado":
                    self.assertIn("<details", page.text)
                    self.assertIn('name="confirmar_adocao"', page.text)
                    self.assertIn('required', page.text)
                    self.assertIn("Após confirmar a adoção, o animal deixará de aparecer no catálogo. "
                                  "O anúncio continuará salvo na sua conta e não poderá ser reativado por aqui.", page.text)
                self.assertIn("Fubá", page.text)
                self.assertFalse(any("/storage/" in r.url.path for r in self.calls))

    def test_requires_explicit_confirmation(self):
        for value in (None, "", "nao", "true"):
            data = dict(csrf_token=self.token)
            if value is not None:
                data["confirmar_adocao"] = value
            response = self.client.post(URL, data=data)
            self.assertEqual(response.location, "/minha-conta")
            self.assertIn("confirmação explícita", self.client.get(response.location).text)
            self.assertEqual(self.rpcs(), [])
        response = self.client.post(URL, data={
            "csrf_token": self.token, "confirmar_adocao": ["sim", "nao"],
        })
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(self.rpcs(), [])

    def test_draft_is_unavailable(self):
        self.row["status_publicacao"] = "rascunho"
        self.assertIn("Anúncio indisponível para adoção.", self.submit().text)
        self.assertEqual(self.rpcs(), [])

    def test_list_validates_adoption_and_hidden_fields(self):
        for field, values in (("status_adocao", (None, "unknown", [], False)),
                              ("oculto_admin", (None, "false", 0, [], {}))):
            for value in values:
                with self.subTest(field=field, value=value):
                    original = self.row.copy()
                    self.row[field] = value
                    self.assertEqual(self.client.get("/minha-conta").status_code, 503)
                    self.row = original
            original = self.row.copy()
            del self.row[field]
            self.assertEqual(self.client.get("/minha-conta").status_code, 503)
            self.row = original

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
        self.assertEqual(queries[0].url.params["select"], "id,usuario_id,status_publicacao,status_adocao")
        self.assertLess(self.calls.index(queries[0]), self.calls.index(rpc))
        self.assertLess(self.calls.index(rpc), self.calls.index(queries[1]))
        for req in [rpc, *queries]:
            self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
            self.assertEqual(req.headers["apikey"], "sb_publishable_test")
        for req in queries:
            self.assertEqual(req.url.params["id"], "eq." + ANIMAL_ID)
            self.assertEqual(req.url.params["usuario_id"], "eq." + self.identity["id"])

    def test_repeated_submission_does_not_repeat_rpc(self):
        self.assertIn(SUCESSO, self.submit().text)
        self.assertIn("já está adotado", self.submit().text)
        self.assertEqual(len(self.rpcs()), 1)

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

    def test_invalid_session_and_partial_cookies_cannot_adopt(self):
        self.invalid_auth = True
        response = self.client.post(URL, data={"csrf_token": self.token, "confirmar_adocao": "sim"})
        self.assertEqual(response.location, "/login")
        self.assertIsNone(self.client.get_cookie("adotae_access"))
        self.invalid_auth = False
        self.client.set_cookie("adotae_access", self.access)
        response = self.client.post(URL, data={"csrf_token": self.token, "confirmar_adocao": "sim"})
        self.assertEqual(response.location, "/login")
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

    def test_already_adopted_does_not_call_rpc_or_claim_new_success(self):
        self.row["status_adocao"] = "adotado"
        page = self.submit()
        self.assertIn("já está adotado", page.text)
        self.assertNotIn(SUCESSO, page.text)
        self.assertEqual(self.rpcs(), [])

    def test_refusal_and_lock_conflict(self):
        self.adopt = False
        for code, text in (("42501", "Não foi possível confirmar a adoção."), ("55P03", "Aguarde")):
            with self.subTest(code=code):
                self.calls.clear()
                self.rpc_failure = code
                page = self.submit()
                self.assertIn(text, page.text)
                self.assertIn('role="alert"', page.text)
                self.assertNotIn(SUCESSO, page.text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_lost_response_and_unexpected_rpc_response_confirm_adoption(self):
        for failure in ("network", "invalid-json", "XX000"):
            with self.subTest(failure=failure):
                self.row["status_adocao"] = "disponivel"
                self.calls.clear()
                self.rpc_failure = failure
                self.assertIn(SUCESSO, self.submit().text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_no_false_success_from_rpc_return_even_if_it_claims_adopted(self):
        self.adopt = False
        for failure, result in ((None, None), (None, [dict(self.row, status_adocao="adotado")]),
                                ("network", None), ("connection", None), ("invalid-json", None)):
            with self.subTest(failure=failure, result=result):
                self.calls.clear()
                self.rpc_failure, self.rpc_result = failure, result
                page = self.submit()
                self.assertNotIn(SUCESSO, page.text)
                self.assertIn("continua disponível", page.text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_failed_or_invalid_confirmation_is_uncertain(self):
        for result in ("network", None, {}, "unexpected", [], [{}], [None], [self.row, self.row],
                       [dict(self.row, usuario_id="other")], [dict(self.row, id="other")],
                       [dict(self.row, status_publicacao="unknown")]):
            for failure in (None, "network"):
                with self.subTest(result=result, failure=failure):
                    self.row["status_adocao"] = "disponivel"
                    self.reads = [copy.deepcopy([self.row]), result]
                    self.calls.clear()
                    self.rpc_failure = failure
                    page = self.submit()
                    self.assertIn("resultado da adoção é incerto", page.text)
                    self.assertIn("Meus anúncios", page.text)
                    self.assertNotIn(SUCESSO, page.text)
                    self.assertEqual(len(self.rpcs()), 1)

    def test_invalid_initial_read_blocks_rpc(self):
        for result in ("network", None, {}, "unexpected", [{}], [None], [self.row, self.row],
                       [dict(self.row, status_publicacao="unknown")],
                       [dict(self.row, status_adocao=None)], [dict(self.row, status_adocao="unknown")]):
            with self.subTest(result=result):
                self.reads = [result]
                self.assertNotIn(SUCESSO, self.submit().text)
                self.assertEqual(self.rpcs(), [])

    def test_csrf_and_get_do_not_adopt(self):
        other = app.test_client()
        for token in ("", "invalid", self.csrf(other)):
            self.calls.clear()
            response = self.client.post(URL, data={"csrf_token": token})
            self.assertEqual(response.status_code, 400)
            self.assertIn("Recarregue", response.text)
            self.assertIn("confirmação da adoção foi recusada", response.text)
            self.assertIn("CSRF inválido ou expirado", response.text)
            self.assertIn('href="/minha-conta"', response.text)
            self.assertNotIn("Não foi possível carregar seus anúncios", response.text)
            self.assertNotIn("Você ainda não cadastrou nenhum animal", response.text)
            self.assertFalse(any(r.url.path.startswith("/rest/") for r in self.calls))
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
