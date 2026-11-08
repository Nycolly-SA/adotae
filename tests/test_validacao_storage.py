"""Testes offline: nenhum cliente usa transporte de rede real."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from storage3.exceptions import StorageApiError
from supabase import ClientOptions, create_client

SCRIPTS = Path(__file__).resolve().parents[1] / "supabase/tests"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("validar_storage", SCRIPTS / "validar_storage.py")
    roteiro = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roteiro)
finally:
    sys.path.remove(str(SCRIPTS))


class StorageLocalTests(unittest.TestCase):
    def test_destino_nao_confirmado_nao_cria_cliente_ou_manifesto(self):
        with (
            patch.object(roteiro.sys.stdin, "isatty", return_value=True),
            patch.object(roteiro, "configuracao", return_value=("url", "chave", "ref")),
            patch("builtins.input", return_value="outro-projeto ref"),
            patch.object(roteiro, "novo_cliente") as cliente,
            patch.object(roteiro, "salvar") as salvar,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(roteiro.main(), 1)
        cliente.assert_not_called()
        salvar.assert_not_called()

    def test_senha_oculta_e_identidade_confirmada_no_auth(self):
        cliente = Mock()
        usuario = Mock(id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", email_confirmed_at="sim")
        cliente.auth.sign_in_with_password.return_value = Mock(
            user=usuario, session=Mock(access_token="JWT-SENSIVEL"))
        cliente.auth.get_user.return_value = Mock(user=usuario)
        with patch("builtins.input", return_value="a@example.invalid"), patch.object(
            roteiro, "getpass", return_value="SENHA-SENSIVEL"
        ), contextlib.redirect_stdout(io.StringIO()) as saida:
            self.assertEqual(roteiro.entrar(cliente, "A"), "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertNotIn("SENSIVEL", saida.getvalue())
        cliente.auth.get_user.assert_called_once_with("JWT-SENSIVEL")

    def test_erro_de_rede_jwt_limite_ou_bucket_ausente_nao_prova_bloqueio(self):
        for erro in (
            httpx.ConnectError("SEGREDO"), StorageApiError("SEGREDO", "InvalidJWT", 401),
            StorageApiError("SEGREDO", "TooManyRequests", 429),
            StorageApiError("SEGREDO", "NoSuchBucket", 404),
            StorageApiError("SEGREDO", "InternalError", 500),
            StorageApiError("SEGREDO", "InvalidMimeType", 400),
        ):
            with self.subTest(erro=type(erro).__name__), self.assertRaises(Exception):
                roteiro.bloqueio_storage(Mock(side_effect=erro), oculto=True)

    def test_storage_so_aceita_negacao_conhecida_e_404_na_leitura(self):
        roteiro.bloqueio_storage(Mock(side_effect=StorageApiError("SEGREDO", "AccessDenied", 403)))
        roteiro.bloqueio_storage(Mock(side_effect=StorageApiError("SEGREDO", "NoSuchKey", 404)), oculto=True)
        with self.assertRaises(roteiro.Falha):
            roteiro.bloqueio_storage(lambda: {"signedURL": "SEGREDO"})
        with self.assertRaises(roteiro.Falha):
            roteiro.bloqueio_storage(Mock(side_effect=StorageApiError("SEGREDO", "NoSuchKey", 404)))

    def test_bucket_nao_confirmado_nao_inicia_conexao(self):
        with (
            patch.object(roteiro.sys.stdin, "isatty", return_value=True),
            patch.object(roteiro, "configuracao", return_value=("url", "chave", "ref")),
            patch("builtins.input", side_effect=["adotae-testes ref", "não"]),
            patch.object(roteiro, "novo_cliente") as cliente,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(roteiro.main(), 1)
        cliente.assert_not_called()

    def test_terminal_sem_ocultacao_de_senha_interrompe_antes_do_auth(self):
        cliente = Mock()
        with (
            patch("builtins.input", return_value="a@example.invalid"),
            patch.object(roteiro, "getpass", side_effect=roteiro.GetPassWarning("sem terminal")),
            self.assertRaises(roteiro.GetPassWarning),
        ):
            roteiro.entrar(cliente, "A")
        cliente.auth.sign_in_with_password.assert_not_called()

    def test_exclusao_silenciosa_nao_e_confundida_com_exclusao_real(self):
        roteiro.remocao_negada(Mock(return_value=[]))
        with self.assertRaises(roteiro.Falha):
            roteiro.remocao_negada(lambda: [{"name": "objeto"}])

    def test_manifesto_persiste_identificadores_sem_sessoes(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "execucao.json"
            manifesto = {"nome": "TESTE-STORAGE-unico", "caminhos": ["A/animal/foto.webp"]}
            roteiro.salvar(caminho, manifesto)
            self.assertEqual(json.loads(caminho.read_text(encoding="utf-8")), manifesto)

    def test_sdk_envia_webp_sem_upsert_e_rpc_sem_parametros_forgados(self):
        chamadas = []
        def servidor(request):
            chamadas.append(request)
            if request.url.path.endswith("/rpc/confirmar_foto"):
                return httpx.Response(200, json=None)
            return httpx.Response(200, json={"Key": "animal-fotos/A/animal/foto.webp"})
        with httpx.Client(transport=httpx.MockTransport(servidor)) as transporte:
            cliente = create_client("https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_local",
                                    options=ClientOptions(httpx_client=transporte, persist_session=False,
                                                          auto_refresh_token=False))
            roteiro.enviar(cliente.storage.from_("animal-fotos"), "A/animal/foto.webp")
            roteiro.confirmar(cliente, "foto")
        self.assertEqual(chamadas[0].headers["x-upsert"], "false")
        self.assertIn(b"Content-Type: image/webp", chamadas[0].content)
        self.assertIn(roteiro.WEBP, chamadas[0].content)
        self.assertEqual(json.loads(chamadas[1].content), {"p_foto_id": "foto"})

    def executar_simulado(self, *, falha=None):
        """Executa o roteiro inteiro com SDK real e HTTP estritamente em memória."""
        servidor = ServidorStorage(falha)
        transportes = [httpx.Client(transport=httpx.MockTransport(servidor)) for _ in range(3)]
        clientes = [create_client(
            "https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_local",
            options=ClientOptions(httpx_client=transporte, persist_session=False,
                                  auto_refresh_token=False,
                                  headers={"Authorization": f"Bearer {ator}"}),
        ) for ator, transporte in zip(("A", "B", "visitante"), transportes)]
        if falha == "cache_e_sessao":
            clientes[0].auth.sign_out = Mock(side_effect=RuntimeError("SEGREDO"))
        # Falhas de autenticação são exercitadas separadamente; aqui as três
        # identidades HTTP são fixas e nenhuma sessão Auth é criada.
        with tempfile.TemporaryDirectory() as pasta:
            saida = io.StringIO()
            with (
                patch.object(roteiro.sys.stdin, "isatty", return_value=True),
                patch.object(roteiro, "configuracao", return_value=(
                    "https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_local", "abcdefghijklmnopqrst")),
                patch("builtins.input", side_effect=["adotae-testes abcdefghijklmnopqrst", "bucket conferido"]),
                patch.object(roteiro, "novo_cliente", side_effect=clientes),
                patch.object(roteiro, "entrar", side_effect=[servidor.A, servidor.B]),
                patch.object(roteiro, "RUNS", Path(pasta)),
                contextlib.redirect_stdout(saida),
            ):
                codigo = roteiro.main()
            arquivos = list(Path(pasta).glob("*.json"))
            manifesto = json.loads(arquivos[0].read_text(encoding="utf-8")) if arquivos else {}
        for transporte in transportes:
            transporte.close()
        return codigo, servidor, manifesto, saida.getvalue()

    def test_cenarios_completos_sdk_preservam_registros_sql_e_removem_so_arquivo_proprio(self):
        codigo, servidor, manifesto, saida = self.executar_simulado()
        self.assertEqual(codigo, 0, saida)
        self.assertIn("APROVADO: cenários Storage", saida)
        self.assertNotIn("APROVADO COM RESSALVA", saida)
        self.assertFalse(servidor.objeto)
        self.assertEqual(servidor.foto["estado"], "removendo")
        self.assertTrue(manifesto["exclusao_confirmada"])
        self.assertIn("LIMPEZA SQL PENDENTE", saida)
        self.assertEqual(servidor.confirmacoes, 2)
        self.assertEqual(len(servidor.exclusoes), 1)
        self.assertEqual(servidor.exclusoes[0], manifesto["caminho"])
        self.assertNotIn("SEGREDO", saida)

    def test_upload_com_resposta_perdida_recupera_reserva_e_limpa_storage(self):
        codigo, servidor, manifesto, saida = self.executar_simulado(falha="upload_perdido")
        self.assertEqual(codigo, 1)
        self.assertFalse(servidor.objeto)
        self.assertEqual(servidor.foto["estado"], "removendo")
        self.assertEqual(manifesto["limpeza_storage"], "arquivo removido")
        self.assertNotIn("SEGREDO", saida)

    def test_reserva_com_resposta_perdida_e_recuperada_sem_delete_sql(self):
        codigo, servidor, manifesto, _ = self.executar_simulado(falha="reserva_perdida")
        self.assertEqual(codigo, 1)
        self.assertEqual(servidor.foto["estado"], "removendo")
        self.assertEqual(manifesto["limpeza_storage"], "ausência física não comprovada")
        self.assertEqual(servidor.exclusoes, [])

    def test_insert_animal_com_resposta_perdida_e_localizado_por_dono_e_nome_exato(self):
        codigo, servidor, manifesto, _ = self.executar_simulado(falha="animal_perdido")
        self.assertEqual(codigo, 1)
        self.assertEqual(manifesto["animal_id"], servidor.ANIMAL)
        self.assertEqual(manifesto["reservas_restantes"], [])

    def test_assinatura_indevida_reprova_sem_imprimir_token_e_executa_limpeza(self):
        codigo, servidor, _, saida = self.executar_simulado(falha="assinatura_liberada")
        self.assertEqual(codigo, 1)
        self.assertNotIn("SEGREDO", saida)
        self.assertFalse(servidor.objeto)

    def test_falha_de_preflight_nao_tenta_escrita_ou_limpeza(self):
        codigo, servidor, manifesto, _ = self.executar_simulado(falha="data_api")
        self.assertEqual(codigo, 1)
        self.assertIsNone(servidor.animal)
        self.assertFalse(manifesto["escritas_iniciadas"])
        self.assertEqual(servidor.exclusoes, [])

    def test_bytes_alterados_reprovam_e_arquivo_proprio_e_removido(self):
        codigo, servidor, _, _ = self.executar_simulado(falha="bytes_alterados")
        self.assertEqual(codigo, 1)
        self.assertFalse(servidor.objeto)
        self.assertEqual(servidor.foto["estado"], "removendo")

    def test_checkpoints_pos_confirmacao_em_ordem(self):
        codigo, _, _, saida = self.executar_simulado()
        self.assertEqual(codigo, 0, saida)
        anterior = -1
        for etapa in ("segunda_rpc", "conferencia_reserva", "download_proprio"):
            for marco in ("INÍCIO", "OK"):
                posicao = saida.index(f"CHECKPOINT {marco}: {etapa}")
                self.assertGreater(posicao, anterior)
                anterior = posicao

    def test_falhas_pos_confirmacao_identificam_etapa_sem_vazar_e_limpam(self):
        for etapa, tipo, extra in (
            ("segunda_rpc", "APIError", "código=42501"),
            ("segunda_rpc_inesperada", "TypeError", ""),
            ("conferencia_reserva", "AttributeError", ""),
            ("download_proprio", "StorageApiError", "status=403; código=AccessDenied"),
            ("download_inesperado", "ValueError", ""),
            ("download_bytes", "Falha", ""),
        ):
            with self.subTest(etapa=etapa):
                codigo, servidor, manifesto, saida = self.executar_simulado(falha=etapa)
                nome = {
                    "segunda_rpc_inesperada": "segunda_rpc",
                    "download_inesperado": "download_proprio",
                    "download_bytes": "download_proprio",
                }.get(etapa, etapa)
                self.assertEqual(codigo, 1)
                self.assertIn(f"CHECKPOINT INÍCIO: {nome}", saida)
                self.assertIn(f"etapa={nome}; tipo={tipo}", saida)
                self.assertIn(extra, saida)
                self.assertNotIn(f"CHECKPOINT OK: {nome}", saida)
                self.assertNotIn("SEGREDO", saida)
                self.assertFalse(servidor.objeto)
                self.assertEqual(manifesto["estado"], "pronta")
                self.assertEqual(manifesto["limpeza_storage"], "arquivo removido")

    def test_diagnostico_omite_codigo_status_e_mensagem_nao_seguros(self):
        for erro in (
            StorageApiError("SEGREDO", "https://host/?token=SEGREDO", "SEGREDO"),
            AttributeError("senha=SEGREDO JWT=SEGREDO chave=SEGREDO"),
        ):
            with self.subTest(tipo=type(erro).__name__):
                texto = roteiro.diagnostico_erro(erro)
                self.assertIn(f"tipo={type(erro).__name__}", texto)
                self.assertNotIn("SEGREDO", texto)
                self.assertNotIn("https://", texto)

    def test_status_http_seguro_sem_imprimir_request_ou_response(self):
        resposta = httpx.Response(503, text="SEGREDO", request=httpx.Request(
            "GET", "https://host/?token=SEGREDO", headers={"Authorization": "Bearer SEGREDO"}))
        erro = httpx.HTTPStatusError("SEGREDO", request=resposta.request, response=resposta)
        self.assertEqual(roteiro.diagnostico_erro(erro), "tipo=HTTPStatusError; status=503")
        self.assertEqual(roteiro.diagnostico_erro(StorageApiError(
            "SEGREDO", "SEGREDO", "429")), "tipo=StorageApiError; status=429")

    def test_interrupcao_no_checkpoint_identifica_etapa_sem_mensagem(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            with self.assertRaises(roteiro.Falha) as contexto:
                roteiro.executar_etapa("segunda_rpc", Mock(side_effect=KeyboardInterrupt("SEGREDO")))
        self.assertEqual(str(contexto.exception), "etapa=segunda_rpc; tipo=KeyboardInterrupt")
        self.assertNotIn("CHECKPOINT OK", saida.getvalue())

    def test_erro_inesperado_generico_informa_tipo_sem_mensagem(self):
        codigo, _, _, saida = self.executar_simulado(falha="animal_perdido")
        self.assertEqual(codigo, 1)
        self.assertIn("tipo=ReadTimeout", saida)
        self.assertNotIn("SEGREDO", saida)

    def test_delete_autorizado_vazio_reprova_e_nao_promete_ausencia_fisica(self):
        codigo, servidor, manifesto, _ = self.executar_simulado(falha="delete_vazio")
        self.assertEqual(codigo, 1)
        self.assertTrue(servidor.objeto)
        self.assertNotIn("exclusao_confirmada", manifesto)
        self.assertEqual(manifesto["limpeza_storage"], "ausência física não comprovada")

    def test_download_sem_cache_preserva_rota_sessao_e_usa_nonce_novo(self):
        chamadas = []
        def servidor(req):
            chamadas.append(req)
            return httpx.Response(200, content=roteiro.WEBP)
        with httpx.Client(transport=httpx.MockTransport(servidor)) as transporte:
            cliente = create_client("https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_local",
                                    options=ClientOptions(httpx_client=transporte, persist_session=False,
                                                          headers={"Authorization": "Bearer JWT-LOCAL"}))
            bucket = cliente.storage.from_("animal-fotos")
            for _ in range(2):
                self.assertEqual(roteiro.baixar_sem_cache(bucket, "A/animal/foto.webp"), roteiro.WEBP)
        for req in chamadas:
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.url.path, "/storage/v1/object/animal-fotos/A/animal/foto.webp")
            self.assertEqual(req.headers["authorization"], "Bearer JWT-LOCAL")
            self.assertEqual(set(req.url.params), {"cacheNonce"})
            self.assertEqual(len(req.url.params["cacheNonce"]), 32)
        self.assertNotEqual(chamadas[0].url.params["cacheNonce"], chamadas[1].url.params["cacheNonce"])

    def test_cache_pos_revogacao_aprova_com_ressalva_e_conclui_exclusao(self):
        codigo, servidor, manifesto, saida = self.executar_simulado(falha="cache_revogado")
        self.assertEqual(codigo, 0, saida)
        self.assertIn("CHECKPOINT OK: download_removendo_sem_cache", saida)
        self.assertIn("CHECKPOINT OK: download_excluido_sem_cache", saida)
        self.assertIn("APROVADO COM RESSALVA", saida)
        self.assertIn("possível acesso residual por cache", saida)
        self.assertIn("revogação imediata não comprovada", saida)
        self.assertIn("LIMPEZA SQL PENDENTE", saida)
        self.assertNotIn("SEGREDO", saida)
        self.assertFalse(servidor.objeto)
        self.assertTrue(manifesto["exclusao_confirmada"])
        self.assertEqual(servidor.exclusoes, [manifesto["caminho"]])

    def test_sucesso_com_nonce_pos_revogacao_reprova_e_limpa(self):
        codigo, servidor, _, saida = self.executar_simulado(falha="leitura_revogada")
        self.assertEqual(codigo, 1, saida)
        self.assertIn("CHECKPOINT INÍCIO: download_removendo_sem_cache", saida)
        self.assertNotIn("CHECKPOINT OK: download_removendo_sem_cache", saida)
        self.assertNotIn("APROVADO COM RESSALVA", saida)
        self.assertIn("NÃO APROVADO", saida)
        self.assertFalse(servidor.objeto)

    def test_cache_apos_exclusao_aprova_com_ressalva_sem_perder_confirmacao_da_limpeza(self):
        codigo, servidor, manifesto, saida = self.executar_simulado(falha="cache_excluido")
        self.assertEqual(codigo, 0, saida)
        self.assertIn("CHECKPOINT OK: download_excluido_sem_cache", saida)
        self.assertIn("APROVADO COM RESSALVA", saida)
        self.assertIn("possível acesso residual por cache", saida)
        self.assertTrue(manifesto["exclusao_confirmada"])
        self.assertFalse(servidor.objeto)
        self.assertEqual(len(servidor.exclusoes), 1)

    def test_ressalva_nao_mascara_falhas_posteriores(self):
        for falha in ("delete_vazio", "erro_download", "nonce_excluido", "sessao"):
            with self.subTest(falha=falha):
                codigo, _, _, saida = self.executar_simulado(falha=f"cache_e_{falha}")
                self.assertEqual(codigo, 1, saida)
                self.assertIn("possível acesso residual por cache", saida)
                self.assertNotIn("APROVADO COM RESSALVA", saida)
                self.assertNotIn("SEGREDO", saida)


class ServidorStorage:
    """Modelo de respostas HTTP, não uma implementação de RLS/Supabase.

    Catch-all falha para qualquer rota não prevista. Não possui fallback de rede.
    """
    A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    ANIMAL = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    FOTO = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

    def __init__(self, falha):
        self.falha = falha
        self.animal = None
        self.foto = None
        self.objeto = False
        self.confirmacoes = 0
        self.exclusoes = []

    @property
    def caminho(self):
        return f"{self.A}/{self.ANIMAL}/{self.FOTO}.webp"

    def __call__(self, req):
        ator = req.headers.get("Authorization", "").removeprefix("Bearer ")
        path = req.url.path
        body = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") and req.content else {}
        def sql_negado():
            return httpx.Response(403, json={"code": "42501", "message": "SEGREDO", "details": None, "hint": None})
        def storage_negado():
            return httpx.Response(403, json={"statusCode": "403", "error": "AccessDenied", "message": "SEGREDO"})
        if path == "/rest/v1/animais":
            if req.method == "POST":
                self.animal = {**body, "id": self.ANIMAL, "usuario_id": self.A,
                               "status_publicacao": "rascunho", "status_adocao": "disponivel", "oculto_admin": False}
                if self.falha == "animal_perdido":
                    raise httpx.ReadTimeout("SEGREDO")
                return httpx.Response(201, json=[self.animal])
            if req.method == "GET":
                if "nome" in req.url.params:
                    assert req.url.params["usuario_id"] == f"eq.{self.A}"
                    assert req.url.params["nome"] == f"eq.{self.animal['nome']}"
                return httpx.Response(200, json=[self.animal] if self.animal else [])
        if path == "/rest/v1/animal_fotos":
            if self.falha == "data_api":
                return httpx.Response(404, json={"code": "PGRST205", "message": "SEGREDO"})
            if req.method == "POST":
                assert body == {"animal_id": self.ANIMAL, "posicao": 1}
                self.foto = {**body, "id": self.FOTO, "estado": "pendente", "criado_em": "2026-11-07T12:00:00Z"}
                if self.falha == "reserva_perdida":
                    raise httpx.ReadTimeout("SEGREDO")
                return httpx.Response(201, json=[self.foto])
            if req.method == "GET":
                if self.falha == "conferencia_reserva" and self.confirmacoes == 2 and self.foto["estado"] == "pronta":
                    self.falha = None  # a limpeza posterior deve continuar disponível
                    raise AttributeError("SEGREDO https://host/?token=SEGREDO")
                match = req.url.params.get("animal_id") == f"eq.{self.ANIMAL}" or req.url.params.get("id") == f"eq.{self.FOTO}"
                return httpx.Response(200, json=[self.foto.copy()] if ator == "A" and self.foto and match else [])
            if req.method == "PATCH" and ator == "B":
                return httpx.Response(200, json=[])
            if req.method == "PATCH" and body == {"estado": "removendo"}:
                assert req.url.params["id"] == f"eq.{self.FOTO}"
                assert req.url.params["animal_id"] == f"eq.{self.ANIMAL}"
                self.foto["estado"] = "removendo"
                return httpx.Response(200, json=[self.foto.copy()])
            return sql_negado()
        if path == "/rest/v1/rpc/confirmar_foto":
            assert body == {"p_foto_id": self.FOTO}
            if ator == "A" and self.objeto and self.foto["estado"] in ("pendente", "pronta"):
                if self.falha == "segunda_rpc_inesperada" and self.confirmacoes == 1:
                    raise TypeError("SEGREDO")
                if self.falha == "segunda_rpc" and self.confirmacoes == 1:
                    return sql_negado()
                self.foto["estado"] = "pronta"
                self.confirmacoes += 1
                return httpx.Response(200, json=None)
            return sql_negado()
        base = "/storage/v1/object/animal-fotos"
        if path == base and req.method == "DELETE":
            assert body == {"prefixes": [self.caminho]}
            if ator == "A" and self.foto["estado"] == "removendo" and self.objeto:
                if self.falha in ("delete_vazio", "cache_e_delete_vazio"):
                    return httpx.Response(200, json=[])
                self.objeto = False
                self.exclusoes.append(self.caminho)
                return httpx.Response(200, json=[{"name": self.caminho}])
            return httpx.Response(200, json=[])
        if path == f"{base}/{self.caminho}":
            if req.method == "GET" and ator == "A" and self.exclusoes:
                if self.falha == "cache_e_erro_download":
                    raise httpx.ReadTimeout("SEGREDO")
                if self.falha == "cache_e_nonce_excluido":
                    return httpx.Response(200, content=roteiro.WEBP)
            if (req.method == "GET" and ator == "A" and self.exclusoes
                    and self.falha == "cache_excluido" and "cacheNonce" not in req.url.params):
                return httpx.Response(200, content=roteiro.WEBP)
            if req.method == "GET" and ator == "A" and self.objeto and self.foto["estado"] == "removendo":
                if self.falha == "leitura_revogada" or (
                    (self.falha == "cache_revogado" or (self.falha or "").startswith("cache_e_"))
                    and "cacheNonce" not in req.url.params
                ):
                    return httpx.Response(200, content=roteiro.WEBP)
            if req.method == "GET" and ator == "A" and self.objeto and self.foto["estado"] == "pronta":
                if self.falha == "download_inesperado":
                    raise ValueError("SEGREDO")
                if self.falha == "download_proprio":
                    return storage_negado()
                return httpx.Response(200, content=b"alterado" if self.falha in ("bytes_alterados", "download_bytes") else roteiro.WEBP)
            if req.method == "POST" and ator == "A" and self.foto["estado"] == "pendente" and req.headers["x-upsert"] == "false":
                assert roteiro.WEBP in req.content
                self.objeto = True
                if self.falha == "upload_perdido":
                    raise httpx.ReadTimeout("SEGREDO")
                return httpx.Response(200, json={"Key": f"animal-fotos/{self.caminho}"})
            return storage_negado()
        if path == "/storage/v1/object/list/animal-fotos":
            assert body["prefix"] == f"{self.A}/{self.ANIMAL}"
            return httpx.Response(200, json=[])
        if path.startswith("/storage/v1/object/sign/") and self.falha == "assinatura_liberada":
            return httpx.Response(200, json={"signedURL": "/object/sign/animal-fotos/x?token=SEGREDO"})
        if path.startswith("/storage/v1/object/"):
            return storage_negado()
        raise AssertionError(f"Rota HTTP não prevista no teste: {req.method} {path}")


if __name__ == "__main__":
    unittest.main()
