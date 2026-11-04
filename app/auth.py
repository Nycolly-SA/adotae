"""Sessão Supabase em cookies HttpOnly, sem armazenamento em disco ou memória global."""

import logging
import re

from flask import current_app, flash, g, redirect, render_template, request, session, url_for
from flask_wtf import FlaskForm
from httpx import HTTPError
from supabase import AuthApiError, AuthError, ClientOptions, SupabaseException, create_client
from supabase_auth.errors import AuthRetryableError

from app.forms import LoginForm

ACCESS_COOKIE = "adotae_access"
REFRESH_COOKIE = "adotae_refresh"
INDISPONIVEL = "Não foi possível acessar sua conta agora. Tente novamente mais tarde."
FALHAS = (AuthError, HTTPError, SupabaseException, ValueError, IndexError, KeyError)
CONFIRMACAO_INVALIDA = "Este link de confirmação é inválido, expirou ou já foi utilizado."
CONFIRMACAO_INDISPONIVEL = "Não foi possível confirmar seu e-mail agora. Tente novamente mais tarde."


class OcultarConfirmacao(logging.Filter):
    """Remove a query sensível dos registros de acesso do servidor local."""

    def filter(self, record):
        record.msg = re.sub(r"(/auth/confirm)\?[^\s]*", r"\1?[oculto]", record.getMessage())
        record.args = ()
        return True


def novo_cliente():
    config = current_app.config
    if not config["SUPABASE_URL"] or not config["SUPABASE_PUBLISHABLE_KEY"]:
        raise ValueError("Configuração de autenticação ausente")
    return create_client(
        config["SUPABASE_URL"], config["SUPABASE_PUBLISHABLE_KEY"],
        options=ClientOptions(persist_session=False, auto_refresh_token=False),
    )


