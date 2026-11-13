"""Aplicação demonstrativa do projeto acadêmico Adotaê."""

import os
from datetime import datetime
import re
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlsplit
from uuid import UUID

from dotenv import load_dotenv
from email_validator import validate_email
from flask import Flask, Response, abort, flash, g, redirect, render_template, render_template_string, request, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from httpx import HTTPError
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError
from supabase import AuthApiError, AuthError, ClientOptions, SupabaseException, create_client
from supabase_auth.types import SignUpWithEmailAndPasswordCredentials

from app.forms import AnimalForm, CadastroForm, FotoForm, LoginForm
from app.auth import INDISPONIVEL, novo_cliente, registrar_auth
from app.fotos import FotoInvalida, criar_miniatura, processar_foto

load_dotenv(Path(__file__).with_name(".env"))

app = Flask(
    __name__,
    template_folder="app/templates",
    static_folder="app/static",
)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY"),
    SUPABASE_URL=os.getenv("SUPABASE_URL", ""),
    SUPABASE_PUBLISHABLE_KEY=os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # HTTP só mediante opção explícita para desenvolvimento; Vercel sempre HTTPS.
    SESSION_COOKIE_SECURE=bool(os.getenv("VERCEL")) or os.getenv("ADOTAE_LOCAL_HTTP") != "true",
)


@app.before_request
def verificar_configuracao_csrf():
    if request.endpoint in ("anunciar", "adicionar_foto", "editar_anuncio"):
        # Uma imagem de até 3 MiB mais campos e envelope multipart.
        request.max_content_length = 4 * 1024 * 1024
    if request.endpoint == "adicionar_foto" and not app.secret_key:
        return render_template("foto.html", mensagem=INDISPONIVEL), 503
    if request.endpoint in ("login", "minha_conta", "logout", "anunciar", "editar_anuncio", "publicar_anuncio", "adotar_anuncio", "remover_anuncio") and not app.secret_key:
        return render_template("login.html", indisponivel=True,
                               mensagem="Acesso à conta indisponível no momento. Tente novamente mais tarde."), 503
    if request.endpoint == "cadastro" and not app.secret_key:
        return render_template("cadastro.html", indisponivel=True,
                               mensagem="Cadastro indisponível no momento. Tente novamente mais tarde."), 503


registrar_auth(app)
CSRFProtect(app)


@app.errorhandler(413)
def arquivo_grande(error):
    if request.endpoint == "editar_anuncio":
        return render_template("anunciar.html", edicao=True, indisponivel=True,
                               animal_id=request.view_args["animal_id"], mensagem=(
                                   "O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB. "
                                   "Recarregue a edição para conferir o rascunho antes de enviar.")), 413
    if request.endpoint == "adicionar_foto":
        return render_template("foto.html", mensagem=(
            "O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB. "
            "Recarregue a página para conferir o rascunho antes de enviar.")), 413
    return render_template("anunciar.html", form=AnimalForm(OPCOES_FILTROS, formdata=None),
                           mensagem="O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB e preencha o formulário novamente."), 413


@app.errorhandler(CSRFError)
def erro_csrf(error):
    if request.endpoint == "remover_anuncio":
        return render_template_string("""
            {% extends "base.html" %}
            {% block title %}Exclusão recusada — Adotaê{% endblock %}
            {% block content %}
            <section class="container registration">
                <div class="registration-card">
                    <h1>Exclusão recusada</h1>
                    <p class="registration-message" role="alert">A exclusão foi recusada por CSRF inválido ou expirado. Recarregue sua conta e tente novamente.</p>
                    <a class="text-link" href="{{ url_for('minha_conta') }}">Voltar à minha conta</a>
                </div>
            </section>
            {% endblock %}
        """), 400
    if request.endpoint == "adotar_anuncio":
        return render_template_string("""
            {% extends "base.html" %}
            {% block title %}Confirmação da adoção — Adotaê{% endblock %}
            {% block content %}
            <section class="container registration" aria-labelledby="adocao-titulo">
                <div class="registration-card">
                    <h1 id="adocao-titulo">Confirmação da adoção recusada</h1>
                    <p class="registration-message" role="alert">A confirmação da adoção foi recusada por CSRF inválido ou expirado. Recarregue sua conta e tente novamente.</p>
                    <a class="text-link" href="{{ url_for('minha_conta') }}">Voltar à minha conta</a>
                </div>
            </section>
            {% endblock %}
        """), 400
    if request.endpoint == "adicionar_foto":
        # Não oferecer upload sem consultar novamente propriedade e reservas.
        return render_template("foto.html", mensagem=(
            "Não foi possível validar o formulário. Recarregue a página e tente novamente.")), 400
    if request.endpoint == "editar_anuncio":
        return render_template("anunciar.html", indisponivel=True,
                               edicao=True, animal_id=request.view_args["animal_id"],
                               mensagem="Não foi possível validar o formulário. Recarregue a página e tente novamente."), 400
    if request.endpoint == "anunciar":
        return render_template("anunciar.html", indisponivel=True,
                               mensagem="Não foi possível validar o formulário. Recarregue a página e tente novamente."), 400
    if request.endpoint != "cadastro":
        return render_template("login.html", form=LoginForm(formdata=None),
                               mensagem="Não foi possível validar o formulário. Recarregue a página e tente novamente."), 400
    return render_template("cadastro.html", form=CadastroForm(formdata=None),
                           mensagem="Não foi possível validar o formulário. Recarregue a página e tente novamente."), 400


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    form = CadastroForm()
    if request.method == "GET":
        return render_template("cadastro.html", form=form)
    if not form.validate_on_submit():
        return render_template("cadastro.html", form=form), 400

    confirmacao = ("Se o cadastro foi realizado, você receberá um e-mail com um link de "
                   "confirmação. Confira sua caixa de entrada e também a pasta de spam.")
    indisponivel = "Cadastro indisponível no momento. Tente novamente mais tarde."
    if not app.config["SUPABASE_URL"] or not app.config["SUPABASE_PUBLISHABLE_KEY"]:
        return render_template("cadastro.html", form=form, mensagem=indisponivel), 503
    try:
        # Nunca reutilizar este cliente: o SDK pode guardar estado de autenticação.
        supabase = create_client(
            app.config["SUPABASE_URL"], app.config["SUPABASE_PUBLISHABLE_KEY"],
            options=ClientOptions(persist_session=False, auto_refresh_token=False),
        )
        # A validação acima garante strings, mas não estreita os tipos do WTForms.
        credenciais: SignUpWithEmailAndPasswordCredentials = {
            "email": cast(str, form.email.data),
            "password": cast(str, form.senha.data),
            "options": {"data": {"nome": cast(str, form.nome.data)}},
        }
        supabase.auth.sign_up(credenciais)
    except AuthApiError as error:
        if error.code in ("user_already_exists", "email_exists"):
            return render_template("cadastro.html", confirmacao=confirmacao)
        if error.status == 429:
            return render_template("cadastro.html", form=form,
                                   mensagem="Muitas tentativas de cadastro. Aguarde alguns minutos e tente novamente."), 429
        if error.code == "weak_password":
            return render_template("cadastro.html", form=form,
                                   mensagem="Escolha uma senha mais forte, com pelo menos 8 caracteres."), 400
        return render_template("cadastro.html", form=form, mensagem=indisponivel), 503
    except (AuthError, HTTPError, SupabaseException, ValueError):
        return render_template("cadastro.html", form=form, mensagem=indisponivel), 503
    # Não guardar nem retornar usuário, sessão ou tokens recebidos do provedor.
    return render_template("cadastro.html", confirmacao=confirmacao)

