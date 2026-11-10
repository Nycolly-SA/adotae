"""Detalhes públicos com SDK Supabase real e transporte HTTP offline."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_routes
from tests.test_login import jwt, user
from run import app


class DetalhesPublicosTests(unittest.TestCase):
    # Reutiliza transporte e auxiliares; as regressões do catálogo ficam em test_routes.
    catalog_calls = test_routes.RouteTests.catalog_calls
    photo = test_routes.RouteTests.photo
    photo_calls = test_routes.RouteTests.photo_calls
    sign_calls = test_routes.RouteTests.sign_calls

    def setUp(self):
        test_routes.RouteTests.setUp(self)
        self.row = self.rows[0]
        self.path = f'/animais/{self.row["id"]}'

    def details(self, status=200):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
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
                   "especie_outro,bairro,email_contato,whatsapp,status_publicacao,status_adocao,oculto_admin,usuario_id",
            id="eq." + self.row["id"], status_publicacao="eq.publicado",
            status_adocao="eq.disponivel", oculto_admin="eq.false"))
        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
        self.assertEqual(call.headers["apikey"], "sb_publishable_test")
        self.assertEqual(len(self.photo_calls()), 1)
        self.assertEqual(self.sign_calls(), [])

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
        self.photos = [self.photo()]
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
        self.assertEqual(self.photo_calls(), [])
        self.assertEqual(self.sign_calls(), [])

    def test_returned_restricted_record_never_exposes_contacts(self):
        self.photos = [self.photo()]
        self.override_payload = True
        for field, value in (("status_publicacao", "rascunho"),
                             ("status_adocao", "adotado"), ("oculto_admin", True)):
            with self.subTest(field=field):
                self.payload = [dict(self.row, **{field: value})]
                response = self.details(404)
                self.assertNotIn(self.row["email_contato"], response.text)
                self.assertNotIn(self.row["whatsapp"], response.text)
        self.assertEqual(self.photo_calls(), [])
        self.assertEqual(self.sign_calls(), [])

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
        self.assertEqual(self.photo_calls(), [])
        self.assertEqual(self.sign_calls(), [])

    def test_escape_and_no_private_data_without_photo(self):
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
        self.assertEqual(self.sign_calls(), [])

    def assert_photo_unavailable(self):
        response = self.details()
        page = test_routes.PageParser()
        page.feed(response.text)
        self.assertEqual(page.images, [])
        self.assertIn("Fotografia temporariamente indisponível", response.text)
        self.assertIn("mailto:private-contact@example.org", page.links)
        self.assertIn("https://wa.me/5522999999999", page.links)
        return response

    def test_photo_uses_single_anonymous_signature_even_after_login(self):
        self.photos = [self.photo(), self.photo(1, 2)]
        for logged_in in (False, True):
            with self.subTest(logged_in=logged_in):
                if logged_in:
                    self.client.set_cookie("adotae_access", jwt())
                    self.client.set_cookie("adotae_refresh", "private-refresh")
                self.calls.clear()
                with patch("run.render_template", wraps=__import__("run").render_template) as render:
                    response = self.details()
                    self.assertNotIn("usuario_id", render.call_args.kwargs["animal"])
                page = test_routes.PageParser()
                page.feed(response.text)
                path = f'{user()["id"]}/{self.row["id"]}/{self.photos[0]["id"]}.webp'
                self.assertEqual(len(page.images), 1)
                self.assertEqual(page.images[0]["src"],
                    "https://example.supabase.co/storage/v1/object/sign/animal-fotos/" + path + "?token=signature.test")
                self.assertEqual(page.images[0]["alt"], "Fotografia de Bento")
                self.assertEqual(page.images[0]["class"], "detail-image")
                self.assertEqual(page.images[0]["referrerpolicy"], "no-referrer")
                self.assertNotIn("Fotografia temporariamente indisponível", response.text)
                self.assertIn("mailto:private-contact@example.org", page.links)
                self.assertIn("https://wa.me/5522999999999", page.links)
                self.assertEqual(len(self.photo_calls()), 1)
                self.assertEqual(dict(self.photo_calls()[0].url.params), dict(
                    select="id,animal_id,estado", animal_id=f'in.({self.row["id"]})', estado="eq.pronta"))
                self.assertEqual(len(self.sign_calls()), 1)
                sign = self.sign_calls()[0]
                self.assertEqual(sign.method, "POST")
                self.assertEqual(json.loads(sign.content), {"expiresIn": "60"})
                for call in self.calls:
                    if not call.url.path.endswith("/user"):
                        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
                        self.assertEqual(call.headers["apikey"], "sb_publishable_test")
                for private in ("usuario_id", "private-refresh", jwt(), "sb_publishable_test"):
                    self.assertNotIn(private, response.text)

    def test_photo_selection_and_escaped_alt_preserve_layout(self):
        self.row["nome"] = '<Bento "amigo">'
        self.photos = [self.photo(0, 9), self.photo(0, 2)]
        for _ in range(2):
            page = test_routes.PageParser()
            response = self.details()
            page.feed(response.text)
            self.assertEqual(len(page.images), 1)
            self.assertIn(self.photo(0, 2)["id"], page.images[0]["src"])
            self.assertEqual(page.images[0]["alt"], 'Fotografia de <Bento "amigo">')
            self.assertNotIn('<Bento "amigo">', response.text)
            self.assertIn('class="detail-layout"', response.text)
            self.photos.reverse()

    def test_missing_photo_and_reservation_errors_preserve_contacts(self):
        self.assert_photo_unavailable()
        for failure in (403, "network"):
            with self.subTest(failure=failure):
                self.photo_failure = failure
                self.assert_photo_unavailable()
        self.photo_failure = None
        for payload in ({}, "private", 42, [None, {}, self.photo(estado="pendente"),
                         self.photo(id="invalid"), self.photo(1, 2)]):
            with self.subTest(payload=payload):
                self.reservation_payload = payload
                self.assert_photo_unavailable()
        self.assertEqual(self.sign_calls(), [])

    def test_signing_errors_and_invalid_urls_preserve_contacts(self):
        self.photos = [self.photo()]
        self.sign_failure = {self.photos[0]["id"]}
        self.assert_photo_unavailable()
        self.sign_failure = set()
        self.sign_network_failure = True
        self.assert_photo_unavailable()
        self.sign_network_failure = False
        for payload in ({}, [], {"signedURL": 123},
                        {"signedURL": "/https://evil.example/image?token=secret"},
                        {"signedURL": "/object/public/animal-fotos/x?token=x"},
                        {"signedURL": "/object/sign/animal-fotos/wrong.webp?token=x"}):
            with self.subTest(payload=payload):
                self.sign_payload = payload
                response = self.assert_photo_unavailable()
                self.assertNotIn("evil.example", response.text)

    def test_invalid_owner_only_disables_photo(self):
        self.photos = [self.photo()]
        for owner in (None, "invalid", 123):
            self.row["usuario_id"] = owner
            self.assert_photo_unavailable()
        self.assertEqual(self.sign_calls(), [])

    def test_photo_urls_and_errors_are_not_logged(self):
        self.photos = [self.photo()]
        for failure in (False, True):
            self.sign_network_failure = failure
            with self.assertLogs("httpx", level="INFO") as logs:
                self.details()
            self.assertNotIn("token=", "\n".join(logs.output))

    def test_responsive_structure_is_preserved(self):
        self.photos = [self.photo()]
        html = self.details().text
        self.assertIn('name="viewport"', html)
        self.assertIn('class="detail-layout"', html)
        css = Path("app/static/css/style.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn(".detail-layout { grid-template-columns: 1fr", css)

