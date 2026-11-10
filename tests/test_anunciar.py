import copy
import json
import logging
import re
import unittest
from io import BytesIO
from email.parser import BytesParser
from email.policy import default
from unittest.mock import patch

import httpx
from PIL import Image
from supabase import create_client

from run import ANIMAIS, OPCOES_FILTROS, app
from tests.test_login import jwt, user


SUCESSO = ("Animal cadastrado como rascunho! Você poderá adicionar fotos e solicitar "
           "a publicação posteriormente.")


class AnunciarTests(unittest.TestCase):
    def setUp(self):
        original = app.config.copy()
        self.addCleanup(app.config.update, original)
        app.config.update(TESTING=True, SECRET_KEY="test-secret", SESSION_COOKIE_SECURE=False,
                          SUPABASE_URL="https://example.supabase.co",
                          SUPABASE_PUBLISHABLE_KEY="sb_publishable_test")
        self.client = app.test_client()
        self.access = jwt()
        self.refresh = "private-refresh"
        self.identity = user()
        self.calls = []
        self.creations = 0
        self.auth_failure = None
        self.api_failure = None
        self.photo_failure = None
        self.photo_result = [dict(id="00000000-0000-0000-0000-000000000020",
                                  animal_id="00000000-0000-0000-0000-000000000010",
                                  posicao=1, estado="pendente")]
        self.photo_state = "pronta"
        self.publicar = True
        self.rpc_failure = None
        self.rpc_result = None
        self.final_result = "normal"
        self.result = [dict(id="00000000-0000-0000-0000-000000000010",
                            usuario_id=self.identity["id"], status_publicacao="rascunho")]

        def transport(request):
            self.calls.append(request)
            path = request.url.path
            if path == "/rest/v1/rpc/publicar_anuncio":
                if self.publicar:
                    self.result[0]["status_publicacao"] = "publicado"
                if self.rpc_failure == "network":
                    raise httpx.ReadError("private " + self.access)
                if self.rpc_failure == "invalid-json":
                    return httpx.Response(200, text="private")
                if self.rpc_failure:
                    return httpx.Response(500, json=dict(code=self.rpc_failure, message="private " + self.access,
                                                        details=None, hint=None))
                return httpx.Response(200, json=self.rpc_result)
            if path in ("/rest/v1/animal_fotos", "/rest/v1/rpc/confirmar_foto") or path.startswith("/storage/v1/"):
                stage = ("reserva" if path.endswith("animal_fotos") and request.method == "POST"
                         else "leitura" if request.method == "GET"
                         else "confirmacao" if "/rpc/" in path else "upload")
                if self.photo_failure == stage + "-network":
                    raise httpx.ConnectError("private " + self.access)
                if self.photo_failure == stage:
                    return httpx.Response(500, json=dict(
                        code="42501", statusCode="500", error="private", message="private " + self.access))
                if stage == "reserva":
                    return httpx.Response(201, json=self.photo_result)
                if stage == "upload":
                    return httpx.Response(200, json={"Key": path.split("/object/")[1]})
                if stage == "confirmacao":
                    return httpx.Response(204)
                return httpx.Response(200, json=[dict(self.photo_result[0], estado=self.photo_state)])
            if request.url.path == "/rest/v1/animais":
                if request.method == "GET":
                    result = []
                    if "order" not in request.url.params:
                        result = self.result if self.final_result == "normal" else self.final_result
                        if result == "network":
                            raise httpx.ReadError("private " + self.access)
                    return httpx.Response(200, content=json.dumps(result),
                                          headers={"Content-Type": "application/json"})
                if self.api_failure == "network":
                    raise httpx.ConnectError("private network details " + self.access)
                if self.api_failure == "invalid-json":
                    return httpx.Response(201, text="private " + self.access)
                if self.api_failure:
                    return httpx.Response(self.api_failure, json=dict(
                        code="42501", message="private provider details " + self.access,
                        details=self.refresh, hint="sb_publishable_test"))
                return httpx.Response(201, json=self.result)
            if self.auth_failure:
                return httpx.Response(self.auth_failure, json={"msg": "private auth details"})
            if request.url.path.endswith("/user"):
                return httpx.Response(200, json=self.identity)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json=dict(
                    access_token=self.access, refresh_token=self.refresh,
                    token_type="bearer", expires_in=3600, user=self.identity))
            raise AssertionError(f"Requisição inesperada: {request.method} {request.url.path}")

        def factory(url, key, options):
            self.creations += 1
            self.assertEqual(url, "https://example.supabase.co")
            self.assertEqual(key, "sb_publishable_test")
            self.assertFalse(options.persist_session)
            self.assertFalse(options.auto_refresh_token)
            options.httpx_client = httpx.Client(transport=httpx.MockTransport(transport))
            self.addCleanup(options.httpx_client.close)
            return create_client(url, key, options=options)

        patcher = patch("app.auth.create_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cookies()

    def cookies(self, access=None, client=None):
        client = client or self.client
        client.set_cookie("adotae_access", access or self.access)
        client.set_cookie("adotae_refresh", self.refresh)

    def csrf(self, client=None):
        response = (client or self.client).get("/anunciar")
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text).group(1)

    def data(self, **changes):
        data = dict(nome=" Fubá ", especie="Cachorro", sexo="Macho",
                    idade_aproximada=" 2 anos ", porte="Médio", descricao=" Amigável. ",
                    distrito="Grussaí", csrf_token=self.csrf())
        data.update(changes)
        return data

    def inserts(self):
        return [r for r in self.calls if r.url.path == "/rest/v1/animais" and r.method == "POST"]

    def photo_requests(self):
        return [r for r in self.calls if "animal_fotos" in r.url.path
                or "/storage/" in r.url.path or "/rpc/" in r.url.path]

    def photo(self, formato="PNG"):
        arquivo = BytesIO()
        Image.new("RGB", (24, 16), "red").save(arquivo, format=formato)
        arquivo.seek(0)
        return arquivo, "foto." + formato.lower()

    def publicar_data(self, **changes):
        return self.data(**dict(dict(acao="publicar", email_contato="ana@example.com",
                                    foto=self.photo()), **changes))

    def publicacoes(self):
        return [r for r in self.calls if r.url.path == "/rest/v1/rpc/publicar_anuncio"]

    def conferir_publicacao(self, response):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        page = self.client.get(response.location)
        for secret in (self.access, self.refresh, "private", "sb_publishable_test"):
            self.assertNotIn(secret, page.text)
        self.assertFalse(any(r.method in ("PATCH", "DELETE") for r in self.calls))
        return page.text

    def test_creation_buttons_and_instructions(self):
        page = self.client.get("/anunciar").text
        self.assertIn('name="acao" value="rascunho">Salvar como rascunho</button>', page)
        self.assertIn('name="acao" value="publicar">Publicar agora</button>', page)
        self.assertIn("pelo menos um contato válido", page)
        self.assertIn("uma fotografia válida", page)

    def test_explicit_draft_without_contacts_or_photo(self):
        response = self.client.post("/anunciar", data=self.data(acao="rascunho"))
        self.assertIn(SUCESSO, self.conferir_publicacao(response))
        self.assertEqual(len(self.inserts()), 1)
        self.assertEqual(self.photo_requests(), [])

    def test_unknown_or_ambiguous_action_never_writes(self):
        for action in ("", "excluir", "PUBLICAR", ["publicar", "rascunho"]):
            with self.subTest(action=action):
                response = self.client.post("/anunciar", data=self.publicar_data(acao=action))
                self.assertEqual(response.status_code, 400)
                self.assertIn('role="alert"', response.text)
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_publish_validation_never_inserts(self):
        changes = [dict(email_contato="", whatsapp=""), dict(email_contato="inválido"),
                   dict(whatsapp="+5522999999999"), dict(nome=""), dict(foto=None),
                   dict(foto=(BytesIO(b""), "foto.png")),
                   dict(foto=(BytesIO(b"invalid"), "foto.png")),
                   dict(foto=[self.photo(), self.photo()])]
        for change in changes:
            with self.subTest(change=change):
                response = self.client.post("/anunciar", data=self.publicar_data(**change))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn('role="alert"', response.text)
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_direct_publication_order_payload_and_owner_confirmation(self):
        response = self.client.post("/anunciar", data=self.publicar_data(
            usuario_id="forged", status_publicacao="publicado", p_animal_id="forged"))
        operations = [r for r in self.calls if not r.url.path.startswith("/auth/")]
        self.assertEqual([(r.method, r.url.path) for r in operations], [
            ("POST", "/rest/v1/animais"), ("POST", "/rest/v1/animal_fotos"),
            ("POST", "/storage/v1/object/animal-fotos/" + self.identity["id"] +
             "/00000000-0000-0000-0000-000000000010/00000000-0000-0000-0000-000000000020.webp"),
            ("POST", "/rest/v1/rpc/confirmar_foto"), ("GET", "/rest/v1/animal_fotos"),
            ("POST", "/rest/v1/rpc/publicar_anuncio"), ("GET", "/rest/v1/animais")])
        self.assertNotIn("status_publicacao", json.loads(self.inserts()[0].content))
        self.assertNotIn("usuario_id", json.loads(self.inserts()[0].content))
        self.assertEqual(json.loads(self.publicacoes()[0].content),
                         {"p_animal_id": "00000000-0000-0000-0000-000000000010"})
        query = operations[-1]
        self.assertEqual(query.url.params["usuario_id"], "eq." + self.identity["id"])
        self.assertEqual(query.url.params["id"], "eq.00000000-0000-0000-0000-000000000010")
        for req in operations:
            self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
        self.assertIn("Anúncio publicado com sucesso!", self.conferir_publicacao(response))
        self.assertNotIn("Anúncio publicado com sucesso!", self.client.get("/minha-conta").text)

    def test_direct_publication_accepts_whatsapp_without_email(self):
        response = self.client.post("/anunciar", data=self.publicar_data(
            email_contato="", whatsapp="5522999999999"))
        self.assertIn("Anúncio publicado com sucesso!", self.conferir_publicacao(response))

    def test_direct_insert_failure_or_uncertainty_stops_flow(self):
        for failure in (400, 403, 500, "network", "invalid-json"):
            with self.subTest(failure=failure):
                self.calls.clear()
                self.api_failure = failure
                response = self.client.post("/anunciar", data=self.publicar_data())
                self.assertEqual(response.status_code, 503)
                self.assertIn("Meus anúncios", response.text)
                self.assertNotIn("rascunho foi criado", response.text)
                self.assertNotIn("Tente novamente", response.text)
                self.assertEqual(len(self.inserts()), 1)
                self.assertEqual(self.photo_requests(), [])

    def test_direct_invalid_insert_never_uploads_or_publishes(self):
        original = self.result[0].copy()
        for result in ([], None, {}, [None], [{}], [dict(original, id="invalid")],
                       [dict(original, usuario_id="other")],
                       [dict(original, status_publicacao="publicado")], [original, original]):
            with self.subTest(result=result):
                self.calls.clear()
                self.result = result
                response = self.client.post("/anunciar", data=self.publicar_data())
                self.assertEqual(response.status_code, 503)
                self.assertIn("Meus anúncios", response.text)
                self.assertEqual(len(self.inserts()), 1)
                self.assertEqual(self.photo_requests(), [])

    def test_direct_photo_failure_preserves_draft_and_never_publishes(self):
        for stage, count in (("reserva", 1), ("upload", 2), ("confirmacao", 3), ("leitura", 4)):
            for suffix in ("", "-network"):
                with self.subTest(stage=stage, suffix=suffix):
                    self.calls.clear()
                    self.photo_failure = stage + suffix
                    response = self.client.post("/anunciar", data=self.publicar_data())
                    self.assertEqual(len(self.inserts()), 1)
                    self.assertEqual(len(self.photo_requests()), count)
                    self.assertEqual(self.publicacoes(), [])
                    page = self.conferir_publicacao(response)
                    self.assertIn("rascunho foi criado", page)
                    self.assertIn("Meus anúncios", page)
                    self.assertNotIn("Anúncio publicado com sucesso!", page)
                    self.assertEqual(self.result[0]["status_publicacao"], "rascunho")

    def test_direct_unready_photo_blocks_publication(self):
        for state in ("pendente", "removendo", "unexpected"):
            self.photo_state = state
            response = self.client.post("/anunciar", data=self.publicar_data())
            self.assertIn("rascunho foi criado", self.conferir_publicacao(response))
            self.assertEqual(self.publicacoes(), [])

    def test_direct_invalid_reservation_blocks_upload_and_publication(self):
        original = self.photo_result[0].copy()
        for result in ([], [{}], [dict(original, animal_id="other")],
                       [dict(original, id="invalid")], [original, original]):
            with self.subTest(result=result):
                self.calls.clear()
                self.photo_result = result
                response = self.client.post("/anunciar", data=self.publicar_data())
                self.assertIn("rascunho foi criado", self.conferir_publicacao(response))
                self.assertEqual(len(self.inserts()), 1)
                self.assertEqual(len(self.photo_requests()), 1)
                self.assertEqual(self.publicacoes(), [])

    def test_direct_lost_rpc_response_is_confirmed_without_retry(self):
        for failure in ("network", "invalid-json", "XX000"):
            with self.subTest(failure=failure):
                self.calls.clear()
                self.result[0]["status_publicacao"] = "rascunho"
                self.rpc_failure = failure
                response = self.client.post("/anunciar", data=self.publicar_data())
                self.assertIn("Anúncio publicado com sucesso!", self.conferir_publicacao(response))
                self.assertEqual(len(self.publicacoes()), 1)
                self.assertEqual(len(self.inserts()), 1)

    def test_direct_refused_or_unconfirmed_publication_preserves_photo(self):
        self.publicar = False
        for failure in (None, "42501", "55P03", "network", "invalid-json"):
            with self.subTest(failure=failure):
                self.calls.clear()
                self.rpc_failure = failure
                self.rpc_result = {"status_publicacao": "publicado"}
                response = self.client.post("/anunciar", data=self.publicar_data())
                page = self.conferir_publicacao(response)
                self.assertNotIn("Anúncio publicado com sucesso!", page)
                self.assertIn("Meus anúncios", page)
                self.assertEqual(len(self.publicacoes()), 1)
                self.assertEqual(len(self.inserts()), 1)
                self.assertEqual(len(self.photo_requests()), 5)
                if failure == "55P03":
                    self.assertIn("Aguarde", page)

    def test_direct_final_read_failure_is_uncertain(self):
        original = self.result[0].copy()
        for result in ("network", None, {}, [], [None], [original, original],
                       [dict(original, usuario_id="other")], [dict(original, id="other")],
                       [dict(original, status_publicacao="unexpected")]):
            for failure in (None, "network"):
                with self.subTest(result=result, failure=failure):
                    self.calls.clear()
                    self.result[0]["status_publicacao"] = "rascunho"
                    self.final_result = result
                    self.rpc_failure = failure
                    response = self.client.post("/anunciar", data=self.publicar_data())
                    page = self.conferir_publicacao(response)
                    self.assertIn("resultado da publicação é incerto", page)
                    self.assertNotIn("Anúncio publicado com sucesso!", page)
                    self.assertEqual(len(self.publicacoes()), 1)
                    self.assertEqual(len(self.inserts()), 1)

    def test_direct_security_guards_prevent_writes(self):
        for change in ({"csrf_token": "invalid"}, {"csrf_token": ""}):
            response = self.client.post("/anunciar", data=self.publicar_data(**change))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        data = self.publicar_data()
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.post("/anunciar", data=data).location, "/login")
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_direct_body_limit_prevents_writes(self):
        response = self.client.post("/anunciar", data=self.publicar_data(
            foto=(BytesIO(b"x" * (4 * 1024 * 1024)), "foto.png")))
        response.request.environ["wsgi.input"].close()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.inserts(), [])

    def test_direct_anonymous_unavailable_auth_and_configuration(self):
        visitor = app.test_client()
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                          visitor.get("/login").text).group(1)
        response = visitor.post("/anunciar", data=self.publicar_data(csrf_token=token))
        self.assertEqual(response.location, "/login")
        data = self.publicar_data()
        self.auth_failure = 503
        response = self.client.post("/anunciar", data=data)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.auth_failure = None
        for config in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}):
            data = self.publicar_data()
            with patch.dict(app.config, config):
                response = self.client.post("/anunciar", data=data)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_direct_refreshed_jwt_used_through_confirmation(self):
        data = self.publicar_data()
        self.cookies(access=jwt(expired=True))
        response = self.client.post("/anunciar", data=data)
        self.assertIn("Anúncio publicado com sucesso!", self.conferir_publicacao(response))
        for req in self.calls:
            if req.url.path.startswith(("/rest/", "/storage/")):
                self.assertEqual(req.headers["authorization"], "Bearer " + self.access)

    def test_photo_form_and_no_photo(self):
        page = self.client.get("/anunciar").text
        self.assertIn('enctype="multipart/form-data"', page)
        self.assertIn('type="file"', page)
        self.assertIn('accept="image/jpeg,image/png,image/webp"', page)
        self.assertNotIn('multiple', page)
        self.client.post("/anunciar", data=self.data())
        self.assertEqual(self.photo_requests(), [])

    def test_valid_photo_contract(self):
        for formato in ("JPEG", "PNG", "WEBP"):
            with self.subTest(formato=formato):
                self.calls.clear()
                response = self.client.post("/anunciar", data=self.data(foto=self.photo(formato),
                    foto_id="forged", animal_id="forged", posicao="3", estado="pronta"))
                self.assertEqual(response.status_code, 302)
                requests = self.photo_requests()
                self.assertEqual(len(requests), 4)
                reserva, upload, rpc, leitura = requests
                self.assertEqual(json.loads(reserva.content), {"animal_id": self.result[0]["id"], "posicao": 1})
                caminho = f'{self.identity["id"]}/{self.result[0]["id"]}/{self.photo_result[0]["id"]}.webp'
                self.assertEqual(upload.url.path, "/storage/v1/object/animal-fotos/" + caminho)
                envelope = BytesParser(policy=default).parsebytes(
                    b"Content-Type: " + upload.headers["content-type"].encode() + b"\r\n\r\n" + upload.content)
                arquivo = next(part for part in envelope.iter_parts()
                               if part.get_param("name", header="content-disposition") == "file")
                self.assertEqual(arquivo.get_content_type(), "image/webp")
                conteudo = arquivo.get_payload(decode=True)
                self.assertEqual(upload.headers["x-upsert"], "false")
                cache = next(part for part in envelope.iter_parts()
                             if part.get_param("name", header="content-disposition") == "cacheControl")
                self.assertEqual(cache.get_payload(decode=True), b"0")
                with Image.open(BytesIO(conteudo)) as image:
                    self.assertEqual(image.format, "WEBP")
                self.assertLessEqual(len(conteudo), 512_000)
                self.assertEqual(rpc.url.path, "/rest/v1/rpc/confirmar_foto")
                self.assertEqual(json.loads(rpc.content), {"p_foto_id": self.photo_result[0]["id"]})
                self.assertEqual(leitura.url.params["id"], "eq." + self.photo_result[0]["id"])
                for req in requests:
                    self.assertEqual(req.headers["authorization"], f"Bearer {self.access}")
                    self.assertEqual(req.headers["apikey"], "sb_publishable_test")
                self.assertIn("fotografia adicionada", self.client.get(response.location).text)

    def test_invalid_photo_before_animal(self):
        for conteudo in (b"", b"invalid", b"x" * (3 * 1024 * 1024 + 1)):
            response = self.client.post("/anunciar", data=self.data(foto=(BytesIO(conteudo), "foto.png")))
            response.request.environ["wsgi.input"].close()
            self.assertEqual(response.status_code, 400)
            self.assertIn('id="foto-erro"', response.text)
            self.assertIn('value="Fubá"', response.text)
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_multiple_photos_rejected_before_animal(self):
        response = self.client.post("/anunciar", data=self.data(foto=[self.photo(), self.photo()]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.inserts(), [])

    def test_partial_photo_failures(self):
        for stage, count in (("reserva", 1), ("upload", 2), ("confirmacao", 3), ("leitura", 4)):
            for suffix in ("", "-network"):
                with self.subTest(stage=stage, suffix=suffix):
                    self.calls.clear()
                    self.photo_failure = stage + suffix
                    response = self.client.post("/anunciar", data=self.data(foto=self.photo()))
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(len(self.inserts()), 1)
                    self.assertEqual(len(self.photo_requests()), count)
                    page = self.client.get(response.location).text
                    self.assertIn("rascunho foi criado", page)
                    self.assertIn("fotografia", page)
                    self.assertNotIn("fotografia adicionada", page)
                    self.assertNotIn(SUCESSO, page)
                    self.assertNotIn(self.access, page)
                    self.assertNotIn("private", page)
                    self.assertTrue(all(r.method in ("GET", "POST") for r in self.calls))

    def test_invalid_reservation_prevents_upload(self):
        original = self.photo_result
        for result in ([], [{}], original * 2, [dict(original[0], id="invalid")],
                       [dict(original[0], animal_id="other")], [dict(original[0], posicao=2)],
                       [dict(original[0], estado="pronta")]):
            with self.subTest(result=result):
                self.calls.clear()
                self.photo_result = result
                response = self.client.post("/anunciar", data=self.data(foto=self.photo()))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(self.photo_requests()), 1)
                self.assertNotIn("fotografia adicionada", self.client.get(response.location).text)

    def test_unready_photo_never_reports_success(self):
        for estado in ("pendente", "removendo"):
            self.photo_state = estado
            response = self.client.post("/anunciar", data=self.data(foto=self.photo()))
            page = self.client.get(response.location).text
            self.assertIn("rascunho foi criado", page)
            self.assertNotIn("fotografia adicionada", page)

    def test_photo_preserves_auth_csrf_and_animal_validation(self):
        for change in ({"csrf_token": "invalid"}, {"nome": ""}):
            response = self.client.post("/anunciar", data=self.data(foto=self.photo(), **change))
            self.assertEqual(response.status_code, 400)
        data = self.data(foto=self.photo())
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.post("/anunciar", data=data).location, "/login")
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_photo_request_body_limit(self):
        response = self.client.post("/anunciar", data=self.data(
            foto=(BytesIO(b"x" * (4 * 1024 * 1024)), "foto.png")))
        response.request.environ["wsgi.input"].close()
        self.assertEqual(response.status_code, 413)
        self.assertIn("excedeu o limite", response.text)
        self.assertEqual(self.inserts(), [])
        self.assertEqual(self.photo_requests(), [])

    def test_animal_failure_prevents_photo_reservation(self):
        self.api_failure = 500
        response = self.client.post("/anunciar", data=self.data(foto=self.photo()))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.photo_requests(), [])

    def test_photo_uses_refreshed_user_token(self):
        data = self.data(foto=self.photo())
        self.cookies(access=jwt(expired=True))
        response = self.client.post("/anunciar", data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.photo_requests()), 4)
        for req in self.photo_requests():
            self.assertEqual(req.headers["authorization"], f"Bearer {self.access}")

    def test_access_and_account_link(self):
        self.assertIn('href="/anunciar"', self.client.get("/minha-conta").text)
        page = self.client.get("/anunciar")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn("somente dígitos", page.text)
        for options in OPCOES_FILTROS.values():
            for value in options:
                self.assertIn(value, page.text)
        visitor = app.test_client()
        self.assertEqual(visitor.get("/anunciar").location, "/login")
        # Um POST com CSRF válido também exige identidade.
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                          visitor.get("/login").text).group(1)
        self.assertEqual(visitor.post("/anunciar", data={"csrf_token": token}).location, "/login")
        self.assertEqual(self.inserts(), [])

    def test_invalid_unconfirmed_and_unavailable_auth_cannot_insert(self):
        data = self.data()
        for status in (401, 503):
            with self.subTest(status=status):
                self.cookies()
                self.auth_failure = status
                for response in (self.client.get("/anunciar"),
                                 self.client.post("/anunciar", data=data)):
                    self.assertEqual(response.status_code, 302 if status == 401 else 503)
                    self.assertNotIn("private auth details", response.text)
                    if status == 401:
                        self.assertEqual(response.location, "/login")
                    else:
                        self.assertNotIn('type="submit"', response.text)
        self.auth_failure = None
        self.identity["email_confirmed_at"] = None
        self.cookies()
        self.assertEqual(self.client.post("/anunciar", data=data).location, "/login")
        self.assertEqual(self.inserts(), [])

    def test_missing_configuration_blocks_writes(self):
        data = self.data()
        for change in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""},
                       {"SUPABASE_PUBLISHABLE_KEY": ""}):
            self.cookies()
            with patch.dict(app.config, change):
                self.assertEqual(self.client.post("/anunciar", data=data).status_code, 503)
        self.assertEqual(self.inserts(), [])

    def test_csrf_and_preservation(self):
        data = self.data()
        other = app.test_client()
        self.cookies(client=other)
        foreign = self.csrf(other)
        for token in ("", "invalid", foreign):
            response = self.client.post("/anunciar", data=dict(data, csrf_token=token))
            self.assertEqual(response.status_code, 400)
            self.assertIn("Recarregue", response.text)
            self.assertNotIn('type="file"', response.text)
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            self.assertEqual(self.client.post("/anunciar", data=data).status_code, 400)
        self.assertEqual(self.inserts(), [])

    def test_required_fields_and_invalid_values(self):
        data = self.data()
        cases = [{field: value} for field in (
            "nome", "especie", "sexo", "idade_aproximada", "porte", "descricao", "distrito")
            for value in ("", " \t\n ")]
        cases += [{field: value} for field, value in (
            ("especie", "Silvestre"), ("sexo", "Outro"), ("porte", "Gigante"),
            ("distrito", "Outra cidade"), ("email_contato", "invalido"),
            ("email_contato", "a@@example.com"))]
        cases += [{"whatsapp": value} for value in (
            "012345678", "1234567", "1234567890123456", "+5522999999999",
            "22 99999999", "1234567a", "١٢٣٤٥٦٧٨", "12345678\n9")]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post("/anunciar", data=dict(data, **changes))
                self.assertEqual(response.status_code, 400)
                self.assertIn('role="alert"', response.text)
        self.assertEqual(self.inserts(), [])

    def test_text_limits(self):
        data = self.data(especie="Outro animal doméstico", especie_outro="Animal doméstico")
        for field, limit in (("nome", 100), ("idade_aproximada", 50), ("descricao", 2000),
                             ("bairro", 100), ("especie_outro", 60)):
            with self.subTest(field=field):
                response = self.client.post("/anunciar", data=dict(data, **{field: "a" * (limit + 1)}))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.inserts(), [])
                response = self.client.post("/anunciar", data=dict(data, **{field: "a" * limit}))
                self.assertEqual(response.status_code, 302)
                self.calls.clear()

    def test_other_species_consistency(self):
        data = self.data()
        for value in ("", "   "):
            response = self.client.post("/anunciar", data=dict(
                data, especie="Outro animal doméstico", especie_outro=value))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.inserts(), [])
        response = self.client.post("/anunciar", data=dict(
            data, especie="Outro animal doméstico", especie_outro=" Porco doméstico "))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(json.loads(self.inserts()[-1].content)["especie_outro"], "Porco doméstico")
        self.client.post("/anunciar", data=dict(data, especie_outro="Ignorar ao trocar a espécie"))
        self.assertIsNone(json.loads(self.inserts()[-1].content)["especie_outro"])

    def test_success_payload_prg_flash_and_unchanged_catalog(self):
        animals_before = copy.deepcopy(ANIMAIS)
        catalog_before = self.client.get("/animais").text
        data = self.data(usuario_id="attacker", status_publicacao="publicado", id="forced",
                         status_adocao="adotado", oculto_admin="true", criado_em="forced",
                         atualizado_em="forced", bairro="   ", whatsapp=" ", email_contato=" ")
        response = self.client.post("/anunciar", data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/minha-conta")
        self.assertEqual(len(self.inserts()), 1)
        sent = self.inserts()[0]
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.headers["apikey"], "sb_publishable_test")
        self.assertEqual(sent.headers["authorization"], f"Bearer {self.access}")
        self.assertIn("return=representation", sent.headers["prefer"])
        self.assertEqual(json.loads(sent.content), dict(
            nome="Fubá", especie="Cachorro", especie_outro=None, sexo="Macho",
            idade_aproximada="2 anos", porte="Médio", descricao="Amigável.",
            distrito="Grussaí", bairro=None, whatsapp=None, email_contato=None))
        self.assertIn(SUCESSO, self.client.get(response.location).text)
        self.assertNotIn(SUCESSO, self.client.get(response.location).text)
        self.assertEqual(len(self.inserts()), 1)
        self.assertEqual(ANIMAIS, animals_before)
        self.assertEqual(self.client.get("/animais").text, catalog_before)

    def test_optional_contacts_and_whatsapp_boundaries(self):
        data = self.data()
        for phone in ("12345678", "123456789012345"):
            response = self.client.post("/anunciar", data=dict(
                data, whatsapp=phone, email_contato=" ana@example.com ", bairro=" Centro "))
            self.assertEqual(response.status_code, 302)
            sent = json.loads(self.inserts()[-1].content)
            self.assertEqual(sent["whatsapp"], phone)
            self.assertEqual(sent["email_contato"], "ana@example.com")
            self.assertEqual(sent["bairro"], "Centro")

    def test_refreshed_jwt_and_isolated_users(self):
        data = self.data()
        self.cookies(access=jwt(expired=True))
        self.assertEqual(self.client.post("/anunciar", data=data).status_code, 302)
        self.assertEqual(self.inserts()[-1].headers["authorization"], f"Bearer {self.access}")
        self.assertEqual(self.client.get_cookie("adotae_access").value, self.access)
        other = app.test_client()
        other_access = jwt("bia")
        self.cookies(access=other_access, client=other)
        self.identity = dict(user(), id="00000000-0000-0000-0000-000000000002")
        self.result[0]["usuario_id"] = self.identity["id"]
        self.assertEqual(other.post("/anunciar", data=dict(data, csrf_token=self.csrf(other))).status_code, 302)
        self.assertEqual(self.inserts()[-1].headers["authorization"], f"Bearer {other_access}")
        self.identity = user()
        self.result[0]["usuario_id"] = self.identity["id"]
        self.assertEqual(self.client.post("/anunciar", data=data).status_code, 302)
        self.assertEqual(self.inserts()[-1].headers["authorization"], f"Bearer {self.access}")
        self.assertEqual(self.creations, 5)

    def test_api_failures_preserve_data_without_credential_leaks(self):
        data = self.data()
        messages = []
        class Capture(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())
        handler = Capture()
        logger = logging.getLogger()
        level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            for failure in (400, 401, 403, 409, 500, "network"):
                with self.subTest(failure=failure):
                    self.api_failure = failure
                    response = self.client.post("/anunciar", data=data)
                    self.assertGreaterEqual(response.status_code, 400)
                    self.assertIn('value="Fubá"', response.text)
                    self.assertNotIn(SUCESSO, response.text)
                    for secret in (self.access, self.refresh, "sb_publishable_test", "private"):
                        self.assertNotIn(secret, response.text)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        for secret in (self.access, self.refresh, "sb_publishable_test", "private"):
            self.assertNotIn(secret, "\n".join(messages))

    def test_invalid_insert_responses_do_not_report_success(self):
        data = self.data()
        for result in ([], [{}], [dict(self.result[0], status_publicacao="publicado")],
                       [dict(self.result[0], usuario_id="another-user")],
                       [dict(self.result[0], id="invalid")], self.result * 2):
            with self.subTest(result=result):
                self.result = result
                response = self.client.post("/anunciar", data=data)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertNotIn(SUCESSO, self.client.get("/minha-conta").text)


if __name__ == "__main__":
    unittest.main()