# Dados fictícios mantidos apenas para compatibilidade da rota de detalhes demonstrativa.
ANIMAIS = [
    {
        "id": 1,
        "nome": "Bento",
        "distrito": "São João da Barra (sede)",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "2 anos",
        "porte": "Médio",
        "descricao": "Companheiro de caminhadas e de tardes preguiçosas no sofá.",
        "imagem": "bento.svg",
        "imagem_alt": "Ilustração de Bento, um cachorro caramelo de orelhas caídas.",
    },
    {
        "id": 2,
        "nome": "Luna",
        "distrito": "Atafona",
        "especie": "Gato",
        "sexo": "Fêmea",
        "idade": "1 ano",
        "porte": "Pequeno",
        "descricao": "Curiosa, tranquila e especialista em encontrar um cantinho de sol.",
        "imagem": "luna.svg",
        "imagem_alt": "Ilustração de Luna, uma gata cinza de olhos verdes.",
    },
    {
        "id": 3,
        "nome": "Paçoca",
        "distrito": "Grussaí",
        "especie": "Cachorro",
        "sexo": "Macho",
        "idade": "6 meses",
        "porte": "Pequeno",
        "descricao": "Um filhote cheio de energia, sempre pronto para uma brincadeira.",
        "imagem": "pacoca.svg",
        "imagem_alt": "Ilustração de Paçoca, um cachorro claro com uma mancha marrom.",
    },
    {
        "id": 4,
        "nome": "Amora",
        "distrito": "Barcelos",
        "especie": "Gato",
        "sexo": "Fêmea",
        "idade": "3 anos",
        "porte": "Pequeno",
        "descricao": "Carinhosa e observadora. Gosta de companhia e de uma boa soneca.",
        "imagem": "amora.svg",
        "imagem_alt": "Ilustração de Amora, uma gata preta de olhos amarelos.",
    },
]

OPCOES_FILTROS = {
    "tipo": ("Cachorro", "Gato", "Ave", "Pequeno mamífero", "Outro animal doméstico"),
    "porte": ("Pequeno", "Médio", "Grande"),
    "distrito": ("São João da Barra (sede)", "Atafona", "Grussaí", "Cajueiro", "Pipeiras", "Barcelos"),
}


