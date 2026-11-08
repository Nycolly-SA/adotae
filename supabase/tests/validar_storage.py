"""Roteiro MANUAL opt-in de adotae-testes. Não importa Flask nem carrega .env.

python supabase/tests/validar_storage.py
Consulte ../README.md antes de executar. SQL restante exige limpeza controlada.
"""

import base64
from getpass import getpass, GetPassWarning
import json
import logging
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4
import warnings

from httpx import HTTPStatusError
from postgrest import APIError
from storage3.exceptions import StorageApiError

from validar_data_api import (
    Falha, configuracao, novo_cliente, exigir, dados, criar, exigir_bloqueio,
)

BUCKET = "animal-fotos"
# WebP lossless de 1x1, sem dados pessoais. RIFF/WEBP, VP8L, 34 bytes.
WEBP = base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")
RUNS = Path(__file__).resolve().parent / "storage-runs"


def diagnostico_erro(erro):
    """Não lê mensagem, args, corpo da resposta ou URLs da exceção."""
    partes = [f"tipo={type(erro).__name__}"]
    status, codigo = None, None
    if isinstance(erro, StorageApiError):
        status, codigo = erro.status, erro.code
    elif isinstance(erro, APIError):
        codigo = erro.code
    elif isinstance(erro, HTTPStatusError):
        status = erro.response.status_code
    # Aceitar somente status HTTP e códigos conhecidos, nunca texto arbitrário.
    if type(status) is str and status in {str(n) for n in range(100, 600)}:
        status = int(status)
    if type(status) is int and 100 <= status <= 599:
        partes.append(f"status={status}")
    if type(codigo) is str and codigo in {
        "42501", "23503", "23505", "23514", "P0001", "PGRST202", "PGRST205",
        "AccessDenied", "Unauthorized", "Forbidden", "NoSuchKey", "ObjectNotFound",
        "not_found", "InvalidJWT", "TooManyRequests", "NoSuchBucket", "InternalError",
        "InvalidMimeType",
    }:
        partes.append(f"código={codigo}")
    return "; ".join(partes)


def executar_etapa(etapa, operacao):
    """Rótulos fixos do roteiro; não registra parâmetros nem resultados."""
    print(f"CHECKPOINT INÍCIO: {etapa}", flush=True)
    try:
        operacao()
    except (Exception, KeyboardInterrupt) as erro:
        raise Falha(f"etapa={etapa}; {diagnostico_erro(erro)}") from None
    print(f"CHECKPOINT OK: {etapa}", flush=True)


def entrar(cliente, rotulo):
    email = input(f"E-mail da conta {rotulo} existente em adotae-testes: ").strip()
    # Se o terminal não puder ocultar a senha, falhar em vez de exibi-la.
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        senha = getpass(f"Senha da conta {rotulo} (oculta): ")
    try:
        resposta = cliente.auth.sign_in_with_password({"email": email, "password": senha})
        exigir(resposta.user is not None and resposta.session is not None, "Login sem sessão.")
        identidade = cliente.auth.get_user(resposta.session.access_token).user
        exigir(identidade is not None and identidade.id == resposta.user.id
               and bool(identidade.email_confirmed_at), "Identidade ou e-mail não confirmado.")
        return str(UUID(identidade.id))
    finally:
        del email, senha


