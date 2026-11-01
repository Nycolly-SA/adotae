# Adotaê

Projeto acadêmico de Engenharia de Software com o objetivo de facilitar a adoção responsável de animais.

## Etapa atual

Esta primeira versão apresenta o projeto e um catálogo com quatro animais fictícios. O site se adapta a celulares e computadores. O catálogo é demonstrativo: ainda não é possível cadastrar animais ou solicitar uma adoção.

Em `/animais`, é possível combinar filtros por tipo de animal, porte e distrito. Use “Aplicar filtros” para consultar e “Limpar” para voltar ao catálogo completo. Os filtros são enviados por parâmetros GET na URL.

Cada cartão oferece “Ver detalhes”, que abre `/animais/<id>` com a imagem, os dados e a descrição do animal, além de um link de volta ao catálogo. A seção de contato é apenas demonstrativa, sem contatos ou ações funcionais. Identificadores inexistentes retornam HTTP 404.

Em `/cadastro`, é possível criar uma conta com nome, e-mail e senha de pelo menos 8 caracteres pelo Supabase Auth. O nome é enviado nos metadados `nome`. O formulário valida os dados no servidor e possui proteção CSRF. Após o envio, orienta a confirmação do e-mail, sem iniciar sessão. Login, logout e recuperação de senha ainda não foram implementados.

## Tecnologias

- Python e Flask
- HTML, CSS e JavaScript
- Supabase Auth (SDK oficial Python), python-dotenv e Flask-WTF

## Como executar

Com o Python 3.10 ou superior instalado, abra o terminal na pasta do projeto.

No Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

No Linux ou macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

A criação do ambiente e a instalação das dependências são necessárias apenas na primeira vez. Se o ambiente `.venv` já existir, pule o primeiro comando.

Depois, abra **http://localhost:5000** no navegador. Para encerrar, pressione `Ctrl+C` no terminal.

### Configurar o cadastro

Copie `.env.example` para `.env` e preencha `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (chave publicável, nunca administrativa) e `SECRET_KEY`. Se já existir um `.env`, acrescente apenas as variáveis ausentes. Gere um segredo aleatório com `python -c "import secrets; print(secrets.token_hex(32))"` e use o mesmo valor em todas as instâncias da aplicação. Não publique o `.env`. Sem `SECRET_KEY`, o cadastro fica indisponível; sem as variáveis Supabase, o envio fica indisponível.

No painel de um projeto gratuito do Supabase, habilite o provedor de e-mail e o cadastro de novos usuários, mantenha **Confirm email** ativado e configure **Site URL** com `http://localhost:5000` durante o desenvolvimento (a URL pública quando houver hospedagem). A confirmação usa o link padrão do Supabase; o Adotaê não consome nem guarda os tokens retornados no redirecionamento.

Sem SMTP próprio, o serviço padrão envia apenas para e-mails de membros da equipe do projeto e possui limites de envio. A validação manual deve usar um desses endereços. SMTP não foi configurado nesta etapa; o envio ao público geral fica pendente de uma decisão futura. Consulte as [limitações oficiais de envio](https://supabase.com/docs/guides/auth/auth-smtp).

## Testes

Os testes verificam páginas, catálogo, arquivos estáticos e cadastro (validações, CSRF, metadados, isolamento de clientes e tratamento de erros). Os testes de cadastro usam mocks, sem chamadas reais ao Supabase e sem credenciais reais. Para executá-los no Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

No Linux ou macOS, use `.venv/bin/python -m unittest discover -v`.
