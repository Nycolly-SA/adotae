import unittest
from html.parser import HTMLParser

from run import app


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
        app.config.update(TESTING=True)
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

    def test_navigation_links_resolve_on_both_pages(self):
        for path in ("/", "/animais"):
            page = PageParser()
            page.feed(self.client.get(path).get_data(as_text=True))
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