def salvar(arquivo, manifesto):
    """Somente identificadores operacionais, nunca respostas de Auth/Storage."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(arquivo)


def conferir_negacao_storage(erro, *, oculto=False):
    # HTTP 400 é usado por algumas versões para AccessDenied/RLS.
    negado = str(erro.status) in {"400", "403"} and erro.code in {
        "AccessDenied", "Unauthorized", "Forbidden", "42501",
    }
    ausente = oculto and str(erro.status) in {"400", "404"} and erro.code in {
        "NoSuchKey", "ObjectNotFound", "not_found",
    }
    exigir(negado or ausente, "Storage falhou sem comprovar o bloqueio esperado.")


def bloqueio_storage(operacao, *, oculto=False, vazio=False):
    try:
        resposta = operacao()
    except StorageApiError as erro:
        conferir_negacao_storage(erro, oculto=oculto)
        return
    exigir(vazio and resposta == [], "Storage autorizou uma operação proibida.")


def remocao_negada(operacao):
    bloqueio_storage(operacao, vazio=True)


def baixar_sem_cache(bucket, caminho):
    """Mesmo GET e sessão do SDK; nonce novo solicita bypass da CDN.

    Não prova sozinho que a implantação respeitou o bypass: correlacionar logs.
    download() retorna só bytes e não expõe os cabeçalhos da resposta.
    """
    return bucket.download(caminho, query_params={"cacheNonce": uuid4().hex})


def conferir_leitura_revogada(bucket, caminho, etapa):
    executar_etapa(etapa, lambda: bloqueio_storage(
        lambda: baixar_sem_cache(bucket, caminho), oculto=True))

    # Só a URL já utilizada pode gerar ressalva, após o bloqueio com nonce.
    try:
        resposta = bucket.download(caminho)
    except StorageApiError as erro:
        conferir_negacao_storage(erro, oculto=True)
        return False
    exigir(isinstance(resposta, bytes), "Download habitual retornou resultado inesperado.")
    print(f"AVISO: {etapa}; URL previamente utilizada entregou bytes: "
          "possível acesso residual por cache; origem não comprovada; "
          "não caracteriza falha RLS; revogação imediata não comprovada.")
    return True


def enviar(bucket, caminho, *, sobrescrever=False):
    return bucket.upload(caminho, WEBP, {
        "content-type": "image/webp", "upsert": "true" if sobrescrever else "false",
        "cache-control": "0",
    })


def confirmar(cliente, foto_id):
    cliente.rpc("confirmar_foto", {"p_foto_id": foto_id}).execute()


def fotos(cliente, animal_id):
    return dados(cliente.table("animal_fotos").select("*").eq("animal_id", animal_id).execute())


def conferir(cliente, animal_id, esperado):
    exigir(fotos(cliente, animal_id) == [esperado], "A reserva mudou ou desapareceu inesperadamente.")


def marcar_remocao(cliente, animal_id, foto_id):
    registros = dados(cliente.table("animal_fotos").update({"estado": "removendo"}).eq(
        "animal_id", animal_id).eq("id", foto_id).execute())
    exigir(len(registros) == 1 and registros[0]["id"] == foto_id
           and registros[0]["estado"] == "removendo", "Solicitação de remoção não confirmada.")
    return registros[0]


def limpar_storage(cliente, manifesto, arquivo):
    """Recupera respostas perdidas por nome exato; não apaga registros SQL.

    Limita a remoção à posição 1 do animal exclusivo desta execução. Caminhos
    negativos eventualmente aceitos ficam para inspeção manual, nunca por prefixo.
    """
    animais = dados(cliente.table("animais").select("*").eq(
        "usuario_id", manifesto["usuario_a"]).eq("nome", manifesto["nome"]).execute())
    exigir(len(animais) <= 1, "Mais de um animal corresponde ao nome exclusivo; limpeza suspensa.")
    if not animais:
        return
    animal = animais[0]
    manifesto["animal_id"] = str(UUID(animal["id"]))
    exigir(animal["usuario_id"] == manifesto["usuario_a"], "Dono inesperado; limpeza suspensa.")
    reservas = fotos(cliente, animal["id"])
    manifesto["reservas_restantes"] = reservas
    salvar(arquivo, manifesto)
    for foto in reservas:
        exigir(foto["posicao"] == 1 and foto["animal_id"] == animal["id"],
               "Reserva inesperada; limpeza suspensa.")
        foto_id = str(UUID(foto["id"]))
        caminho = f"{manifesto['usuario_a']}/{animal['id']}/{foto_id}.webp"
        if foto["estado"] in ("pendente", "pronta"):
            marcar_remocao(cliente, animal["id"], foto_id)
        # Uma resposta perdida pode já ter removido o arquivo. [] é inconclusivo;
        # não declaramos ausência física com base apenas na falta de download.
        resultado = cliente.storage.from_(BUCKET).remove([caminho])
        exigir(isinstance(resultado, list) and all(item.get("name") == caminho for item in resultado),
               "Storage retornou exclusões inesperadas.")
        manifesto["limpeza_storage"] = "arquivo removido" if resultado else "ausência física não comprovada"
        manifesto["reservas_restantes"] = fotos(cliente, animal["id"])
        salvar(arquivo, manifesto)


def cenarios(a, b, visitante, manifesto, arquivo):
    # Preflight autenticado com filtro vazio: não lê fotografias preexistentes.
    for cliente in (a, b):
        exigir(fotos(cliente, manifesto["sonda_id"]) == [], "Sonda encontrou dados inesperados.")
    print("OK: A e B acessam animal_fotos pela Data API.")
    manifesto["escritas_iniciadas"] = True
    salvar(arquivo, manifesto)
    animal = criar(a, manifesto["usuario_a"], manifesto["nome"])
    animal_id = str(UUID(animal["id"]))
    manifesto["animal_id"] = animal_id
    salvar(arquivo, manifesto)
    reserva = dados(a.table("animal_fotos").insert({"animal_id": animal_id, "posicao": 1}).execute())
    exigir(len(reserva) == 1, "Reserva não retornou exatamente uma linha.")
    foto = reserva[0]
    foto_id = str(UUID(foto["id"]))
    exigir(foto["animal_id"] == animal_id and foto["posicao"] == 1
           and foto["estado"] == "pendente", "Reserva não nasceu pendente na posição solicitada.")
    caminho = f"{manifesto['usuario_a']}/{animal_id}/{foto_id}.webp"
    prefixo = f"{manifesto['usuario_a']}/{animal_id}"
    forjados = [
        f"{manifesto['usuario_b']}/{animal_id}/{foto_id}.webp",
        f"{manifesto['usuario_a']}/{manifesto['sonda_id']}/{foto_id}.webp",
        f"{prefixo}/{manifesto['sonda_id']}.webp",  # sem reserva
        f"{prefixo}/{foto_id}.jpg", f"{prefixo}/extra/{foto_id}.webp",
    ]
    manifesto.update(foto_id=foto_id, caminho=caminho, estado="pendente",
                     caminhos_testados=[caminho, *forjados])
    salvar(arquivo, manifesto)  # antes de qualquer tentativa de upload
    sa, sb, sv = [cliente.storage.from_(BUCKET) for cliente in (a, b, visitante)]
    for forjado in forjados:
        bloqueio_storage(lambda: enviar(sa, forjado))
        conferir(a, animal_id, foto)
    for ator in (b, visitante):
        exigir_bloqueio(lambda: ator.table("animal_fotos").select("*").eq("id", foto_id).execute())
        exigir_bloqueio(lambda: ator.rpc("confirmar_foto", {"p_foto_id": foto_id}).execute(), vazio=False)
    for storage in (sb, sv):
        bloqueio_storage(lambda: enviar(storage, caminho))
    bloqueio_storage(lambda: sa.create_signed_upload_url(caminho))
    bloqueio_storage(lambda: baixar_sem_cache(sa, caminho), oculto=True)
    enviar(sa, caminho)
    manifesto["upload_retornou"] = True
    salvar(arquivo, manifesto)
    # O objeto pendente deve continuar ilegível, mesmo para seu proprietário.
    bloqueio_storage(lambda: baixar_sem_cache(sa, caminho), oculto=True)
    exigir_bloqueio(lambda: a.table("animal_fotos").update({"estado": "pronta"}).eq(
        "id", foto_id).execute(), vazio=False)
    confirmar(a, foto_id)
    pronto = fotos(a, animal_id)
    exigir(len(pronto) == 1 and pronto[0] == {**foto, "estado": "pronta"},
           "RPC não preservou os campos da reserva ao confirmar.")
    foto = pronto[0]
    manifesto["estado"] = "pronta"
    salvar(arquivo, manifesto)
    executar_etapa("segunda_rpc", lambda: confirmar(a, foto_id))
    executar_etapa("conferencia_reserva", lambda: conferir(a, animal_id, foto))
    executar_etapa("download_proprio", lambda: exigir(
        baixar_sem_cache(sa, caminho) == WEBP and sa.download(caminho) == WEBP,
        "Download próprio diverge do WebP enviado."))
    print("OK: reserva, upload, confirmação idempotente e download de A.")

    def intacta():
        conferir(a, animal_id, foto)
        exigir(baixar_sem_cache(sa, caminho) == WEBP, "Uma tentativa proibida alterou o objeto.")

    for storage in (sb, sv):
        bloqueio_storage(lambda: baixar_sem_cache(storage, caminho), oculto=True)
        bloqueio_storage(lambda: storage.create_signed_url(caminho, 30), oculto=True)
        remocao_negada(lambda: storage.remove([caminho]))
        intacta()
    for operacao in (
        lambda: sa.create_signed_url(caminho, 30),
        lambda: sa.create_signed_upload_url(caminho),
        lambda: enviar(sa, caminho, sobrescrever=True),
        lambda: sa.update(caminho, WEBP, {"content-type": "image/webp"}),
        lambda: sa.copy(caminho, forjados[2]),
        lambda: sa.move(caminho, forjados[2]),
    ):
        bloqueio_storage(operacao, oculto=True)
        intacta()
    # Listagem pode retornar [] por RLS; qualquer entrada é falha.
    bloqueio_storage(lambda: sa.list(prefixo, {"limit": 10}), vazio=True)
    remocao_negada(lambda: sa.remove([caminho]))
    intacta()
    exigir_bloqueio(lambda: a.table("animal_fotos").delete().eq("id", foto_id).execute(), vazio=False)
    exigir_bloqueio(lambda: b.table("animal_fotos").update({"estado": "removendo"}).eq(
        "id", foto_id).execute())
    intacta()
    print("OK: isolamento, caminhos forjados, assinaturas e operações proibidas.")
    foto = marcar_remocao(a, animal_id, foto_id)
    conferir(a, animal_id, foto)
    manifesto["estado"] = "removendo"
    salvar(arquivo, manifesto)
    exigir_bloqueio(lambda: a.rpc("confirmar_foto", {"p_foto_id": foto_id}).execute(), vazio=False)
    ressalva_cache = conferir_leitura_revogada(sa, caminho, "download_removendo_sem_cache")
    removidos = sa.remove([caminho])
    exigir(isinstance(removidos, list) and len(removidos) == 1 and removidos[0].get("name") == caminho,
           "A API Storage não confirmou a exclusão do caminho exato.")
    manifesto["exclusao_confirmada"] = True
    salvar(arquivo, manifesto)
    conferir(a, animal_id, foto)
    cache_excluido = conferir_leitura_revogada(sa, caminho, "download_excluido_sem_cache")
    exigir(sa.remove([caminho]) == [], "A repetição da exclusão retornou objetos inesperados.")
    print("OK: removendo, confirmação recusada e arquivo excluído pela API Storage.")
    return ressalva_cache or cache_excluido


def main():
    logging.disable(logging.CRITICAL)
    clientes = []
    manifesto = None
    arquivo = None
    sucesso = False
    ressalva_cache = False
    encerramento_ok = True
    try:
        exigir(sys.stdin.isatty(), "Use um terminal interativo sem transcrição de credenciais.")
        url, chave, referencia = configuracao(os.environ)
        print(f"Destino solicitado: {url}; bucket privado: {BUCKET}")
        print("Confira independentemente no painel de adotae-testes a Project Reference e o bucket:")
        print("public=false; file_size_limit=512000; allowed_mime_types=[image/webp].")
        exigir(input("Digite adotae-testes <Project Reference> conferida no painel: ").strip()
               == f"adotae-testes {referencia}", "Destino não confirmado; nenhuma conexão iniciada.")
        exigir(input("Para confirmar os três valores do bucket, digite bucket conferido: ").strip()
               == "bucket conferido", "Bucket não confirmado.")
        usuarios = []
        for rotulo in ("A", "B"):
            cliente = novo_cliente(url, chave)
            clientes.append(cliente)
            usuarios.append(entrar(cliente, rotulo))
        exigir(usuarios[0] != usuarios[1], "A e B devem ser contas distintas.")
        visitante = novo_cliente(url, chave)
        exigir(visitante.auth.get_session() is None, "Visitante recebeu sessão.")
        execucao = uuid4().hex
        manifesto = {"projeto": "adotae-testes", "referencia": referencia, "execucao": execucao,
                     "nome": f"TESTE-STORAGE-{execucao}", "usuario_a": usuarios[0],
                     "usuario_b": usuarios[1], "sonda_id": str(uuid4()), "escritas_iniciadas": False}
        arquivo = RUNS / f"{execucao}.json"
        salvar(arquivo, manifesto)
        print(f"Manifesto local para limpeza: {arquivo}")
        ressalva_cache = cenarios(*clientes, visitante, manifesto, arquivo)
        sucesso = True
    except Falha as erro:
        print(f"NÃO APROVADO: {erro}")
    except (Exception, KeyboardInterrupt) as erro:
        # Nunca imprimir exceções SDK, respostas, URLs assinadas ou tracebacks.
        print(f"NÃO APROVADO: operação interrompida ou resultado inesperado; {diagnostico_erro(erro)}.")
    finally:
        if manifesto and manifesto["escritas_iniciadas"]:
            if not manifesto.get("exclusao_confirmada"):
                try:
                    limpar_storage(clientes[0], manifesto, arquivo)
                except (Exception, KeyboardInterrupt):
                    print("Limpeza Storage inconclusiva; consulte os caminhos exatos no manifesto.")
            print("LIMPEZA SQL PENDENTE: conferir o manifesto e remover reservas antes do animal, manualmente.")
            print("Caminhos negativos eventualmente aceitos exigem inspeção manual; não excluir por prefixo.")
        for cliente in clientes:
            try:
                cliente.auth.sign_out({"scope": "local"})
            except (Exception, KeyboardInterrupt):
                encerramento_ok = False
                print("Encerramento de sessão não confirmado; tokens não foram persistidos.")
    if sucesso and encerramento_ok:
        resultado = "APROVADO COM RESSALVA" if ressalva_cache else "APROVADO"
        print(f"{resultado}: cenários Storage; limpeza SQL continua pendente. Não valida o projeto principal.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