@app.route("/anuncios/<uuid:animal_id>/editar", methods=["GET", "POST"])
def editar_anuncio(animal_id):
    contexto = dict(edicao=True, animal_id=animal_id)
    if g.auth_indisponivel:
        return render_template("anunciar.html", **contexto, indisponivel=True, mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    form = AnimalForm(OPCOES_FILTROS)
    campos = tuple(form.dados_insert())
    mensagem = "Não foi possível carregar o rascunho agora. Tente novamente mais tarde."
    try:
        registros = (g.auth_cliente.table("animais")
                     .select(",".join(("id", "usuario_id", "status_publicacao", "status_adocao", "removido_em", *campos)))
                     .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").eq("status_adocao", "disponivel")
                     .is_("removido_em", "null").execute().data)
        if registros == []:
            abort(404)
        if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
            raise ValueError("Resposta de leitura inválida")
        animal = registros[0]
        if any(campo not in animal for campo in ("id", "usuario_id", "status_publicacao", "status_adocao", "removido_em")):
            raise ValueError("Identificação de rascunho ausente")
        if (animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id)
                or animal.get("status_publicacao") != "rascunho" or animal.get("status_adocao") != "disponivel"
                or animal["removido_em"] is not None):
            abort(404)
        opcionais = ("especie_outro", "bairro", "email_contato", "whatsapp")
        if any(campo not in animal or (
                not isinstance(animal[campo], str)
                and not (campo in opcionais and animal[campo] is None)) for campo in campos):
            raise ValueError("Dados de rascunho inválidos")
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", **contexto, indisponivel=True, mensagem=mensagem), 503
    try:
        contexto["reservas"] = consultar_reservas(str(animal_id))
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", **contexto, indisponivel=True, mensagem=(
            "Não foi possível consultar as fotografias do rascunho. Recarregue a edição antes de enviar.")), 503
    if request.method == "GET":
        form = AnimalForm(OPCOES_FILTROS, formdata=None, data=animal)
        return render_template("anunciar.html", **contexto, form=form)
    if not form.validate_on_submit():
        return render_template("anunciar.html", **contexto, form=form), 400
    foto = None
    try:
        arquivos = request.files.getlist("foto")
        if any(campo != "foto" for campo in request.files) or request.form.getlist("foto"):
            raise FotoInvalida("Envie a fotografia somente pelo campo indicado.")
        if len(arquivos) > 1:
            raise FotoInvalida("Envie apenas uma fotografia nesta etapa.")
        if arquivos and not form.foto.data and arquivos[0].stream.read(1):
            raise FotoInvalida("Selecione uma fotografia válida com nome de arquivo.")
        if form.foto.data:
            if contexto["reservas"]:
                form.foto.errors.append("Já existe uma fotografia ou operação de fotografia neste rascunho. Não é possível enviar outra.")
                return render_template("anunciar.html", **contexto, form=form), 409
            foto = processar_foto(form.foto.data)
    except FotoInvalida as erro:
        form.foto.errors.append(str(erro))
        return render_template("anunciar.html", **contexto, form=form), 400
    dados = form.dados_insert()
    mensagem = ("Não foi possível confirmar a atualização do rascunho. "
                "Confira seus anúncios antes de tentar novamente.")
    try:
        # A condição também no UPDATE protege contra publicação, remoção ou
        # mudança de proprietário entre a leitura e a gravação.
        registros = (g.auth_cliente.table("animais").update(dados)
                     .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").eq("status_adocao", "disponivel").execute().data)
        if registros == []:
            return render_template("anunciar.html", **contexto, form=form, mensagem=(
                "O rascunho não pôde ser atualizado. Volte à sua conta e confira se ele ainda está disponível para edição.")), 409
        if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
            raise ValueError("Resposta de UPDATE inválida")
        atualizado = registros[0]
        if (atualizado.get("id") != str(animal_id) or atualizado.get("usuario_id") != str(g.usuario.id)
                or atualizado.get("status_publicacao") != "rascunho"
                or atualizado.get("status_adocao") != "disponivel"
                or any(campo not in atualizado or atualizado[campo] != valor for campo, valor in dados.items())):
            raise ValueError("Atualização não confirmada")
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", **contexto, form=form, mensagem=mensagem), 503
    if foto is not None:
        aviso = enviar_foto(str(animal_id), foto)
        if aviso:
            flash("Os dados foram salvos, mas a fotografia não foi confirmada. " + aviso +
                  " Pode haver uma reserva ou arquivo aguardando recuperação, ainda indisponível pela interface. "
                  "Confira o rascunho antes de tentar novamente. Não reenvie automaticamente.", "rascunho-aviso")
        else:
            flash("Os dados e a fotografia foram salvos! O anúncio continua como rascunho.", "rascunho-sucesso")
        return redirect(url_for("minha_conta"))
    flash("Rascunho atualizado com sucesso!", "rascunho-sucesso")
    return redirect(url_for("minha_conta"))


@app.get("/anuncios/<uuid:animal_id>/miniatura")
def miniatura_propria(animal_id):
    if g.auth_indisponivel:
        abort(503)
    if not g.usuario:
        abort(401)
    try:
        # O join exige propriedade mesmo quando a RLS permitir leitura pública.
        fotos = (g.auth_cliente.table("animal_fotos")
                 .select("id,animal_id,estado,animais!inner(id,usuario_id)")
                 .eq("animal_id", str(animal_id))
                 .eq("animais.usuario_id", str(g.usuario.id)).eq("estado", "pronta")
                 .order("posicao").order("id").limit(1).execute().data)
        if fotos == []:
            abort(404)
        if not isinstance(fotos, list) or len(fotos) != 1 or not isinstance(fotos[0], dict):
            raise ValueError("Resposta de fotografia inválida")
        foto = fotos[0]
        animal = foto.get("animais")
        if (foto.get("animal_id") != str(animal_id) or foto.get("estado") != "pronta"
                or not isinstance(animal, dict) or animal.get("id") != str(animal_id)
                or animal.get("usuario_id") != str(g.usuario.id)):
            abort(404)
        foto_id = str(UUID(foto["id"]))
        caminho = f"{g.usuario.id}/{animal_id}/{foto_id}.webp"
        # Download autenticado: nenhum link de Storage ou JWT vai para o HTML.
        conteudo = g.auth_cliente.storage.from_("animal-fotos").download(caminho)
        return Response(criar_miniatura(conteudo), mimetype="image/webp", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })
    except (APIError, StorageApiError, HTTPError, SupabaseException,
            ValueError, TypeError, KeyError):
        abort(503)


