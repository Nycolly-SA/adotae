"""Roteiro opt-in da Data API de adotae-testes. Nunca importa a aplicação Flask.

Executar manualmente, em terminal: python supabase/tests/validar_data_api.py
Configuração e limites em supabase/README.md. Não carrega arquivos .env.
"""

import logging
import os
import re
import sys
from uuid import uuid4

from httpx import HTTPError, TimeoutException
from postgrest import APIError
from supabase import AuthApiError, AuthError, ClientOptions, create_client
from supabase_auth.errors import AuthRetryableError


class Falha(Exception):
    """Mensagem controlada, sem detalhes sensíveis recebidos dos serviços."""


def exigir(condicao, mensagem):
    if not condicao:
        raise Falha(mensagem)


def configuracao(ambiente):
    url = ambiente.get("ADOTAE_TEST_SUPABASE_URL", "").strip().rstrip("/")
    chave = ambiente.get("ADOTAE_TEST_SUPABASE_PUBLISHABLE_KEY", "").strip()
    destino = re.fullmatch(r"https://([a-z0-9]{20})\.supabase\.co", url)
    exigir(destino is not None, "Defina ADOTAE_TEST_SUPABASE_URL com a URL HTTPS do projeto de testes.")
    exigir(re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", chave) is not None,
           "Use exclusivamente uma chave sb_publishable_ do projeto de testes; JWTs e chaves secretas são recusados.")
    exigir(url != ambiente.get("SUPABASE_URL", "").strip().rstrip("/"),
           "O destino coincide com SUPABASE_URL da aplicação. Execução recusada.")
    return url, chave, destino.group(1)


def novo_cliente(url, chave):
    return create_client(url, chave, options=ClientOptions(
        persist_session=False, auto_refresh_token=False, postgrest_client_timeout=20,
    ))


def diagnostico_auth(erro):
    """Traduz tipos/códigos conhecidos; nunca incorpora texto bruto do serviço."""
    if isinstance(erro, TimeoutException):
        return "Tempo de conexão esgotado. Confira sua conexão e a disponibilidade de adotae-testes."
    if isinstance(erro, (AuthRetryableError, HTTPError)):
        return "Falha de conexão ou resposta HTTP. Confira a rede, a URL e a disponibilidade de adotae-testes."
    if isinstance(erro, AuthApiError):
        mensagens = {
            "invalid_credentials": "E-mail ou senha não reconhecidos. Use uma conta cadastrada em Auth no projeto adotae-testes, com a senha dessa conta.",
            "email_not_confirmed": "Falta a confirmação do e-mail dessa conta de teste.",
            "over_request_rate_limit": "Muitas tentativas. Aguarde antes de tentar novamente.",
            "email_provider_disabled": "O login por e-mail está desabilitado no projeto de destino.",
            "user_banned": "A conta de teste está bloqueada no Auth.",
            "captcha_failed": "O Auth exige ou recusou CAPTCHA; este roteiro não resolve CAPTCHA.",
            "validation_failed": "O Auth recusou o formato dos dados. Confira o e-mail informado.",
            "bad_jwt": "O Auth recusou o token da sessão recém-criada.",
            "session_not_found": "O Auth não encontrou a sessão recém-criada.",
        }
        status = erro.status if isinstance(erro.status, int) and 100 <= erro.status <= 599 else None
        codigo = erro.code if erro.code in mensagens else "não reconhecido"
        if codigo in mensagens:
            motivo = mensagens[codigo]
        elif status == 429:
            motivo = "Muitas tentativas. Aguarde antes de tentar novamente."
        elif status and status >= 500:
            motivo = "O serviço Auth está indisponível ou apresentou erro interno."
        elif status in (401, 403):
            motivo = "O Auth recusou o acesso. Confira se URL e chave publicável pertencem a adotae-testes."
        else:
            motivo = "O Auth recusou a operação. Confira as contas de teste, a URL e a chave publicável no painel de adotae-testes."
        return f"{motivo} (Auth; HTTP {status or 'não informado'}; código {codigo}.)"
    if isinstance(erro, AuthError):
        return "O SDK Auth não conseguiu concluir a operação ou recuperar a sessão."
    if isinstance(erro, (AttributeError, TypeError, ImportError)):
        return "Falha local de integração com o SDK; o roteiro encontrou um objeto ou interface inesperada."
    return "Falha inesperada de autenticação ou SDK. Detalhes omitidos por segurança."