def registrar_auth(app):
    logging.getLogger("werkzeug").addFilter(OcultarConfirmacao())

    @app.before_request
    def carregar_usuario():
        g.usuario = None
        g.auth_indisponivel = False
        g.auth_tokens = None
        g.limpar_auth = False
        if request.endpoint in ("static", "confirmar_email"):
            return
        access = request.cookies.get(ACCESS_COOKIE)
        refresh = request.cookies.get(REFRESH_COOKIE)
        if not access and not refresh:
            return
        if not access or not refresh:
            g.limpar_auth = True
            return
        try:
            cliente = novo_cliente()
            # O SDK renova access tokens expirados. Seus dados locais não são
            # prova de identidade: get_user consulta o servidor em todos os casos.
            resposta = cliente.auth.set_session(access, refresh)
            if not resposta.session:
                g.limpar_auth = True
                return
            atual = resposta.session
            identidade = cliente.auth.get_user(atual.access_token)
            if not identidade or not identidade.user or not identidade.user.email_confirmed_at:
                g.limpar_auth = True
                return
            g.usuario = identidade.user
            g.auth_cliente = cliente
            if (atual.access_token, atual.refresh_token) != (access, refresh):
                g.auth_tokens = atual
        except AuthApiError as error:
            if error.status >= 500 or error.status == 429:
                g.auth_indisponivel = True
            else:
                g.limpar_auth = True
        except (AuthRetryableError, HTTPError, SupabaseException):
            g.auth_indisponivel = True
        except (AuthError, ValueError, IndexError, KeyError):
            g.limpar_auth = True

    @app.after_request
    def cookies_auth(response):
        opcoes = dict(httponly=True, secure=app.config["SESSION_COOKIE_SECURE"],
                      samesite="Lax", path="/")
        if getattr(g, "limpar_auth", False):
            for nome in (ACCESS_COOKIE, REFRESH_COOKIE):
                response.delete_cookie(nome, **opcoes)
        elif getattr(g, "auth_tokens", None):
            # Cookies de navegador: a validade efetiva é controlada pelo Supabase.
            response.set_cookie(ACCESS_COOKIE, g.auth_tokens.access_token, **opcoes)
            response.set_cookie(REFRESH_COOKIE, g.auth_tokens.refresh_token, **opcoes)
        if (request.endpoint in ("login", "minha_conta", "logout", "cadastro", "confirmar_email")
                or session.modified
                or request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
                or getattr(g, "auth_tokens", None)):
            response.headers["Cache-Control"] = "no-store"
        if request.endpoint == "confirmar_email":
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/auth/confirm")
    def confirmar_email():
        if not app.secret_key:
            # Sem assinatura de sessão não é possível transportar um aviso confiável.
            return render_template("index.html", avisos_confirmacao=[
                ("confirmacao-erro", CONFIRMACAO_INDISPONIVEL),
            ]), 503
        mensagem, categoria = CONFIRMACAO_INVALIDA, "confirmacao-erro"
        token_hash = request.args.get("token_hash", "")
        if token_hash.strip() and request.args.get("type") == "email":
            mensagem = CONFIRMACAO_INDISPONIVEL
            try:
                cliente = novo_cliente()
                resposta = cliente.auth.verify_otp({"token_hash": token_hash, "type": "email"})
                if resposta and resposta.user and resposta.user.email_confirmed_at:
                    mensagem = ("E-mail confirmado com sucesso! Sua conta está pronta. "
                                "Você já pode entrar no Adotaê.")
                    categoria = "confirmacao-sucesso"
                # Não copiar a sessão retornada para g, cookies ou sessão Flask.
            except AuthApiError as error:
                if error.status < 500 and error.status != 429:
                    mensagem = CONFIRMACAO_INVALIDA
            except FALHAS:
                pass
        # Um novo resultado substitui avisos de confirmação ainda não exibidos.
        session["_flashes"] = [
            (tipo, texto) for tipo, texto in session.get("_flashes", [])
            if tipo not in ("confirmacao-sucesso", "confirmacao-erro")
        ]
        flash(mensagem, categoria)
        return redirect(url_for("inicio"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.usuario:
            return redirect(url_for("minha_conta"))
        form = LoginForm()
        if request.method == "GET":
            return render_template("login.html", form=form)
        if not form.validate_on_submit():
            return render_template("login.html", form=form), 400
        mensagem, status = INDISPONIVEL, 503
        try:
            cliente = novo_cliente()
            resposta = cliente.auth.sign_in_with_password({
                "email": form.email.data, "password": form.senha.data,
            })
            if resposta.session and resposta.user and resposta.user.email_confirmed_at:
                session.clear()  # Também troca o segredo CSRF após a autenticação.
                g.limpar_auth = False
                g.auth_tokens = resposta.session
                return redirect(url_for("minha_conta"))
            mensagem, status = "Confirme seu e-mail antes de entrar.", 400
        except AuthApiError as error:
            if error.code == "email_not_confirmed":
                mensagem, status = "Confirme seu e-mail antes de entrar.", 400
            elif error.code == "invalid_credentials":
                mensagem, status = "E-mail ou senha incorretos. Confira os dados e tente novamente.", 400
            elif error.status == 429:
                mensagem, status = "Muitas tentativas. Aguarde alguns minutos e tente novamente.", 429
        except FALHAS:
            pass
        return render_template("login.html", form=form, mensagem=mensagem), status

    @app.get("/minha-conta")
    def minha_conta():
        if g.auth_indisponivel:
            return render_template("login.html", indisponivel=True, mensagem=INDISPONIVEL), 503
        if not g.usuario:
            return redirect(url_for("login"))
        return render_template("minha_conta.html", form=FlaskForm())

    @app.post("/logout")
    def logout():
        # O CSRFProtect já validou o POST, inclusive sem uma sessão Supabase válida.
        falhou = g.auth_indisponivel
        try:
            if g.usuario:
                g.auth_cliente.auth.sign_out({"scope": "local"})
        except FALHAS:
            falhou = True
        finally:
            session.clear()
            g.usuario = None
            g.auth_tokens = None
            g.limpar_auth = True
        if falhou:
            return render_template("login.html", indisponivel=True, mensagem=(
                "Você saiu da conta neste navegador. Não foi possível confirmar o encerramento "
                "da sessão no serviço de autenticação."))
        return redirect(url_for("login"))
