"""Aplicação demonstrativa do projeto acadêmico Adotaê."""

import os
from pathlib import Path
from typing import cast
from uuid import UUID

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from httpx import HTTPError
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError
from supabase import AuthApiError, AuthError, ClientOptions, SupabaseException, create_client
from supabase_auth.types import SignUpWithEmailAndPasswordCredentials

from app.forms import AnimalForm, CadastroForm, FotoForm, LoginForm
from app.auth import INDISPONIVEL, novo_cliente, registrar_auth
from app.fotos import FotoInvalida, processar_foto

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
    if request.endpoint in ("anunciar", "adicionar_foto"):
        # Uma imagem de até 3 MiB mais campos e envelope multipart.
        request.max_content_length = 4 * 1024 * 1024
    if request.endpoint == "adicionar_foto" and not app.secret_key:
        return render_template("foto.html", mensagem=INDISPONIVEL), 503
    if request.endpoint in ("login", "minha_conta", "logout", "anunciar", "editar_anuncio", "publicar_anuncio") and not app.secret_key:
        return render_template("login.html", indisponivel=True,
                               mensagem="Acesso à conta indisponível no momento. Tente novamente mais tarde."), 503
    if request.endpoint == "cadastro" and not app.secret_key:
        return render_template("cadastro.html", indisponivel=True,
                               mensagem="Cadastro indisponível no momento. Tente novamente mais tarde."), 503


registrar_auth(app)
CSRFProtect(app)


@app.errorhandler(413)
def arquivo_grande(error):
    if request.endpoint == "adicionar_foto":
        return render_template("foto.html", mensagem=(
            "O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB. "
            "Recarregue a página para conferir o rascunho antes de enviar.")), 413
    return render_template("anunciar.html", form=AnimalForm(OPCOES_FILTROS, formdata=None),
                           mensagem="O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB e preencha o formulário novamente."), 413


