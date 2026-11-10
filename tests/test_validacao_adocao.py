"""Controles offline do diagnóstico; não validam RLS nem abrem rede."""

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from postgrest import APIError
from storage3.exceptions import StorageApiError

SCRIPTS = Path(__file__).resolve().parents[1] / "supabase/tests"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("validar_adocao", SCRIPTS / "validar_adocao.py")
    roteiro = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roteiro)
finally:
    sys.path.remove(str(SCRIPTS))


class AdocaoDiagnosticoTests(unittest.TestCase):
    def simular(self, autorizar, alteracao=False):
        foto_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        antes = ({"pub": {"status_publicacao": "publicado", "status_adocao": "disponivel",
                           "oculto_admin": False, "atualizado_em": "2026-11-09T00:00:00Z"},
                  "ras": {"status_publicacao": "rascunho", "status_adocao": "disponivel"}},
                 {"pub": [{"id": foto_id, "estado": "pronta"}], "ras": []}, {"path": b"foto"})
        depois = ({**antes[0], "pub": {**antes[0]["pub"], "status_adocao": "adotado",
                                       "atualizado_em": "2026-11-10T00:00:00Z"}}, *antes[1:])
        a, b, visitante = Mock(), Mock(), Mock()
        negado = APIError({"code": "42501", "message": "SEGREDO"})
        a.rpc.return_value.execute.side_effect = [negado, None, negado]
        b.rpc.return_value.execute.side_effect = negado
        visitante.rpc.return_value.execute.side_effect = negado
        a.table.return_value.update.return_value.eq.return_value.execute.side_effect = negado
        # Cada consulta anônima tem sua própria cadeia para respeitar filtros distintos.
        def tabela(nome):
            tabela = Mock()
            if nome == "animal_fotos":
                resposta = [{"id": foto_id}] if not estado[0] else []
                tabela.select.return_value.eq.return_value.execute.return_value.data = resposta
            else:
                cadeia = tabela.select.return_value.eq.return_value
                cadeia.execute.return_value.data = [{}] if not estado[0] else []
                cadeia.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                    [{"id": "pub"}] if not estado[0] else [])
            return tabela
        estado = [False]
        visitante.table.side_effect = tabela
        bucket = visitante.storage.from_.return_value
        bucket.create_signed_url.return_value = {"signedURL": "SEGREDO"}
        def adotar(*args):
            if autorizar:
                roteiro.rpc(a, "pub")
                estado[0] = True
                bucket.create_signed_url.side_effect = StorageApiError("SEGREDO", "AccessDenied", 403)
            return autorizar
        def ler(*args):
            if alteracao:
                return depois if snapshots.call_count > 1 else antes
            return depois if estado[0] else antes
        with (patch.object(roteiro, "snapshot", side_effect=ler) as snapshots,
              patch.object(roteiro, "adotar_autorizado", side_effect=adotar) as adocao,
              contextlib.redirect_stdout(io.StringIO()) as saida):
            if alteracao:
                with self.assertRaises(roteiro.Falha):
                    roteiro.cenarios(a, b, visitante, "A", "pub", "ras")
                adocao.assert_not_called()
                return
            resultado = roteiro.cenarios(a, b, visitante, "A", "pub", "ras")
        self.assertNotIn("SEGREDO", saida.getvalue())
        self.assertEqual(a.rpc.call_count, 3 if autorizar else 1)
        self.assertEqual(resultado, 0 if autorizar else 2)
        if not autorizar:
            self.assertIn("6–12 NÃO EXECUTADAS", saida.getvalue())

    def test_fluxo_completo_simulado(self):
        self.simular(True)

    def test_parada_antes_da_adocao_nao_executa_etapas_positivas(self):
        self.simular(False)

    def test_alteracao_em_tentativa_negativa_impede_adocao(self):
        self.simular(True, alteracao=True)

    def test_destino_sem_autorizacao_nao_cria_clientes(self):
        with (patch.object(roteiro.sys.stdin, "isatty", return_value=True),
              patch.object(roteiro, "configuracao", return_value=("url", "key", "ref")),
              patch("builtins.input", return_value="nao"),
              patch.object(roteiro, "novo_cliente") as cliente,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(roteiro.main(), 1)
        cliente.assert_not_called()

    def test_adocao_exige_autorizacao_exata(self):
        cliente = Mock()
        for resposta in ("", "sim", "ADOTAR outro"):
            with patch("builtins.input", return_value=resposta):
                self.assertFalse(roteiro.adotar_autorizado(cliente, "id"))
        cliente.rpc.assert_not_called()
        with patch("builtins.input", return_value="ADOTAR id"):
            self.assertTrue(roteiro.adotar_autorizado(cliente, "id"))
        cliente.rpc.assert_called_once_with("marcar_adotado", {"p_animal_id": "id"})
        cliente.rpc.return_value.execute.assert_called_once()

    def test_timeout_na_adocao_nao_repete_chamada(self):
        cliente = Mock()
        cliente.rpc.return_value.execute.side_effect = TimeoutError("SEGREDO")
        with patch("builtins.input", return_value="ADOTAR id"), self.assertRaises(TimeoutError):
            roteiro.adotar_autorizado(cliente, "id")
        cliente.rpc.return_value.execute.assert_called_once()

    def test_negacao_conhecida_exige_estado_preservado(self):
        negado = APIError({"code": "42501", "message": "SEGREDO"})
        conferir = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            roteiro.recusada("teste", Mock(side_effect=negado), conferir)
        conferir.assert_called_once()
        conferir.side_effect = roteiro.Falha("Registro alterado")
        with self.assertRaises(roteiro.Falha):
            roteiro.recusada("teste", Mock(side_effect=negado), conferir)

    def test_erro_de_rede_ou_rpc_ausente_nao_aprova(self):
        for erro in (TimeoutError("SEGREDO"), APIError({"code": "PGRST202", "message": "SEGREDO"})):
            conferir = Mock()
            with self.assertRaises(Exception):
                roteiro.recusada("teste", Mock(side_effect=erro), conferir)
            conferir.assert_called_once()

    def test_sucesso_indevido_e_conferido_e_reprovado(self):
        conferir = Mock()
        with self.assertRaises(roteiro.Falha):
            roteiro.recusada("teste", Mock(return_value=None), conferir)
        conferir.assert_called_once()

    def test_preservacao_so_permite_status_e_data(self):
        antes = {"status_adocao": "disponivel", "atualizado_em": "2026-11-09T00:00:00+00:00", "nome": "fixture"}
        depois = {**antes, "status_adocao": "adotado", "atualizado_em": "2026-11-10T00:00:00+00:00"}
        roteiro.conferir_adotado(antes, depois)
        for campo, valor in (("nome", "alterado"), ("status_adocao", "disponivel"),
                             ("atualizado_em", "2026-11-08T00:00:00+00:00")):
            with self.assertRaises(roteiro.Falha):
                roteiro.conferir_adotado(antes, {**depois, campo: valor})


if __name__ == "__main__":
    unittest.main()
