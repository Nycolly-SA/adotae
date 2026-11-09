import copy
import json
import unittest

import httpx
from supabase import create_client
from html.parser import HTMLParser
from unittest.mock import patch

from run import ANIMAIS, app
from tests.test_login import jwt, user


class PageParser(HTMLParser):
    """Coleta links, imagens e títulos do HTML entregue ao visitante."""

    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.heading_count = 0
        self.forms = []
        self.selects = {}
        self.current_select = None
        self.clear_link = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.links.append(attributes.get("href", ""))
            if "filter-clear" in attributes.get("class", "").split():
                self.clear_link = attributes.get("href")
        elif tag == "img":
            self.images.append(attributes)
        elif tag == "h1":
            self.heading_count += 1
        elif tag == "form":
            self.forms.append(attributes)
        elif tag == "select":
            self.current_select = attributes["name"]
            self.selects[self.current_select] = []
        elif tag == "option" and self.current_select:
            self.selects[self.current_select].append(attributes)

    def handle_endtag(self, tag):
        if tag == "select":
            self.current_select = None


class RouteTests(unittest.TestCase):
    def setUp(self):
        original_config = app.config.copy()
        self.addCleanup(app.config.update, original_config)
        app.config.update(TESTING=True, SECRET_KEY="test-only-secret")
        self.client = app.test_client()
        app.config.update(SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test")
        self.calls = []
        self.failure = None
        self.payload = None
        self.override_payload = False
        self.rows = []
        for index, (nome, especie, sexo, idade, porte, distrito) in enumerate((
            ("Bento", "Cachorro", "Macho", "2 anos", "Médio", "São João da Barra (sede)"),
            ("Luna", "Gato", "Fêmea", "1 ano", "Pequeno", "Atafona"),
            ("Paçoca", "Cachorro", "Macho", "6 meses", "Pequeno", "Grussaí"),
            ("Amora", "Gato", "Fêmea", "3 anos", "Pequeno", "Barcelos"),
        ), 1):
            self.rows.append(dict(id=f"00000000-0000-0000-0000-{index:012d}", nome=nome,
                especie=especie, sexo=sexo, idade_aproximada=idade, porte=porte,
                distrito=distrito, descricao="Descrição pública.", criado_em=f"2026-11-0{5-index}T00:00:00Z",
                status_publicacao="publicado", status_adocao="disponivel", oculto_admin=False,
                usuario_id=user()["id"], email_contato="private-contact@example.org", whatsapp="5522999999999",
                especie_outro=None, bairro=None))

        def transport(request):
            self.calls.append(request)
            if request.url.path.endswith("/user"):
                return httpx.Response(200, json=user())
            self.assertEqual(request.url.path, "/rest/v1/animais")
            self.assertEqual(request.method, "GET")
            if self.failure == "network":
                raise httpx.ConnectError("private provider details sb_publishable_test")
            if self.failure == "invalid-json":
                return httpx.Response(200, text="private provider details")
            if self.failure:
                return httpx.Response(self.failure, json=dict(code="42501", message="private provider details"))
            if self.override_payload:
                return httpx.Response(200, content=json.dumps(self.payload), headers={"Content-Type": "application/json"})
            rows = copy.deepcopy(self.rows)
            for campo, value in request.url.params.multi_items():
                if value.startswith("eq."):
                    rows = [row for row in rows if str(row[campo]).lower() == value[3:].lower()]
            if request.url.params.get("order") == "criado_em.desc":
                rows.sort(key=lambda row: row["criado_em"], reverse=True)
            campos = request.url.params.get("select", "*").split(",")
            if campos != ["*"]:
                rows = [{campo: row[campo] for campo in campos} for row in rows]
            return httpx.Response(200, json=rows)

        def factory(url, key, options):
            self.assertFalse(options.persist_session)
            self.assertFalse(options.auto_refresh_token)
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            return create_client(url, key, options=options)

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_home_presents_project_and_catalog_link(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Adotaê", html)
        self.assertIn("Um encontro pode mudar duas vidas.", html)
        page = PageParser()
        page.feed(html)
        self.assertIn("/animais", page.links)
        self.assertEqual(page.heading_count, 1)

    def test_catalog_presents_public_animals_without_demo_images(self):
        html, page = self.assert_catalog({}, ("Bento", "Luna", "Paçoca", "Amora"))
        self.assertEqual(page.heading_count, 1)
        self.assertEqual(page.images, [])
        self.assertEqual(html.count("Fotografia temporariamente indisponível"), 4)
        self.assertIn("Ver detalhes", html)
        self.assertNotIn("os animais e os dados são fictícios", html)
        self.assertIn("dados de validação podem ser fictícios", html)
        self.assertIn("2 anos", html)

    def assert_catalog(self, query, names):
        response = self.client.get("/animais", query_string=query)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for name in ("Bento", "Luna", "Paçoca", "Amora"):
            with self.subTest(name=name):
                self.assertEqual(f">{name}</h2>" in html, name in names)
        count = len(names)
        self.assertIn(
            f"{count} animal encontrado" if count == 1 else f"{count} animais encontrados",
            html,
        )
        page = PageParser()
        page.feed(html)
        self.assertEqual(html.count('class="animal-card"'), count)
        return html, page

    def test_catalog_filter_options_default_to_all(self):
        _, page = self.assert_catalog({}, ("Bento", "Luna", "Paçoca", "Amora"))
        self.assertIn({"method": "get", "action": "/animais", "class": "catalog-filters"}, page.forms)
        expected_options = {
            "tipo": ["", "Cachorro", "Gato", "Ave", "Pequeno mamífero", "Outro animal doméstico"],
            "porte": ["", "Pequeno", "Médio", "Grande"],
            "distrito": ["", "São João da Barra (sede)", "Atafona", "Grussaí", "Cajueiro", "Pipeiras", "Barcelos"],
        }
        self.assertEqual(set(page.selects), set(expected_options))
        for name, values in expected_options.items():
            with self.subTest(field=name):
                self.assertEqual([option["value"] for option in page.selects[name]], values)
                self.assertEqual([option["value"] for option in page.selects[name] if "selected" in option], [""])

    def test_catalog_has_no_links_to_demo_details_even_after_filtering(self):
        for query in ({}, {"tipo": "Gato"}, {"distrito": "Grussaí"}, {"porte": "Grande"}):
            with self.subTest(query=query):
                html = self.client.get("/animais", query_string=query).text
                page = PageParser()
                page.feed(html)
                details = [link for link in page.links if link.startswith("/animais/")]
                expected = [f'/animais/{row["id"]}' for row in self.rows
                            if (not query.get("tipo") or row["especie"] == query["tipo"])
                            and (not query.get("distrito") or row["distrito"] == query["distrito"])
                            and (not query.get("porte") or row["porte"] == query["porte"])]
                self.assertEqual(details, expected)

    def test_details_present_each_animals_data_and_existing_image(self):
        cases = (
            (1, "Bento", "Cachorro", "Macho", "2 anos", "Médio", "São João da Barra (sede)",
             "Companheiro de caminhadas e de tardes preguiçosas no sofá.", "bento.svg"),
            (2, "Luna", "Gato", "Fêmea", "1 ano", "Pequeno", "Atafona",
             "Curiosa, tranquila e especialista em encontrar um cantinho de sol.", "luna.svg"),
            (3, "Paçoca", "Cachorro", "Macho", "6 meses", "Pequeno", "Grussaí",
             "Um filhote cheio de energia, sempre pronto para uma brincadeira.", "pacoca.svg"),
            (4, "Amora", "Gato", "Fêmea", "3 anos", "Pequeno", "Barcelos",
             "Carinhosa e observadora. Gosta de companhia e de uma boa soneca.", "amora.svg"),
        )
        for animal_id, name, species, sex, age, size, district, description, image in cases:
            with self.subTest(animal=name):
                response = self.client.get(f"/animais/{animal_id}")
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn(f"{name} — Adotaê</title>", html)
                self.assertIn(f">{name}</h1>", html)
                for label, value in (("Espécie", species), ("Sexo", sex), ("Idade", age),
                                     ("Porte", size), ("Distrito", district)):
                    self.assertIn(f"<dt>{label}</dt><dd>{value}</dd>", html)
                self.assertIn(description, html)
                page = PageParser()
                page.feed(html)
                self.assertEqual(page.heading_count, 1)
                self.assertEqual(len(page.images), 1)
                self.assertEqual(page.images[0]["src"], f"/static/images/{image}")
                self.assertTrue(page.images[0].get("alt"))
                self.assertIn("/animais", page.links)
                self.assertIn("Voltar ao catálogo</a>", html)
                self.assertIn("Pular para o conteúdo", html)

    def test_detail_identifiers_do_not_depend_on_catalog_order(self):
        with patch("run.ANIMAIS", list(reversed(ANIMAIS))):
            for animal_id, name in ((1, "Bento"), (2, "Luna"), (3, "Paçoca"), (4, "Amora")):
                with self.subTest(animal=name):
                    response = self.client.get(f"/animais/{animal_id}")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(f">{name}</h1>", response.get_data(as_text=True))

    def test_details_contact_is_demonstrative_without_contact_actions(self):
        for animal_id in (1, 2, 3, 4):
            with self.subTest(animal_id=animal_id):
                html = self.client.get(f"/animais/{animal_id}").get_data(as_text=True)
                self.assertIn("Contato demonstrativo", html)
                self.assertIn("dados são fictícios", html)
                self.assertIn("Não há contatos disponíveis nesta demonstração.", html)
                page = PageParser()
                page.feed(html)
                self.assertEqual(page.forms, [])
                self.assertFalse(any(link.startswith(("tel:", "mailto:", "http:", "https:"))
                                     for link in page.links))

    def test_unknown_animal_identifiers_return_404(self):
        for animal_id in ("0", "5", "999", "-1", "inexistente", "1.5"):
            with self.subTest(animal_id=animal_id):
                self.assertEqual(self.client.get(f"/animais/{animal_id}").status_code, 404)

    def test_catalog_filters_by_type(self):
        for value, names in (("Cachorro", ("Bento", "Paçoca")), ("Gato", ("Luna", "Amora")),
                             ("Ave", ()), ("Pequeno mamífero", ()), ("Outro animal doméstico", ())):
            with self.subTest(tipo=value):
                self.assert_catalog({"tipo": value}, names)

    def test_catalog_filters_by_size(self):
        for value, names in (("Pequeno", ("Luna", "Paçoca", "Amora")), ("Médio", ("Bento",)), ("Grande", ())):
            with self.subTest(porte=value):
                self.assert_catalog({"porte": value}, names)

    def test_catalog_filters_by_district(self):
        cases = (("São João da Barra (sede)", ("Bento",)), ("Atafona", ("Luna",)),
                 ("Grussaí", ("Paçoca",)), ("Barcelos", ("Amora",)), ("Cajueiro", ()), ("Pipeiras", ()))
        for value, names in cases:
            with self.subTest(distrito=value):
                self.assert_catalog({"distrito": value}, names)

    def test_catalog_combines_filters_and_preserves_selection(self):
        query = {"tipo": "Cachorro", "porte": "Pequeno", "distrito": "Grussaí"}
        _, page = self.assert_catalog(query, ("Paçoca",))
        for field, value in query.items():
            self.assertEqual([option["value"] for option in page.selects[field] if "selected" in option], [value])

    def test_catalog_shows_friendly_empty_result(self):
        html, _ = self.assert_catalog({"tipo": "Gato", "porte": "Médio"}, ())
        self.assertIn("Nenhum animal encontrado", html)
        self.assertIn("Tente ajustar os filtros", html)

    def test_clear_filters_restores_full_catalog(self):
        _, page = self.assert_catalog({"tipo": "Gato", "distrito": "Atafona"}, ("Luna",))
        self.assertEqual(page.clear_link, "/animais")
        response = self.client.get(page.clear_link)
        cleared = PageParser()
        cleared.feed(response.get_data(as_text=True))
        self.assertEqual(response.text.count('class="animal-card"'), 4)
        for options in cleared.selects.values():
            self.assertEqual([option["value"] for option in options if "selected" in option], [""])

    def test_unknown_parameters_and_values_are_ignored(self):
        self.assert_catalog({"desconhecido": "valor"}, ("Bento", "Luna", "Paçoca", "Amora"))
        _, page = self.assert_catalog({"tipo": "Dragão", "porte": "Gigante", "distrito": "Inexistente"},
                                     ("Bento", "Luna", "Paçoca", "Amora"))
        for options in page.selects.values():
            self.assertEqual([option["value"] for option in options if "selected" in option], [""])
        self.assert_catalog({"tipo": "Gato", "porte": "inválido", "extra": "valor"}, ("Luna", "Amora"))

    def test_explicit_all_options_restore_full_catalog(self):
        self.assert_catalog({"tipo": "", "porte": "", "distrito": ""}, ("Bento", "Luna", "Paçoca", "Amora"))

    def test_navigation_links_resolve_on_all_pages(self):
        for path in ("/", "/animais", "/animais/1", "/animais/2", "/animais/3", "/animais/4"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            page = PageParser()
            page.feed(response.get_data(as_text=True))
            for href in page.links:
                if href.startswith("/"):
                    with self.subTest(page=path, link=href):
                        self.assertEqual(self.client.get(href).status_code, 200)

    def test_stylesheet_and_navigation_script_are_served(self):
        for path in ("/static/css/style.css", "/static/js/main.js"):
            with self.subTest(path=path):
                with self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.data)

    def catalog_calls(self):
        return [r for r in self.calls if r.url.path == "/rest/v1/animais"]

    def test_public_query_is_anonymous_explicit_and_newest_first(self):
        self.rows.reverse()
        html, _ = self.assert_catalog({}, ("Bento", "Luna", "Paçoca", "Amora"))
        call = self.catalog_calls()[-1]
        self.assertEqual(dict(call.url.params), dict(
            select="id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito",
            status_publicacao="eq.publicado", status_adocao="eq.disponivel",
            oculto_admin="eq.false", order="criado_em.desc"))
        self.assertEqual(call.headers["apikey"], "sb_publishable_test")
        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sorted((html.index(f">{name}</h2>"), name) for name in
                              ("Bento", "Luna", "Paçoca", "Amora")),
                         [(html.index(f">{name}</h2>"), name) for name in
                          ("Bento", "Luna", "Paçoca", "Amora")])

    def test_authenticated_catalog_uses_same_public_query_and_visibility(self):
        for field, value in (("status_publicacao", "rascunho"),
                             ("status_adocao", "adotado"), ("oculto_admin", True)):
            self.rows.append(dict(self.rows[0], nome="Privado " + field, **{field: value}))
        visitor = self.client.get("/animais")
        public_call = self.catalog_calls()[-1]
        access = jwt()
        self.client.set_cookie("adotae_access", access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        authenticated = self.client.get("/animais")
        self.assertEqual(authenticated.status_code, 200)
        self.assertIn("Minha conta", authenticated.text)
        for response in (visitor, authenticated):
            self.assertIn("4 animais encontrados", response.text)
            self.assertNotIn("Privado ", response.text)
        call = self.catalog_calls()[-1]
        self.assertEqual(call.url, public_call.url)
        self.assertEqual(call.headers.get("authorization"), public_call.headers.get("authorization"))
        self.assertEqual(call.headers["authorization"], "Bearer sb_publishable_test")
        self.assertTrue(any(r.headers.get("authorization") == "Bearer " + access
                            for r in self.calls if r.url.path.endswith("/user")))

    def test_filters_are_sent_to_api_individually_and_combined(self):
        for query in ({"tipo": "Gato"}, {"porte": "Pequeno"}, {"distrito": "Atafona"},
                      {"tipo": "Gato", "porte": "Pequeno", "distrito": "Atafona"},
                      {"tipo": "Dragão", "porte": "Pequeno", "extra": "ignorar"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/animais", query_string=query).status_code, 200)
                params = self.catalog_calls()[-1].url.params
                expected = {"especie": "eq.Gato"} if query.get("tipo") == "Gato" else {}
                expected.update({k: "eq." + query[k] for k in ("porte", "distrito") if k in query})
                self.assertEqual({k: params[k] for k in ("especie", "porte", "distrito") if k in params}, expected)
                self.assertNotIn("extra", params)

    def test_legitimately_empty_catalog(self):
        self.rows = []
        html, _ = self.assert_catalog({}, ())
        self.assertIn("Nenhum animal encontrado", html)
        self.assertNotIn("Catálogo indisponível", html)

    def assert_unavailable(self):
        response = self.client.get("/animais")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Catálogo indisponível", response.text)
        for value in ("Nenhum animal encontrado", "0 animais encontrados", "private", "sb_publishable_test"):
            self.assertNotIn(value, response.text)
        return response

    def test_api_failures_do_not_look_like_empty_catalog(self):
        for failure in (401, 403, 500, "network", "invalid-json"):
            with self.subTest(failure=failure):
                self.failure = failure
                self.assert_unavailable()

    def test_missing_or_invalid_configuration(self):
        for field, value in (("SUPABASE_URL", ""), ("SUPABASE_PUBLISHABLE_KEY", ""),
                             ("SUPABASE_URL", "invalid")):
            with self.subTest(field=field, value=value), patch.dict(app.config, {field: value}):
                self.assert_unavailable()
        self.assertEqual(self.calls, [])

    def test_unexpected_records_fail_without_rendering_partial_results(self):
        row = {k: self.rows[0][k] for k in (
            "id", "nome", "especie", "sexo", "idade_aproximada", "porte", "descricao", "distrito")}
        invalid_rows = [None, "private", {}, dict(row, id="invalid"), dict(row, id=123),
                        dict(row, especie="Dragão"), dict(row, sexo="invalid"),
                        dict(row, porte="Gigante"), dict(row, distrito="invalid")]
        for field in row:
            invalid_rows.extend(({k: v for k, v in row.items() if k != field},
                                 dict(row, **{field: None}), dict(row, **{field: []}),
                                 dict(row, **{field: " "})))
        invalid_rows.extend((dict(row, nome="x" * 101), dict(row, idade_aproximada="x" * 51),
                             dict(row, descricao="x" * 2001)))
        self.override_payload = True
        for payload in (None, {}, "private", 42, *([row, invalid] for invalid in invalid_rows)):
            with self.subTest(payload=payload):
                self.payload = payload
                response = self.assert_unavailable()
                self.assertNotIn('class="animal-card"', response.text)

    def test_no_private_fields_and_html_is_escaped(self):
        self.rows[0]["nome"] = '<script>alert("x")</script>'
        response = self.client.get("/animais")
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn('<script>alert(', response.text)
        for value in ("private-contact", "5522999999999", "usuario_id", "email_contato",
                      "whatsapp", "sb_publishable_test"):
            self.assertNotIn(value, response.text)


if __name__ == "__main__":
    unittest.main()
