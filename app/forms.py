"""Validação dos formulários; senhas são gerenciadas pelo Supabase."""

import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import EmailField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp, ValidationError


class LoginForm(FlaskForm):
    email = EmailField("E-mail", filters=[lambda value: value.strip() if value else ""],
                       validators=[DataRequired(message="Informe seu e-mail."),
                                   Email(message="Informe um e-mail válido.", test_environment=True),
                                   Length(max=254, message="Informe um e-mail válido.")])
    senha = PasswordField("Senha", validators=[DataRequired(message="Informe sua senha.")])


class CadastroForm(FlaskForm):
    nome = StringField("Nome", filters=[lambda value: value.strip() if value else ""],
                       validators=[DataRequired(message="Informe seu nome."),
                                   Length(max=100, message="Use até 100 caracteres no nome.")])
    email = EmailField("E-mail", filters=[lambda value: value.strip() if value else ""],
                       validators=[DataRequired(message="Informe seu e-mail."),
                                   Email(message="Informe um e-mail válido."),
                                   Length(max=254, message="Informe um e-mail válido.")])
    senha = PasswordField("Senha", validators=[
        DataRequired(message="Informe sua senha."),
        Length(min=8, message="A senha deve ter pelo menos 8 caracteres."),
    ])


class FotoForm(FlaskForm):
    foto = FileField("Fotografia", render_kw={"accept": "image/jpeg,image/png,image/webp"})


def limpar_texto(value):
    return value.strip() if value else ""


def limpar_opcional(value):
    return limpar_texto(value) or None


def limpar_whatsapp(value):
    if not value or not value.strip():
        return None
    # Remove a formatação usual; letras e outros caracteres continuam inválidos.
    return re.sub(r"[\s()+.\-]", "", value)


class SelecaoAnimal(SelectField):
    def pre_validate(self, form):
        try:
            super().pre_validate(form)
        except ValidationError:
            raise ValidationError("Selecione uma opção válida.") from None


class AnimalForm(FlaskForm):
    foto = FileField("Fotografia", render_kw={"accept": "image/jpeg,image/png,image/webp"})
    nome = StringField("Nome do animal", filters=[limpar_texto], validators=[
        DataRequired(message="Informe o nome do animal."),
        Length(max=100, message="Use até 100 caracteres no nome."),
    ], render_kw={"maxlength": 100})
    especie = SelecaoAnimal("Espécie", validators=[DataRequired(message="Selecione a espécie.")])
    especie_outro = StringField("Qual outro animal doméstico?", filters=[limpar_opcional],
                                validators=[Optional(), Length(max=60, message="Use até 60 caracteres.")],
                                render_kw={"maxlength": 60})
    sexo = SelecaoAnimal("Sexo", choices=[("", "Selecione o sexo"), ("Macho", "Macho"), ("Fêmea", "Fêmea"),
                                       ("Não identificado", "Não identificado")],
                       validators=[DataRequired(message="Selecione o sexo.")])
    idade_aproximada = StringField("Idade aproximada", filters=[limpar_texto], validators=[
        DataRequired(message="Informe a idade aproximada."),
        Length(max=50, message="Use até 50 caracteres na idade aproximada."),
    ], render_kw={"maxlength": 50})
    porte = SelecaoAnimal("Porte", validators=[DataRequired(message="Selecione o porte.")])
    descricao = TextAreaField("Descrição", filters=[limpar_texto], validators=[
        DataRequired(message="Descreva o animal."),
        Length(max=2000, message="Use até 2.000 caracteres na descrição."),
    ], render_kw={"maxlength": 2000, "rows": 6})
    distrito = SelecaoAnimal("Distrito", validators=[DataRequired(message="Selecione o distrito.")])
    bairro = StringField("Bairro", filters=[limpar_opcional], validators=[
        Optional(), Length(max=100, message="Use até 100 caracteres no bairro."),
    ], render_kw={"maxlength": 100})
    email_contato = EmailField("E-mail de contato", filters=[limpar_opcional],
                               validators=[Optional(), Email(message="Informe um e-mail válido.")],
                               render_kw={"placeholder": "seuemail@exemplo.com"})
    whatsapp = StringField("WhatsApp", filters=[limpar_whatsapp], validators=[
        Optional(), Regexp(r"\A[1-9][0-9]{7,14}\Z", message=(
            "Informe um WhatsApp válido, com 8 a 15 dígitos.")),
    ], render_kw={"maxlength": 40, "type": "tel", "inputmode": "tel", "placeholder": "+55 (22) 99999-9999"})

    def __init__(self, opcoes_filtros, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.contato_ausente = False
        for field, key, instrucao in ((self.especie, "tipo", "Selecione a espécie"),
                                      (self.porte, "porte", "Selecione o porte"),
                                      (self.distrito, "distrito", "Selecione o distrito")):
            field.choices = [("", instrucao)] + [(opcao, opcao) for opcao in opcoes_filtros[key]]
        if self.especie.data != "Outro animal doméstico":
            self.especie_outro.data = None

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators)
        if self.especie.data == "Outro animal doméstico" and not self.especie_outro.data:
            self.especie_outro.errors.append("Informe qual é o outro animal doméstico.")
            valid = False
        return valid

    def dados_insert(self):
        # Campos do formulário permitidos no INSERT e na edição do rascunho.
        return {name: self[name].data for name in (
            "nome", "especie", "especie_outro", "sexo", "idade_aproximada", "porte",
            "descricao", "distrito", "bairro", "email_contato", "whatsapp",
        )}