@app.errorhandler(CSRFError)
def erro_csrf(error):
    if request.endpoint == "adicionar_foto":
        # Não oferecer upload sem consultar novamente propriedade e reservas.
        return render_template("foto.html", mensagem=(
            "Não foi possível validar o formulário. Recarregue a página e tente novamente.")), 400
    if request.endpoint == "editar_anuncio":
        return render_template("anunciar.html", form=AnimalForm(OPCOES_FILTROS),
                               edicao=True, animal_id=request.view_args["animal_id"],
                               mensagem="Não foi possível validar o formulário. Recarregue a página e tente novamente."), 400
    if request.endpoint == "anunciar":
        return render_template("anunciar.html", form=AnimalForm(OPCOES_FILTROS),
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
                     .select(",".join(("id", "usuario_id", "status_publicacao", *campos)))
                     .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").execute().data)
        if registros == []:
            abort(404)
        if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
            raise ValueError("Resposta de leitura inválida")
        animal = registros[0]
        if any(campo not in animal for campo in ("id", "usuario_id", "status_publicacao")):
            raise ValueError("Identificação de rascunho ausente")
        if (animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id)
                or animal.get("status_publicacao") != "rascunho"):
            abort(404)
        opcionais = ("especie_outro", "bairro", "email_contato", "whatsapp")
        if any(campo not in animal or (
                not isinstance(animal[campo], str)
                and not (campo in opcionais and animal[campo] is None)) for campo in campos):
            raise ValueError("Dados de rascunho inválidos")
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", **contexto, indisponivel=True, mensagem=mensagem), 503
    if request.method == "GET":
        form = AnimalForm(OPCOES_FILTROS, formdata=None, data=animal)
        return render_template("anunciar.html", **contexto, form=form)
    if not form.validate_on_submit():
        return render_template("anunciar.html", **contexto, form=form), 400
    dados = form.dados_insert()
    mensagem = ("Não foi possível confirmar a atualização do rascunho. "
                "Confira seus anúncios antes de tentar novamente.")
    try:
        # A condição também no UPDATE protege contra publicação, remoção ou
        # mudança de proprietário entre a leitura e a gravação.
        registros = (g.auth_cliente.table("animais").update(dados)
                     .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").execute().data)
        if registros == []:
            return render_template("anunciar.html", **contexto, form=form, mensagem=(
                "O rascunho não pôde ser atualizado. Volte à sua conta e confira se ele ainda está disponível para edição.")), 409
        if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
            raise ValueError("Resposta de UPDATE inválida")
        atualizado = registros[0]
        if (atualizado.get("id") != str(animal_id) or atualizado.get("usuario_id") != str(g.usuario.id)
                or atualizado.get("status_publicacao") != "rascunho"
                or any(campo not in atualizado or atualizado[campo] != valor for campo, valor in dados.items())):
            raise ValueError("Atualização não confirmada")
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("anunciar.html", **contexto, form=form, mensagem=mensagem), 503
    flash("Rascunho atualizado com sucesso!", "rascunho-sucesso")
    return redirect(url_for("minha_conta"))


def consultar_publicacao_propria(animal_id):
    """Lê apenas o estado do anúncio próprio; nunca aceita outra identidade."""
    registros = (g.auth_cliente.table("animais")
                 .select("id,usuario_id,status_publicacao")
                 .eq("id", str(animal_id)).eq("usuario_id", str(g.usuario.id))
                 .execute().data)
    if registros == []:
        return None
    if not isinstance(registros, list) or len(registros) != 1 or not isinstance(registros[0], dict):
        raise ValueError("Resposta de leitura inválida")
    animal = registros[0]
    if animal.get("id") != str(animal_id) or animal.get("usuario_id") != str(g.usuario.id):
        return None
    if animal.get("status_publicacao") not in ("rascunho", "publicado"):
        raise ValueError("Estado de publicação inválido")
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
    if estado is None:
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


@app.route("/anuncios/<uuid:animal_id>/foto", methods=["GET", "POST"])
def adicionar_foto(animal_id):
    if g.auth_indisponivel:
        return render_template("foto.html", mensagem=INDISPONIVEL), 503
    if not g.usuario:
        return redirect(url_for("login"))
    animal_id = str(animal_id)
    try:
        registros = (g.auth_cliente.table("animais").select("id,usuario_id,status_publicacao")
                     .eq("id", animal_id).eq("usuario_id", str(g.usuario.id))
                     .eq("status_publicacao", "rascunho").execute().data)
        if registros == []:
            abort(404)
        if (not isinstance(registros, list) or len(registros) != 1
                or not isinstance(registros[0], dict)
                or any(campo not in registros[0] for campo in ("id", "usuario_id", "status_publicacao"))):
            raise ValueError("Resposta de rascunho inválida")
        animal = registros[0]
        if (animal["id"] != animal_id or animal["usuario_id"] != str(g.usuario.id)
                or animal["status_publicacao"] != "rascunho"):
            abort(404)
        # Consultar todas as reservas, inclusive pendentes e em remoção.
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
    if publicar and not (form.email_contato.data or form.whatsapp.data):
        form.email_contato.errors.append("Para publicar, informe pelo menos um contato válido: e-mail ou WhatsApp.")
        return render_template("anunciar.html", form=form), 400
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
                    .select("id,nome,especie,sexo,idade_aproximada,porte,descricao,distrito")
                    .eq("status_publicacao", "publicado")
                    .eq("status_adocao", "disponivel").eq("oculto_admin", False))
        for parametro, coluna in (("tipo", "especie"), ("porte", "porte"), ("distrito", "distrito")):
            if filtros[parametro]:
                consulta = consulta.eq(coluna, filtros[parametro])
        registros = consulta.order("criado_em", desc=True).execute().data
        animais_publicos = validar_catalogo(registros)
    except (APIError, HTTPError, SupabaseException, ValueError, TypeError):
        return render_template("animais.html", **contexto, indisponivel=True), 503
    return render_template("animais.html", **contexto, animais=animais_publicos)


def validar_catalogo(registros):
    """Recusa respostas incompletas e limita os dados enviados ao template."""
    if not isinstance(registros, list):
        raise ValueError("Resposta de catálogo inválida")
    limites = dict(id=36, nome=100, especie=30, sexo=5, idade_aproximada=50,
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
                or registro["sexo"] not in ("Macho", "Fêmea")):
            raise ValueError("Opções de catálogo inválidas")
        animais.append({campo: registro[campo] for campo in limites})
    return animais


@app.get("/animais/<int:id>")
def animal_detalhes(id):
    for animal in ANIMAIS:
        if animal["id"] == id:
            return render_template("animal_detalhes.html", animal=animal)
    abort(404)


if __name__ == "__main__":
    app.run()
