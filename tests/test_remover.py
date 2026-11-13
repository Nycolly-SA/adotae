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
URL = f"/anuncios/{ANIMAL_ID}/remover"
SUCESSO = "Anúncio excluído com sucesso!"


class RemoverTests(unittest.TestCase):
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
                        especie="Gato", distrito="Grussaí", status_publicacao="publicado", status_adocao="disponivel", oculto_admin=False, removido_em=None)
        self.calls = []
        self.reads = []
        self.rpc_failure = None
        self.rpc_result = None
        self.commit_removal = True
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
            if request.url.path == "/rest/v1/rpc/remover_anuncio":
                if self.commit_removal:
                    self.row["removido_em"] = "2026-11-12T12:00:00+00:00"
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
        response = self.client.post(URL, data=dict(csrf_token=self.token, confirmar_remocao="sim", **changes))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        page = self.client.get(response.location)
        for secret in (self.access, "private", "sb_publishable_test"):
            self.assertNotIn(secret, page.text)
        return page

    def test_draft_and_published_removal_uses_own_jwt_and_confirms(self):
        for publication in ("rascunho", "publicado"):
            with self.subTest(publication=publication):
                self.row.update(status_publicacao=publication, removido_em=None)
                self.calls.clear()
                self.assertIn(SUCESSO, self.submit().text)
                self.assertEqual(len(self.rpcs()), 1)
                rpc = self.rpcs()[0]
                self.assertEqual(rpc.method, "POST")
                self.assertEqual(json.loads(rpc.content), {"p_animal_id": ANIMAL_ID})
                reads = [r for r in self.calls if r.url.path == "/rest/v1/animais" and "order" not in r.url.params]
                self.assertEqual(len(reads), 2)
                self.assertLess(self.calls.index(reads[0]), self.calls.index(rpc))
                self.assertLess(self.calls.index(rpc), self.calls.index(reads[1]))
                for req in [rpc, *reads]:
                    self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
                    self.assertEqual(req.headers["apikey"], "sb_publishable_test")
                for req in reads:
                    self.assertEqual(req.url.params["id"], "eq." + ANIMAL_ID)
                    self.assertEqual(req.url.params["usuario_id"], "eq." + self.identity["id"])
                self.assertFalse(any(r.method == "DELETE" or "/storage/" in r.url.path for r in self.calls))

    def test_adopted_missing_and_other_owner_are_refused(self):
        for result in ([], [dict(self.row, usuario_id="other")], [dict(self.row, id="other")],
                       [dict(self.row, status_adocao="adotado")]):
            with self.subTest(result=result):
                self.reads = [result]
                page = self.submit()
                self.assertNotIn(SUCESSO, page.text)
                self.assertEqual(self.rpcs(), [])
        self.row["status_adocao"] = "adotado"
        self.assertIn("adotado", self.submit().text)

    def test_repeated_removal_does_not_repeat_rpc(self):
        self.assertIn(SUCESSO, self.submit().text)
        self.assertIn("já foi excluído", self.submit().text)
        self.assertEqual(len(self.rpcs()), 1)

    def test_confirmation_required(self):
        for values in ([], [""], ["nao"], ["sim", "nao"]):
            response = self.client.post(URL, data={"csrf_token": self.token, "confirmar_remocao": values})
            self.assertIn("confirmação explícita", self.client.get(response.location).text)
            self.assertEqual(self.rpcs(), [])

    def test_authentication_identity_and_configuration(self):
        visitor = app.test_client()
        response = visitor.post(URL, data={"csrf_token": self.csrf(visitor)})
        self.assertEqual(response.location, "/login")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).location, "/login")
        self.identity = user()
        self.client.set_cookie("adotae_access", self.access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        self.auth_failure = True
        self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).status_code, 503)
        self.auth_failure = False
        self.invalid_auth = True
        self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).location, "/login")
        for config in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}):
            self.client.set_cookie("adotae_access", self.access)
            self.client.set_cookie("adotae_refresh", "private-refresh")
            with patch.dict(app.config, config):
                response = self.client.post(URL, data={"csrf_token": self.token})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.rpcs(), [])

    def test_csrf_and_get_cannot_remove(self):
        other = app.test_client()
        for token in ("", "invalid", self.csrf(other)):
            self.calls.clear()
            response = self.client.post(URL, data={"csrf_token": token, "confirmar_remocao": "sim"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("CSRF inválido ou expirado", response.text)
            self.assertIn('href="/minha-conta"', response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertFalse(any(r.url.path.startswith("/rest/") for r in self.calls))
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.client.post(URL, data={"csrf_token": self.token}).status_code, 400)
        self.assertEqual(self.client.get(URL).status_code, 405)

    def test_lost_response_after_commit_is_confirmed_without_retry(self):
        for failure in ("network", "connection", "invalid-json", "XX000"):
            with self.subTest(failure=failure):
                self.row["removido_em"] = None
                self.calls.clear()
                self.rpc_failure = failure
                self.assertIn(SUCESSO, self.submit().text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_rpc_failure_or_payload_never_proves_success(self):
        self.commit_removal = False
        for failure in (None, "network", "connection", "invalid-json", "42501", "55P03", "XX000"):
            with self.subTest(failure=failure):
                self.calls.clear()
                self.rpc_failure = failure
                self.rpc_result = [dict(self.row, removido_em="2026-11-12T12:00:00+00:00")]
                page = self.submit()
                self.assertNotIn(SUCESSO, page.text)
                self.assertIn('role="alert"', page.text)
                self.assertIn("Aguarde" if failure == "55P03" else "não foi confirmada", page.text)
                self.assertEqual(len(self.rpcs()), 1)

    def test_invalid_initial_reads_block_removal(self):
        for result in ("network", None, {}, "unexpected", [{}], [None], [self.row, self.row],
                       [dict(self.row, status_publicacao="unknown")],
                       [dict(self.row, status_adocao=None)],
                       [dict(self.row, removido_em=False)],
                       [{k: v for k, v in self.row.items() if k != "removido_em"}]):
            with self.subTest(result=result):
                self.reads = [copy.deepcopy(result)]
                self.assertNotIn(SUCESSO, self.submit().text)
                self.assertEqual(self.rpcs(), [])

    def test_uncertain_when_confirmation_unavailable_or_invalid(self):
        for result in ("network", None, {}, [], [{}], [None], [self.row, self.row],
                       [dict(self.row, usuario_id="other")], [dict(self.row, id="other")],
                       [dict(self.row, removido_em="invalid")], [dict(self.row, removido_em=42)]):
            for failure in (None, "network"):
                with self.subTest(result=result, failure=failure):
                    self.row["removido_em"] = None
                    self.reads = [copy.deepcopy([self.row]), copy.deepcopy(result)]
                    self.calls.clear()
                    self.rpc_failure = failure
                    page = self.submit()
                    self.assertIn("resultado da exclusão é incerto", page.text)
                    self.assertNotIn(SUCESSO, page.text)
                    self.assertEqual(len(self.rpcs()), 1)

    def test_list_filters_removed_and_only_offers_eligible_actions(self):
        for publication, adoption in (("rascunho", "disponivel"), ("publicado", "disponivel"),
                                      ("publicado", "adotado")):
            self.row.update(status_publicacao=publication, status_adocao=adoption, removido_em=None)
            page = self.client.get("/minha-conta")
            self.assertEqual(f'action="{URL}"' in page.text, adoption == "disponivel")
            if adoption == "disponivel":
                self.assertIn("não poderá ser recuperado pela interface", page.text)
                self.assertRegex(page.text, r'name="confirmar_remocao"[^>]*required')
        self.row["removido_em"] = "2026-11-12T12:00:00+00:00"
        page = self.client.get("/minha-conta")
        self.assertNotIn("Fubá", page.text)
        self.assertIn("0 anúncios", page.text)
        queries = [r for r in self.calls if r.url.path == "/rest/v1/animais" and "order" in r.url.params]
        for query in queries:
            self.assertEqual(query.url.params["removido_em"], "is.null")

    def test_refreshed_session_is_used(self):
        self.client.set_cookie("adotae_access", jwt(expired=True))
        self.assertIn(SUCESSO, self.submit().text)
        self.assertEqual(self.rpcs()[0].headers["authorization"], "Bearer " + self.access)

    def test_second_user_uses_own_session_and_owner_filter(self):
        self.identity = dict(user(), id="00000000-0000-0000-0000-000000000002")
        self.row["usuario_id"] = self.identity["id"]
        second_access = jwt("bia")
        self.client.set_cookie("adotae_access", second_access)
        self.assertIn(SUCESSO, self.submit().text)
        requests = [r for r in self.calls if r.url.path.startswith("/rest/")]
        for req in requests:
            self.assertEqual(req.headers["authorization"], "Bearer " + second_access)
            if req.url.path == "/rest/v1/animais":
                self.assertEqual(req.url.params["usuario_id"], "eq." + self.identity["id"])

    def test_adoption_during_removal_is_reported_without_success(self):
        self.commit_removal = False
        self.rpc_failure = "42501"
        self.reads = [copy.deepcopy([self.row]), [dict(self.row, status_adocao="adotado")]]
        page = self.submit()
        self.assertIn("adotado não pode ser excluído", page.text)
        self.assertNotIn(SUCESSO, page.text)
        self.assertEqual(len(self.rpcs()), 1)
