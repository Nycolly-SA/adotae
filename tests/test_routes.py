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

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a":
            self.links.append(attributes.get("href", ""))
        elif tag == "img":
            self.images.append(attributes)
        elif tag == "h1":
            self.heading_count += 1


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