def entrar(cliente, rotulo):
    # Entrada visível solicitada para o teste local; nunca gravada em arquivo/log.
    email = input(f"E-mail fictício do usuário {rotulo}: ").strip()
    senha = input(f"Senha do usuário {rotulo} (visível): ")
    try:
        exigir(bool(email) and bool(senha), "Informe o e-mail e a senha da conta de teste; os campos não podem ficar vazios.")
        resposta = cliente.auth.sign_in_with_password({"email": email, "password": senha})
    except Falha:
        raise
    except Exception as erro:
        raise Falha(f"Falha no login: {diagnostico_auth(erro)}") from None
    finally:
        del email, senha
    exigir(resposta.user is not None and resposta.session is not None,
           "O login não retornou usuário e sessão reais.")
    try:
        identidade = cliente.auth.get_user(resposta.session.access_token)
    except Exception as erro:
        raise Falha(f"Falha na verificação da sessão: {diagnostico_auth(erro)}") from None
    exigir(identidade is not None and identidade.user is not None and identidade.user.id == resposta.user.id,
           "O servidor Auth não confirmou a identidade da sessão.")
    exigir(bool(identidade.user.email_confirmed_at), "Use contas de teste com e-mail confirmado.")
    return identidade.user.id


def dados(resposta):
    exigir(isinstance(resposta.data, list), "A Data API não retornou uma lista de registros.")
    return resposta.data


def exigir_bloqueio(operacao, *, vazio=True):
    """Só 42501 (permissão/RLS), ou zero linhas quando cabível, prova bloqueio.

    Falhas de rede, JWT e constraints não contam como aprovação do cenário.
    O chamador deve conferir também o estado pela sessão do proprietário.
    """
    try:
        resposta = operacao()
    except APIError as erro:
        exigir(erro.code == "42501", "A API falhou por motivo diferente de permissão/RLS.")
        return
    exigir(vazio and dados(resposta) == [], "A operação proibida não foi bloqueada como esperado.")


def fixture(nome):
    return {
        "nome": nome, "especie": "Gato", "sexo": "Macho",
        "idade_aproximada": "2 anos", "porte": "Pequeno", "distrito": "Grussaí",
        "descricao": "TESTE FICTÍCIO AUTOMATIZADO DE RLS. Não é um animal para adoção.",
        # Contato reservado, sem pessoa real. Permite distinguir bloqueio de grant
        # de uma eventual violação do CHECK de contato obrigatório na publicação.
        "email_contato": "animal-ficticio@example.invalid",
    }


def consultar(cliente, identificador):
    return dados(cliente.table("animais").select("*").eq("id", identificador).execute())


def conferir_estado(cliente, esperado):
    exigir(consultar(cliente, esperado["id"]) == [esperado],
           "O registro do proprietário mudou após uma operação proibida ou desapareceu.")


def criar(cliente, usuario_id, nome):
    payload = fixture(nome)
    linhas = dados(cliente.table("animais").insert(payload, returning="representation").execute())
    exigir(len(linhas) == 1, "A inserção não retornou exatamente um anúncio.")
    registro = linhas[0]
    exigir(bool(registro.get("id")), "A API não retornou o ID gerado.")
    exigir(registro.get("usuario_id") == usuario_id, "O proprietário gerado não corresponde ao usuário autenticado.")
    exigir(registro.get("status_publicacao") == "rascunho", "O anúncio não nasceu como rascunho.")
    exigir(registro.get("status_adocao") == "disponivel" and registro.get("oculto_admin") is False,
           "Os demais estados iniciais não correspondem à migração.")
    exigir(all(registro.get(campo) == valor for campo, valor in payload.items()),
           "Os dados fictícios retornados divergem da inserção.")
    conferir_estado(cliente, registro)
    return registro


