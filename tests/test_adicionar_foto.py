import json
import re
import unittest
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from unittest.mock import patch

import httpx
from PIL import Image
from supabase import create_client

from run import app
from tests.test_login import jwt, user


ANIMAL_ID = "00000000-0000-0000-0000-000000000010"
FOTO_ID = "00000000-0000-0000-0000-000000000020"
URL = f"/anuncios/{ANIMAL_ID}/foto"
SUCESSO = "Fotografia adicionada ao rascunho!"


class AdicionarFotoTests(unittest.TestCase):
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
        self.animal = dict(id=ANIMAL_ID, usuario_id=self.identity["id"], status_publicacao="rascunho",
                        status_adocao="disponivel", oculto_admin=False,
                           nome="Fubá", especie="Gato", distrito="Grussaí")
        self.animais = [self.animal]
        self.reservas = []
        self.reserva = [dict(id=FOTO_ID, animal_id=ANIMAL_ID, posicao=1, estado="pendente")]
        self.confirmada = [dict(self.reserva[0], estado="pronta")]
        self.failure = None
        self.failure_stage = None
        self.auth_failure = None

        def transport(request):
            self.calls.append(request)
            path = request.url.path
            if path.endswith("/user"):
                return httpx.Response(self.auth_failure or 200, json=self.identity)
            if path.endswith("/token"):
                return httpx.Response(200, json=dict(access_token=self.access, refresh_token="private-refresh",
                    token_type="bearer", expires_in=3600, user=self.identity))
            if path == "/rest/v1/animais":
                stage = "animal"
                result = [self.animal] if "order" in request.url.params else self.animais
            elif path == "/rest/v1/animal_fotos":
                stage = "reserva" if request.method == "POST" else (
                    "confirmada" if "id" in request.url.params else "reservas")
                result = {"reserva": self.reserva, "reservas": self.reservas,
                          "confirmada": self.confirmada}[stage]
            elif path.startswith("/storage/v1/object/"):
                stage, result = "upload", {"Key": path.split("/object/")[1]}
            elif path == "/rest/v1/rpc/confirmar_foto":
                stage, result = "rpc", None
            else:
                raise AssertionError(f"Requisição inesperada: {request.method} {path}")
            if stage == self.failure_stage:
                if self.failure == "network":
                    raise httpx.ReadTimeout("private " + self.access)
                if self.failure == "invalid-json":
                    return httpx.Response(200, text="private " + self.access)
                return httpx.Response(self.failure, json=dict(code="23505", statusCode=str(self.failure),
                    error="private", message="private " + self.access))
            if stage == "rpc":
                return httpx.Response(204)
            return httpx.Response(201 if stage == "reserva" else 200, content=json.dumps(result),
                                  headers={"Content-Type": "application/json"})

        def factory(url, key, options):
            self.assertFalse(options.persist_session)
            self.assertFalse(options.auto_refresh_token)
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

    def photo(self, formato="PNG"):
        arquivo = BytesIO()
        Image.new("RGB", (24, 16), "red").save(arquivo, format=formato)
        arquivo.seek(0)
        return arquivo, "foto." + formato.lower()

    def post(self, **changes):
        return self.client.post(URL, data=dict(dict(csrf_token=self.csrf(), foto=self.photo()), **changes))

    def writes(self):
        return [r for r in self.calls if r.method != "GET" and not r.url.path.startswith("/auth/")]

    def assert_blocked(self, page, status):
        self.assertEqual(page.status_code, status)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertNotIn('type="file"', page.text)
        self.assertNotIn('type="submit"', page.text)
        for secret in (self.access, "private", "sb_publishable_test", SUCESSO):
            self.assertNotIn(secret, page.text)

    def test_get_own_draft_only_photo_form_and_filtered_reads(self):
        page = self.client.get(URL)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertIn(f'action="{URL}"', page.text)
        self.assertIn('type="file"', page.text)
        self.assertIn('enctype="multipart/form-data"', page.text)
        self.assertNotIn('name="nome"', page.text)
        animal, fotos = self.calls[-2:]
        self.assertEqual(animal.url.params["id"], "eq." + ANIMAL_ID)
        self.assertEqual(animal.url.params["usuario_id"], "eq." + self.identity["id"])
        self.assertEqual(animal.url.params["status_publicacao"], "eq.rascunho")
        self.assertEqual(fotos.url.params["animal_id"], "eq." + ANIMAL_ID)
        self.assertNotIn("estado", fotos.url.params)
        self.assertEqual(self.writes(), [])

    def test_account_links_only_drafts_without_photo_queries(self):
        self.assertIn(f'href="{URL}"', self.client.get("/minha-conta").text)
        self.animal["status_publicacao"] = "publicado"
        self.assertNotIn(f'href="{URL}"', self.client.get("/minha-conta").text)
        self.assertFalse(any(r.url.path.endswith("animal_fotos") for r in self.calls))

    def test_anonymous_and_unconfirmed_blocked(self):
        visitor = app.test_client()
        token = self.csrf(visitor)
        for page in (visitor.get(URL), visitor.post(URL, data={"csrf_token": token})):
            self.assertEqual(page.location, "/login")
            self.assertEqual(page.headers["Cache-Control"], "no-store")
        token = self.csrf()
        self.calls.clear()
        self.identity["email_confirmed_at"] = None
        self.assertEqual(self.client.get(URL).location, "/login")
        self.assertEqual(self.client.post(URL, data={"csrf_token": token}).location, "/login")
        self.assertFalse(any("/rest/" in r.url.path for r in self.calls))

    def test_missing_foreign_published_and_wrong_id_are_private(self):
        token = self.csrf()
        for result in ([], [dict(self.animal, usuario_id="other")],
                       [dict(self.animal, status_publicacao="publicado")],
                       [dict(self.animal, id=FOTO_ID)]):
            self.animais = result
            self.calls.clear()
            for page in (self.client.get(URL), self.client.post(URL, data={"csrf_token": token})):
                self.assert_blocked(page, 404)
                self.assertNotIn("Fubá", page.text)
            self.assertFalse(any(r.url.path.endswith("animal_fotos") for r in self.calls))
        self.assertEqual(self.writes(), [])

    def test_upload_contract_and_confirmed_success_without_new_animal(self):
        for formato in ("JPEG", "PNG", "WEBP"):
            token = self.csrf()
            self.calls.clear()
            page = self.client.post(URL, data=dict(csrf_token=token, foto=self.photo(formato),
                usuario_id="forged", animal_id=FOTO_ID, posicao="3", estado="pronta", nome="forged"))
            self.assertEqual(page.status_code, 302)
            self.assertEqual(page.location, "/minha-conta")
            self.assertEqual(page.headers["Cache-Control"], "no-store")
            reserva, upload, rpc = self.writes()
            self.assertEqual(json.loads(reserva.content), {"animal_id": ANIMAL_ID, "posicao": 1})
            self.assertEqual(upload.url.path,
                f'/storage/v1/object/animal-fotos/{self.identity["id"]}/{ANIMAL_ID}/{FOTO_ID}.webp')
            self.assertEqual(upload.headers["x-upsert"], "false")
            envelope = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + upload.headers["content-type"].encode() + b"\r\n\r\n" + upload.content)
            arquivo = next(p for p in envelope.iter_parts()
                           if p.get_param("name", header="content-disposition") == "file")
            self.assertEqual(arquivo.get_content_type(), "image/webp")
            conteudo = arquivo.get_payload(decode=True)
            self.assertLessEqual(len(conteudo), 512_000)
            with Image.open(BytesIO(conteudo)) as image:
                self.assertEqual(image.format, "WEBP")
            self.assertEqual(json.loads(rpc.content), {"p_foto_id": FOTO_ID})
            self.assertEqual(self.calls[-1].url.params["id"], "eq." + FOTO_ID)
            for req in self.calls:
                if not req.url.path.startswith("/auth/"):
                    self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
                    self.assertEqual(req.headers["apikey"], "sb_publishable_test")
            self.assertIn(SUCESSO, self.client.get(page.location).text)
            self.assertNotIn(SUCESSO, self.client.get(page.location).text)

    def test_all_reserved_states_block_get_and_post_even_other_positions(self):
        token = self.csrf()
        for estado, message in (("pendente", "não concluída"), ("pronta", "já existe"),
                                ("removendo", "ocupada")):
            for posicao in (1, 2, 3):
                self.reservas = [dict(self.reserva[0], estado=estado, posicao=posicao)]
                self.assert_blocked(self.client.get(URL), 200)
                page = self.client.post(URL, data=dict(csrf_token=token, foto=self.photo()))
                self.assert_blocked(page, 409)
                self.assertIn(message, page.text.lower())
        self.assertEqual(self.writes(), [])

    def test_invalid_animal_and_reservation_responses_fail_closed(self):
        token = self.csrf()
        for target, invalid in (("animais", [None, {}, "unexpected", [{}], [None], self.animais * 2]),
                ("reservas", [None, {}, "unexpected", [{}], [None], [self.reserva[0]] * 2,
                    [dict(self.reserva[0], id="invalid")], [dict(self.reserva[0], animal_id=FOTO_ID)],
                    [dict(self.reserva[0], posicao=True)], [dict(self.reserva[0], posicao=4)],
                    [dict(self.reserva[0], estado="unexpected")]])):
            for result in invalid:
                with self.subTest(target=target, result=result):
                    setattr(self, target, result)
                    self.assert_blocked(self.client.get(URL), 503)
                    self.assert_blocked(self.client.post(URL, data={"csrf_token": token}), 503)
            self.animais = [self.animal]
            self.reservas = []
        self.assertEqual(self.writes(), [])

    def test_read_failures_block_form_and_writes(self):
        token = self.csrf()
        for stage in ("animal", "reservas"):
            self.failure_stage = stage
            for failure in (401, 403, 429, 500, "network", "invalid-json"):
                self.failure = failure
                self.assert_blocked(self.client.get(URL), 503)
                self.assert_blocked(self.client.post(URL, data={"csrf_token": token}), 503)
        self.assertEqual(self.writes(), [])

    def test_absent_invalid_oversize_multiple_or_animated_images_do_not_reserve(self):
        token = self.csrf()
        animated = BytesIO()
        Image.new("RGB", (24, 16), "red").save(animated, format="PNG", save_all=True,
            append_images=[Image.new("RGB", (24, 16), "blue")])
        for data in ({}, {"foto": (BytesIO(b""), "foto.png")},
                     {"foto": (BytesIO(b"bad"), "foto.png")},
                     {"foto": (BytesIO(b"x" * (3 * 1024 * 1024 + 1)), "foto.png")},
                     {"foto": [self.photo(), self.photo()]},
                     {"foto": (BytesIO(animated.getvalue()), "foto.png")}):
            page = self.client.post(URL, data=dict(data, csrf_token=token))
            page.request.environ["wsgi.input"].close()
            self.assertEqual(page.status_code, 400)
            self.assertIn('id="foto-erro"', page.text)
            self.assertNotIn('name="nome"', page.text)
        self.assertEqual(self.writes(), [])

    def test_csrf_and_http_limit_render_photo_page_without_upload_offer(self):
        token = self.csrf()
        foreign = self.csrf(app.test_client())
        for invalid in ("", "invalid", foreign):
            page = self.client.post(URL, data=dict(csrf_token=invalid, foto=self.photo()))
            self.assert_blocked(page, 400)
            self.assertIn("Recarregue", page.text)
            self.assertIn("Fotografia do rascunho", page.text)
            self.assertNotIn('name="nome"', page.text)
        with patch.dict(app.config, WTF_CSRF_TIME_LIMIT=-1):
            self.assert_blocked(self.client.post(URL, data={"csrf_token": token}), 400)
        page = self.client.post(URL, data=dict(csrf_token=token,
            foto=(BytesIO(b"x" * (4 * 1024 * 1024)), "foto.png")))
        page.request.environ["wsgi.input"].close()
        self.assert_blocked(page, 413)
        self.assertIn("excedeu o limite", page.text)
        self.assertIn("Fotografia do rascunho", page.text)
        self.assertNotIn('name="nome"', page.text)
        self.assertEqual(self.writes(), [])

    def test_partial_failures_do_not_retry_delete_or_claim_success(self):
        token = self.csrf()
        for stage, count in (("reserva", 1), ("upload", 2), ("rpc", 3), ("confirmada", 3)):
            for failure in (409, 500, "network", "invalid-json"):
                if stage == "rpc" and failure == "invalid-json":
                    continue  # RPC void: o SDK aceita corpo textual; a leitura final é obrigatória.
                with self.subTest(stage=stage, failure=failure):
                    self.failure_stage, self.failure = stage, failure
                    self.calls.clear()
                    page = self.client.post(URL, data=dict(csrf_token=token, foto=self.photo()))
                    self.assert_blocked(page, 503)
                    self.assertIn("operação incompleta", page.text)
                    self.assertEqual(len(self.writes()), count)
                    self.assertTrue(all(r.method == "POST" for r in self.writes()))
                    self.assertFalse(any(r.url.path == "/rest/v1/animais" for r in self.writes()))

    def test_rpc_body_is_not_proof_of_ready_photo(self):
        self.failure_stage, self.failure = "rpc", "invalid-json"
        self.confirmada = [dict(self.reserva[0], estado="pendente")]
        self.assert_blocked(self.post(), 503)
        self.assertEqual(self.calls[-1].url.params["id"], "eq." + FOTO_ID)
        self.assertEqual(len(self.writes()), 3)

    def test_invalid_reservation_or_confirmation_never_reports_success(self):
        token = self.csrf()
        for target in ("reserva", "confirmada"):
            original = getattr(self, target)
            for result in (None, {}, "unexpected", [], [{}], [None], original * 2,
                           [dict(original[0], id="invalid")], [dict(original[0], animal_id=FOTO_ID)],
                           [dict(original[0], posicao=2)], [dict(original[0], estado="removendo")]):
                setattr(self, target, result)
                self.calls.clear()
                self.assert_blocked(self.client.post(URL, data=dict(csrf_token=token, foto=self.photo())), 503)
                self.assertEqual(len(self.writes()), 1 if target == "reserva" else 3)
            setattr(self, target, original)
        for estado in ("pendente", "removendo"):
            self.confirmada = [dict(self.reserva[0], estado=estado)]
            self.assert_blocked(self.post(), 503)

    def test_non_integer_position_in_write_responses_is_not_confirmation(self):
        token = self.csrf()
        for target in ("reserva", "confirmada"):
            original = getattr(self, target)
            for posicao in (True, 1.0, "1"):
                with self.subTest(target=target, posicao=posicao):
                    setattr(self, target, [dict(original[0], posicao=posicao)])
                    self.calls.clear()
                    self.assert_blocked(self.client.post(URL, data=dict(csrf_token=token, foto=self.photo())), 503)
                    self.assertEqual(len(self.writes()), 1 if target == "reserva" else 3)
            setattr(self, target, original)

    def test_invalid_or_unavailable_auth_never_reads_private_draft(self):
        token = self.csrf()
        for status in (401, 503):
            self.client.set_cookie("adotae_access", self.access)
            self.client.set_cookie("adotae_refresh", "private-refresh")
            self.auth_failure = status
            self.calls.clear()
            for page in (self.client.get(URL), self.client.post(URL, data={"csrf_token": token})):
                if status == 401:
                    self.assertEqual(page.location, "/login")
                else:
                    self.assert_blocked(page, 503)
            self.assertFalse(any("/rest/" in r.url.path for r in self.calls))

    def test_second_user_upload_uses_request_identity(self):
        other = app.test_client()
        other_access = jwt("bia")
        other.set_cookie("adotae_access", other_access)
        other.set_cookie("adotae_refresh", "private-refresh")
        self.identity["id"] = "00000000-0000-0000-0000-000000000002"
        self.animal["usuario_id"] = self.identity["id"]
        page = other.post(URL, data=dict(csrf_token=self.csrf(other), foto=self.photo()))
        self.assertEqual(page.status_code, 302)
        for req in self.writes():
            self.assertEqual(req.headers["authorization"], "Bearer " + other_access)
        upload = self.writes()[1]
        self.assertEqual(upload.url.path,
            f"/storage/v1/object/animal-fotos/00000000-0000-0000-0000-000000000002/{ANIMAL_ID}/{FOTO_ID}.webp")

    def test_concurrent_reservation_conflict_does_not_upload(self):
        token = self.csrf()
        self.failure_stage, self.failure = "reserva", 409
        page = self.client.post(URL, data=dict(csrf_token=token, foto=self.photo()))
        self.assert_blocked(page, 503)
        self.assertEqual(len(self.writes()), 1)
        self.failure_stage = None
        self.reservas = self.reserva
        self.assert_blocked(self.client.get(URL), 200)
        self.assert_blocked(self.post(), 409)
        self.assertEqual(len(self.writes()), 1)

    def test_refreshed_token_and_missing_configuration(self):
        token = self.csrf()
        self.client.set_cookie("adotae_access", jwt(expired=True))
        self.assertEqual(self.client.post(URL, data=dict(csrf_token=token, foto=self.photo())).status_code, 302)
        for req in self.writes():
            self.assertEqual(req.headers["authorization"], "Bearer " + self.access)
        for config in ({"SECRET_KEY": None}, {"SUPABASE_URL": ""}, {"SUPABASE_PUBLISHABLE_KEY": ""}):
            self.calls.clear()
            with patch.dict(app.config, config):
                self.assert_blocked(self.client.get(URL), 503)
                self.assert_blocked(self.client.post(URL, data={"csrf_token": token}), 503)
            self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
