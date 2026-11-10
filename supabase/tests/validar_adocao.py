"""Diagnóstico temporário MANUAL. Não importa Flask, não carrega .env.

Execute em terminal conforme supabase/README.md. Não há rollback nem retry.
Snapshots e bytes permanecem exclusivamente em memória, sem dados em arquivo.
"""

from datetime import datetime
import logging
import os
import sys
from uuid import UUID

from validar_data_api import (
    Falha, configuracao, novo_cliente, exigir, dados, consultar, exigir_bloqueio,
)
from validar_storage import (
    BUCKET, entrar, fotos, baixar_sem_cache, bloqueio_storage, diagnostico_erro,
)


def rpc(cliente, animal_id):
    return cliente.rpc("marcar_adotado", {"p_animal_id": animal_id}).execute()


def recusada(rotulo, operacao, conferir):
    # Mesmo se houver sucesso indevido/timeout, conferir os registros conhecidos.
    try:
        exigir_bloqueio(operacao, vazio=False)
    finally:
        conferir()
    print(f"OK: {rotulo}; registros e fotografias preservados.")


def snapshot(cliente, ids, usuario):
    registros, reservas, arquivos = {}, {}, {}
    for animal_id in ids:
        linhas = consultar(cliente, animal_id)
        exigir(len(linhas) == 1 and linhas[0]["usuario_id"] == usuario,
               "Anúncio ausente ou não pertence a A. Crie fixtures descartáveis pela interface.")
        registros[animal_id] = linhas[0]
        reservas[animal_id] = sorted(fotos(cliente, animal_id), key=lambda f: f["id"])
        for foto in reservas[animal_id]:
            if foto["estado"] == "pronta":
                caminho = f"{usuario}/{animal_id}/{str(UUID(foto['id']))}.webp"
                conteudo = baixar_sem_cache(cliente.storage.from_(BUCKET), caminho)
                exigir(isinstance(conteudo, bytes) and bool(conteudo), "Fotografia sem bytes válidos.")
                arquivos[caminho] = conteudo
    return registros, reservas, arquivos


def conferir_adotado(antes, depois):
    exigir(depois.get("status_adocao") == "adotado", "Adoção não confirmada.")
    ignorar = {"status_adocao", "atualizado_em"}
    exigir({k: v for k, v in antes.items() if k not in ignorar}
           == {k: v for k, v in depois.items() if k not in ignorar},
           "Outros dados do anúncio foram alterados.")
    exigir(datetime.fromisoformat(depois["atualizado_em"]) > datetime.fromisoformat(antes["atualizado_em"]),
           "Data de atualização não avançou.")


def adotar_autorizado(a, animal_id):
    print("Etapas 1–5 concluídas. A adoção a seguir é irreversível pela interface.")
    print("Sem a frase exata, as etapas 6–12 NÃO serão executadas.")
    if input(f"Para autorizar uma única adoção, digite ADOTAR {animal_id}: ").strip() != f"ADOTAR {animal_id}":
        return False
    print("INÍCIO: adoção autorizada. Em falha/timeout, não repita: o resultado pode ser incerto.", flush=True)
    rpc(a, animal_id)  # sem retry, uma chamada positiva; a repetição negativa vem na etapa 12
    return True


