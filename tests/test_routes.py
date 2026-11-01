import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from run import ANIMAIS, app


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

    def test_catalog_presents_four_fictional_animals(self):
        response = self.client.get("/animais")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for name in ("Bento", "Luna", "Paçoca", "Amora"):
            with self.subTest(name=name):
                self.assertIn(name, html)
        self.assertIn("fictícios", html)
        page = PageParser()
        page.feed(html)
        self.assertEqual(page.heading_count, 1)
        self.assertEqual(len(page.images), 4)
        for animal_image in page.images:
            with self.subTest(image=animal_image.get("src")):
                self.assertTrue(animal_image.get("alt"))
                with self.client.get(animal_image["src"]) as image_response:
                    self.assertEqual(image_response.status_code, 200)

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
        self.assertEqual(len(page.images), count)
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

    def test_catalog_links_to_each_animal_even_after_filtering(self):
        cases = (({}, [1, 2, 3, 4]), ({"tipo": "Gato"}, [2, 4]),
                 ({"distrito": "Grussaí"}, [3]), ({"porte": "Grande"}, []))
        for query, ids in cases:
            with self.subTest(query=query):
                html = self.client.get("/animais", query_string=query).get_data(as_text=True)
                page = PageParser()
                page.feed(html)
                self.assertEqual([link for link in page.links if link.startswith("/animais/")],
                                 [f"/animais/{animal_id}" for animal_id in ids])
                self.assertEqual(html.count(">Ver detalhes</a>"), len(ids))

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
        self.assertEqual(len(cleared.images), 4)
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


if __name__ == "__main__":
    unittest.main()