def consultar_publicacao_propria(animal_id):
    """Lê publicação e remoção do anúncio próprio; nunca aceita outra identidade."""
    registros = (g.auth_cliente.table("animais")
                 .select("id,usuario_id,status_publicacao,removido_em")
                 .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                 .execute().data)
    if registros == []:
        return None
    if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
        raise ValueError("Resposta de leitura inválida")
    animal = registros[0]
    if animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id):
        return None
    if (animal.get("status_publicacao") not in ("rascunho", "publicado")
            or "removido_em" not in animal):
        raise ValueError("Estado de publicação inválido")
    if animal["removido_em"] is not None:
        return "removido"
    return animal["status_publicacao"]


@app.post("/anuncios/<uuid:animal_id>/publicar")
def publicar_anuncio(animal_id):
    if g.auth_indisponivel:
        return render_template("login.html", indisponivel=True, mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    falhas = (APIError, HTTPError, SupabaseException, ValueError, TypeError)
    try:
        estado = consultar_publicacao_propria(animal_id)
    except falhas:
        flash("Não foi possível consultar o anúncio agora. Tente novamente mais tarde.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if estado is None or estado == "removido":
        # Não distinguir inexistência de falta de autorização.
        flash("Anúncio indisponível para publicação.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if estado == "publicado":
        flash("Este anúncio já está publicado.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))

    return executar_publicacao(animal_id)


def executar_publicacao(animal_id):
    """Publica uma única vez e informa o resultado confirmado da leitura própria."""
    falhas = (APIError, HTTPError, SupabaseException, ValueError, TypeError)
    codigo = None
    try:
        # O PostgreSQL decide autorização e pré-requisitos. Nunca repetir a RPC:
        # uma resposta perdida pode ocorrer depois do COMMIT.
        g.auth_cliente.rpc("publicar_anuncio", {"p_animal_id": str(animal_id)}).execute()
    except APIError as erro:
        codigo = erro.code
    except falhas:
        pass

    try:
        # O retorno void (ou qualquer payload) da RPC não comprova publicação.
        estado = consultar_publicacao_propria(animal_id)
    except falhas:
        estado = None
    if estado == "publicado":
        flash("Anúncio publicado com sucesso!", "rascunho-sucesso")
    elif estado == "removido":
        flash("A publicação não foi confirmada porque o anúncio foi excluído. Confira ‘Meus anúncios’.",
              "rascunho-aviso")
    elif estado is None:
        flash("O resultado da publicação é incerto. Confira ‘Meus anúncios’ antes de tentar novamente.",
              "rascunho-aviso")
    elif codigo == "42501":
        flash("Não foi possível publicar. Confira se o rascunho tem pelo menos um contato e uma "
              "fotografia pronta e se está disponível para publicação. Confira ‘Meus anúncios’.",
              "rascunho-aviso")
    elif codigo == "55P03":
        flash("O anúncio está sendo utilizado em outra operação. Aguarde e confira ‘Meus anúncios’ "
              "antes de tentar novamente.", "rascunho-aviso")
    else:
        flash("A publicação não foi confirmada. Na consulta, o anúncio continua como rascunho. "
              "Confira ‘Meus anúncios’ antes de tentar novamente.", "rascunho-aviso")
    return redirect(url_for("minha_conta"))


def consultar_adocao_propria(animal_id):
    """Valida identidade e estados usando somente a sessão do proprietário."""
    registros = (g.auth_cliente.table("animais")
                 .select("id,usuario_id,status_publicacao,status_adocao")
                 .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                 .execute().data)
    if registros == []:
        return None
    if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
        raise ValueError("Resposta de leitura inválida")
    animal = registros[0]
    if animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id):
        return None
    if (animal.get("status_publicacao") not in ("rascunho", "publicado")
            or animal.get("status_adocao") not in ("disponivel", "adotado")):
        raise ValueError("Estado do anúncio inválido")
    return animal


@app.post("/anuncios/<uuid:animal_id>/adotar")
def adotar_anuncio(animal_id):
    if g.auth_indisponivel:
        return render_template("login.html", indisponivel=True, mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    if request.form.getlist("confirmar_adocao") != ["sim"]:
        flash("É necessária a confirmação explícita da adoção. Confira o aviso no cartão do animal.",
              "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    falhas = (APIError, HTTPError, SupabaseException, ValueError, TypeError)
    try:
        animal = consultar_adocao_propria(animal_id)
    except falhas:
        flash("Não foi possível consultar o anúncio agora. Tente novamente mais tarde.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if animal is None or animal["status_publicacao"] != "publicado":
        flash("Anúncio indisponível para adoção.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if animal["status_adocao"] == "adotado":
        flash("Este anúncio já está adotado.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))

    codigo = None
    try:
        # A RPC é a autoridade final. Uma resposta perdida pode ocorrer após o
        # COMMIT: nunca repetir automaticamente, nem confiar no retorno void.
        g.auth_cliente.rpc("marcar_adotado", {"p_animal_id": str(animal_id)}).execute()
    except APIError as erro:
        codigo = erro.code
    except falhas:
        pass
    try:
        animal = consultar_adocao_propria(animal_id)
    except falhas:
        animal = None
    if animal is not None and animal["status_adocao"] == "adotado":
        flash("Adoção confirmada com sucesso!", "rascunho-sucesso")
    elif animal is None:
        flash("O resultado da adoção é incerto. Confira ‘Meus anúncios’ antes de tentar novamente.",
              "rascunho-aviso")
    elif codigo == "42501":
        flash("Não foi possível confirmar a adoção. Na consulta, o anúncio continua disponível. "
              "Confira ‘Meus anúncios’ antes de tentar novamente.", "rascunho-aviso")
    elif codigo == "55P03":
        flash("O anúncio está sendo utilizado em outra operação. Aguarde e confira ‘Meus anúncios’ "
              "antes de tentar novamente.", "rascunho-aviso")
    else:
        flash("A adoção não foi confirmada. Na consulta, o anúncio continua disponível. "
              "Confira ‘Meus anúncios’ antes de tentar novamente.", "rascunho-aviso")
    return redirect(url_for("minha_conta"))


def consultar_remocao_propria(animal_id):
    """Inclui removidos para confirmar a operação com a sessão do dono."""
    registros = (g.auth_cliente.table("animais")
                 .select("id,usuario_id,status_publicacao,status_adocao,removido_em")
                 .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                 .execute().data)
    if registros == []:
        return None
    if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
        raise ValueError("Resposta de leitura inválida")
    animal = registros[0]
    if animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id):
        return None
    if (animal.get("status_publicacao") not in ("rascunho", "publicado")
            or animal.get("status_adocao") not in ("disponivel", "adotado")
            or "removido_em" not in animal):
        raise ValueError("Estado do anúncio inválido")
    if animal["removido_em"] is not None:
        if not isinstance(animal["removido_em"], str):
            raise ValueError("Data de remoção inválida")
        data = datetime.fromisoformat(animal["removido_em"])
        if data.tzinfo is None:
            raise ValueError("Data de remoção sem fuso")
    return animal


@app.post("/anuncios/<uuid:animal_id>/remover")
def remover_anuncio(animal_id):
    if g.auth_indisponivel:
        return render_template("login.html", indisponivel=True, mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    if request.form.getlist("confirmar_remocao") != ["sim"]:
        flash("É necessária a confirmação explícita da exclusão. Confira o aviso no cartão do animal.",
              "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    falhas = (APIError, HTTPError, SupabaseException, ValueError, TypeError)
    try:
        animal = consultar_remocao_propria(animal_id)
    except falhas:
        flash("Não foi possível consultar o anúncio agora. Tente novamente mais tarde.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if animal is None:
        flash("Anúncio indisponível para exclusão.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if animal["status_adocao"] == "adotado":
        flash("Um anúncio de animal adotado não pode ser excluído.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))
    if animal["removido_em"] is not None:
        flash("Este anúncio já foi excluído.", "rascunho-aviso")
        return redirect(url_for("minha_conta"))

    codigo = None
    try:
        # Não repetir: a resposta pode se perder depois do COMMIT.
        # A RPC decide a autorização e o estado sob bloqueio no banco.
        g.auth_cliente.rpc("remover_anuncio", {"p_animal_id": str(animal_id)}).execute()
    except APIError as erro:
        codigo = erro.code
    except falhas:
        pass
    try:
        animal = consultar_remocao_propria(animal_id)
    except falhas:
        animal = None
    if animal is not None and animal["removido_em"] is not None:
        flash("Anúncio excluído com sucesso! Ele foi retirado da plataforma.", "rascunho-sucesso")
    elif animal is None:
        flash("O resultado da exclusão é incerto. Confira ‘Meus anúncios’ antes de tentar novamente.",
              "rascunho-aviso")
    elif animal["status_adocao"] == "adotado":
        flash("A exclusão não foi confirmada. Um anúncio de animal adotado não pode ser excluído.",
              "rascunho-aviso")
    elif codigo == "55P03":
        flash("O anúncio está sendo utilizado em outra operação. Aguarde e confira ‘Meus anúncios’ "
              "antes de tentar novamente.", "rascunho-aviso")
    else:
        flash("A exclusão não foi confirmada. Na consulta, o anúncio continua na sua conta. "
              "Confira ‘Meus anúncios’ antes de tentar novamente.", "rascunho-aviso")
    return redirect(url_for("minha_conta"))


def enviar_foto(animal_id, foto):
    """Reserva, envia e confirma com o cliente autenticado; retorna aviso se incerto."""
    etapa = "reserva"
    try:
        resposta = g.auth_cliente.table("animal_fotos").insert({
            "animal_id": animal_id, "posicao": 1,
        }).execute()
        reservas = resposta.data
        if not isinstance(reservas, list) or len(reservas) != 1:
            raise ValueError("Reserva não confirmada")
        reserva = reservas[0]
        if (not isinstance(reserva, dict) or reserva.get("animal_id") != animal_id
                or type(reserva.get("posicao")) is not int or reserva["posicao"] != 1
                or reserva.get("estado") != "pendente"):
            raise ValueError("Reserva inválida")
        foto_id = str(UUID(str(reserva.get("id"))))
        caminho = f"{g.usuario.id}/{animal_id}/{foto_id}.webp"
        etapa = "upload"
        g.auth_cliente.storage.from_("animal-fotos").upload(caminho, foto.conteudo, {
            "content-type": "image/webp", "upsert": "false", "cache-control": "0",
        })
        etapa = "confirmação"
        g.auth_cliente.rpc("confirmar_foto", {"p_foto_id": foto_id}).execute()
        # A RPC retorna void. Só apresentar conclusão após ler pronta;
        # removendo pode ter sido solicitado em outra sessão do usuário.
        registros = g.auth_cliente.table("animal_fotos").select(
            "id,animal_id,posicao,estado").eq("id", foto_id).execute().data
        if (not isinstance(registros, list) or len(registros) != 1
                or not isinstance(registros[0], dict) or type(registros[0].get("posicao")) is not int
                or registros[0] != dict(id=foto_id, animal_id=animal_id, posicao=1, estado="pronta")):
            raise ValueError("Fotografia pronta não confirmada")
    except (APIError, StorageApiError, HTTPError, SupabaseException, ValueError, TypeError):
        # Não apagar reserva/objeto nem reenviar: uma resposta perdida pode
        # corresponder a uma operação concluída. A posição continua ocupada.
        avisos = {
            "reserva": "Não foi possível confirmar a reserva da fotografia; o envio não foi iniciado.",
            "upload": "O envio da fotografia falhou ou não pôde ser confirmado.",
            "confirmação": "A fotografia foi enviada, mas não foi possível confirmar que está pronta.",
        }
        return avisos[etapa]
    return None


def consultar_reservas(animal_id):
    """Consulta e valida todas as reservas próprias, inclusive operações incompletas."""
    reservas = (g.auth_cliente.table("animal_fotos").select("id,animal_id,posicao,estado")
                .eq("animal_id", animal_id).execute().data)
    if not isinstance(reservas, list) or len(reservas) > 3:
        raise ValueError("Resposta de reservas inválida")
    ids, posicoes = set(), set()
    for reserva in reservas:
        if (not isinstance(reserva, dict) or not isinstance(reserva.get("id"), str)
                or reserva.get("animal_id") != animal_id
                or type(reserva.get("posicao")) is not int or reserva["posicao"] not in (1, 2, 3)
                or reserva.get("estado") not in ("pendente", "pronta", "removendo")):
            raise ValueError("Reserva inválida")
        foto_id = str(UUID(reserva["id"]))
        if foto_id in ids or reserva["posicao"] in posicoes:
            raise ValueError("Reserva duplicada")
        ids.add(foto_id)
        posicoes.add(reserva["posicao"])
    return reservas


@app.route("/anuncios/<uuid:animal_id>/foto", methods=["GET", "POST"])
def adicionar_foto(animal_id):
    if g.auth_indisponivel:
        return render_template("foto.html", mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    animal_id = str(animal_id)
    try:
        registros = (g.auth_cliente.table("animais").select("id,usuario_id,status_publicacao,status_adocao")
                     .eq("id", animal_id).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").eq("status_adocao", "disponivel").execute().data)
        if registros == []:
            abort(404)
        if (not isinstance(registros, list) or len(registros) != 1
                or not isinstance(registros[0], dict)
                or any(campo not in registros[0] for campo in ("id", "usuario_id", "status_publicacao", "status_adocao"))):
            raise ValueError("Resposta de rascunho inválida")
        animal = registros[0]
        if (animal["id"] != animal_id or animal["usuario_id"] != str(g.usuario.id)
                or animal["status_publicacao"] != "rascunho" or animal["status_adocao"] != "disponivel"):
            abort(404)
        reservas = consultar_reservas(animal_id)
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("foto.html", mensagem=(
            "Não foi possível consultar o rascunho e suas fotografias agora. Tente novamente mais tarde.")), 503
    if reservas:
        mensagens = {
            "pronta": "Já existe uma fotografia registrada neste rascunho.",
            "pendente": "Existe uma operação não concluída de fotografia. A recuperação ainda não está disponível.",
            "removendo": "A posição está ocupada por uma fotografia em remoção. A recuperação ainda não está disponível.",
        }
        return render_template("foto.html", mensagem=" ".join(
            mensagens[estado] for estado in mensagens if any(r["estado"] == estado for r in reservas)
        )), 409 if request.method == "POST" else 200
    form = FotoForm()
    if request.method == "GET":
        return render_template("foto.html", form=form, animal_id=animal_id)
    if not form.validate_on_submit():
        return render_template("foto.html", form=form, animal_id=animal_id), 400
    try:
        if len(request.files.getlist("foto")) > 1:
            raise FotoInvalida("Envie apenas uma fotografia nesta etapa.")
        if not form.foto.data:
            raise FotoInvalida("Selecione uma fotografia.")
        foto = processar_foto(form.foto.data)
    except FotoInvalida as erro:
        form.foto.errors.append(str(erro))
        return render_template("foto.html", form=form, animal_id=animal_id), 400
    # A leitura inicial não substitui UNIQUE, RLS e validações do banco:
    # outra sessão pode reservar a posição ou alterar o anúncio nesse intervalo.
    aviso = enviar_foto(animal_id, foto)
    if aviso:
        return render_template("foto.html", mensagem=aviso + (
            " Pode haver uma operação incompleta, com reserva ou arquivo aguardando recuperação, "
            "ainda indisponível pela interface. Volte à sua conta e confira o estado da fotografia. "
            "Não tente reenviar automaticamente.")), 503
    flash("Fotografia adicionada ao rascunho! O anúncio ainda não é público.", "rascunho-sucesso")
    return redirect(url_for("minha_conta"))


@app.route("/anunciar", methods=["GET", "POST"])
def anunciar():
    if g.auth_indisponivel:
        return render_template("anunciar.html", indisponivel=True, mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    form = AnimalForm(OPCOES_FILTROS)
    if request.method == "GET":
        return render_template("anunciar.html", form=form)
    if not form.validate_on_submit():
        return render_template("anunciar.html", form=form), 400
    # POSTs antigos sem ação continuam salvando rascunhos.
    acoes = request.form.getlist("acao")
    if acoes and (len(acoes) != 1 or acoes[0] not in ("rascunho", "publicar")):
        return render_template("anunciar.html", form=form,
                               mensagem="Escolha Salvar como rascunho ou Publicar agora."), 400
    publicar = acoes == ["publicar"]
    valido = True
    if publicar and not (form.email_contato.data or form.whatsapp.data):
        form.contato_ausente = True
        valido = False
    foto = None
    try:
        arquivos = request.files.getlist("foto")
        if len(arquivos) > 1:
            raise FotoInvalida("Envie apenas uma fotografia nesta etapa.")
        if publicar and not form.foto.data:
            raise FotoInvalida("Para publicar, selecione uma fotografia válida.")
        if form.foto.data:
            foto = processar_foto(form.foto.data)
    except FotoInvalida as erro:
        form.foto.errors.append(str(erro))
        valido = False
    if not valido:
        return render_template("anunciar.html", form=form), 400
    mensagem = ("Não foi possível confirmar a criação do rascunho. Confira ‘Meus anúncios’ "
                "na sua conta antes de cadastrar outro animal. Não reenvie este formulário, "
                "pois o cadastro pode ter sido concluído mesmo sem confirmação.")
    try:
        # set_session no cliente desta requisição envia o JWT validado/renovado
        # também à Data API. A chave publicável permanece no cabeçalho apikey.
        resposta = g.auth_cliente.table("animais").insert(form.dados_insert()).execute()
        registros = resposta.data
        if not isinstance(registros, list) or len(registros) != 1:
            raise ValueError("Resposta de INSERT inválida")
        animal = registros[0]
        if (not isinstance(animal, dict) or animal.get("status_publicacao") != "rascunho"
                or animal.get("usuario_id") != str(g.usuario.id)):
            raise ValueError("Rascunho não confirmado")
        UUID(str(animal.get("id")))
    except APIError:
        # Não retornar nem registrar detalhes da Data API ou credenciais.
        return render_template("anunciar.html", form=form, mensagem=mensagem), 503
    except (HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", form=form, mensagem=mensagem), 503
    if foto is not None:
        aviso = enviar_foto(animal["id"], foto)
        if aviso:
            flash("O rascunho foi criado. " + aviso +
                  " A fotografia não foi confirmada como adicionada. Pode haver uma reserva ou arquivo "
                  "aguardando recuperação, ainda indisponível pela interface. Confira ‘Meus anúncios’. "
                  "Não reenvie o formulário "
                  "para tentar adicionar a foto, pois isso criaria outro anúncio.", "rascunho-aviso")
            return redirect(url_for("minha_conta"))
        if publicar:
            return executar_publicacao(animal["id"])
        flash("Animal cadastrado como rascunho, com fotografia adicionada! O anúncio ainda não é público.",
              "rascunho-sucesso")
        return redirect(url_for("minha_conta"))
    flash("Animal cadastrado como rascunho! Você poderá adicionar fotos e solicitar "
          "a publicação posteriormente.", "rascunho-sucesso")
    return redirect(url_for("minha_conta"))


@app.get("/")
def inicio():
    return render_template("index.html")


@app.get("/animais")
def animais():
    filtros = {}
    for campo, opcoes in OPCOES_FILTROS.items():
        valor = request.args.get(campo, "")
        filtros[campo] = valor if valor in opcoes else ""

    contexto = dict(filtros=filtros, opcoes_filtros=OPCOES_FILTROS)
    try:
        # Cliente novo, sem restaurar sessão: a RLS de proprietário também
        # permite ler rascunhos, mas o catálogo deve ter sempre acesso anônimo.
        cliente = novo_cliente()
        consulta = (cliente.table("animais")
                    .select("id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito,usuario_id")
                    .eq("status_publicacao", "publicado")
                    .eq("status_adocao", "disponivel").eq("oculto_admin", False))
        for parametro, coluna in (("tipo", "especie"), ("porte", "porte"), ("distrito", "distrito")):
            if filtros[parametro]:
                consulta = consulta.eq(coluna, filtros[parametro])
        registros = consulta.order("criado_em", desc=True).execute().data
        animais_publicos = validar_catalogo(registros)
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("animais.html", **contexto, indisponivel=True), 503
    adicionar_fotos_catalogo(cliente, registros, animais_publicos)
    return render_template("animais.html", **contexto, animais=animais_publicos)


def adicionar_fotos_catalogo(cliente, registros, animais):
    """Adiciona fotos aos anúncios públicos validados; falhas preservam os dados."""
    if not animais:
        return
    try:
        reservas = (cliente.table("animal_fotos").select("id,animal_id,estado")
                    .in_("animal_id", [animal["id"] for animal in animais])
                    .eq("estado", "pronta").execute().data)
        if not isinstance(reservas, list):
            return
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return

    escolhidas = {}
    ids = {str(UUID(animal["id"])) for animal in animais}
    for reserva in reservas:
        try:
            if not isinstance(reserva, dict) or reserva.get("estado") != "pronta":
                continue
            animal_id = str(UUID(reserva["animal_id"]))
            foto_id = str(UUID(reserva["id"]))
            if animal_id in ids and (animal_id not in escolhidas or foto_id < escolhidas[animal_id]):
                escolhidas[animal_id] = foto_id
        except (ValueError, TypeError, KeyError, AttributeError):
            continue

    for registro, animal in zip(registros, animais):
        try:
            animal_id = str(UUID(animal["id"]))
            if animal_id not in escolhidas:
                continue
            dono_id = str(UUID(registro["usuario_id"]))
            caminho = f"{dono_id}/{animal_id}/{escolhidas[animal_id]}.webp"
            resposta = cliente.storage.from_("animal-fotos").create_signed_url(caminho, 60)
            url = validar_url_foto(resposta, caminho)
            if url:
                animal["foto_url"] = url
        except (APIError, HTTPError, StorageApiError, SupabaseException,
                ValueError, TypeError, KeyError, AttributeError):
            # Não registrar a resposta ou exceção: podem conter a URL assinada.
            continue


def validar_url_foto(resposta, caminho):
    if not isinstance(resposta, dict):
        return None
    url = resposta.get("signedURL")
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return None
    origem = urlsplit(app.config["SUPABASE_URL"])
    partes = urlsplit(url)
    esperado = origem.path.rstrip("/") + "/storage/v1/object/sign/animal-fotos/" + caminho
    if (partes.scheme != "https" or partes.scheme != origem.scheme
            or partes.netloc != origem.netloc or partes.username or partes.password
            or partes.path != esperado or partes.fragment
            or not re.fullmatch(r"token=[A-Za-z0-9._~-]+", partes.query)):
        return None
    return url


def validar_catalogo(registros):
    """Recusa respostas incompletas e limita os dados enviados ao template."""
    if not isinstance(registros, list):
        raise ValueError("Resposta de catálogo inválida")
    limites = dict(id=36, nome=100, especie=30, sexo=16, idade_aproximada=50,
                   porte=7, descricao=2000, distrito=30)
    animais = []
    for registro in registros:
        if not isinstance(registro, dict) or any(
            not isinstance(registro.get(campo), str)
            or not registro[campo].strip() or len(registro[campo]) > limite
            for campo, limite in limites.items()
        ):
            raise ValueError("Registro de catálogo inválido")
        UUID(registro["id"])
        if (registro["especie"] not in OPCOES_FILTROS["tipo"]
                or registro["porte"] not in OPCOES_FILTROS["porte"]
                or registro["distrito"] not in OPCOES_FILTROS["distrito"]
                or registro["sexo"] not in ("Macho", "Fêmea", "Não identificado")):
            raise ValueError("Opções de catálogo inválidas")
        animais.append({campo: registro[campo] for campo in limites})
    return animais


@app.get("/animais/<uuid:animal_id>")
def animal_detalhes_publico(animal_id):
    try:
        # Mesmo após login, nunca usar as permissões do proprietário nesta página.
        cliente = novo_cliente()
        registros = (cliente.table("animais")
                     .select("id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito,"
                             "especie_outro,bairro,email_contato,whatsapp,"
                             "status_publicacao,status_adocao,oculto_admin,usuario_id")
                     .eq("id", str(animal_id)).eq("status_publicacao", "publicado")
                     .eq("status_adocao", "disponivel").eq("oculto_admin", False)
                     .execute().data)
        if registros == []:
            abort(404)
        if not isinstance(registros, list) or len(registros) != 1:
            raise ValueError("Resposta de detalhes inválida")
        animal = validar_catalogo(registros)[0]
        registro = registros[0]
        if UUID(animal["id"]) != animal_id:
            raise ValueError("Identificação de anúncio inválida")
        if (registro.get("status_publicacao") not in ("publicado", "rascunho")
                or registro.get("status_adocao") not in ("disponivel", "adotado")
                or type(registro.get("oculto_admin")) is not bool):
            raise ValueError("Estado de anúncio inválido")
        if (registro["status_publicacao"] != "publicado"
                or registro["status_adocao"] != "disponivel" or registro["oculto_admin"]):
            abort(404)
        for campo, limite in (("especie_outro", 60), ("bairro", 100),
                              ("email_contato", 254), ("whatsapp", 15)):
            if campo not in registro:
                raise ValueError("Campo de detalhes ausente")
            valor = registro[campo]
            if valor is not None and (not isinstance(valor, str) or not valor.strip()
                                      or valor != valor.strip() or len(valor) > limite):
                raise ValueError("Campo de detalhes inválido")
            animal[campo] = valor
        if (animal["especie"] == "Outro animal doméstico") != bool(animal["especie_outro"]):
            raise ValueError("Detalhe de espécie inválido")
        if animal["whatsapp"] and not re.fullmatch(r"[1-9][0-9]{7,14}", animal["whatsapp"]):
            raise ValueError("WhatsApp inválido")
        if animal["email_contato"]:
            validate_email(animal["email_contato"], check_deliverability=False)
            animal["email_link"] = "mailto:" + quote(animal["email_contato"], safe="@")
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("animal_detalhes.html", publico=True, indisponivel=True), 503
    adicionar_fotos_catalogo(cliente, registros, [animal])
    return render_template("animal_detalhes.html", animal=animal, publico=True)


@app.get("/animais/<int:id>")
def animal_detalhes(id):
    for animal in ANIMAIS:
        if animal["id"] == id:
            return render_template("animal_detalhes.html", animal=animal)
    abort(404)


if __name__ == "__main__":
    app.run()
