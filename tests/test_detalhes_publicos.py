"""Detalhes públicos com SDK Supabase real e transporte HTTP offline."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_routes
from tests.test_login import jwt, user
from run import app


class DetalhesPublicosTests(unittest.TestCase):
    # Reutiliza apenas o transporte; as regressões continuam em test_routes.
    catalog_calls = test_routes.RouteTests.catalog_calls

    def setUp(self):
        test_routes.RouteTests.setUp(self)
        self.row = self.rows[0]
        self.path = f'/animais/{self.row["id"]}'

    def details(self, status=200):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        return response

    def test_public_details_and_exact_anonymous_query(self):
        self.row.update(bairro="Centro", descricao="Descrição completa.\nSegunda linha.")
        response = self.details()
        for field in ("nome", "especie", "sexo", "idade_aproximada", "porte", "distrito",
                      "bairro", "descricao", "email_contato", "whatsapp"):
            self.assertIn(self.row[field], response.text)
        page = test_routes.PageParser()
        page.feed(response.text)
        self.assertIn("mailto:private-contact@example.org", page.links)
        self.assertIn("https://wa.me/5522999999999", page.links)
        self.assertIn("/animais", page.links)
        self.assertEqual(page.images, [])
        self.assertEqual(page.forms, [])
        self.assertEqual(page.heading_count, 1)
        self.assertIn("Fotografia temporariamente indisponível", response.text)
        self.assertNotIn("Contato demonstrativo", response.text)
        call = self.catalog_calls()[-1]
        self.assertEqual(dict(call.url.params), dict(
            select="id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito,"
                   "especie_outro,bairro,email_contato,whatsapp,status_publicacao,status_adocao,oculto_admin",
            id="eq." + self.row["id"], status_publicacao="eq.publicado",
            status_adocao="eq.disponivel", oculto_admin="eq.false"))
        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
        self.assertEqual(call.headers["apikey"], "sb_publishable_test")
        self.assertEqual(len(self.calls), 1)

    def test_authenticated_owner_has_only_public_access(self):
        self.details()
        public_call = self.catalog_calls()[-1]
        access = jwt()
        self.client.set_cookie("adotae_access", access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        self.assertIn("Minha conta", self.details().text)
        call = self.catalog_calls()[-1]
        self.assertEqual(call.url, public_call.url)
        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
        for field, value in (("status_publicacao", "rascunho"),
                             ("status_adocao", "adotado"), ("oculto_admin", True)):
            original = self.row[field]
            self.row[field] = value
            self.details(404)
            self.row[field] = original

    def test_optional_fields_and_contacts(self):
        for email, whatsapp in ((None, "12345678"), ("contact@example.org", None), (None, None)):
            with self.subTest(email=email, whatsapp=whatsapp):
                self.row.update(email_contato=email, whatsapp=whatsapp, bairro=None)
                page = test_routes.PageParser()
                response = self.details()
                page.feed(response.text)
                self.assertEqual(any(link.startswith("mailto:") for link in page.links), bool(email))
                self.assertEqual(any(link.startswith("https://wa.me/") for link in page.links), bool(whatsapp))
                if whatsapp:
                    self.assertIn("https://wa.me/12345678", page.links)
                self.assertNotIn("<dt>Bairro</dt>", response.text)
                self.assertNotIn("None", response.text)

    def test_other_species(self):
        self.row.update(especie="Outro animal doméstico", especie_outro="Cabra")
        self.assertIn("Cabra", self.details().text)
        self.row["especie_outro"] = None
        self.details(503)

    def test_unavailable_animals_are_404_without_contacts(self):
        for field, value in (("status_publicacao", "rascunho"),
                             ("status_adocao", "adotado"), ("oculto_admin", True)):
            with self.subTest(field=field):
                original = self.row[field]
                self.row[field] = value
                response = self.details(404)
                self.assertNotIn(self.row["email_contato"], response.text)
                self.row[field] = original
        self.rows = []
        self.details(404)

    def test_returned_restricted_record_never_exposes_contacts(self):
        self.override_payload = True
        for field, value in (("status_publicacao", "rascunho"),
                             ("status_adocao", "adotado"), ("oculto_admin", True)):
            with self.subTest(field=field):
                self.payload = [dict(self.row, **{field: value})]
                response = self.details(404)
                self.assertNotIn(self.row["email_contato"], response.text)
                self.assertNotIn(self.row["whatsapp"], response.text)

    def test_availability_is_checked_again_and_secret_is_not_required(self):
        with patch.dict(app.config, {"SECRET_KEY": None}):
            self.details()
            self.row["status_adocao"] = "adotado"
            self.details(404)
        self.assertEqual(len(self.catalog_calls()), 2)
        self.assertEqual(self.client.post(self.path).status_code, 405)

    def test_invalid_uuid_does_not_query_data_api(self):
        for value in ("invalid", "00000000-0000-0000-0000-zzzzzzzzzzzz", "aaaaaaaa000000000000000000000001"):
            self.assertEqual(self.client.get("/animais/" + value).status_code, 404)
        self.assertEqual(self.catalog_calls(), [])

    def test_detail_api_and_configuration_failures(self):
        for failure in (401, 403, 500, "network", "invalid-json"):
            self.failure = failure
            response = self.details(503)
            self.assertIn("Detalhes indisponíveis", response.text)
            self.assertNotIn("private", response.text)
            self.assertNotIn("sb_publishable_test", response.text)
        self.failure = None
        for config in ({"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}, {"SUPABASE_URL": "invalid"}):
            with patch.dict(app.config, config):
                self.details(503)

    def test_malformed_responses_never_render_partial_details(self):
        row = copy.deepcopy(self.row)
        bad = [None, {}, "private", 42, [None], [row, row], [dict(row, id=self.rows[1]["id"])]]
        for field in ("id", "nome", "especie", "sexo", "idade_aproximada", "porte", "distrito", "descricao",
                      "especie_outro", "bairro", "email_contato", "whatsapp", "status_publicacao", "status_adocao", "oculto_admin"):
            bad.extend(([{k: v for k, v in row.items() if k != field}], [dict(row, **{field: []})]))
        for field, value in (("nome", " "), ("bairro", "x" * 101), ("especie_outro", "x" * 61),
                             ("whatsapp", "0123456789"), ("whatsapp", "12345678\n"),
                             ("whatsapp", "+5522999999999"), ("whatsapp", "https://evil.test"),
                             ("email_contato", "javascript:alert(1)"),
                             ("email_contato", "a@example.org?subject=evil"),
                             ("email_contato", "a@example.org\r\nBcc:b@example.org"),
                             ("oculto_admin", "false"), ("status_adocao", "unknown")):
            bad.append([dict(row, **{field: value})])
        self.override_payload = True
        for payload in bad:
            with self.subTest(payload=payload):
                self.payload = payload
                response = self.details(503)
                self.assertNotIn("mailto:", response.text)
                self.assertNotIn("https://wa.me/", response.text)
                self.assertNotIn('id="nome-animal"', response.text)

    def test_escape_and_no_private_data_or_storage(self):
        self.row.update(nome='<script>alert("x")</script>', bairro='<img src=x onerror="x">',
                        descricao="<script>private-script</script>", email_contato="a+tag@example.org")
        response = self.details()
        self.assertIn("&lt;script&gt;", response.text)
        self.assertIn("&lt;img", response.text)
        self.assertNotIn('<script>alert', response.text)
        self.assertNotIn('<img src=x', response.text)
        self.assertIn("mailto:a%2Btag@example.org", response.text)
        for value in (user()["id"], "usuario_id", "sb_publishable_test", "private-refresh"):
            self.assertNotIn(value, response.text)
        self.assertTrue(all(call.url.path == "/rest/v1/animais" for call in self.calls))

    def test_responsive_structure_is_preserved(self):
        html = self.details().text
        self.assertIn('name="viewport"', html)
        self.assertIn('class="detail-layout"', html)
        css = Path("app/static/css/style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".detail-layout { grid-template-columns: 1fr", css)

