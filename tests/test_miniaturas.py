
"""Miniaturas próprias: SDK real, Auth/Data API/Storage simulados, sem rede."""

from io import BytesIO
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from supabase import create_client

from run import app
from tests.test_login import jwt, user


ANIMAL = "00000000-0000-0000-0000-000000000010"
FOTO = "00000000-0000-0000-0000-000000000020"
URL = f"/anuncios/{ANIMAL}/miniatura"


class MiniaturasTests(unittest.TestCase):
    def setUp(self):
        original = app.config.copy()
        self.addCleanup(app.config.update, original)
        app.config.update(TESTING=True, SECRET_KEY="test", SESSION_COOKIE_SECURE=False,
                          SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test")
        self.client = app.test_client()
        self.access = jwt()
        self.client.set_cookie("adotae_access", self.access)
        self.client.set_cookie("adotae_refresh", "private-refresh")
        self.identity = user()

        owner_id = self.identity["id"]
        if not isinstance(owner_id, str):
            raise AssertionError("ID do usuário de teste inválido.")
        self.owner: str = owner_id

        self.calls = []
        self.fotos = [dict(id=FOTO, animal_id=ANIMAL, estado="pronta", posicao=1)]
        self.animal = dict(id=ANIMAL, usuario_id=self.owner, nome="Fubá", especie="Gato",
                           distrito="Grussaí", status_publicacao="rascunho", status_adocao="disponivel", oculto_admin=False)
        self.storage_error = None
        self.auth_status = 200
        self.image = BytesIO()
        Image.new("RGB", (800, 400), "red").save(self.image, format="WEBP")

        def transport(request):
            self.calls.append(request)
            path = request.url.path
            if path.endswith("/user"):
                return httpx.Response(self.auth_status, json=self.identity)
            if path == "/rest/v1/animais":
                return httpx.Response(200, json=[dict(self.animal, animal_fotos=self.fotos)])
            if path == "/rest/v1/animal_fotos":
                return httpx.Response(200, json=[dict(f, animais=self.animal) for f in self.fotos[:1]])
            if path.startswith("/storage/v1/object/animal-fotos/"):
                if self.storage_error == "network":
                    raise httpx.ReadTimeout("private " + self.access)
                if isinstance(self.storage_error, int):
                    return httpx.Response(self.storage_error, json=dict(statusCode=str(self.storage_error),
                        error="private", message=self.access))
                return httpx.Response(200, content=self.image.getvalue(), headers={"Content-Type": "image/webp"})
            raise AssertionError(f"Requisição inesperada: {request.method} {request.url}")

        def factory(url, key, options):
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            return create_client(url, key, options=options)

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def storage_calls(self):
        return [r for r in self.calls if r.url.path.startswith("/storage/")]

    def test_own_draft_published_and_adopted_are_small_private_webp(self):
        for publicacao, adocao in (("rascunho", "disponivel"), ("publicado", "disponivel"),
                                   ("publicado", "adotado")):
            with self.subTest(publicacao=publicacao, adocao=adocao):
                self.animal.update(status_publicacao=publicacao, status_adocao=adocao)
                response = self.client.get(URL)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content_type, "image/webp")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                with Image.open(BytesIO(response.data)) as image:
                    self.assertEqual(image.size, (192, 192))
                self.assertLess(len(response.data), 32_000)
                query = next(r for r in reversed(self.calls) if r.url.path.endswith("/animal_fotos"))
                self.assertEqual(query.url.params["animais.usuario_id"], "eq." + self.owner)
                self.assertEqual(query.url.params["animal_id"], "eq." + ANIMAL)
                self.assertEqual(query.url.params["estado"], "eq.pronta")
                self.assertEqual(query.url.params["order"], "posicao.asc,id.asc")
                self.assertEqual(query.url.params["limit"], "1")
                download = self.storage_calls()[-1]
                self.assertEqual(download.headers["authorization"], "Bearer " + self.access)
                self.assertTrue(download.url.path.endswith(f"/{self.owner}/{ANIMAL}/{FOTO}.webp"))
                self.assertFalse(download.url.query)

    def test_requires_valid_confirmed_session_even_for_published_animals(self):
        response = app.test_client().get(URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.calls, [])
        for auth_status, confirmed in ((401, True), (200, False), (503, True)):
            self.auth_status = auth_status
            self.identity = user(confirmed=confirmed)
            self.client.set_cookie("adotae_access", self.access)
            self.client.set_cookie("adotae_refresh", "private-refresh")
            self.assertIn(self.client.get(URL).status_code, (401, 503))
        self.assertEqual(self.storage_calls(), [])

    def test_owner_isolation_absence_and_unready_photo_never_download(self):
        cases = [[], [dict(self.fotos[0], estado="pendente")],
                 [dict(self.fotos[0], animal_id="00000000-0000-0000-0000-000000000099")]]
        for fotos in cases:
            self.fotos = fotos
            self.assertEqual(self.client.get(URL).status_code, 404)
        self.fotos = [dict(id=FOTO, animal_id=ANIMAL, estado="pronta", posicao=1)]
        self.identity = dict(user(), id="00000000-0000-0000-0000-000000000002")
        self.access = jwt("bia")
        self.client.set_cookie("adotae_access", self.access)
        self.assertEqual(self.client.get(URL).status_code, 404)
        self.assertEqual(self.storage_calls(), [])

    def test_storage_failure_or_corrupt_image_is_generic_and_not_cached(self):
        for error in (400, 403, 404, 500, "network", None):
            if error is None:
                self.image = BytesIO(b"invalid private bytes")
            self.storage_error = error
            response = self.client.get(URL)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            for secret in (self.access, self.owner, "private", "supabase.co"):
                self.assertNotIn(secret, response.text)

    def test_list_includes_thumbnail_only_when_ready_without_extra_queries(self):
        self.fotos = [dict(id="00000000-0000-0000-0000-000000000001", estado="pendente", posicao=1),
                      dict(id=FOTO, estado="pronta", posicao=2),
                      dict(id="00000000-0000-0000-0000-000000000021", estado="pronta", posicao=2)]
        page = self.client.get("/minha-conta")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f'src="{URL}"', page.text)
        self.assertIn('loading="lazy"', page.text)
        self.assertIn('width="128" height="128"', page.text)
        self.assertIn('class="account-thumbnail"', page.text)
        self.assertIn('class="account-animal-content"', page.text)
        self.assertIn('class="account-thumbnail-placeholder" aria-hidden="true"', page.text)
        self.assertIn("Sem fotografia", page.text)
        self.assertEqual(len([r for r in self.calls if r.url.path.startswith("/rest/")]), 1)
        self.assertEqual(self.storage_calls(), [])
        self.assertNotIn(self.owner, page.text)
        self.fotos = []
        page = self.client.get("/minha-conta")
        self.assertNotIn(f'src="{URL}"', page.text)
        self.assertIn('class="account-thumbnail-placeholder">Sem fotografia', page.text)
        self.assertIn("Sem fotografia", page.text)

    @unittest.skipUnless(Path("C:/Program Files/Google/Chrome/Application/chrome.exe").exists(),
                         "Chrome local necessário para verificar o layout")
    def test_responsive_layout_and_image_error_fallback_in_browser(self):
        page = self.client.get("/minha-conta").text
        # Documento autossuficiente: nenhum servidor ou serviço externo.
        page = re.sub(r"<link[^>]*>", "", page)
        page = re.sub(r"<script[^>]*>.*?</script>", "", page, flags=re.S)
        page = page.replace(URL, "data:image/webp;base64," +
                            base64.b64encode(self.image.getvalue()).decode())
        page = page.replace("</head>", "<style>" + Path("app/static/css/style.css").read_text(encoding="utf8") + "</style></head>")
        page = page.replace("</body>", "<script>" + Path("app/static/js/main.js").read_text(encoding="utf8") + "</script></body>")
        probe = """
        const results = [];
        for (const width of [320, 375, 1280]) {
            const frame = document.createElement('iframe');
            frame.style.width = width + 'px';
            frame.style.height = '900px';
            frame.srcdoc = PAGE;
            document.body.append(frame);
            frame.onload = () => {
                const doc = frame.contentDocument;
                const image = doc.querySelector('.account-thumbnail img');
                const thumb = image.parentElement.getBoundingClientRect();
                const content = doc.querySelector('.account-animal-content').getBoundingClientRect();
                const card = doc.querySelector('.account-page').getBoundingClientRect();
                const button = doc.querySelector('.account-animal-actions button[type="submit"]').getBoundingClientRect();
                results.push({width, square: thumb.width === thumb.height,
                    left: thumb.right <= content.left,
                    cover: frame.contentWindow.getComputedStyle(image).objectFit === 'cover',
                    size: thumb.width, overflow: doc.documentElement.scrollWidth > width,
                    cardWidth: card.width,
                    centered: Math.abs(card.left - (doc.documentElement.clientWidth - card.right)) < 1,
                    compactButton: button.width < card.width,
                    distinctSurface: frame.contentWindow.getComputedStyle(doc.querySelector('.account-animals li')).backgroundColor !==
                        frame.contentWindow.getComputedStyle(doc.querySelector('.account-page')).backgroundColor});
                image.dispatchEvent(new Event('error'));
                results[results.length - 1].fallback = image.hidden &&
                    frame.contentWindow.getComputedStyle(image).display === 'none' &&
                    !doc.querySelector('.account-thumbnail-placeholder').hasAttribute('aria-hidden');
                document.querySelector('#result').textContent = JSON.stringify(results);
            };
        }
        """.replace("PAGE", json.dumps(page).replace("<", "\\u003c"))
        with tempfile.TemporaryDirectory(prefix="adotae-miniaturas-") as temp:
            arquivo = Path(temp) / "layout.html"
            arquivo.write_text('<!doctype html><pre id="result"></pre><script>' + probe + '</script>', encoding="utf8")
            result = subprocess.run([
                "C:/Program Files/Google/Chrome/Application/chrome.exe", "--headless",
                "--disable-gpu", "--disable-background-networking", "--disable-extensions",
                "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
                "--host-resolver-rules=MAP * ~NOTFOUND", "--virtual-time-budget=2000",
                "--user-data-dir=" + str(Path(temp) / "profile"), "--dump-dom", arquivo.as_uri(),
            ], capture_output=True, text=True, encoding="utf8", errors="replace", timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
            if output is None:
                raise AssertionError(result.stdout)
            rows = json.loads(output.group(1))
            self.assertEqual(len(rows), 3)
            for row in rows:
                with self.subTest(width=row["width"]):
                    self.assertTrue(row["square"])
                    self.assertTrue(row["left"])
                    self.assertTrue(row["cover"])
                    self.assertTrue(row["fallback"])
                    self.assertFalse(row["overflow"])
                    self.assertEqual(row["size"], 80 if row["width"] < 640 else 128)
                    self.assertTrue(row["centered"])
                    self.assertTrue(row["compactButton"])
                    self.assertTrue(row["distinctSurface"])
                    if row["width"] == 1280:
                        self.assertEqual(row["cardWidth"], 1040)


if __name__ == "__main__":
    unittest.main()
