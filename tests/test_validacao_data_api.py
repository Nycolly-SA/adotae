"""Verificações locais do roteiro; não abrem conexões com o Supabase."""

import importlib.util
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from httpx import ConnectError
from postgrest import APIError
from supabase import AuthApiError

spec = importlib.util.spec_from_file_location(
    "validar_data_api", Path(__file__).parents[1] / "supabase/tests/validar_data_api.py",
)
roteiro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roteiro)


class ValidacaoLocalTests(unittest.TestCase):
    def test_diagnostico_auth_distingue_causas_sem_expor_mensagem_do_servidor(self):
        for codigo, status, trecho in (
            ("invalid_credentials", 400, "E-mail ou senha"),
            ("email_not_confirmed", 400, "confirmação"),
            ("over_request_rate_limit", 429, "Muitas tentativas"),
            (None, 401, "chave publicável"),
            ("CODIGO-SENSIVEL", 503, "indisponível"),
        ):
            with self.subTest(codigo=codigo):
                erro = AuthApiError("SENHA-JWT-EMAIL-SENSIVEIS", status, codigo)
                mensagem = roteiro.diagnostico_auth(erro)
                self.assertIn(trecho, mensagem)
                self.assertNotIn("SENHA-JWT-EMAIL-SENSIVEIS", mensagem)
                self.assertNotIn("CODIGO-SENSIVEL", mensagem)
        self.assertIn("conexão", roteiro.diagnostico_auth(ConnectError("SEGREDO")))
        self.assertNotIn("SEGREDO", roteiro.diagnostico_auth(ConnectError("SEGREDO")))

    def test_entrar_confere_identidade_com_token_retornado_sem_persistir(self):
        cliente = Mock()
        usuario = SimpleNamespace(id="usuario-teste", email_confirmed_at="confirmado")
        cliente.auth.sign_in_with_password.return_value = SimpleNamespace(
            user=usuario, session=SimpleNamespace(access_token="TOKEN-APENAS-EM-MEMORIA"),
        )
        cliente.auth.get_user.return_value = SimpleNamespace(user=usuario)
        with (
            patch("builtins.input", side_effect=["teste@example.invalid", "senha"]) as entrada,
        ):
            self.assertEqual(roteiro.entrar(cliente, "A"), usuario.id)
        self.assertEqual(entrada.call_count, 2)
        cliente.auth.get_user.assert_called_once_with("TOKEN-APENAS-EM-MEMORIA")

    def test_entrar_identifica_se_falha_foi_no_login_ou_validacao_da_sessao(self):
        for fase in ("login", "verificação da sessão"):
            cliente = Mock()
            erro = AuthApiError("SEGREDO", 400, "invalid_credentials")
            if fase == "login":
                cliente.auth.sign_in_with_password.side_effect = erro
            else:
                cliente.auth.sign_in_with_password.return_value = SimpleNamespace(
                    user=SimpleNamespace(id="usuario"),
                    session=SimpleNamespace(access_token="TOKEN"),
                )
                cliente.auth.get_user.side_effect = erro
            with (
                patch("builtins.input", side_effect=["email", "senha"]),
                self.assertRaises(roteiro.Falha) as falha,
            ):
                roteiro.entrar(cliente, "A")
            self.assertIn(fase, str(falha.exception))
            self.assertNotIn("SEGREDO", str(falha.exception))

    def test_falha_no_login_nao_consulta_dados_nem_pede_limpeza_de_anuncios(self):
        saida = io.StringIO()
        cliente = Mock()
        with (
            patch.object(roteiro.sys.stdin, "isatty", return_value=True),
            patch.object(roteiro, "configuracao", return_value=("url-teste", "chave", "referencia")),
            patch("builtins.input", return_value="adotae-testes referencia"),
            patch.object(roteiro, "novo_cliente", return_value=cliente),
            patch.object(roteiro, "entrar", side_effect=roteiro.Falha("E-mail ou senha incorretos.")),
            patch.object(roteiro, "limpar") as limpar,
            patch.object(roteiro.logging, "disable"),
            contextlib.redirect_stdout(saida),
        ):
            self.assertEqual(roteiro.main(), 1)
        limpar.assert_not_called()
        cliente.table.assert_not_called()
        self.assertIn("Nenhuma inserção de anúncio foi tentada", saida.getvalue())
        self.assertNotIn("remoção manual", saida.getvalue())

    def test_configuracao_nao_usa_variaveis_da_aplicacao(self):
        with self.assertRaises(roteiro.Falha):
            roteiro.configuracao({"SUPABASE_URL": "https://principal.supabase.co"})

    def test_configuracao_rejeita_chaves_que_nao_sao_publicaveis(self):
        for chave in ("sb_secret_teste", "eyJ.jwt.service_role", ""):
            with self.subTest(chave=chave), self.assertRaises(roteiro.Falha):
                roteiro.configuracao({
                    "ADOTAE_TEST_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
                    "ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY": chave,
                })

    def test_configuracao_rejeita_url_igual_a_principal(self):
        url = "https://abcdefghijklmnopqrst.supabase.co"
        with self.assertRaises(roteiro.Falha):
            roteiro.configuracao({
                "ADOTAE_TEST_SUPABASE_URL": url,
                "ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_teste",
                "SUPABASE_URL": url + "/",
            })

    def test_configuracao_extrai_referencia_sem_carregar_dotenv(self):
        self.assertEqual(roteiro.configuracao({
            "ADOTAE_TEST_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co/",
            "ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_teste",
        }), ("https://abcdefghijklmnopqrst.supabase.co", "sb_publishable_teste",
             "abcdefghijklmnopqrst"))

    def test_bloqueio_rls_aceita_zero_linhas_mas_rejeita_linha_retornada(self):
        roteiro.exigir_bloqueio(lambda: SimpleNamespace(data=[]))
        with self.assertRaises(roteiro.Falha):
            roteiro.exigir_bloqueio(lambda: SimpleNamespace(data=[{"id": "temporario"}]))

    def test_bloqueio_por_grant_exige_erro_de_permissao(self):
        roteiro.exigir_bloqueio(Mock(side_effect=APIError({"code": "42501"})), vazio=False)
        with self.assertRaises(roteiro.Falha):
            roteiro.exigir_bloqueio(lambda: SimpleNamespace(data=[]), vazio=False)

    def test_rede_jwt_e_constraint_nao_contam_como_bloqueio_rls(self):
        for erro in (ConnectError("sem rede"), APIError({"code": "23514"}),
                     APIError({"code": "PGRST301"})):
            with self.subTest(tipo=type(erro).__name__), self.assertRaises(Exception):
                roteiro.exigir_bloqueio(Mock(side_effect=erro))

    def test_limpeza_so_apaga_registros_do_usuario_e_verifica_estado(self):
        cliente = Mock()
        consulta = cliente.table.return_value
        consulta.select.return_value.eq.return_value.eq.return_value.execute.side_effect = [
            SimpleNamespace(data=[{"id": "temporario"}]), SimpleNamespace(data=[]),
        ]
        consulta.delete.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "temporario"}])
        )
        self.assertTrue(roteiro.limpar(cliente, "usuario-teste", ["TESTE-RLS-execucao-A"]))
        consulta.delete.return_value.eq.assert_called_with("usuario_id", "usuario-teste")

    def test_limpeza_com_zero_linhas_e_registro_restante_falha(self):
        cliente = Mock()
        consulta = cliente.table.return_value
        consulta.select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[{"id": "temporario"}])
        )
        consulta.delete.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
            SimpleNamespace(data=[])
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(roteiro.limpar(cliente, "usuario-teste", ["TESTE-RLS-execucao-A"]))

    def test_finally_limpa_ambos_usuarios_quando_insert_perde_resposta(self):
        saida = io.StringIO()
        clientes = [Mock(), Mock(), Mock()]
        clientes[2].auth.get_session.return_value = None
        with (
            patch.object(roteiro.sys.stdin, "isatty", return_value=True),
            patch.object(roteiro, "configuracao", return_value=("url-teste", "chave", "referencia")),
            patch("builtins.input", return_value="adotae-testes referencia"),
            patch.object(roteiro, "novo_cliente", side_effect=clientes),
            patch.object(roteiro, "entrar", side_effect=["usuario-A", "usuario-B"]),
            patch.object(roteiro, "criar", side_effect=RuntimeError("DETALHE-SENSIVEL")),
            patch.object(roteiro, "limpar", return_value=True) as limpar,
            patch.object(roteiro.logging, "disable"),
            contextlib.redirect_stdout(saida),
        ):
            self.assertEqual(roteiro.main(), 1)
        self.assertEqual(limpar.call_count, 2)
        self.assertEqual(limpar.call_args_list[0].args[:2], (clientes[0], "usuario-A"))
        self.assertEqual(limpar.call_args_list[1].args[:2], (clientes[1], "usuario-B"))
        self.assertEqual(len(limpar.call_args_list[0].args[2]), 5)
        self.assertNotIn("DETALHE-SENSIVEL", saida.getvalue())
        for cliente in clientes[:2]:
            cliente.auth.sign_out.assert_called_once_with({"scope": "local"})