def isolamento(ator, dono, registro):
    identificador = registro["id"]
    exigir_bloqueio(lambda: ator.table("animais").select("*").eq("id", identificador).execute())
    conferir_estado(dono, registro)
    exigir_bloqueio(lambda: ator.table("animais").update(
        {"descricao": "TESTE FICTÍCIO: alteração indevida por outro usuário."},
        returning="representation",
    ).eq("id", identificador).execute())
    conferir_estado(dono, registro)
    exigir_bloqueio(lambda: ator.table("animais").delete(
        returning="representation",
    ).eq("id", identificador).execute())
    conferir_estado(dono, registro)


def editar_proprio(cliente, registro):
    descricao = "TESTE FICTÍCIO: edição legítima do proprietário."
    linhas = dados(cliente.table("animais").update(
        {"descricao": descricao}, returning="representation",
    ).eq("id", registro["id"]).execute())
    exigir(len(linhas) == 1 and linhas[0].get("id") == registro["id"],
           "A edição própria não retornou o anúncio esperado.")
    atualizado = linhas[0]
    exigir(atualizado.get("descricao") == descricao, "A descrição não foi alterada.")
    exigir(all(atualizado.get(campo) == valor for campo, valor in registro.items()
               if campo not in ("descricao", "atualizado_em")),
           "A edição própria alterou outros campos inesperadamente.")
    conferir_estado(cliente, atualizado)
    return atualizado


def publicacao_bloqueada(cliente, registro, nome):
    exigir_bloqueio(lambda: cliente.table("animais").update(
        {"status_publicacao": "publicado"}, returning="representation",
    ).eq("id", registro["id"]).execute(), vazio=False)
    conferir_estado(cliente, registro)
    payload = fixture(nome)
    payload["status_publicacao"] = "publicado"
    exigir_bloqueio(lambda: cliente.table("animais").insert(
        payload, returning="representation",
    ).execute(), vazio=False)
    exigir(dados(cliente.table("animais").select("*").eq("nome", nome).execute()) == [],
           "A tentativa de publicação criou um registro inesperado.")


def limpar(cliente, usuario_id, nomes):
    """Localiza inclusive INSERT confirmado no servidor que perdeu a resposta.

    Nunca exclui por prefixo amplo nem usando a sessão do outro proprietário.
    """
    sucesso = True
    for nome in nomes:
        ids = []
        try:
            registros = dados(cliente.table("animais").select("*").eq(
                "usuario_id", usuario_id,
            ).eq("nome", nome).execute())
            ids = [registro["id"] for registro in registros]
            for identificador in ids:
                removidos = dados(cliente.table("animais").delete(
                    returning="representation",
                ).eq("usuario_id", usuario_id).eq("nome", nome).eq("id", identificador).execute())
                exigir([item["id"] for item in removidos] == [identificador],
                       "A exclusão própria não retornou o registro removido.")
            restantes = dados(cliente.table("animais").select("*").eq(
                "usuario_id", usuario_id,
            ).eq("nome", nome).execute())
            exigir(restantes == [], "Há registros remanescentes após a limpeza.")
        except (Exception, KeyboardInterrupt):
            sucesso = False
            print(f"LIMPEZA PENDENTE: nome exato={nome}; IDs conhecidos={','.join(ids) or 'não recuperados'}")
    return sucesso


