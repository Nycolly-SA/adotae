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

from app.forms import AnimalForm, CadastroForm, LoginForm
from app.auth import INDISPONIVEL, registrar_auth
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
    if request.endpoint == "anunciar":
        # Uma imagem de até 3 MiB mais campos e envelope multipart.
        request.max_content_length = 4 * 1024 * 1024
    if request.endpoint in ("login", "minha_conta", "logout", "anunciar") and not app.secret_key:
        return render_template("login.html", indisponivel=True,
                               mensagem="Acesso à conta indisponível no momento. Tente novamente mais tarde."), 503
    if request.endpoint == "cadastro" and not app.secret_key:
        return render_template("cadastro.html", indisponivel=True,
                               mensagem="Cadastro indisponível no momento. Tente novamente mais tarde."), 503


registrar_auth(app)
CSRFProtect(app)


@app.errorhandler(413)
def arquivo_grande(error):
    return render_template("anunciar.html", form=AnimalForm(OPCOES_FILTROS, formdata=None),
                           mensagem="O envio excedeu o limite de 4 MiB. Use uma fotografia de até 3 MiB e preencha o formulário novamente."), 413


@app.errorhandler(CSRFError)
def erro_csrf(error):
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

# Dados temporários para apresentar a interface. Não representam animais reais.
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
    foto = None
    try:
        arquivos = request.files.getlist("foto")
        if len(arquivos) > 1:
            raise FotoInvalida("Envie apenas uma fotografia nesta etapa.")
        if form.foto.data:
            foto = processar_foto(form.foto.data)
    except FotoInvalida as erro:
        form.foto.errors.append(str(erro))
        return render_template("anunciar.html", form=form), 400
    mensagem = "Não foi possível salvar o rascunho agora. Tente novamente mais tarde."
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
        etapa = "reserva"
        try:
            resposta = g.auth_cliente.table("animal_fotos").insert({
                "animal_id": animal["id"], "posicao": 1,
            }).execute()
            reservas = resposta.data
            if not isinstance(reservas, list) or len(reservas) != 1:
                raise ValueError("Reserva não confirmada")
            reserva = reservas[0]
            if (not isinstance(reserva, dict) or reserva.get("animal_id") != animal["id"]
                    or reserva.get("posicao") != 1 or reserva.get("estado") != "pendente"):
                raise ValueError("Reserva inválida")
            foto_id = str(UUID(str(reserva.get("id"))))
            caminho = f"{g.usuario.id}/{animal['id']}/{foto_id}.webp"
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
                    or registros[0] != dict(id=foto_id, animal_id=animal["id"], posicao=1, estado="pronta")):
                raise ValueError("Fotografia pronta não confirmada")
        except (APIError, StorageApiError, HTTPError, SupabaseException, ValueError, TypeError):
            # Não apagar reserva/objeto nem reenviar: uma resposta perdida pode
            # corresponder a uma operação concluída. A posição continua ocupada.
            avisos = {
                "reserva": "Não foi possível confirmar a reserva da fotografia; o envio não foi iniciado.",
                "upload": "O envio da fotografia falhou ou não pôde ser confirmado.",
                "confirmação": "A fotografia foi enviada, mas não foi possível confirmar que está pronta.",
            }
            flash("O rascunho foi criado. " + avisos[etapa] +
                  " A fotografia não foi confirmada como adicionada. Pode haver uma reserva ou arquivo "
                  "aguardando recuperação, ainda indisponível pela interface. Não reenvie o formulário "
                  "para tentar adicionar a foto, pois isso criaria outro anúncio.", "rascunho-aviso")
            return redirect(url_for("minha_conta"))
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

    animais_filtrados = [
        animal for animal in ANIMAIS
        if (not filtros["tipo"] or animal["especie"] == filtros["tipo"])
        and (not filtros["porte"] or animal["porte"] == filtros["porte"])
        and (not filtros["distrito"] or animal["distrito"] == filtros["distrito"])
    ]
    return render_template(
        "animais.html",
        animais=animais_filtrados,
        filtros=filtros,
        opcoes_filtros=OPCOES_FILTROS,
    )


@app.get("/animais/<int:id>")
def animal_detalhes(id):
    for animal in ANIMAIS:
        if animal["id"] == id:
            return render_template("animal_detalhes.html", animal=animal)
    abort(404)


if __name__ == "__main__":
    app.run()
