"""Validação do formulário de cadastro; senhas são gerenciadas pelo Supabase."""

from flask_wtf import FlaskForm
from wtforms import EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, Length


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