def cenarios(a, b, visitante, usuario, publicado, rascunho):
    ids = (publicado, rascunho)
    antes = snapshot(a, ids, usuario)
    registro = antes[0][publicado]
    exigir(registro["status_publicacao"] == "publicado"
           and registro["status_adocao"] == "disponivel" and registro["oculto_admin"] is False,
           "Fixture deve estar publicada, disponível e visível. Crie outra pela interface.")
    exigir(antes[0][rascunho]["status_publicacao"] == "rascunho"
           and antes[0][rascunho]["status_adocao"] == "disponivel",
           "Informe um rascunho descartável disponível de A, criado pela interface.")
    prontas = [f for f in antes[1][publicado] if f["estado"] == "pronta"]
    exigir(bool(prontas), "Falta fotografia pronta. Prepare o anúncio pela interface antes do teste.")

    def conferir_antes():
        exigir(snapshot(a, ids, usuario) == antes, "Registros ou fotografias mudaram. Interrompa e investigue.")

    # Controles positivos: o acesso era possível antes da adoção. Só colunas concedidas a anon.
    catalogo = lambda: dados(visitante.table("animais").select("id").eq("id", publicado).eq(
        "status_publicacao", "publicado").eq("status_adocao", "disponivel").eq("oculto_admin", False).execute())
    detalhes = lambda: dados(visitante.table("animais").select(
        "id,nome,descricao,whatsapp,email_contato").eq("id", publicado).execute())
    metadados = lambda: dados(visitante.table("animal_fotos").select(
        "id,animal_id,estado").eq("animal_id", publicado).execute())
    exigir(catalogo() == [{"id": publicado}] and len(detalhes()) == 1
           and len(metadados()) == len(prontas), "Controle público anterior à adoção falhou.")
    caminhos = [f"{usuario}/{publicado}/{str(UUID(f['id']))}.webp" for f in prontas]
    publico = visitante.storage.from_(BUCKET)
    for caminho in caminhos:
        assinatura = publico.create_signed_url(caminho, 60)
        exigir(isinstance(assinatura, dict) and bool(assinatura.get("signedURL")),
               "Controle positivo da assinatura pública falhou.")
        del assinatura  # não imprimir, persistir nem consumir a URL

    recusada("1 visitante sem EXECUTE", lambda: rpc(visitante, publicado), conferir_antes)
    recusada("2 B sem titularidade", lambda: rpc(b, publicado), conferir_antes)
    recusada("3 rascunho de A", lambda: rpc(a, rascunho), conferir_antes)
    recusada("4 UPDATE direto de A", lambda: a.table("animais").update(
        {"status_adocao": "adotado"}).eq("id", publicado).execute(), conferir_antes)
    conferir_antes()
    print("OK: 5 nenhuma alteração observada nos dois anúncios, reservas e bytes prontos.")
    if not adotar_autorizado(a, publicado):
        print("PARCIAL: etapas 1–5 aprovadas; etapas 6–12 NÃO EXECUTADAS.")
        return 2

    depois = snapshot(a, ids, usuario)
    conferir_adotado(registro, depois[0][publicado])
    exigir(antes[0][rascunho] == depois[0][rascunho] and antes[1:] == depois[1:],
           "Rascunho, reservas ou bytes das fotografias foram alterados.")
    print("OK: 6–8 adoção confirmada; demais campos, rascunho, reservas e bytes preservados.")
    exigir(catalogo() == [], "Anúncio ainda aparece no catálogo anônimo.")
    print("OK: 9 catálogo anônimo sem o anúncio.")
    exigir(detalhes() == [] and metadados() == [], "Detalhes ou reservas ainda são públicos.")
    print("OK: 10 detalhes e metadados públicos inacessíveis pela Data API.")
    for caminho in caminhos:
        bloqueio_storage(lambda: publico.create_signed_url(caminho, 60), oculto=True)
    print("OK: 11 novas assinaturas públicas recusadas para todas as fotos prontas.")

    def conferir_depois():
        exigir(snapshot(a, ids, usuario) == depois, "Nova alteração após a adoção.")

    recusada("12 segunda RPC do proprietário", lambda: rpc(a, publicado), conferir_depois)
    print("APROVADO: etapas 1–12 pela Data API/Storage. Não valida a interface Flask.")
    return 0


def main():
    anterior_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    clientes = []
    resultado = 1
    try:
        exigir(sys.stdin.isatty(), "Use terminal interativo sem transcrição de credenciais.")
        url, chave, referencia = configuracao(os.environ)
        print(f"Confira no painel: adotae-testes; Project Reference {referencia}.")
        exigir(input("Autorizar login, leituras, assinaturas e tentativas negativas: digite "
                     "AUTORIZAR adotae-testes <Project Reference>: ").strip()
               == f"AUTORIZAR adotae-testes {referencia}", "Operações remotas não autorizadas.")
        exigir(input("Confirma contas fictícias comuns e DOIS anúncios descartáveis, sem uso compartilhado? "
                     "Digite FIXTURES DESCARTAVEIS: ").strip() == "FIXTURES DESCARTAVEIS",
               "Prepare contas e anúncios descartáveis pela interface antes do teste.")
        publicado = str(UUID(input("UUID exato do anúncio publicado descartável de A: ").strip()))
        rascunho = str(UUID(input("UUID exato do rascunho descartável de A: ").strip()))
        exigir(publicado != rascunho, "Informe dois anúncios diferentes.")
        # Nunca reutilizar a sessão autenticada no cliente visitante.
        visitante = novo_cliente(url, chave)
        for _ in range(2):
            clientes.append(novo_cliente(url, chave))
        a, b = clientes
        usuario = entrar(a, "A")
        exigir(entrar(b, "B") != usuario, "A e B devem ser contas distintas.")
        resultado = cenarios(a, b, visitante, usuario, publicado, rascunho)
    except (Exception, KeyboardInterrupt) as erro:
        # Nunca imprimir mensagens SDK, dados pessoais, credenciais ou URLs.
        print(f"NÃO APROVADO: {diagnostico_erro(erro)}.")
        if isinstance(erro, Falha):
            print(str(erro))  # apenas mensagens controladas deste roteiro/helpers
        print("Não há reversão nem limpeza automática. Se houve envio de adoção, confira como A antes de repetir.")
    finally:
        for cliente in clientes:
            try:
                cliente.auth.sign_out({"scope": "local"})
            except (Exception, KeyboardInterrupt):
                resultado = 1
                print("Encerramento de sessão não confirmado; tokens não persistidos.")
        logging.disable(anterior_logging)
    print("PENDENTE FLASK: ação de adoção, autorização/CSRF, mensagens e atualização das páginas após adoção.")
    return resultado


if __name__ == "__main__":
    sys.exit(main())
