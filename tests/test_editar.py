import copy
import json
import re
import unittest
from io import BytesIO
from unittest.mock import patch

import httpx
from supabase import create_client

from run import ANIMAIS, app
from tests.test_login import jwt, user


ANIMAL_ID = "00000000-0000-0000-0000-000000000010"
URL = f"/anuncios/{ANIMAL_ID}/editar"
SUCESSO = "Rascunho atualizado com sucesso!"
ATUALIZAR_NORMALMENTE = object()


class EditarTests(unittest.TestCase):
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
        self.calls = []
        self.row = dict(id=ANIMAL_ID, usuario_id=self.identity["id"], status_publicacao="rascunho",
                        nome="Fubá", especie="Outro animal doméstico", especie_outro="Porco doméstico",
                        sexo="Fêmea", idade_aproximada="2 anos", porte="Médio", descricao="Amigável.",
                        distrito="Grussaí", bairro="Centro", email_contato="ana@example.com",
                        whatsapp="5522999999999")
        self.read_result = [self.row]
        self.update_result = ATUALIZAR_NORMALMENTE
        self.failure = None
        self.failure_method = "PATCH"

        def transport(request):
            self.calls.append(request)
            if request.url.path == "/rest/v1/animais":
                if request.method == self.failure_method and self.failure:
                    if self.failure == "network":
                        raise httpx.ConnectError("private " + self.access)
                    if self.failure == "invalid-json":
                        return httpx.Response(200, text="private " + self.access)
                    return httpx.Response(self.failure, json=dict(
                        code="42501", message="private " + self.access, details="private-refresh"))
                if request.method == "PATCH":
                    result = self.update_result
                    if result is ATUALIZAR_NORMALMENTE:
                        self.row.update(json.loads(request.content))
                        result = [self.row]
                else:
                    result = [self.row] if "order" in request.url.params else self.read_result
                return httpx.Response(200, content=json.dumps(result),
                                      headers={"Content-Type": "application/json"})
            if request.url.path.endswith("/user"):
                return httpx.Response(200, json=self.identity)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json=dict(access_token=self.access, refresh_token="private-refresh",
                                     token_type="bearer", expires_in=3600, user=self.identity))
            raise AssertionError(f"Requisição inesperada: {request.method} {request.url.path}")

        def factory(url, key, options):
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            return create_client(url, key, options=options)

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def csrf(self, client=None):
        page = (client or self.client).get("/login")
        if page.status_code == 302:
            page = (client or self.client).get("/minha-conta")
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)

    def data(self, **changes):
        data = dict(nome=" Novo nome ", especie="Gato", especie_outro="ignorar", sexo="Macho",
                    idade_aproximada=" 3 anos ", porte="Pequeno", descricao=" Nova descrição ",
                    distrito="Atafona", bairro=" ", email_contato=" contato@example.com ",
                    whatsapp="12345678", csrf_token=self.csrf())
        return dict(data, **changes)

    def updates(self):
        return [r for r in self.calls if r.method == "PATCH"]

    def test_get_prefills_all_fields_without_photo_controls(self):
        page = self.client.get(URL)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        for value in ("Fubá", "Porco doméstico", "2 anos", "Amigável.", "Centro",
                      "ana@example.com", "5522999999999"):
            self.assertIn(value, page.text)
        for value in ("Outro animal doméstico", "Fêmea", "Médio", "Grussaí"):
            self.assertIn(f'selected value="{value}"', page.text)
        self.assertIn(f'action="{URL}"', page.text)
        self.assertNotIn('type="file"', page.text)
        self.assertNotIn("multipart/form-data", page.text)
        self.assertIn(">Salvar alterações</button>", page.text)
        self.assertNotIn("Salvar como rascunho", page.text)
        self.assertNotIn("Publicar agora", page.text)
        self.assertNotIn('name="acao"', page.text)
        query = self.calls[-1]
        self.assertEqual(query.url.params["id"], "eq." + ANIMAL_ID)
        self.assertEqual(query.url.params["usuario_id"], "eq." + self.identity["id"])
        self.assertEqual(query.url.params["status_publicacao"], "eq.rascunho")
        self.assertEqual(self.updates(), [])

    def test_update_allowlist_filters_jwt_and_confirmation(self):
        catalog = self.client.get("/animais").text
        animals = copy.deepcopy(ANIMAIS)
        response = self.client.post(URL, data=self.data(
            id="forged", usuario_id="forged", status_publicacao="publicado", status_adocao="adotado",
            oculto_admin="true", criado_em="forged", atualizado_em="forged",
            foto=(BytesIO(b"not an image"), "foto.png"), animal_fotos="forged"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(len(self.updates()), 1)
        sent = self.updates()[0]
        self.assertEqual(dict(sent.url.params), dict(id="eq." + ANIMAL_ID,
                         usuario_id="eq." + self.identity["id"], status_publicacao="eq.rascunho"))
        self.assertEqual(sent.headers["authorization"], f"Bearer {self.access}")
        self.assertEqual(sent.headers["apikey"], "sb_publishable_test")
        self.assertIn("return=representation", sent.headers["prefer"])
        self.assertEqual(json.loads(sent.content), dict(nome="Novo nome", especie="Gato", especie_outro=None,
                         sexo="Macho", idade_aproximada="3 anos", porte="Pequeno", descricao="Nova descrição",
                         distrito="Atafona", bairro=None, email_contato="contato@example.com", whatsapp="12345678"))
        page = self.client.get(response.location)
        self.assertIn(SUCESSO, page.text)
        self.assertIn("Novo nome", page.text)
        self.assertNotIn(SUCESSO, self.client.get(response.location).text)
        self.assertTrue(all(r.url.path in ("/rest/v1/animais", "/auth/v1/user") for r in self.calls))
        self.assertEqual(ANIMAIS, animals)
        self.assertEqual(self.client.get("/animais").text, catalog)

    def test_account_only_links_drafts(self):
        page = self.client.get("/minha-conta")
        self.assertIn(f'href="{URL}"', page.text)
        self.row["status_publicacao"] = "publicado"
        page = self.client.get("/minha-conta")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(f'href="{URL}"', page.text)
        self.assertIn("Publicado", page.text)
        self.assertIn('action="/logout"', page.text)

    def test_anonymous_and_unconfirmed_cannot_read_or_update(self):
        visitor = app.test_client()
        token = self.csrf(visitor)
        for response in (visitor.get(URL), visitor.post(URL, data={"csrf_token": token})):
            self.assertEqual(response.location, "/login")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = self.data()
        self.calls.clear()
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.get(URL).location, "/login")
        self.assertEqual(self.client.post(URL, data=data).location, "/login")
        self.assertFalse(any(r.url.path == "/rest/v1/animais" for r in self.calls))

    def test_missing_other_owner_and_published_are_indistinguishable(self):
        data = self.data()
        for result in ([], [dict(self.row, usuario_id="other")],
                       [dict(self.row, status_publicacao="publicado")],
                       [dict(self.row, id="00000000-0000-0000-0000-000000000011")]):
            self.read_result = result
            for response in (self.client.get(URL), self.client.post(URL, data=data)):
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Fubá", response.text)
                self.assertNotIn('type="submit"', response.text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.updates(), [])
        self.assertEqual(self.client.get("/anuncios/not-a-uuid/editar").status_code, 404)

    def test_validation_and_limits_preserve_submitted_values(self):
        data = self.data()
        changes = [{field: ""} for field in ("nome", "especie", "sexo", "idade_aproximada",
                                             "porte", "descricao", "distrito")]
        changes += [{field: "x" * (limit + 1)} for field, limit in (
            ("nome", 100), ("idade_aproximada", 50), ("descricao", 2000), ("bairro", 100))]
        changes += [dict(especie="Outro animal doméstico", especie_outro=value)
                    for value in ("", "x" * 61)]
        changes += [{field: value} for field, value in (("especie", "Silvestre"), ("sexo", "Outro"),
                    ("porte", "Gigante"), ("distrito", "Outra cidade"), ("email_contato", "invalido"),
                    ("whatsapp", "+5522999999999"), ("whatsapp", "1234567"))]
        for change in changes:
            with self.subTest(change=change):
                page = self.client.post(URL, data=dict(data, **change))
                self.assertEqual(page.status_code, 400)
                self.assertIn('role="alert"', page.text)
                self.assertIn("Nova descrição" if "descricao" not in change else "Novo nome", page.text)
        self.assertEqual(self.updates(), [])

    def test_optional_values_and_other_species(self):
        page = self.client.post(URL, data=self.data(especie="Outro animal doméstico",
            especie_outro=" Porco doméstico ", email_contato=" ", whatsapp=" "))
        self.assertEqual(page.status_code, 302)
        sent = json.loads(self.updates()[0].content)
        self.assertEqual(sent["especie_outro"], "Porco doméstico")
        self.assertIsNone(sent["email_contato"])
        self.assertIsNone(sent["whatsapp"])

    def test_csrf_missing_invalid_foreign_and_expired(self):
        data = self.data()
        foreign = self.csrf(app.test_client())
        for token in ("", "invalid", foreign):
            response = self.client.post(URL, data=dict(data, csrf_token=token))
            self.assertEqual(response.status_code, 400)
            self.assertIn("Recarregue", response.text)
            self.assertIn('value="Novo nome"', response.text)
            self.assertNotIn('type="file"', response.text)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.client.post(URL, data=data).status_code, 400)
        self.assertEqual(self.updates(), [])

    def test_api_failures_never_report_success_or_leak_secrets(self):
        data = self.data()
        for method in ("GET", "PATCH"):
            self.failure_method = method
            for failure in (401, 403, 409, 429, 500, "network", "invalid-json"):
                self.failure = failure
                with self.subTest(method=method, failure=failure):
                    page = self.client.post(URL, data=data)
                    self.assertEqual(page.status_code, 503)
                    for secret in (SUCESSO, self.access, "private", "sb_publishable_test"):
                        self.assertNotIn(secret, page.text)
                    self.assertEqual(page.headers["Cache-Control"], "no-store")
                    if method == "PATCH":
                        self.assertIn('value="Novo nome"', page.text)
                    else:
                        self.assertNotIn('type="submit"', page.text)

    def test_concurrent_change_empty_update_and_unexpected_results(self):
        data = self.data()
        invalid = [[], [{}], [None], ["row"], [dict(self.row, id="invalid")],
                   [dict(self.row, id="00000000-0000-0000-0000-000000000011")],
                   [dict(self.row, usuario_id="other")], [dict(self.row, status_publicacao="publicado")],
                   [self.row], [self.row, self.row], None, {"data": []}, "unexpected"]
        for result in invalid:
            self.update_result = result
            with self.subTest(result=result):
                page = self.client.post(URL, data=data)
                self.assertIn(page.status_code, (409, 503))
                self.assertIn('value="Novo nome"', page.text)
                self.assertNotIn(SUCESSO, self.client.get("/minha-conta").text)
        self.update_result = []
        page = self.client.post(URL, data=data)
        self.assertEqual(page.status_code, 409)

    def test_unexpected_read_results_do_not_enable_form(self):
        for result in (None, {}, "unexpected", [{}], [None], [self.row, self.row],
                       [dict(self.row, nome=None)], [dict(self.row, descricao=42)]):
            self.read_result = result
            with self.subTest(result=result):
                page = self.client.get(URL)
                self.assertEqual(page.status_code, 503)
                self.assertNotIn('type="submit"', page.text)
        self.assertEqual(self.updates(), [])

    def test_refreshed_token_and_missing_configuration(self):
        data = self.data()
        self.client.set_cookie("adotae_access", jwt(expired=True))
        self.assertEqual(self.client.post(URL, data=data).status_code, 302)
        self.assertEqual(self.updates()[0].headers["authorization"], f"Bearer {self.access}")
        self.assertEqual(self.client.get_cookie("adotae_access").value, self.access)
        for config in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}):
            self.calls.clear()
            with patch.dict(app.config, config):
                self.assertEqual(self.client.post(URL, data=data).status_code, 503)
            self.assertEqual(self.updates(), [])

    def test_second_user_uses_own_identity_and_request_token(self):
        other = app.test_client()
        other_access = jwt("bia")
        other.set_cookie("adotae_access", other_access)
        other.set_cookie("adotae_refresh", "private-refresh")
        self.identity["id"] = "00000000-0000-0000-0000-000000000002"
        self.row["usuario_id"] = self.identity["id"]
        data = self.data(csrf_token=self.csrf(other))
        response = other.post(URL, data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.updates()), 1)
        sent = self.updates()[0]
        self.assertEqual(sent.headers["authorization"], f"Bearer {other_access}")
        self.assertEqual(sent.url.params["usuario_id"], "eq.00000000-0000-0000-0000-000000000002")

    def test_get_null_optionals_and_html_escaping(self):
        self.row.update(nome='<script>alert(1)</script>', descricao='<img src=x onerror="alert(1)">',
                        especie="Gato", especie_outro=None, bairro=None, email_contato=None, whatsapp=None)
        page = self.client.get(URL)
        self.assertEqual(page.status_code, 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page.text)
        self.assertIn("&lt;img", page.text)
        self.assertNotIn("<script>alert(1)</script>", page.text)
        self.assertNotIn("<img src=x", page.text)
        self.assertIn('name="bairro" type="text" value=""', page.text)


if __name__ == "__main__":
    unittest.main()