def main():
    # Não propaga mensagens de exceções do SDK ou logs que possam incluir tokens.
    logging.disable(logging.CRITICAL)
    clientes = []
    usuarios = []
    nomes = []
    sucesso = False
    limpeza_ok = True
    escritas_iniciadas = False
    etapa = "configuração"
    try:
        exigir(sys.stdin.isatty(), "Execute em um terminal interativo, sem redirecionar credenciais.")
        url, chave, referencia = configuracao(os.environ)
        print(f"Destino solicitado: {url}")
        print("Abra o painel de adotae-testes e confira a Project Reference independentemente da URL acima.")
        confirmacao = input("Digite adotae-testes seguido de espaço e da referência conferida no painel: ").strip()
        exigir(confirmacao == f"adotae-testes {referencia}", "Destino não confirmado. Nenhuma conexão foi iniciada.")
        nomes = [f"TESTE-RLS-{uuid4().hex}-{sufixo}" for sufixo in ("A", "B", "A-publicacao", "B-publicacao", "visitante")]
        print("Registros temporários desta execução (guarde para eventual limpeza manual):")
        for nome in nomes:
            print(f"  {nome}")
        for rotulo in ("A", "B"):
            etapa = f"autenticação de {rotulo}"
            cliente = novo_cliente(url, chave)
            clientes.append(cliente)
            usuarios.append(entrar(cliente, rotulo))
        exigir(usuarios[0] != usuarios[1], "A e B devem ser contas de teste diferentes.")
        a, b = clientes
        visitante = novo_cliente(url, chave)
        exigir(visitante.auth.get_session() is None, "O visitante recebeu uma sessão inesperada.")
        registros = []
        # Marcar antes do INSERT: a operação pode chegar ao servidor e perder a resposta.
        escritas_iniciadas = True
        for indice, cliente in enumerate((a, b)):
            etapa = f"criação e leitura própria de {'AB'[indice]}"
            registros.append(criar(cliente, usuarios[indice], nomes[indice]))
            print(f"OK: {etapa}, usuario_id e estados iniciais.")
        for ator, dono, registro, rotulo in ((a, b, registros[1], "A sobre B"), (b, a, registros[0], "B sobre A")):
            etapa = f"isolamento de {rotulo}"
            isolamento(ator, dono, registro)
            print(f"OK: {etapa} (leitura, edição, exclusão e estado preservado).")
        etapa = "visitante"
        for dono, registro in zip((a, b), registros):
            exigir_bloqueio(lambda: visitante.table("animais").select("*").eq("id", registro["id"]).execute())
            conferir_estado(dono, registro)
        exigir_bloqueio(lambda: visitante.table("animais").insert(
            fixture(nomes[4]), returning="representation",
        ).execute(), vazio=False)
        # Também procura qualquer inserção inesperada visível às três sessões.
        for cliente in (a, b, visitante):
            exigir(dados(cliente.table("animais").select("*").eq("nome", nomes[4]).execute()) == [],
                   "A tentativa do visitante deixou registro visível.")
        print("OK: visitante sem acesso aos rascunhos e sem permissão de inserção.")
        for indice, cliente in enumerate((a, b)):
            etapa = f"edição própria e bloqueio de publicação de {'AB'[indice]}"
            registros[indice] = editar_proprio(cliente, registros[indice])
            publicacao_bloqueada(cliente, registros[indice], nomes[indice + 2])
            print(f"OK: {etapa} (UPDATE e INSERT de status_publicacao recusados).")
        sucesso = True
    except Falha as erro:
        print(f"FALHA na etapa {etapa}: {erro}")
    except (KeyboardInterrupt, EOFError):
        print("Execução interrompida; iniciando limpeza dos possíveis registros temporários.")
    except Exception as erro:
        if etapa.startswith("autenticação"):
            print(f"FALHA na etapa {etapa}: {diagnostico_auth(erro)}")
        else:
            print(f"FALHA na etapa {etapa}: erro inesperado de API, autenticação, rede ou SDK. Detalhes omitidos por segurança.")
    finally:
        if escritas_iniciadas:
            for cliente, usuario_id in zip(clientes, usuarios):
                if not limpar(cliente, usuario_id, nomes):
                    limpeza_ok = False
        for cliente in clientes:
            try:
                cliente.auth.sign_out({"scope": "local"})
            except (Exception, KeyboardInterrupt):
                limpeza_ok = False
                print("Não foi possível confirmar o encerramento de uma sessão de teste; tokens não foram persistidos.")
        if escritas_iniciadas and not sucesso:
            print("Execução inconclusiva/reprovada. Confira os nomes acima no painel de adotae-testes;")
            print("registros sem proprietário correto ou invisíveis por RLS podem exigir remoção manual.")
        elif not escritas_iniciadas:
            print("Nenhuma inserção de anúncio foi tentada; não há anúncios desta execução para limpar.")
        if not limpeza_ok:
            print("Limpeza/encerramento incompleto. Remova apenas os registros TESTE-RLS desta execução em adotae-testes.")
    if sucesso and limpeza_ok:
        print("APROVADO: cenários da Data API conferidos e anúncios temporários removidos pelos respectivos donos.")
        return 0
    print("NÃO APROVADO: consulte as mensagens acima. Nenhum resultado de falha conta como validação concluída.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
